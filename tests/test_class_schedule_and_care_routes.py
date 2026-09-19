# code:test-validation-001:care-routes
"""Class schedule derivation and the proactive care routes
(prd:mas-time-aware-001 P2), run against an in-memory database."""
import sqlite3
from datetime import datetime

import pytest

from fb_pipeline.contracts.l1_class_schedule import (
    describe_session, load_class_definitions, parse_program_code, recent_sessions, upcoming_sessions,
)


# --- schedule ---------------------------------------------------------------

def test_parse_program_code_single_day():
    d = parse_program_code("20h-T3-Hoàng Quốc Việt-HN")
    assert (d.hour, d.minute, d.weekdays, d.place, d.city, d.is_online) == (20, 0, [1], "Hoàng Quốc Việt", "Hà Nội", False)


def test_parse_program_code_multi_day_online():
    d = parse_program_code("21h-T3-T5-T7-Online-HN")
    assert d.weekdays == [1, 3, 5] and d.is_online


def test_parse_program_code_without_weekday_is_rejected():
    assert parse_program_code("20h30-Online-HCM") is None


def test_upcoming_sessions_thursday_morning_window():
    now = datetime(2026, 9, 17, 8, 30)  # Thursday
    keys = [s.class_key for s in upcoming_sessions(now, window_hours=36)]
    assert keys == ["21h-T3-T5-T7-Online-HN"]


def test_upcoming_sessions_saturday_lists_sunday_classes():
    now = datetime(2026, 9, 19, 8, 30)  # Saturday
    sessions = upcoming_sessions(now, window_hours=36)
    assert any(s.class_key == "14h30-CN-Vương Thừa Vũ-HN" and s.session_date == "2026-09-20" for s in sessions)
    assert all(s.starts_at > "2026-09-19 08:30:00" for s in sessions)


def test_recent_sessions_monday_morning_sees_sunday():
    now = datetime(2026, 9, 21, 8, 30)  # Monday
    assert {s.class_key for s in recent_sessions(now, lookback_hours=24)} >= {"14h30-CN-Vương Thừa Vũ-HN"}


def test_describe_session_relative_day():
    now = datetime(2026, 9, 21, 8, 30)
    session = upcoming_sessions(now, window_hours=36)[0]
    assert describe_session(session, now).startswith("Ngày mai Thứ Ba 20h00")


def test_lop_hoc_enrichment_gives_address_and_zalo():
    by_code = {d.program_code: d for d in load_class_definitions()}
    d = by_code["14h30-CN-Vương Thừa Vũ-HN"]
    assert "Vương Thừa Vũ" in d.address and d.zalo_url.startswith("https://zalo.me/")


# --- routes on in-memory DB -------------------------------------------------

@pytest.fixture
def mem_db(monkeypatch):
    import fb_pipeline.persistence.l4_sqlite_store as store
    import tools.l5_action_queue as aq
    import tools.l5_proactive_routes as routes
    import adk_agents.tools.l5_seeker_tools as seeker_tools

    shared = sqlite3.connect(":memory:", check_same_thread=False)
    shared.row_factory = sqlite3.Row
    store.setup_database(shared)
    shared.commit()

    class Conn:
        def __init__(self, c): self._c = c
        def execute(self, *a, **k): return self._c.execute(*a, **k)
        def executemany(self, *a, **k): return self._c.executemany(*a, **k)
        def commit(self): self._c.commit()
        def rollback(self): self._c.rollback()
        def cursor(self): return self._c.cursor()
        def close(self): pass

    factory = lambda *a, **k: Conn(shared)  # noqa: E731
    for mod in (store, aq, routes, seeker_tools):
        monkeypatch.setattr(mod, "get_db_connection", factory)
    monkeypatch.setattr("tools.l5_telegram_hitl.send_proposal_to_telegram", lambda **k: "tg-1")
    monkeypatch.setattr("tools.l5_telegram_hitl.send_telegram_notification", lambda text: "tg-2")
    return shared


def _seed_registered(db, thread_id, name, code, last_interaction, stage="Seeker_Public_Program", phone="0900000000"):
    db.execute("INSERT INTO threads (id, page_id, thread_name) VALUES (?, 'P', ?)", (thread_id, name))
    db.execute(
        "INSERT INTO users (thread_id, thread_name, phone, city, lead_stage, program_code, last_interaction) VALUES (?, ?, ?, 'Hà Nội', ?, ?, ?)",
        (thread_id, name, phone, stage, code, last_interaction),
    )
    db.commit()


