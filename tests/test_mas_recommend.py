# code:test-validation-001:mas-recommend
"""Unit tests for tools/l5_mas_recommend.py with ADK agents mocked out.

Verifies that the web-triggered MAS entrypoint:
- routes each selected thread through an isolated inbox MAS action,
- sanitizes replies and blocks reasoning leaks / OUT_OF_SCOPE,
- only writes pending proposals tagged source='inbox_mas',
- never duplicates a pending proposal of the same kind.
"""
import json
import sqlite3

import pytest

from fb_pipeline.persistence.l4_sqlite_store import setup_database
from tools import l5_action_queue as queue
from tools import l5_mas_recommend as rec
from adk_agents.tools import l5_seeker_tools as seeker_tools
from adk_agents.tools import l5_event_tools as event_tools

HUNG_BUI = "1548373332058326_100001005716854"


@pytest.fixture
def test_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test_mas_rec.db"

    def get_test_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        return conn

    for mod in (queue, rec, seeker_tools, event_tools):
        monkeypatch.setattr(mod, "get_db_connection", get_test_conn)

    conn = get_test_conn()
    conn.execute(
        "INSERT INTO threads (id, page_id, thread_name, last_synced_time) VALUES (?, ?, ?, datetime('now'))",
        (HUNG_BUI, rec.DEFAULT_PAGE_ID, "Hung Bui"),
    )
    conn.execute(
        "INSERT INTO messages (thread_id, sender, content, timestamp) VALUES (?, ?, ?, datetime('now'))",
        (HUNG_BUI, "Customer", "Em muốn hỏi lớp thiền ở Hà Nội ạ"),
    )
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, lead_stage, last_interaction) VALUES (?, ?, ?, ?, datetime('now'))",
        (HUNG_BUI, "Hung Bui", "Hà Nội", "Intake"),
    )
    conn.commit()
    conn.close()
    return get_test_conn


@pytest.fixture
def mocked_llm(monkeypatch):
    """Bypass credentials/reachability and replace ADK agents with canned outputs."""
    monkeypatch.setattr(rec, "setup_llm_env", lambda: {"provider": "google"})
    monkeypatch.setenv("GOOGLE_API_KEY", "mock-gemini-key")
    monkeypatch.setattr(rec, "_check_llm_reachable", lambda timeout=5.0: None)
    # The unified workflow may source knowledge inside its Python coordinator;
    # retain this seam for legacy adapters without requiring the symbol.
    monkeypatch.setattr(rec, "load_knowledge_context", lambda: "KB", raising=False)

    calls = {"inbox": [], "care": []}

    def fake_inbox(messages, seeker, **kwargs):
        calls["inbox"].append((messages, seeker, kwargs))
        draft = f"Chào {seeker.get('name', 'bạn')}! Lớp thiền ở Hà Nội hoàn toàn miễn phí ạ 🙏"
        return {
            "classification": "Intent: question, Language: vi, Urgency: low, Stage: Intake",
            "draft_reply": draft,
            "reply_text": draft,
            "qa_verdict": "PASS",
        }

    def fake_care(messages, seeker, *, care_purpose, care_brief, **kwargs):
        calls["care"].append((messages, seeker, care_purpose, care_brief, kwargs))
        draft = f"Chào {seeker.get('name', 'bạn')}, đây là draft {care_purpose} đã được QA. 🙏"
        return {
            "conversation_analysis": "Seeker is eligible for this operator-opened care session.",
            "knowledge_context": "Verified care facts.",
            "draft_reply": draft,
            "reply_text": draft,
            "qa_verdict": "PASS",
        }

    monkeypatch.setattr(rec, "run_adk_pipeline", fake_inbox)
    monkeypatch.setattr(rec, "run_adk_care_pipeline", fake_care)
    return calls


def _pending(conn_factory):
    conn = conn_factory()
    rows = conn.execute("SELECT queue_type, target_id, action_text, payload_json, status FROM action_queue").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_reply_runs_one_isolated_mas_action_and_enqueues_pending(test_db, mocked_llm):
    result = rec.run([HUNG_BUI], "reply")

    assert result["status"] == "ok"
    assert result["engine"] == "inbox_mas"
    assert result["count"] == 1
    assert len(mocked_llm["inbox"]) == 1
    assert mocked_llm["inbox"][0][0][0]["content"].startswith("Em muốn hỏi")
    assert mocked_llm["inbox"][0][2]["subject_id"] == HUNG_BUI

    rows = _pending(test_db)
    assert len(rows) == 1
    assert rows[0]["queue_type"] == "reply_message"
    assert rows[0]["status"] == "pending"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["source"] == "inbox_mas"
    assert payload["classification"].startswith("Intent: question")


