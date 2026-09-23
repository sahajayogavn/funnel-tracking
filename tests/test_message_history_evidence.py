"""Regression coverage for message-bound reaction annotations handed to MAS."""
import json
import sqlite3


def _connection(path):
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    return conn


def test_seeker_tool_returns_reactions_on_their_target_message(tmp_path, monkeypatch):
    import adk_agents.tools.l5_seeker_tools as seeker_tools
    db_path = str(tmp_path / "history.db")
    conn = _connection(db_path)
    conn.execute("INSERT INTO threads (id, page_id, thread_name) VALUES ('thread-1', 'page-1', 'Lan')")
    annotation = [
        {"actor": "Seeker", "emoji": "👍", "count": 2, "confidence": "explicit"},
        {"actor": "Page", "emoji": "❤", "count": 1, "confidence": "explicit"},
    ]
    conn.execute(
        """INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, source_id, kind,
                                   reaction_annotation_json)
           VALUES (?, ?, ?, ?, ?, ?, 'message', ?)""",
        ("thread-1", "Page", "Cảm ơn bạn", "Sep 10, 2026 9:00 AM", 1, "message-1",
         json.dumps(annotation, ensure_ascii=False)),
    )
    conn.commit(); conn.close()
    monkeypatch.setattr(seeker_tools, "get_db_connection", lambda: _connection(db_path))

    result = seeker_tools.get_thread_messages("thread-1")

    assert result["status"] == "success"
    assert result["messages"][0]["reactions"] == annotation
    assert "reaction_events" not in result


def test_seeker_tool_reads_legacy_message_schema_without_annotation_column(tmp_path, monkeypatch):
    import adk_agents.tools.l5_seeker_tools as seeker_tools
    db_path = str(tmp_path / "legacy-history.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, thread_id TEXT, sender TEXT, content TEXT, message_timestamp TEXT, seq INTEGER)")
    conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
                 ("thread-legacy", "Customer", "Mình muốn hỏi lớp", "Sep 10, 2026", 1))
    conn.commit(); conn.close()
    def legacy_connection():
        connection = sqlite3.connect(db_path); connection.row_factory = sqlite3.Row; return connection
    monkeypatch.setattr(seeker_tools, "get_db_connection", legacy_connection)

    result = seeker_tools.get_thread_messages("thread-legacy")
    assert result["status"] == "success"
    assert result["messages"][0]["reactions"] == []


def test_unbound_reaction_does_not_create_a_synthetic_message(tmp_path):
    from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record
    conn = _connection(str(tmp_path / "reaction-only.db"))
    record = enrich_thread_record(build_thread_record("page-1", {"name": "Hà", "text": "Hà"}), [{
        "sender": "Unknown", "text": "", "source_id": "reaction-control-1",
        "reactions": [{"actor": "unknown", "emoji": "👍", "target_type": "unknown"}],
    }], extract_user_info, detect_city)
    result = persist_thread_record(conn, record, detect_city)
    assert result["messages_added"] == 0
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id=?", (record.thread_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawled_message_reactions'").fetchone() is None


def test_two_party_rule_forces_reactor_to_the_other_message_participant():
    from fb_pipeline.contracts.l1_inbox import InboxMessage
    from fb_pipeline.inbox.l3_pipeline import _reaction_annotations
    annotations = _reaction_annotations([
        InboxMessage(sender="Page", content="Mời bạn", source_id="page-1", reactions=[{
            "actor": "unknown", "emoji": "👍", "target_type": "message", "target_message_id": "page-1",
        }]),
        InboxMessage(sender="Customer", content="Dạ", source_id="seeker-1", reactions=[{
            "actor": "Customer", "emoji": "❤", "target_type": "message", "target_message_id": "seeker-1",
        }]),
    ])
    assert annotations == {
        "page-1": [{"actor": "Seeker", "emoji": "👍", "count": 1, "confidence": "two_party_rule"}],
        "seeker-1": [{"actor": "Page", "emoji": "❤", "count": 1, "confidence": "two_party_rule"}],
    }