def test_reminder_digest_creates_one_session_proposal(mem_db):
    from tools.l5_proactive_routes import run_class_reminder_digest
    _seed_registered(mem_db, "T1", "Chú Chiến", "14h30-CN-Vương Thừa Vũ-HN", "2026-09-14 11:10:00")
    _seed_registered(mem_db, "T2", "Cô Hương", "14h30-CN-Vương Thừa Vũ-HN", "2026-09-04 20:06:00")
    _seed_registered(mem_db, "T3", "Old", "14h30-CN-Vương Thừa Vũ-HN", "2026-07-01 10:00:00")  # >21d: excluded
    now = datetime(2026, 9, 19, 8, 30)
    result = run_class_reminder_digest("P", dry_run=False, now=now)
    assert len(result["proposals"]) == 1 and result["proposals"][0]["count"] == 2
    rows = mem_db.execute("SELECT queue_type, status, target_id FROM action_queue").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("session_proposal", "pending")]
    assert rows[0][2] == "14h30-CN-Vương Thừa Vũ-HN|2026-09-20"
    # Running again the same morning does not duplicate the proposal.
    assert run_class_reminder_digest("P", dry_run=False, now=now)["proposals"] == []


def test_session_open_creates_drafts_only_after_approval(mem_db, monkeypatch):
    from tools.l5_proactive_routes import run_class_reminder_digest, run_session_open_cycle
    from tools.l5_action_queue import approve_action
    _seed_registered(mem_db, "T1", "Chú Chiến", "14h30-CN-Vương Thừa Vũ-HN", "2026-09-14 11:10:00")
    now = datetime(2026, 9, 19, 8, 30)
    run_class_reminder_digest("P", dry_run=False, now=now)
    # Nothing happens while the proposal is pending.
    assert run_session_open_cycle("P", dry_run=False)["opened"] == []
    proposal_id = mem_db.execute("SELECT id FROM action_queue WHERE queue_type='session_proposal'").fetchone()[0]
    approve_action(proposal_id, "webui")
    monkeypatch.setattr("tools.l5_scheduler_adk.run_adk_class_reminder",
                        lambda *a, **k: "Chú ơi, hẹn chú 14h30 Chủ Nhật tại 40 Vương Thừa Vũ nhé 🙏")
    result = run_session_open_cycle("P", dry_run=False)
    assert result["opened"][0]["drafted"] == 1
    draft = mem_db.execute("SELECT queue_type, status, target_id, action_text FROM action_queue WHERE queue_type='proactive_message'").fetchone()
    assert draft[1] == "pending" and draft[2] == "T1" and "Vương Thừa Vũ" in draft[3]
    assert mem_db.execute("SELECT status FROM action_queue WHERE id=?", (proposal_id,)).fetchone()[0] == "executed"
    assert mem_db.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0] == 1


def test_session_open_dry_run_is_read_only(mem_db):
    from tools.l5_action_queue import approve_action
    from tools.l5_proactive_routes import run_class_reminder_digest, run_session_open_cycle

    _seed_registered(mem_db, "T1", "Chú Chiến", "14h30-CN-Vương Thừa Vũ-HN", "2026-09-14 11:10:00")
    run_class_reminder_digest("P", dry_run=False, now=datetime(2026, 9, 19, 8, 30))
    proposal_id = mem_db.execute("SELECT id FROM action_queue WHERE queue_type='session_proposal'").fetchone()[0]
    approve_action(proposal_id, "webui")

    result = run_session_open_cycle("P", dry_run=True)

    assert result["opened"] == [{
        "session_proposal_id": proposal_id, "drafted": 0, "failed": 0,
        "would_draft": 1, "dry_run": True,
    }]
    assert mem_db.execute("SELECT status FROM action_queue WHERE id=?", (proposal_id,)).fetchone()[0] == "approved"
    assert mem_db.execute("SELECT COUNT(*) FROM action_queue WHERE queue_type='proactive_message'").fetchone()[0] == 0
    assert mem_db.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0] == 0