def test_reply_forwards_operator_instruction_to_inbox_mas(test_db, mocked_llm):
    instruction = "Ưu tiên xác nhận lịch lớp phù hợp trước khi trả lời."

    result = rec.run([HUNG_BUI], "reply", instruction=instruction)

    assert result["count"] == 1
    assert mocked_llm["inbox"][0][2]["feedback"] == instruction


def test_operator_care_command_creates_personalized_class_pending_message(test_db, mocked_llm):
    conn = test_db()
    conn.execute("UPDATE users SET program_code=?, lead_stage=? WHERE thread_id=?",
                 ("14h30-CN-Vương Thừa Vũ-HN", "Seeker_Public_Program", HUNG_BUI))
    conn.commit()
    conn.close()

    result = rec.run(
        [HUNG_BUI], "care", city="Hà Nội",
        instruction="Nhắc lịch lớp 14h30 Chủ Nhật tuần này nhé",
        program_code="14h30-CN-Vương Thừa Vũ-HN",
        care_purpose="class_reminder",
    )

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert len(mocked_llm["care"]) == 1
    assert mocked_llm["care"][0][2] == "class_reminder"
    assert mocked_llm["care"][0][3]["verified_session"]["program_code"] == "14h30-CN-Vương Thừa Vũ-HN"
    row = _pending(test_db)[0]
    assert row["queue_type"] == "proactive_message"
    assert json.loads(row["payload_json"])["trigger"] == "operator_care_command"


def test_class_reminder_uses_selected_seekers_program_without_a_program_filter(test_db, mocked_llm):
    """The default UI filter is "all", not an instruction to merge all classes.

    A selected seeker with a verified program must still receive the one
    upcoming session for that program even when the weekly schedule includes
    other classes.
    """
    conn = test_db()
    conn.execute("UPDATE users SET program_code=?, lead_stage=? WHERE thread_id=?",
                 ("14h30-CN-Vương Thừa Vũ-HN", "Seeker_Public_Program", HUNG_BUI))
    conn.commit()
    conn.close()

    result = rec.run(
        [HUNG_BUI], "care", city="Hà Nội",
        instruction="Nhắc lịch lớp phù hợp với đăng ký đã xác thực.",
        care_purpose="class_reminder",
    )

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert mocked_llm["care"][0][3]["verified_session"]["program_code"] == "14h30-CN-Vương Thừa Vũ-HN"
    assert json.loads(_pending(test_db)[0]["payload_json"])["session"]["program_code"] == "14h30-CN-Vương Thừa Vũ-HN"


def test_two_operator_care_commands_can_coexist_for_one_seeker(test_db, mocked_llm):
    first = rec.run([HUNG_BUI], "care", instruction="Hỏi thăm nhẹ nhàng tuần này", trace_id="job-1",
                    care_purpose="warmup")
    second = rec.run([HUNG_BUI], "care", instruction="Gửi một lời chào nhẹ nhàng khác tuần này", trace_id="job-2",
                     care_purpose="warmup")

    assert first["count"] == 1
    assert second["count"] == 1
    rows = _pending(test_db)
    assert len(rows) == 2
    assert {json.loads(row["payload_json"])["dedupe_key"] for row in rows} == {
        "operator-care:warmup:job-1", "operator-care:warmup:job-2",
    }


