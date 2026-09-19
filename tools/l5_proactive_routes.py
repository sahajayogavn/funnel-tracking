"""
Time-aware proactive routes for the seeker-care MAS.
prd:mas-time-aware-001 (P2)

Every route here produces a *notification* or an *internal decision item*.
Drafts for seekers are generated only after a human approves a session, and
even then they land in the outbound action queue for a volunteer to send.

Routes:
  * code:route-class-reminder-001   run_class_reminder_digest  (daily 08:00–09:00)
                                    run_session_open_cycle     (every few minutes)
  * code:route-post-session-001     run_post_session_checklist (daily, same job)
                                    run_attendance_sync        (every few minutes)
  * code:route-registration-sla-001 run_registration_sla_cycle (every 30 min)
  * code:route-morning-brief-001    run_morning_brief          (daily 08:00)
"""
import json
import logging
from datetime import datetime, timedelta

from fb_pipeline.contracts.l1_class_schedule import (
    describe_session, recent_sessions, upcoming_sessions,
)
from fb_pipeline.contracts.l1_conversation_state import (
    ACTION_REPLY, ACTION_REPLY_LATE, compute_conversation_state, format_now_context,
)
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, log_mas_decision

logger = logging.getLogger("proactive_routes")

REMINDER_STAGES = ("Seeker_Public_Program", "Seeker_18_Weeks", "Registered", "Public Program Seeker")
REMINDER_RECENT_DAYS = 21
REMINDER_WINDOW_HOURS = 36
POST_SESSION_LOOKBACK_HOURS = 24
SLA_REALERT_HOURS = 12
PAGE_ID_DEFAULT = "1548373332058326"


# --------------------------------------------------------------------------- helpers

