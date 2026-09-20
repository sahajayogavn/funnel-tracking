# code:tool-mas-recommend-001:cli
"""MAS-backed recommendation generator for user-selected seeker threads.

Thin CLI entrypoint that the web dashboard (`POST /api/action-queue/recommendations`)
spawns when the operator clicks "⚡ Chạy đề xuất MAS". Unlike
`l5_inbox_mas_runner.py` (which scans the whole inbox via CDP), this tool:

- takes an explicit list of thread IDs chosen in the UI,
- NEVER opens a browser,
- runs isolated per-thread ADK MAS agents (plus WarmUpComposer / EventAdvertiser),
- sanitizes every reply with `_sanitize_reply()`,
- and ONLY enqueues proposals into `action_queue` with status='pending'
  (payload.source = 'inbox_mas') for human approval.

Usage:
    .venv/bin/python tools/l5_mas_recommend.py \
        --thread-ids 1548373332058326_1000,1548373332058326_2000 \
        --type all            # reply | warmup | event | all
        [--regenerate]        # replace the current pending/approved draft

Output: a single JSON document on stdout. All logs go to stderr.
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
from fb_pipeline.persistence.l4_llm_trace import link_outcome, span
from tools.l5_action_queue import active_proposal_status, enqueue_action, replace_action
from tools.l5_delivery_guard import conversation_snapshot
from tools.l5_care_admission import interpret_repeat_permission, record_care_decision
from tools.l5_inbox_mas_context import setup_llm_env
from tools.l5_inbox_mas_pipeline import _approved_draft, _sanitize_reply, run_adk_care_pipeline, run_adk_pipeline
from adk_agents.tools.l5_event_tools import get_upcoming_events
from adk_agents.tools.l5_seeker_tools import get_thread_messages, lookup_seeker
from adk_agents.tools.l5_warmup_tools import select_warmup_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("tools.mas_recommend")

DEFAULT_PAGE_ID = "1548373332058326"
SOURCE = "inbox_mas"
MAX_MESSAGES_PER_THREAD = 15
VALID_TYPES = ("all", "reply", "warmup", "event", "care")
CARE_PURPOSES = ("class_reminder", "warmup", "event")


# code:tool-mas-recommend-001:pending-guard
# `active_proposal_status` (tools/l5_action_queue.py) is the single source of
# truth for the "one live proposal per target+queue_type" invariant: it ignores
# only the terminal statuses (executed/rejected/failed), so an *approved*
# proposal still blocks a duplicate the same way a *pending* one does.
#
# `--regenerate` relaxes this for pending/approved drafts: the operator picked
# an existing queue item and wants MAS to try again. The old draft is only
# superseded once a sanitized new draft exists (`replace_action` swaps them in
# one transaction), so a failed LLM call never leaves the seeker with no
# proposal. An `executing` draft is never replaced: the worker already owns it.


def _guard(thread_id: str, queue_type: str, kind: str, regenerate: bool,
           payload_type: Optional[str] = None) -> Optional[str]:
    """Skip reason for `thread_id`, or None when MAS may produce a new draft."""
    status = active_proposal_status(thread_id, queue_type, payload_type)
    if status is None:
        return None
    if status == "executing":
        return f"executing_{kind}_exists"
    return None if regenerate else f"pending_{kind}_exists"


def _enqueue(regenerate: bool, **kwargs: Any) -> tuple[int, list[int]]:
    """enqueue_action, or replace_action when regenerating. Returns (id, superseded_ids)."""
    if not regenerate:
        return enqueue_action(**kwargs), []
    new_id, old = replace_action(**kwargs)
    if old:
        logger.info("Regenerated %s for %s: #%s supersedes %s", kwargs["queue_type"], kwargs["target_id"], new_id, old)
    return new_id, old


def _days_dormant(seeker: dict) -> int:
    last = seeker.get("last_interaction")
    if not last:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return max(0, (datetime.now() - datetime.strptime(str(last)[:19], fmt)).days)
        except ValueError:
            continue
    return 0


def _load_thread(thread_id: str) -> Optional[dict]:
    """Assemble seeker profile + recent messages for one thread. None if no messages."""
    msg_result = get_thread_messages(thread_id)
    if msg_result.get("status") != "success" or not msg_result.get("messages"):
        logger.warning("No messages found for thread %s", thread_id)
        return None
    seeker = lookup_seeker(thread_id)
    messages = msg_result["messages"][-MAX_MESSAGES_PER_THREAD:]
    thread_name = seeker.get("name") or _thread_name_from_db(thread_id) or "Seeker"
    # code:inbox-conv-state-001 — manual recommendations are operator-initiated,
    # so the gate does not block them, but the LLM still gets the time context.
    from fb_pipeline.contracts.l1_conversation_state import compute_conversation_state, format_conversation_lines
    state = compute_conversation_state(msg_result["messages"])
    return {
        "thread_id": thread_id,
        "thread_name": thread_name,
        "recipient_name": _thread_name_from_db(thread_id) or thread_name,
        # Delivery verifies only this fetched transcript's latest stable event.
        # Older Messenger history can be virtualized away before approval.
        "delivery_snapshot": conversation_snapshot(msg_result["messages"]),
        "seeker": seeker,
        "messages": messages,
        "reaction_events": msg_result.get("reaction_events") or [],
        "conversation_text": format_conversation_lines(messages, msg_result.get("reaction_events") or []),
        "conversation_state": state.to_dict(),
        "late": state.late,
        "latest_customer_timestamp": next(
            (m.get("timestamp") for m in reversed(msg_result["messages"]) if m.get("sender") == "Customer"),
            None,
        ),
    }


def _thread_name_from_db(thread_id: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT thread_name FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return row["thread_name"] if row else None
    finally:
        conn.close()


def _proposal(action_id: int, queue_type: str, thread: dict, text: str, kind: str,
              superseded: Optional[list[int]] = None) -> dict:
    return {
        "id": action_id,
        "queueType": queue_type,
        "targetId": thread["thread_id"],
        "targetName": thread["thread_name"],
        "actionText": text,
        "kind": kind,
        "supersededIds": superseded or [],
    }


def _outbound_skip_reason(text: str | None) -> Optional[str]:
    """Map control sentinels to a business outcome before any queue write."""
    value = (text or "").strip()
    upper = value.upper()
    if not value:
        return "no_reply"
    if upper == "[OUT_OF_SCOPE]":
        return "out_of_scope"
    if upper.startswith("[NO_REPLY"):
        return value[:60]
    if upper.startswith("[NO_SEND"):
        return "mas_no_send"
    return None


# code:tool-mas-recommend-001:reply
def recommend_replies(threads: list[dict], page_id: str, regenerate: bool = False,
                      instruction: str = "") -> tuple[list[dict], list[dict]]:
    """Run one isolated inbox MAS action per selected thread and enqueue drafts."""
    created: list[dict] = []
    skipped: list[dict] = []
    for t in threads:
        reason = _guard(t["thread_id"], "reply_message", "reply", regenerate)
        if reason:
            skipped.append({"threadId": t["thread_id"], "reason": reason})
            continue
        delivery_snapshot = t.get("delivery_snapshot") or conversation_snapshot(t["messages"])
        # No cross-thread prompt or trace: the selected thread is the action boundary.
        llm = run_adk_pipeline(
            t["messages"], t["seeker"], trigger="manual_recommendation",
            page_id=page_id, subject_id=t["thread_id"], feedback=instruction or None,
            conversation_state=t.get("conversation_state"),
            reaction_events=t.get("reaction_events") or [],
        )
        # An escalation note is for the human operator, never outward-facing
        # copy.  `run_adk_pipeline` exposes it separately from reply_text, but
        # older callers may still receive a sanitized note in reply_text.
        # Do not let a manually-triggered MAS run enqueue that note as a DM.
        escalation_reason = (llm or {}).get("escalation_reason", "")
        if escalation_reason:
            skipped.append({
                "threadId": t["thread_id"],
                "reason": f"escalated_{escalation_reason}",
                "escalationNote": (llm or {}).get("escalation_note", ""),
                "classification": (llm or {}).get("classification", ""),
            })
            continue
        reply_text = _approved_draft(llm or {})
        classification = (llm or {}).get("classification", "")
        skip_reason = _outbound_skip_reason(reply_text)
        if skip_reason:
            skipped.append({"threadId": t["thread_id"], "reason": skip_reason, "classification": classification})
            continue
        action_id, old = _enqueue(
            regenerate,
            queue_type="reply_message", page_id=page_id, target_type="thread",
            target_id=t["thread_id"], target_name=t["thread_name"], action_text=reply_text,
            payload={
                "source": SOURCE,
                "trigger": "manual_recommendation",
                "conversation_snapshot": delivery_snapshot,
                "recipient_name": t.get("recipient_name") or t["thread_name"],
                "classification": classification,
                "customer_message_timestamp": t["latest_customer_timestamp"],
                "seeker": t["seeker"],
                "conversation_state": t.get("conversation_state"),
            },
        )
        created.append(_proposal(action_id, "reply_message", t, reply_text, "reply", old))
    if created:
        link_outcome("hitl_queue", ",".join(str(item["id"]) for item in created))
    return created, skipped


# code:tool-mas-recommend-001:warmup
def recommend_warmups(threads: list[dict], page_id: str, knowledge_context: str,
                      regenerate: bool = False) -> tuple[list[dict], list[dict]]:
    """Compatibility adapter: warm-up now uses the common Care workflow."""
    return recommend_care(
        threads, page_id, knowledge_context, None, "", None, regenerate,
        care_purpose="warmup",
    )


def _pick_event(city: Optional[str], event_id: Optional[str | int] = None) -> Optional[dict]:
    upcoming = get_upcoming_events(city=city if city and city != "all" else None)
    if upcoming.get("status") == "success" and upcoming.get("events"):
        events = upcoming["events"]
        if event_id is not None:
            return next((event for event in events if str(event.get("id")) == str(event_id)), None)
        return events[0]
    # An event without an in-scope future record is not verified evidence.
    # Never fall back to an arbitrary historical/cross-city DB row.
    return None


# code:tool-mas-recommend-001:event
def recommend_events(threads: list[dict], page_id: str, knowledge_context: str, city: Optional[str],
                     regenerate: bool = False) -> tuple[list[dict], list[dict]]:
    """Compatibility adapter: events now use the common Care workflow."""
    return recommend_care(
        threads, page_id, knowledge_context, city, "", None, regenerate,
        care_purpose="event",
    )


def _care_skip_reason(thread: dict, route: str) -> Optional[str]:
    seeker = thread["seeker"]
    stage = (seeker.get("lead_stage") or "").lower()
    temperature = (seeker.get("temperature") or "").lower()
    if stage in {"spam", "unsubscribed"} or temperature == "unsubscribed":
        return "opt_out"
    conversation = " ".join(str(m.get("content") or "").lower() for m in thread["messages"][-5:])
    if any(token in conversation for token in ("đừng nhắn", "không nhắn", "ngừng liên hệ", "unsubscribe")):
        return "opt_out"
    if route == "class_reminder" and any(token in conversation for token in (
        "tuần này không đi", "tuần này chưa đi", "bận", "hủy", "đổi sang", "không tham gia",
    )):
        return "context_says_not_this_session"
    if route == "class_reminder" and not seeker.get("program_code"):
        return "no_verified_program_registration"
    return None


def _prior_page_lines(thread: dict, limit: int = 3) -> list[str]:
    return [str(m.get("content") or "")[:160] for m in thread["messages"]
            if m.get("sender") == "Page"][-limit:]


# code:tool-mas-recommend-001:reminder-cadence
def _reminder_cadence(thread_id: str, page_id: str, session: dict) -> dict:
    """Sent evidence is scoped to a recipient and a concrete class occurrence.

    Interpretation of operator permission is a separate, traced semantic step.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT id, executed_at, action_text FROM action_queue
            WHERE page_id=? AND target_id=? AND target_type='thread'
              AND queue_type IN ('proactive_message', 'reply_message')
              AND status='executed' AND executed_at IS NOT NULL
              AND json_valid(payload_json)
              AND json_extract(payload_json, '$.type')='class_reminder'
              AND COALESCE(json_extract(payload_json, '$.delivery_status'), '')
                  NOT IN ('drafted', 'outdated', 'uncertain')
              AND json_extract(payload_json, '$.session.session_date')=?
              AND COALESCE(json_extract(payload_json, '$.session.class_key'),
                           json_extract(payload_json, '$.session.program_code'))=?
            ORDER BY executed_at DESC, id DESC
        """, (page_id, thread_id, session.get("session_date"),
              session.get("class_key") or session.get("program_code"))).fetchall()
        return {"repeat_explicitly_requested": False,
                "sent_reminders": [dict(row) for row in rows]}
    finally:
        conn.close()


def recommend_care(threads: list[dict], page_id: str, knowledge_context: str, city: Optional[str],
                   instruction: str, program_code: Optional[str], regenerate: bool = False,
                   command_id: Optional[str] = None,
                   care_purpose: Optional[str] = None,
                   event_id: Optional[str | int] = None) -> tuple[list[dict], list[dict]]:
    """Create one contextual, reviewable care proposal per selected seeker.

    This is deliberately a manual, selected-list entry point.  It produces
    drafts only; no browser, Facebook action, or automatic send is possible.
    """
    from fb_pipeline.contracts.l1_class_schedule import upcoming_sessions
    from fb_pipeline.contracts.l1_conversation_state import format_now_context

    def skipped_result(t: dict, reason: str, note: str = "", evidence: dict | None = None) -> dict:
        record_care_decision(page_id=page_id, thread=t, instruction=instruction,
                             purpose=care_purpose or "", reason=reason, note=note,
                             evidence=evidence or {"seeker": t.get("seeker"),
                                                   "conversation_state": t.get("conversation_state")})
        return {"threadId": t["thread_id"], "reason": reason, **({"note": note} if note else {})}

    if care_purpose not in CARE_PURPOSES:
        return [], [skipped_result(t, "care_purpose_required") for t in threads]
    route = care_purpose
    # Command idempotency is separate from the sent-reminder cadence below.
    dedupe_key = f"operator-care:{route}:{command_id or instruction.strip()}"
    sessions = []
    scoped_session = None
    if route == "class_reminder":
        sessions = upcoming_sessions(datetime.now(), window_hours=24 * 7)
        # A program filter is an explicit operator scope and must resolve to
        # one forthcoming session.  Without it, the selected seeker's verified
        # program is the scope.  Requiring the *entire* weekly catalogue to
        # have one session made a normal unfiltered single-seeker reminder
        # impossible whenever several classes were scheduled that week.
        matching = [s for s in sessions if s.program_code == program_code] if program_code else []
        if program_code and len(matching) != 1:
            reason = "class_session_not_unique" if matching else "class_session_not_found"
            return [], [skipped_result(t, reason) for t in threads]
        scoped_session = matching[0].to_dict() if matching else None

    event = _pick_event(city, event_id) if route == "event" else None
    if route == "event" and not event:
        return [], [skipped_result(t, "no_event") for t in threads]

    created: list[dict] = []
    skipped: list[dict] = []
    for t in threads:
        reason = _care_skip_reason(t, route)
        seeker = {**t["seeker"], "thread_id": t["thread_id"], "thread_name": t["thread_name"]}
        session = scoped_session
        if not reason and route == "class_reminder":
            seeker_program = seeker.get("program_code")
            if program_code and seeker_program != program_code:
                reason = "registered_for_different_program"
            elif not program_code:
                matching = [s for s in sessions if s.program_code == seeker_program]
                if len(matching) != 1:
                    reason = "class_session_not_unique" if matching else "class_session_not_found"
                else:
                    session = matching[0].to_dict()
        if reason:
            skipped.append(skipped_result(t, reason))
            continue
        cadence = _reminder_cadence(t["thread_id"], page_id, session) if route == "class_reminder" else {}
        if cadence.get("sent_reminders"):
            interpretation = interpret_repeat_permission(
                instruction, page_id=page_id, thread=t, session=session,
                sent_reminders=cadence["sent_reminders"],
            )
            cadence["operator_interpretation"] = interpretation
            cadence["repeat_explicitly_requested"] = interpretation.get("allow_repeat") is True and not interpretation.get("error")
            if interpretation.get("error"):
                skipped.append(skipped_result(t, "care_instruction_unresolved", interpretation["reason"], cadence))
                continue
        if cadence.get("sent_reminders") and not cadence.get("repeat_explicitly_requested"):
            skipped.append(skipped_result(t, "reminder_already_sent_for_session",
                "Chưa phù hợp để nhắc lại: seeker đã được nhắc cho buổi học này. "
                + cadence["operator_interpretation"]["reason"], cadence))
            continue
        if cadence.get("repeat_explicitly_requested"):
            record_care_decision(page_id=page_id, thread=t, instruction=instruction, purpose=route,
                                 reason="explicit_repeat_permission", note=cadence["operator_interpretation"]["reason"],
                                 evidence=cadence, allowed=True)
        kind = route
        active = active_proposal_status(t["thread_id"], "proactive_message", dedupe_key=dedupe_key)
        if active == "executing":
            skipped.append(skipped_result(t, "executing_care_command_exists"))
            continue
        if active and not regenerate:
            reason = "pending_care_command_exists"
            skipped.append(skipped_result(t, reason))
            continue
        strategy = select_warmup_strategy(seeker.get("lead_stage") or "Intake", _days_dormant(seeker)) if route == "warmup" else None
        strategy = strategy or ({"type": "manual_warmup", "cool_step": None} if route == "warmup" else None)
        care_brief = {
            "operator_instruction": instruction,
            "verified_session": session,
            "verified_event": event,
            "warmup_strategy": strategy,
            "prior_page_lines": _prior_page_lines(t),
            "recent_conversation": t.get("conversation_text", ""),
            "conversation_state": t.get("conversation_state") or {},
            "knowledge_context": knowledge_context,
            "reminder_cadence": cadence,
        }
        delivery_snapshot = t.get("delivery_snapshot") or conversation_snapshot(t["messages"])
        llm = run_adk_care_pipeline(
            t["messages"], seeker, care_purpose=route, care_brief=care_brief,
            feedback=instruction, page_id=page_id, trigger="operator_care_command",
            subject_id=t["thread_id"], now_context=format_now_context(datetime.now()),
            reaction_events=t.get("reaction_events") or [],
        )
        if llm.get("no_send_reason"):
            skipped.append(skipped_result(t, "care_not_appropriate_now", llm["no_send_reason"], care_brief))
            continue
        if llm.get("escalation_reason"):
            skipped.append(skipped_result(t, f"escalated_{llm['escalation_reason']}", llm.get("escalation_note", ""), care_brief))
            continue
        text = _approved_draft(llm)
        skip_reason = _outbound_skip_reason(text)
        if skip_reason:
            skipped.append(skipped_result(t, skip_reason, evidence=care_brief))
            continue
        action_id, old = _enqueue(
            regenerate, queue_type="proactive_message", page_id=page_id, target_type="thread",
            target_id=t["thread_id"], target_name=t["thread_name"], action_text=text,
            payload={"source": SOURCE, "type": kind, "trigger": "operator_care_command",
                     "conversation_snapshot": delivery_snapshot,
                     "recipient_name": t.get("recipient_name") or t["thread_name"],
                     "instruction": instruction, "city": seeker.get("city"),
                     "dedupe_key": dedupe_key,
                     "session": session, "event_id": event.get("id") if event else None,
                     "reminder_cadence": cadence,
                     "conversation_state": t.get("conversation_state")},
        )
        created.append(_proposal(action_id, "proactive_message", t, text, kind, old))
    return created, skipped


# code:tool-mas-recommend-001:llm-preflight
def _check_llm_reachable(timeout: float = 8.0) -> Optional[str]:
    """Native Gemini is verified by the ADK call itself.

    There is deliberately no alternate endpoint preflight: this deployment
    must not route MAS recommendations through a stale OpenAI-compatible URL.
    """
    return None


# code:tool-mas-recommend-001:run
def _run(thread_ids: list[str], rec_type: str = "all", page_id: str = DEFAULT_PAGE_ID,
         city: Optional[str] = None, regenerate: bool = False, instruction: str = "",
         program_code: Optional[str] = None, command_id: Optional[str] = None,
         care_purpose: Optional[str] = None, event_id: Optional[str] = None) -> dict:
    # All MAS paths use the shared Gemini-only configuration.
    config = setup_llm_env() or {}
    if config.get("provider") != "google" or not os.environ.get("GOOGLE_API_KEY"):
        return {"status": "error", "error": "Gemini credentials missing (GOOGLE_API_KEY)"}
    reachability_error = _check_llm_reachable()
    if reachability_error:
        return {"status": "error", "error": reachability_error}

    threads = [t for t in (_load_thread(tid) for tid in thread_ids) if t]
    missing = [tid for tid in thread_ids if tid not in {t["thread_id"] for t in threads}]
    if not threads:
        return {"status": "error", "error": "No messages found for the selected threads", "missing": missing}

    # KnowledgeLibrarian retrieves a narrow, source-scoped brief after the
    # analyst. Do not preload the global knowledge corpus into every role.
    knowledge_context = ""
    proposals: list[dict] = []
    skipped: list[dict] = [{"threadId": tid, "reason": "no_messages"} for tid in missing]

    if rec_type in ("all", "reply"):
        c, s = recommend_replies(threads, page_id, regenerate, instruction)
        proposals += c; skipped += s
    if rec_type in ("all", "warmup"):
        c, s = recommend_warmups(threads, page_id, knowledge_context, regenerate)
        proposals += c; skipped += s
    if rec_type in ("all", "event"):
        c, s = recommend_events(threads, page_id, knowledge_context, city, regenerate)
        proposals += c; skipped += s
    if rec_type == "care":
        c, s = recommend_care(
            threads, page_id, knowledge_context, city, instruction, program_code,
            regenerate, command_id, care_purpose, event_id,
        )
        proposals += c; skipped += s

    return {
        "status": "ok",
        "engine": SOURCE,
        "count": len(proposals),
        "proposals": proposals,
        "skipped": skipped,
        "supersededCount": sum(len(p["supersededIds"]) for p in proposals),
    }


def run(thread_ids: list[str], rec_type: str = "all", page_id: str = DEFAULT_PAGE_ID,
        city: Optional[str] = None, regenerate: bool = False,
        trace_id: Optional[str] = None, instruction: str = "", program_code: Optional[str] = None,
        care_purpose: Optional[str] = None, event_id: Optional[str] = None) -> dict:
    """Run a web-selected MAS recommendation under one trace.

    ``trace_id`` is the durable recommendation job id when invoked from
    ``/queues``; this makes the UI link and the SQLite trace one-to-one.
    """
    trace_subject = trace_id or ",".join(thread_ids) or "recommendation"
    with span(
        trigger="web",
        route="recommend",
        page_id=page_id,
        subject=("batch", f"recommend:{trace_subject}", f"{rec_type} recommendation"),
        dry_run=True,
        trace_id=trace_id,
    ):
        return _run(
            thread_ids, rec_type, page_id, city, regenerate, instruction,
            program_code, trace_id, care_purpose, event_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MAS agents for selected threads and enqueue HITL proposals")
    parser.add_argument("--thread-ids", required=True, help="Comma-separated thread IDs")
    parser.add_argument("--type", default="all", choices=VALID_TYPES)
    parser.add_argument("--page-id", default=DEFAULT_PAGE_ID)
    parser.add_argument("--city", default=None)
    parser.add_argument("--instruction", default="", help="Operator care instruction for --type care")
    parser.add_argument("--program-code", default=None, help="Verified class filter for --type care")
    parser.add_argument("--purpose", choices=CARE_PURPOSES, default=None,
                        help="Explicit purpose for --type care; program filters never select a purpose")
    parser.add_argument("--event-id", default=None,
                        help="Verified event ID for an event regeneration; never substitute another event")
    parser.add_argument("--regenerate", action="store_true",
                        help="Replace the seeker's current pending/approved draft with a fresh one")
    parser.add_argument("--job-id", default=None,
                        help="Durable web recommendation job id used as the LLM trace id")
    args = parser.parse_args()

    thread_ids = [t.strip() for t in args.thread_ids.split(",") if t.strip()]
    try:
        result = run(thread_ids, args.type, args.page_id, args.city, args.regenerate, args.job_id,
                     args.instruction, args.program_code, args.purpose, args.event_id)
    except Exception as exc:  # surface as JSON so the web route can fall back
        logger.exception("MAS recommendation failed")
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