@pytest.mark.parametrize("variant,blocked", [
    ("sent", True), ("regenerate", True), ("other_date", False),
    ("other_class", False), ("other_page", False), ("other_thread", False),
    ("pending", False), ("approved", False), ("rejected", False),
    ("failed", False), ("drafted", False),
    ("explicit_override", False), ("negated_override", True),
    ("quoted_override", True), ("generic_urgent", True),
])
def test_trace36_reminder_cadence(test_db, mocked_llm, monkeypatch, variant, blocked):
    from datetime import datetime
    from types import SimpleNamespace
    from fb_pipeline.contracts import l1_class_schedule as schedule

    session = {"class_key": "class-hn", "program_code": "class-hn",
               "session_date": "2026-09-20", "starts_at": "2026-09-20 14:30:00"}
    monkeypatch.setattr(schedule, "upcoming_sessions", lambda *a, **k: [
        SimpleNamespace(program_code="class-hn", to_dict=lambda: dict(session))])
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 9, 19, 13, 11)
    monkeypatch.setattr(rec, "datetime", FrozenDatetime)
    prior = dict(session)
    if variant == "other_date":
        prior["session_date"] = "2026-09-13"
    if variant == "other_class":
        prior.update(class_key="other", program_code="other")
    payload = {"type": "class_reminder", "session": prior, "dedupe_key": "old-command"}
    if variant == "drafted":
        payload["delivery_status"] = "drafted"
    status = variant if variant in {"pending", "approved", "rejected", "failed"} else "executed"
    conn = test_db()
    conn.execute("UPDATE users SET program_code='class-hn' WHERE thread_id=?", (HUNG_BUI,))
    conn.execute("""INSERT INTO action_queue
        (queue_type,page_id,target_type,target_id,status,action_text,payload_json,executed_at)
        VALUES ('proactive_message',?,'thread',?,?,?,?,'2026-09-18 06:39:38')""",
        ("other-page" if variant == "other_page" else rec.DEFAULT_PAGE_ID,
         "other-thread" if variant == "other_thread" else HUNG_BUI, status,
         "Chúng ta có hẹn lớp Chủ Nhật lúc 14h30.", json.dumps(payload)))
    conn.commit()
    conn.close()
    instruction = "Soạn tin nhắc lịch học phù hợp cho seeker đã chọn. Chỉ đề xuất khi có lịch đã được xác thực và seeker còn phù hợp để nhận tin."
    instruction += {
        "explicit_override": " Đây là sự kiện cần nhắc lịch dồn dập.",
        "negated_override": " Không phải đây là sự kiện cần nhắc lịch dồn dập.",
        "quoted_override": ' Seeker nói: "Đây là sự kiện cần nhắc lịch dồn dập".',
        "generic_urgent": " Hãy nhắc lại ngay, ưu tiên gấp.",
    }.get(variant, "")
    result = rec.run([HUNG_BUI], "care", care_purpose="class_reminder",
                     trace_id="36", instruction=instruction, regenerate=variant == "regenerate")
    assert result["count"] == (0 if blocked else 1)
    assert len(mocked_llm["care"]) == (0 if blocked else 1)
    assert len(_pending(test_db)) == (1 if blocked else 2)
    if blocked:
        assert result["skipped"][0]["reason"] == "reminder_already_sent_for_session"
        assert "không" in result["skipped"][0]["note"].lower()
    elif variant == "explicit_override":
        brief = mocked_llm["care"][0][3]
        assert brief["reminder_cadence"]["repeat_explicitly_requested"] is True
        assert brief["reminder_cadence"]["sent_reminders"][0]["executed_at"] == "2026-09-18 06:39:38"


def test_care_no_send_is_explained_without_enqueue(test_db, mocked_llm, monkeypatch):
    monkeypatch.setattr(rec, "run_adk_care_pipeline", lambda *a, **k: {
        "no_send_reason": "Đã nhắc lịch hôm qua; hôm nay không phù hợp để nhắc lại.",
        "reply_text": "", "draft_reply": "", "qa_verdict": "",
    })
    result = rec.run([HUNG_BUI], "care", care_purpose="warmup", instruction="Chăm sóc phù hợp")
    assert result["count"] == 0
    assert result["skipped"][0]["reason"] == "care_not_appropriate_now"
    assert "hôm qua" in result["skipped"][0]["note"]
    assert not _pending(test_db)


def test_intensive_reminder_instruction_does_not_override_opt_out(test_db, mocked_llm):
    conn = test_db()
    conn.execute("UPDATE users SET lead_stage='Unsubscribed' WHERE thread_id=?", (HUNG_BUI,))
    conn.commit()
    conn.close()
    result = rec.run([HUNG_BUI], "care", care_purpose="class_reminder",
                     instruction="Đây là sự kiện cần nhắc lịch dồn dập.")
    assert result["count"] == 0
    assert result["skipped"][0]["reason"] == "opt_out"
    assert not mocked_llm["care"]
    assert not _pending(test_db)