def _rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(sql: str, params: tuple = ()) -> int:
    conn = get_db_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _eligible_seekers_for_session(session, now: datetime) -> list[dict]:
    """Seekers registered for this class who interacted recently and were not
    yet reminded for this session date."""
    since = (now - timedelta(days=REMINDER_RECENT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" for _ in REMINDER_STAGES)
    return _rows(
        f"""
        SELECT u.thread_id, u.thread_name AS name, u.city, u.lead_stage, u.phone, u.program_code,
               u.last_interaction
        FROM users u
        WHERE u.program_code = ?
          AND u.last_interaction >= ?
          AND (u.lead_stage IN ({placeholders}) OR (u.phone IS NOT NULL AND u.phone != ''))
          AND NOT EXISTS (
              SELECT 1 FROM reminder_log r
              WHERE r.thread_id = u.thread_id AND r.class_key = ? AND r.session_date = ?
          )
        ORDER BY u.last_interaction DESC
        """,
        (session.class_key, since, *REMINDER_STAGES, session.class_key, session.session_date),
    )


def _session_target_id(session) -> str:
    return f"{session.class_key}|{session.session_date}"


def _active_internal_item(queue_type: str, target_id: str) -> dict | None:
    rows = _rows(
        "SELECT id, status FROM action_queue WHERE queue_type=? AND target_id=? "
        "AND status IN ('pending','approved','executing') ORDER BY id DESC LIMIT 1",
        (queue_type, target_id),
    )
    return rows[0] if rows else None


def _prior_page_lines(thread_id: str, limit: int = 3) -> list[str]:
    rows = _rows(
        "SELECT content FROM messages WHERE thread_id=? AND sender='Page' AND kind='message' "
        "ORDER BY seq DESC LIMIT ?", (thread_id, limit),
    )
    return [r["content"][:160] for r in rows]


# --------------------------------------------------------------------------- P2.1 reminder digest

# code:route-class-reminder-001:digest
def run_class_reminder_digest(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True,
                              now: datetime | None = None) -> dict:
    """Daily job: one `session_proposal` per upcoming class that has eligible
    seekers, plus one Telegram digest. No LLM, no drafts yet."""
    from tools.l5_action_queue import enqueue_action
    from tools.l5_telegram_hitl import send_proposal_to_telegram

    now = now or datetime.now()
    proposals = []
    for session in upcoming_sessions(now, window_hours=REMINDER_WINDOW_HOURS):
        seekers = _eligible_seekers_for_session(session, now)
        if not seekers:
            continue
        target_id = _session_target_id(session)
        if _active_internal_item("session_proposal", target_id):
            logger.info("[REMINDER] session %s already proposed", target_id)
            continue
        label = describe_session(session, now)
        names = ", ".join(s["name"] for s in seekers[:8]) + (" …" if len(seekers) > 8 else "")
        digest = (
            f"📅 {label}\n"
            f"{len(seekers)} seeker đã ghi danh: {names}\n"
            f"👍 Mở phiên nhắc (MAS sẽ soạn nháp từng người vào /queues) · 👎 Bỏ qua"
        )
        payload = {
            "type": "class_reminder",
            "session": session.to_dict(),
            "seekers": [{"thread_id": s["thread_id"], "name": s["name"]} for s in seekers],
            "count": len(seekers),
        }
        if dry_run:
            proposals.append({"session": label, "count": len(seekers), "dry_run": True})
            continue
        log_mas_decision(page_id, "class_reminder", "class_session", target_id, "proposed",
                         f"{len(seekers)} eligible", dry_run=False, payload=payload)
        action_id = enqueue_action(
            queue_type="session_proposal", page_id=page_id, target_type="class_session",
            target_id=target_id, target_name=label, action_text=digest, payload=payload,
        )
        msg_id = send_proposal_to_telegram(
            route="reminder", thread_id=target_id, proposed_text=digest,
            payload={"action_queue_id": action_id, **payload},
        )
        proposals.append({"session": label, "count": len(seekers), "action_queue_id": action_id,
                          "telegram_message_id": msg_id})
    logger.info("[REMINDER] %d session proposal(s)", len(proposals))
    return {"status": "complete", "proposals": proposals}


# code:route-class-reminder-001:open-session
def run_session_open_cycle(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True) -> dict:
    """Turn approved `session_proposal` items into per-seeker reminder drafts."""
    from tools.l5_action_queue import enqueue_action, finish_action, has_active_proposal
    from tools.l5_scheduler_adk import run_adk_class_reminder
    from tools.l5_telegram_hitl import send_telegram_notification

    approved = _rows(
        "SELECT id, target_id, target_name, payload_json FROM action_queue "
        "WHERE queue_type='session_proposal' AND status='approved' ORDER BY id"
    )
    opened = []
    now = datetime.now()
    now_context = format_now_context(now)
    for item in approved:
        payload = json.loads(item["payload_json"] or "{}")
        session = payload.get("session") or {}
        seekers = payload.get("seekers") or []
        if dry_run:
            # A dry-run is a read-only preview: it neither claims the session
            # nor produces durable drafts/reminder evidence.
            opened.append({
                "session_proposal_id": item["id"], "drafted": 0, "failed": 0,
                "would_draft": len(seekers), "dry_run": True,
            })
            continue
        _exec("UPDATE action_queue SET status='executing', claimed_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
              (item["id"],))
        drafted, failed = 0, 0
        for seeker_ref in seekers:
            thread_id = seeker_ref["thread_id"]
            seeker_rows = _rows("SELECT thread_id, thread_name AS name, city, lead_stage, phone, program_code FROM users WHERE thread_id=?", (thread_id,))
            if not seeker_rows:
                continue
            seeker = seeker_rows[0]
            if has_active_proposal(thread_id, "proactive_message", "class_reminder"):
                continue
            try:
                text = run_adk_class_reminder(
                    seeker, session, _prior_page_lines(thread_id), now_context, page_id=page_id,
                    trigger="scheduler",
                )
                if not text.strip():
                    failed += 1
                    continue
                draft_id = enqueue_action(
                    queue_type="proactive_message", page_id=page_id, target_type="thread",
                    target_id=thread_id, target_name=seeker["name"], action_text=text,
                    payload={"type": "class_reminder", "session": session,
                             "session_proposal_id": item["id"], "trigger": "class_reminder"},
                )
                _exec(
                    "INSERT OR IGNORE INTO reminder_log (thread_id, class_key, session_date, session_proposal_id, draft_action_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (thread_id, session.get("class_key"), session.get("session_date"), item["id"], draft_id),
                )
                drafted += 1
            except Exception as exc:  # one seeker failing must not sink the session
                logger.exception("[REMINDER] draft failed for %s: %s", thread_id, exc)
                failed += 1
        finish_action(item["id"], None if drafted or not seekers else "no drafts produced")
        summary = f"✅ Phiên nhắc « {item['target_name']} »: {drafted} nháp đã vào /queues" + (f", {failed} lỗi" if failed else "")
        if not dry_run:
            send_telegram_notification(summary)
        opened.append({"session_proposal_id": item["id"], "drafted": drafted, "failed": failed})
    return {"status": "complete", "opened": opened}


# --------------------------------------------------------------------------- P2.2 attendance

# code:route-post-session-001:checklist
def run_post_session_checklist(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True,
                               now: datetime | None = None) -> dict:
    """Morning after a reminded session: one `attendance_check` item per seeker
    (approve = attended, reject = absent) plus one Telegram digest."""
    from tools.l5_action_queue import enqueue_action
    from tools.l5_telegram_hitl import send_proposal_to_telegram

    now = now or datetime.now()
    created = []
    for session in recent_sessions(now, lookback_hours=POST_SESSION_LOOKBACK_HOURS):
        reminded = _rows(
            "SELECT r.thread_id, u.thread_name AS name FROM reminder_log r JOIN users u ON u.thread_id=r.thread_id "
            "WHERE r.class_key=? AND r.session_date=? AND NOT EXISTS ("
            "  SELECT 1 FROM attendance a WHERE a.thread_id=r.thread_id AND a.class_key=r.class_key AND a.session_date=r.session_date)",
            (session.class_key, session.session_date),
        )
        if not reminded:
            continue
        label = describe_session(session, now)
        items = []
        for seeker in reminded:
            target_id = f"{seeker['thread_id']}|{session.class_key}|{session.session_date}"
            if _active_internal_item("attendance_check", target_id):
                continue
            text = f"📋 {seeker['name']} — có mặt tại « {label} »? 👍 Có · 👎 Không"
            payload = {"type": "attendance", "thread_id": seeker["thread_id"], "session": session.to_dict()}
            if dry_run:
                items.append({"name": seeker["name"], "dry_run": True})
                continue
            action_id = enqueue_action(
                queue_type="attendance_check", page_id=page_id, target_type="thread",
                target_id=target_id, target_name=seeker["name"], action_text=text, payload=payload,
            )
            msg_id = send_proposal_to_telegram(route="attendance", thread_id=seeker["thread_id"],
                                               proposed_text=text, payload={"action_queue_id": action_id, **payload})
            items.append({"name": seeker["name"], "action_queue_id": action_id, "telegram_message_id": msg_id})
        if items:
            created.append({"session": label, "items": items})
    return {"status": "complete", "sessions": created}


# code:route-post-session-001:sync
def run_attendance_sync(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True) -> dict:
    """Record decided `attendance_check` items into `attendance`, then promote
    or schedule follow-ups."""
    from tools.l5_action_queue import finish_action
    from adk_agents.tools.l5_stage_tools import evaluate_stage_gate

    decided = _rows(
        "SELECT id, status, target_id, target_name, payload_json FROM action_queue "
        "WHERE queue_type='attendance_check' AND status IN ('approved','rejected') "
        "AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.thread_id || '|' || a.class_key || '|' || a.session_date = action_queue.target_id)"
    )
    recorded = []
    for item in decided:
        payload = json.loads(item["payload_json"] or "{}")
        session = payload.get("session") or {}
        thread_id = payload.get("thread_id")
        attended = 1 if item["status"] == "approved" else 0
        if dry_run:
            recorded.append({"thread_id": thread_id, "attended": attended, "dry_run": True})
            continue
        _exec(
            "INSERT OR IGNORE INTO attendance (thread_id, class_key, session_date, attended, source) VALUES (?, ?, ?, ?, ?)",
            (thread_id, session.get("class_key"), session.get("session_date"), attended, "hitl"),
        )
        log_mas_decision(page_id, "attendance", "thread", thread_id, "attended" if attended else "absent",
                         session.get("class_key"), dry_run=False, payload=payload)
        if attended:
            # Attendance is the evidence UC-09 asks for before a stage move.
            stage = evaluate_stage_gate(thread_id)
            if stage.get("promoted"):
                log_mas_decision(page_id, "stage_gate", "thread", thread_id, "promoted",
                                 stage.get("reason"), dry_run=False, payload=stage)
        if item["status"] == "approved":
            finish_action(item["id"])
        recorded.append({"thread_id": thread_id, "attended": attended})
    return {"status": "complete", "recorded": recorded}


# --------------------------------------------------------------------------- P2.3 SLA alert

# code:route-registration-sla-001
def run_registration_sla_cycle(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True,
                               now: datetime | None = None) -> dict:
    """Ping Telegram when a seeker left a phone number and no human replied
    within REGISTRATION_SLA_HOURS. Pure notification, no draft."""
    from adk_agents.tools.seeker_tools import find_unreplied_threads, get_thread_messages
    from tools.l5_telegram_hitl import send_telegram_notification

    now = now or datetime.now()
    alerts = []
    unreplied = find_unreplied_threads(page_id, limit=200)
    for thread in unreplied.get("threads", []):
        msgs = get_thread_messages(thread["thread_id"])["messages"]
        state = compute_conversation_state(msgs, now=now)
        if not state.sla_breached:
            continue
        recent_alert = _rows(
            "SELECT 1 FROM sla_alerts WHERE thread_id=? AND last_customer_at=? AND alerted_at >= datetime('now', ?)",
            (thread["thread_id"], state.last_customer_at, f"-{SLA_REALERT_HOURS} hours"),
        )
        if recent_alert:
            continue
        hours = int(state.age_hours or 0)
        text = (f"⏰ SLA đăng ký: {thread['thread_name']} để SĐT {state.phone} lúc "
                f"{(state.last_customer_at or '')[:16]} — {hours}h chưa có người xác nhận.\n"
                f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}")
        if not dry_run:
            log_mas_decision(page_id, "sla_alert", "thread", thread["thread_id"], "alerted",
                             f"{hours}h", dry_run=False, payload=state.to_dict())
            send_telegram_notification(text)
            _exec("INSERT INTO sla_alerts (thread_id, last_customer_at) VALUES (?, ?)",
                  (thread["thread_id"], state.last_customer_at))
        alerts.append({"thread_name": thread["thread_name"], "phone": state.phone, "hours": hours})
    return {"status": "complete", "alerts": alerts}


# --------------------------------------------------------------------------- P2.4 morning brief

# code:route-morning-brief-001
def build_morning_brief(page_id: str = PAGE_ID_DEFAULT, now: datetime | None = None) -> str:
    from adk_agents.tools.seeker_tools import find_unreplied_threads, get_thread_messages

    now = now or datetime.now()
    waiting, late, sla = [], [], []
    for thread in find_unreplied_threads(page_id, limit=200).get("threads", []):
        state = compute_conversation_state(get_thread_messages(thread["thread_id"])["messages"], now=now)
        if state.action in (ACTION_REPLY, ACTION_REPLY_LATE):
            (late if state.late else waiting).append((thread["thread_name"], int(state.age_hours or 0)))
        if state.sla_breached:
            sla.append(thread["thread_name"])
    pending = _rows("SELECT queue_type, COUNT(*) AS n FROM action_queue WHERE status='pending' GROUP BY queue_type")
    pending_map = {r["queue_type"]: r["n"] for r in pending}
    sessions = [describe_session(s, now) for s in upcoming_sessions(now, window_hours=REMINDER_WINDOW_HOURS)]

    lines = [f"🌅 Morning brief — {now.strftime('%d/%m %H:%M')}"]
    lines.append(f"💬 Chờ người thật trả lời: {len(waiting)} mới, {len(late)} đã quá 24h")
    for name, hours in sorted(late + waiting, key=lambda x: -x[1])[:6]:
        lines.append(f"   • {name} — {hours}h")
    if sla:
        lines.append(f"⏰ SLA đăng ký chưa xác nhận: {', '.join(sla[:5])}")
    if sessions:
        lines.append("📅 Lớp trong 36h tới: " + " | ".join(sessions))
    lines.append(
        f"🗂 /queues: {pending_map.get('reply_message', 0)} reply · "
        f"{pending_map.get('proactive_message', 0)} chủ động · "
        f"{pending_map.get('session_proposal', 0)} phiên nhắc · "
        f"{pending_map.get('attendance_check', 0)} điểm danh"
    )
    return "\n".join(lines)


def run_morning_brief(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True) -> dict:
    from tools.l5_telegram_hitl import send_telegram_notification

    text = build_morning_brief(page_id)
    logger.info("[BRIEF]\n%s", text)
    if not dry_run:
        send_telegram_notification(text)
    return {"status": "complete", "text": text}


# --------------------------------------------------------------------------- daily bundle

def run_daily_care_cycle(page_id: str = PAGE_ID_DEFAULT, dry_run: bool = True) -> dict:
    """08:00–09:00 bundle: events sync → brief → attendance checklist → reminder digest."""
    try:
        from tools.l5_events_sync import sync as sync_events  # code:events-import-001
        events = {k: v for k, v in sync_events(apply=not dry_run).items() if k != "events"}
    except Exception as exc:  # the file may be missing on a fresh checkout
        logger.warning("[CARE] events sync skipped: %s", exc)
        events = {"error": str(exc)}
    return {
        "events": events,
        "brief": run_morning_brief(page_id, dry_run),
        "attendance": run_post_session_checklist(page_id, dry_run),
        "reminders": run_class_reminder_digest(page_id, dry_run),
    }


__all__ = [
    "build_morning_brief", "run_attendance_sync", "run_class_reminder_digest", "run_daily_care_cycle",
    "run_morning_brief", "run_post_session_checklist", "run_registration_sla_cycle", "run_session_open_cycle",
]