def test_post_session_checklist_and_attendance_sync(mem_db):
    from tools.l5_proactive_routes import run_post_session_checklist, run_attendance_sync
    from tools.l5_action_queue import approve_action, reject_action
    _seed_registered(mem_db, "T1", "Chú Chiến", "14h30-CN-Vương Thừa Vũ-HN", "2026-09-14 11:10:00", stage="Seeker")
    _seed_registered(mem_db, "T2", "Cô Hương", "14h30-CN-Vương Thừa Vũ-HN", "2026-09-14 11:10:00")
    mem_db.execute("INSERT INTO reminder_log (thread_id, class_key, session_date) VALUES ('T1','14h30-CN-Vương Thừa Vũ-HN','2026-09-20')")
    mem_db.execute("INSERT INTO reminder_log (thread_id, class_key, session_date) VALUES ('T2','14h30-CN-Vương Thừa Vũ-HN','2026-09-20')")
    mem_db.commit()
    now = datetime(2026, 9, 21, 8, 30)
    result = run_post_session_checklist("P", dry_run=False, now=now)
    assert len(result["sessions"][0]["items"]) == 2
    ids = {r[1]: r[0] for r in mem_db.execute("SELECT id, target_name FROM action_queue WHERE queue_type='attendance_check'")}
    approve_action(ids["Chú Chiến"], "webui")
    reject_action(ids["Cô Hương"], "webui")
    synced = run_attendance_sync("P", dry_run=False)
    assert sorted((r["thread_id"], r["attended"]) for r in synced["recorded"]) == [("T1", 1), ("T2", 0)]
    rows = mem_db.execute("SELECT thread_id, attended FROM attendance ORDER BY thread_id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("T1", 1), ("T2", 0)]
    # Second run is a no-op.
    assert run_attendance_sync("P", dry_run=False)["recorded"] == []


def test_attendance_sync_dry_run_is_read_only(mem_db):
    from tools.l5_action_queue import approve_action, enqueue_action
    from tools.l5_proactive_routes import run_attendance_sync

    action_id = enqueue_action(
        queue_type="attendance_check", page_id="P", target_type="thread",
        target_id="T1|class|2026-09-20", target_name="Lan", action_text="Có mặt?",
        payload={"thread_id": "T1", "session": {"class_key": "class", "session_date": "2026-09-20"}},
    )
    approve_action(action_id, "webui")

    assert run_attendance_sync("P", dry_run=True)["recorded"] == [{"thread_id": "T1", "attended": 1, "dry_run": True}]
    assert mem_db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    assert mem_db.execute("SELECT status FROM action_queue WHERE id=?", (action_id,)).fetchone()[0] == "approved"


def test_sla_alert_only_for_unconfirmed_phone(mem_db):
    from tools.l5_proactive_routes import run_registration_sla_cycle
    mem_db.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T1','P','Sáu',1)")
    mem_db.execute("INSERT INTO users (thread_id, thread_name) VALUES ('T1','Sáu')")
    mem_db.execute("INSERT INTO messages (thread_id, sender, content, seq, kind, message_at) VALUES ('T1','Customer','cho mình xin học với sđt 0393140362',1,'message','2026-09-17 09:00:00')")
    mem_db.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T2','P','Banner',2)")
    mem_db.execute("INSERT INTO messages (thread_id, sender, content, seq, kind, message_at) VALUES ('T2','Customer','Banner replied to an ad.',1,'system_banner','2026-09-17 09:00:00')")
    mem_db.commit()
    result = run_registration_sla_cycle("P", dry_run=False, now=datetime(2026, 9, 17, 14, 0))
    assert [a["phone"] for a in result["alerts"]] == ["0393140362"]
    assert mem_db.execute("SELECT COUNT(*) FROM sla_alerts").fetchone()[0] == 1
    # Re-alert suppressed inside the 12h window.
    assert run_registration_sla_cycle("P", dry_run=False, now=datetime(2026, 9, 17, 15, 0))["alerts"] == []


# --- events sync ------------------------------------------------------------

def test_events_sync_parses_future_and_skips_past():
    from datetime import date
    from tools.l5_events_sync import parse_upcoming_events
    md = ("## 🌿 Sự kiện sắp diễn ra — Thiền & Âm nhạc, Đà Nẵng (Tháng 4/2026)\n\n"
          "- **Khu vực**: Đà Nẵng, Hội An\n- **Thời gian**: Tháng 4/2026\n")
    future = parse_upcoming_events(md, date(2026, 3, 1))
    assert [(e["city"], e["event_date"]) for e in future] == [("Đà Nẵng", "2026-04-01"), ("Hội An", "2026-04-01")]
    assert parse_upcoming_events(md, date(2026, 9, 17)) == []