def test_all_runs_reply_warmup_event(test_db, mocked_llm):
    conn = test_db()
    conn.execute(
        "INSERT INTO events (name, city, event_date) VALUES (?, ?, date('now', '+3 days'))",
        ("Thiền & Âm nhạc", "Hà Nội"),
    )
    conn.commit()
    conn.close()

    result = rec.run([HUNG_BUI], "all", city="Hà Nội")

    assert result["count"] == 3
    kinds = sorted(p["kind"] for p in result["proposals"])
    assert kinds == ["event", "reply", "warmup"]
    # Contract: every operator-triggered outbound draft has the same Care
    # workflow.  `all` must not retain the legacy composer-only bypasses.
    assert [call[2] for call in mocked_llm["care"]] == ["warmup", "event"]
    payloads = [json.loads(r["payload_json"]) for r in _pending(test_db)]
    assert all(p["source"] == "inbox_mas" for p in payloads)
    assert {p.get("type") for p in payloads} == {None, "warmup", "event"}


@pytest.mark.parametrize("qa_verdict", ["", "REPAIR: incorrect date", "ESCALATE: knowledge_gap"])
def test_care_qa_must_pass_before_any_draft_is_enqueued(test_db, mocked_llm, monkeypatch, qa_verdict):
    """A normal-looking final turn is never authority to bypass QA.

    This protects the fail-closed boundary in the recommendation adapter as
    well as the runtime: no missing, repair, or escalation verdict may create
    an outbound queue item.
    """
    monkeypatch.setattr(rec, "run_adk_care_pipeline", lambda *args, **kwargs: {
        "conversation_analysis": "Eligible warm-up.",
        "knowledge_context": "Verified facts.",
        "draft_reply": "Mời bạn ghé lớp thiền nhé.",
        "reply_text": "Mời bạn ghé lớp thiền nhé.",
        "qa_verdict": qa_verdict,
    })

    result = rec.run([HUNG_BUI], "care", instruction="Hỏi thăm nhẹ nhàng", care_purpose="warmup")

    assert result["count"] == 0
    assert _pending(test_db) == []


def test_care_pass_on_an_old_draft_cannot_enqueue_new_final_text(test_db, mocked_llm, monkeypatch):
    """QA approval is bound to the exact draft, never merely to a session."""
    monkeypatch.setattr(rec, "run_adk_care_pipeline", lambda *args, **kwargs: {
        "conversation_analysis": "Eligible warm-up.",
        "knowledge_context": "Verified facts.",
        "draft_reply": "Mời bạn đến lớp lúc 19h nhé.",
        "reply_text": "Mời bạn đến lớp lúc 20h nhé.",
        "qa_verdict": "PASS",
    })

    result = rec.run([HUNG_BUI], "care", instruction="Hỏi thăm nhẹ nhàng", care_purpose="warmup")

    assert result["count"] == 0
    assert _pending(test_db) == []


@pytest.mark.parametrize("sentinel", ["[OUT_OF_SCOPE]", "[NO_REPLY: stale]", "[NO_SEND: opted out]"])
def test_care_control_sentinels_never_enter_outbound_queue(test_db, mocked_llm, monkeypatch, sentinel):
    monkeypatch.setattr(rec, "run_adk_care_pipeline", lambda *args, **kwargs: {
        "draft_reply": sentinel, "reply_text": sentinel, "qa_verdict": "PASS",
    })

    result = rec.run([HUNG_BUI], "care", instruction="Hỏi thăm nhẹ nhàng", care_purpose="warmup")

    assert result["count"] == 0
    assert _pending(test_db) == []


def test_care_forwards_deterministic_conversation_state_to_pipeline(test_db, mocked_llm):
    result = rec.run([HUNG_BUI], "care", instruction="Hỏi thăm nhẹ nhàng", care_purpose="warmup")

    assert result["count"] == 1
    assert mocked_llm["care"][0][3]["conversation_state"]["state"]


def test_explicit_care_purpose_wins_over_program_filter(test_db, mocked_llm):
    """A UI class filter is eligibility scope, not an implicit reminder intent."""
    result = rec.run(
        [HUNG_BUI], "care",
        instruction="Nhắc bạn về hoạt động cuối tuần.",
        program_code="14h30-CN-Vương Thừa Vũ-HN",
        care_purpose="warmup",
    )

    assert result["count"] == 1
    assert mocked_llm["care"][0][2] == "warmup"
    payload = json.loads(_pending(test_db)[0]["payload_json"])
    assert payload["type"] == "warmup"


