"""Unit tests for tools/l5_mas_trace_debug.py (code:mas-debug-001)."""
import sqlite3

import pytest

import fb_pipeline.persistence.l4_sqlite_store as store
import tools.l5_mas_trace_debug as debug


@pytest.fixture(autouse=True)
def shared_memory_db(monkeypatch):
    """One shared in-memory DB for the whole test, patched into both modules
    that call get_db_connection (store itself and the module under test)."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    store.setup_database(conn)

    class NonClosingConn:
        def __getattr__(self, name):
            return getattr(conn, name)

        def close(self):
            pass

    def fake_get_db_connection(memory_dir=None, logger=None):
        return NonClosingConn()

    monkeypatch.setattr(store, "get_db_connection", fake_get_db_connection)
    monkeypatch.setattr(debug, "get_db_connection", fake_get_db_connection)
    return conn


def _insert_call(conn, **overrides):
    row = {
        "trace_id": "t1", "seq_in_trace": 1, "attempt": 1,
        "started_at": "2026-09-17T20:00:00", "trigger": "manual_recommendation",
        "route": "propose", "route_group": "MAS", "agent_name": "InboxOrchestrator",
        "subject_type": "thread", "subject_id": "THREAD_1", "subject_label": "Test User",
        "status": "ok", "tokens_in": 100, "tokens_out": 10,
        "state_json": '{"draft_reply": "hello"}', "response_text": "hi",
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO llm_calls ({cols}) VALUES ({placeholders})", tuple(row.values()))
    conn.commit()


def test_get_trace_calls_orders_by_id_not_by_seq(shared_memory_db):
    conn = shared_memory_db
    _insert_call(conn, seq_in_trace=1)
    _insert_call(conn, seq_in_trace=2)
    # A second, independent run that collided on the same trace_id and reset
    # seq_in_trace back to 1 — the exact anomaly found in trace_id="20".
    _insert_call(conn, seq_in_trace=1, started_at="2026-09-17T20:30:00")

    calls = debug.get_trace_calls("t1")

    assert [c["seq_in_trace"] for c in calls] == [1, 2, 1]
    assert calls[0]["id"] < calls[1]["id"] < calls[2]["id"]


def test_resolve_thread_id_for_trace(shared_memory_db):
    conn = shared_memory_db
    _insert_call(conn, subject_id="THREAD_42")

    assert debug.resolve_thread_id_for_trace("t1") == "THREAD_42"
    assert debug.resolve_thread_id_for_trace("no-such-trace") is None


def test_find_trace_ids_for_thread_orders_most_recent_first(shared_memory_db):
    conn = shared_memory_db
    _insert_call(conn, trace_id="old", subject_id="THREAD_1", started_at="2026-09-17T20:00:00")
    _insert_call(conn, trace_id="new", subject_id="THREAD_1", started_at="2026-09-17T21:00:00")
    _insert_call(conn, trace_id="other-thread", subject_id="THREAD_2", started_at="2026-09-17T22:00:00")

    assert debug.find_trace_ids_for_thread("THREAD_1") == ["new", "old"]


def test_get_conversation_state_flags_already_answered(shared_memory_db):
    conn = shared_memory_db
    conn.executemany(
        "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, kind, message_at) "
        "VALUES (?, ?, ?, ?, ?, 'message', ?)",
        [
            ("THREAD_1", "Customer", "Toi muon dang ky", "x", 0, "2026-09-15 09:05:00"),
            ("THREAD_1", "Page", "Da, ban co the dung Zoom khong?", "x", 1, "2026-09-15 09:05:00"),
        ],
    )
    conn.commit()

    state = debug.get_conversation_state("THREAD_1")

    assert state["state"] == "already_answered"
    assert state["action"] == "skip"


def test_build_report_resolves_thread_from_trace_id(shared_memory_db):
    conn = shared_memory_db
    _insert_call(conn, subject_id="THREAD_9")
    conn.execute(
        "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, kind, message_at) "
        "VALUES ('THREAD_9', 'Customer', 'hello', 'x', 0, 'message', '2026-09-17 10:00:00')"
    )
    conn.commit()

    report = debug.build_report("t1")

    assert report["resolved_thread_id"] == "THREAD_9"
    assert len(report["calls"]) == 1
    assert "conversation_state_now" in report


def test_build_report_accepts_thread_id_directly(shared_memory_db):
    conn = shared_memory_db
    _insert_call(conn, trace_id="t7", subject_id="THREAD_5")

    report = debug.build_report("THREAD_5")

    assert report["resolved_trace_id"] == "t7"
    assert report["resolved_thread_id"] == "THREAD_5"


def test_render_timeline_handles_empty():
    assert "no llm_calls" in debug.render_timeline([])


def test_render_call_io_truncates_by_default_and_full_flag_expands():
    call = {"id": 1, "agent_name": "X", "trace_id": "t1", "seq_in_trace": 1,
            "status": "ok", "tokens_in": 1, "tokens_out": 1, "duration_ms": 1,
            "response_text": "a" * 2000}

    short = debug.render_call_io(call, full=False)
    full = debug.render_call_io(call, full=True)

    assert "+800 chars" in short
    assert "a" * 2000 in full
