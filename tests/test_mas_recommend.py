# code:test-validation-001:mas-recommend
"""Unit tests for tools/l5_mas_recommend.py with ADK agents mocked out.

Verifies that the web-triggered MAS entrypoint:
- routes selected threads through the batch/warmup/event agents,
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
    monkeypatch.setattr(rec, "setup_llm_env", lambda: None)
    monkeypatch.setenv("OPENAI_API_BASE", "http://mock")
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    monkeypatch.setattr(rec, "_check_llm_reachable", lambda timeout=5.0: None)
    monkeypatch.setattr(rec, "load_knowledge_context", lambda: "KB")

    calls = {"batch": [], "warmup": [], "event": []}

    def fake_batch(batch_payload, feedback=None):
        calls["batch"].append(batch_payload)
        return [{
            "thread_id": t["thread_id"],
            "classification": "Intent: question, Language: vi, Urgency: low, Stage: Intake",
            "reply_text": f"Chào {t['thread_name']}! Lớp thiền ở Hà Nội hoàn toàn miễn phí ạ 🙏",
        } for t in batch_payload]

    def fake_warmup(seeker, strategy, knowledge_context, dry_run=True, feedback=None):
        calls["warmup"].append((seeker, strategy))
        return "Bạn ơi, tuần này có buổi thiền miễn phí, bạn ghé nhé 🌿"

    def fake_event(event, seeker, knowledge_context, dry_run=True, feedback=None):
        calls["event"].append((event, seeker))
        return f"Mời bạn tham gia '{event['name']}' tại {event['city']} nhé ✨"

    monkeypatch.setattr(rec, "run_adk_batch_pipeline", fake_batch)
    monkeypatch.setattr(rec, "run_adk_warmup_composer", fake_warmup)
    monkeypatch.setattr(rec, "run_adk_event_advertiser", fake_event)
    return calls


def _pending(conn_factory):
    conn = conn_factory()
    rows = conn.execute("SELECT queue_type, target_id, action_text, payload_json, status FROM action_queue").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_reply_goes_through_batch_agent_and_enqueues_pending(test_db, mocked_llm):
    result = rec.run([HUNG_BUI], "reply")

    assert result["status"] == "ok"
    assert result["engine"] == "inbox_mas"
    assert result["count"] == 1
    assert len(mocked_llm["batch"]) == 1
    assert mocked_llm["batch"][0][0]["thread_id"] == HUNG_BUI
    assert mocked_llm["batch"][0][0]["messages"][0]["content"].startswith("Em muốn hỏi")

    rows = _pending(test_db)
    assert len(rows) == 1
    assert rows[0]["queue_type"] == "reply_message"
    assert rows[0]["status"] == "pending"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["source"] == "inbox_mas"
    assert payload["classification"].startswith("Intent: question")


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
    assert len(mocked_llm["warmup"]) == 1 and len(mocked_llm["event"]) == 1
    payloads = [json.loads(r["payload_json"]) for r in _pending(test_db)]
    assert all(p["source"] == "inbox_mas" for p in payloads)
    assert {p.get("type") for p in payloads} == {None, "warmup", "event"}


def test_event_skipped_when_no_events(test_db, mocked_llm):
    result = rec.run([HUNG_BUI], "event")
    assert result["count"] == 0
    assert result["skipped"] == [{"threadId": HUNG_BUI, "reason": "no_event"}]
    assert mocked_llm["event"] == []


def test_no_duplicate_pending_of_same_kind(test_db, mocked_llm):
    rec.run([HUNG_BUI], "reply")
    second = rec.run([HUNG_BUI], "reply")

    assert second["count"] == 0
    assert second["skipped"][0]["reason"] == "pending_reply_exists"
    assert len(mocked_llm["batch"]) == 1  # LLM not called again
    assert len(_pending(test_db)) == 1


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
    assert len(mocked_llm["batch"]) == 1  # LLM not called again

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
    assert len(mocked_llm["batch"]) == 2
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
    monkeypatch.setattr(rec, "run_adk_batch_pipeline",
                        lambda batch, feedback=None: [{"thread_id": HUNG_BUI, "reply_text": ""}])

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
    assert len(mocked_llm["batch"]) == 1
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
        [{"thread_id": HUNG_BUI, "classification": "x", "reply_text": "**Crafting a warm reply**\nLet me think about this"}],
        [{"thread_id": HUNG_BUI, "classification": "spam", "reply_text": "[OUT_OF_SCOPE]"}],
    ])
    monkeypatch.setattr(rec, "run_adk_batch_pipeline", lambda batch, feedback=None: next(outputs))

    leak = rec.run([HUNG_BUI], "reply")
    assert leak["count"] == 0 and leak["skipped"][0]["reason"] == "no_reply"

    oos = rec.run([HUNG_BUI], "reply")
    assert oos["count"] == 0 and oos["skipped"][0]["reason"] == "out_of_scope"

    assert _pending(test_db) == []


def test_unknown_thread_returns_error_without_llm_call(test_db, mocked_llm):
    result = rec.run(["1548373332058326_does_not_exist"], "all")
    assert result["status"] == "error"
    assert mocked_llm["batch"] == []


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