@pytest.mark.parametrize("rec_type, expected_purpose", [("warmup", "warmup"), ("event", "event")])
def test_legacy_operator_types_use_care_workflow_not_composer_bypass(
        test_db, mocked_llm, rec_type, expected_purpose):
    if rec_type == "event":
        conn = test_db()
        conn.execute(
            "INSERT INTO events (name, city, event_date) VALUES (?, ?, date('now', '+3 days'))",
            ("Thiền & Âm nhạc", "Hà Nội"),
        )
        conn.commit()
        conn.close()

    result = rec.run([HUNG_BUI], rec_type, city="Hà Nội")

    assert result["count"] == 1
    assert [call[2] for call in mocked_llm["care"]] == [expected_purpose]


def test_event_skipped_when_no_events(test_db, mocked_llm):
    result = rec.run([HUNG_BUI], "event")
    assert result["count"] == 0
    assert result["skipped"] == [{"threadId": HUNG_BUI, "reason": "no_event"}]
    assert mocked_llm["care"] == []


def test_no_duplicate_pending_of_same_kind(test_db, mocked_llm):
    rec.run([HUNG_BUI], "reply")
    second = rec.run([HUNG_BUI], "reply")

    assert second["count"] == 0
    assert second["skipped"][0]["reason"] == "pending_reply_exists"
    assert len(mocked_llm["inbox"]) == 1  # LLM not called again
    assert len(_pending(test_db)) == 1


def test_manual_website_mas_bypasses_scheduler_processed_marker(test_db, mocked_llm):
    """An operator-selected regeneration remains possible after the scheduler
    has consumed that exact customer message."""
    assert seeker_tools.claim_scheduled_inbox_message(HUNG_BUI) is True

    result = rec.run([HUNG_BUI], "reply")

    assert result["count"] == 1
    assert len(mocked_llm["inbox"]) == 1


# code:bug-action-queue-duplicate-proposal-001:regression
def test_no_duplicate_after_first_proposal_is_approved(test_db, mocked_llm):
    """Regression for the bug where approving the first proposal made the
    dedup guard blind to it (it only excluded status='pending'), so a second
    click of "Chạy đề xuất MAS" enqueued a second reply_message proposal for
    the same seeker instead of being skipped."""
    first = rec.run([HUNG_BUI], "reply")
    assert first["count"] == 1
    queue_id = first["proposals"][0]["id"]

    conn = test_db()
    conn.execute("UPDATE action_queue SET status='approved' WHERE id=?", (queue_id,))
    conn.commit()
    conn.close()

    second = rec.run([HUNG_BUI], "reply")
    assert second["count"] == 0
    assert second["skipped"][0]["reason"] == "pending_reply_exists"
    assert len(mocked_llm["inbox"]) == 1  # LLM not called again

    rows = _pending(test_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "approved"


# code:tool-mas-recommend-001:regenerate
def test_regenerate_replaces_pending_or_approved_draft(test_db, mocked_llm):
    """Queue-card "Chạy đề xuất MAS" on an already-queued item: the new draft
    is enqueued, then the old pending/approved one is marked rejected."""
    first = rec.run([HUNG_BUI], "reply")
    old_id = first["proposals"][0]["id"]
    conn = test_db()
    conn.execute("UPDATE action_queue SET status='approved' WHERE id=?", (old_id,))
    conn.commit()
    conn.close()

    second = rec.run([HUNG_BUI], "reply", regenerate=True)

    assert second["count"] == 1
    assert second["supersededCount"] == 1
    assert second["proposals"][0]["supersededIds"] == [old_id]
    assert len(mocked_llm["inbox"]) == 2
    conn = test_db()
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, status, error_text FROM action_queue")}
    conn.close()
    new_id = second["proposals"][0]["id"]
    assert rows[old_id]["status"] == "rejected"
    assert rows[old_id]["error_text"] == f"superseded by #{new_id}"
    assert rows[new_id]["status"] == "pending"
    assert queue.active_proposal_status(HUNG_BUI, "reply_message") == "pending"


def test_regenerate_keeps_old_draft_when_llm_gives_nothing(test_db, mocked_llm, monkeypatch):
    first = rec.run([HUNG_BUI], "reply")
    old_id = first["proposals"][0]["id"]
    monkeypatch.setattr(rec, "run_adk_pipeline", lambda *args, **kwargs: {"reply_text": ""})

    second = rec.run([HUNG_BUI], "reply", regenerate=True)

    assert second["count"] == 0 and second["skipped"][0]["reason"] == "no_reply"
    rows = _pending(test_db)
    assert len(rows) == 1 and rows[0]["status"] == "pending"


def test_regenerate_never_touches_executing_draft(test_db, mocked_llm):
    first = rec.run([HUNG_BUI], "reply")
    old_id = first["proposals"][0]["id"]
    conn = test_db()
    conn.execute("UPDATE action_queue SET status='executing' WHERE id=?", (old_id,))
    conn.commit()
    conn.close()

    second = rec.run([HUNG_BUI], "reply", regenerate=True)

    assert second["count"] == 0
    assert second["skipped"][0]["reason"] == "executing_reply_exists"
    assert len(mocked_llm["inbox"]) == 1
    rows = _pending(test_db)
    assert len(rows) == 1 and rows[0]["status"] == "executing"


def test_regenerate_warmup_only_supersedes_same_payload_type(test_db, mocked_llm):
    conn = test_db()
    conn.execute(
        "INSERT INTO events (name, city, event_date) VALUES (?, ?, date('now', '+3 days'))",
        ("Thiền & Âm nhạc", "Hà Nội"),
    )
    conn.commit()
    conn.close()
    rec.run([HUNG_BUI], "all", city="Hà Nội")

    second = rec.run([HUNG_BUI], "warmup", regenerate=True)

    assert second["count"] == 1 and second["supersededCount"] == 1
    conn = test_db()
    live = conn.execute(
        "SELECT queue_type, json_extract(payload_json, '$.type') AS t FROM action_queue "
        "WHERE status NOT IN ('executed','rejected','failed') ORDER BY id"
    ).fetchall()
    conn.close()
    assert [(r["queue_type"], r["t"]) for r in live] == [
        ("reply_message", None), ("proactive_message", "event"), ("proactive_message", "warmup"),
    ]


def test_reasoning_leak_and_out_of_scope_are_blocked(test_db, mocked_llm, monkeypatch):
    outputs = iter([
        {"classification": "x", "reply_text": "**Crafting a warm reply**\nLet me think about this"},
        {"classification": "spam", "draft_reply": "[OUT_OF_SCOPE]", "reply_text": "[OUT_OF_SCOPE]", "qa_verdict": "PASS"},
    ])
    monkeypatch.setattr(rec, "run_adk_pipeline", lambda *args, **kwargs: next(outputs))

    leak = rec.run([HUNG_BUI], "reply")
    assert leak["count"] == 0 and leak["skipped"][0]["reason"] == "no_reply"

    oos = rec.run([HUNG_BUI], "reply")
    assert oos["count"] == 0 and oos["skipped"][0]["reason"] == "out_of_scope"

    assert _pending(test_db) == []


def test_escalation_note_is_never_enqueued_as_a_reply(test_db, mocked_llm, monkeypatch):
    monkeypatch.setattr(rec, "run_adk_pipeline", lambda *args, **kwargs: {
        "reply_text": "No verified facts were provided.",
        "escalation_reason": "knowledge_gap",
        "escalation_note": "A volunteer needs to verify the schedule.",
        "classification": "Intent: question",
    })

    result = rec.run([HUNG_BUI], "reply")

    assert result["count"] == 0
    assert result["skipped"][0]["reason"] == "escalated_knowledge_gap"
    assert _pending(test_db) == []


def test_unknown_thread_returns_error_without_llm_call(test_db, mocked_llm):
    result = rec.run(["1548373332058326_does_not_exist"], "all")
    assert result["status"] == "error"
    assert mocked_llm["inbox"] == []


def test_unreachable_llm_returns_error_without_writing(test_db, mocked_llm, monkeypatch):
    monkeypatch.setattr(rec, "_check_llm_reachable", lambda timeout=5.0: "LLM endpoint unreachable (http://mock)")
    result = rec.run([HUNG_BUI], "all")
    assert result["status"] == "error"
    assert "unreachable" in result["error"]
    assert _pending(test_db) == []


def test_cli_prints_single_json_line(test_db, mocked_llm, capsys):
    import sys
    sys.argv = ["l5_mas_recommend.py", "--thread-ids", HUNG_BUI, "--type", "reply"]
    code = rec.main()
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert len(out) == 1
    assert json.loads(out[0])["engine"] == "inbox_mas"
