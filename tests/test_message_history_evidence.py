"""Regression coverage for source-aware Inbox history handed to MAS."""
import sqlite3


def _connection(path):
    from fb_pipeline.persistence.l4_sqlite_store import setup_database

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    return conn


def test_seeker_tool_keeps_quote_reaction_and_time_evidence_separate(tmp_path, monkeypatch):
    import adk_agents.tools.l5_seeker_tools as seeker_tools

    db_path = str(tmp_path / "history.db")
    conn = _connection(db_path)
    # These nullable fields are created by the migration.  The separate
    # legacy-schema test below proves that the reader also tolerates snapshots
    # without them.
    conn.execute("INSERT INTO threads (id, page_id, thread_name) VALUES ('thread-1', 'page-1', 'Lan')")
    conn.execute(
        """INSERT INTO messages
           (thread_id, sender, content, message_timestamp, message_at, seq,
            source_id, sender_confidence, raw_timestamp, day_context,
            time_precision, reply_to_message_id, quoted_sender,
            quoted_sender_confidence, quoted_text, sender_evidence,
            quote_evidence, kind)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'message')""",
        (
            "thread-1", "Customer", "Dạ", "Sep 10, 2026 9:00 AM",
            "2026-09-10 09:00:00", 1, "message-1", "confirmed",
            "Sep 10, 2026 9:00 AM", "2026-09-10", "date_time",
            "message-page-0", "Page", "explicit", "Bạn ở Hà Nội phải không?",
            "aria-label=Lan sent a message", "reply header=Page",
        ),
    )
    conn.execute(
        """INSERT INTO crawled_message_reactions
           (thread_id, reaction_key, actor, emoji, target_type, target_message_id,
            observed_at, parse_confidence, actor_role, target_scope, evidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "thread-1", "reaction-1", "Page", "❤", "message", "message-1",
            "2026-09-10 09:01:00", "explicit", "Page", "message",
            "aria-label=Page reacted love",
        ),
    )
    conn.execute(
        """INSERT INTO crawled_message_reactions
           (thread_id, reaction_key, actor, emoji, target_type, target_message_id,
            observed_at, parse_confidence, actor_role, target_scope, evidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "thread-1", "reaction-thread", "Customer", "👍", "thread", None,
            "2026-09-10 09:02:00", "explicit", "Customer", "thread",
            "aria-label=Lan reacted to this conversation",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(seeker_tools, "get_db_connection", lambda: _connection(db_path))
    result = seeker_tools.get_thread_messages("thread-1")

    assert result["status"] == "success"
    message = result["messages"][0]
    assert message["content"] == "Dạ"
    assert message["day_context"] == "2026-09-10"
    assert message["quoted_sender"] == "Page"
    assert message["quoted_sender_confidence"] == "explicit"
    assert message["quoted_text"] == "Bạn ở Hà Nội phải không?"
    assert message["sender_evidence"] == "aria-label=Lan sent a message"
    assert message["quote_evidence"] == "reply header=Page"
    assert "reactions" not in message
    # Both the message-targeted and thread-level observations remain events.
    # The latter cannot be attached to a message without inventing a target.
    assert result["reaction_events"] == [
        {
            "reaction_id": 1, "reaction_key": "reaction-1", "source_id": None,
            "actor": "Page", "actor_role": "Page", "emoji": "❤",
            "target_type": "message", "target_message_id": "message-1",
            "target_id": "message-1", "target_scope": "message",
            "observed_at": "2026-09-10 09:01:00", "occurred_at": None,
            "raw_label": None, "evidence": "aria-label=Page reacted love",
            "parse_confidence": "explicit",
        },
        {
            "reaction_id": 2, "reaction_key": "reaction-thread", "source_id": None,
            "actor": "Customer", "actor_role": "Customer", "emoji": "👍",
            "target_type": "thread", "target_message_id": None,
            "target_id": None, "target_scope": "thread",
            "observed_at": "2026-09-10 09:02:00", "occurred_at": None,
            "raw_label": None, "evidence": "aria-label=Lan reacted to this conversation",
            "parse_confidence": "explicit",
        },
    ]


def test_unknown_reaction_target_is_a_separate_event_not_message_metadata(tmp_path, monkeypatch):
    import adk_agents.tools.l5_seeker_tools as seeker_tools

    db_path = str(tmp_path / "unknown-target.db")
    conn = _connection(db_path)
    conn.execute("INSERT INTO threads (id, page_id, thread_name) VALUES ('thread-2', 'page-1', 'Minh')")
    conn.execute(
        "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, source_id, kind) VALUES (?, ?, ?, ?, ?, ?, 'message')",
        ("thread-2", "Unknown", "Xin chào", "9:00 AM", 1, "message-2"),
    )
    conn.execute(
        "INSERT INTO crawled_message_reactions (thread_id, reaction_key, actor, emoji, target_type) VALUES (?, ?, ?, ?, ?)",
        ("thread-2", "reaction-unknown", "unknown", "👍", "unknown"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(seeker_tools, "get_db_connection", lambda: _connection(db_path))
    result = seeker_tools.get_thread_messages("thread-2")

    assert result["status"] == "success"
    assert result["messages"][0]["sender"] == "Unknown"
    assert "reactions" not in result["messages"][0]
    assert result["reaction_events"][0]["target_type"] == "unknown"
    assert result["reaction_events"][0]["target_message_id"] is None


def test_seeker_tool_reads_legacy_message_schema_without_evidence_or_reaction_table(tmp_path, monkeypatch):
    """A read-only snapshot before the notation migration remains usable."""
    import adk_agents.tools.l5_seeker_tools as seeker_tools

    db_path = str(tmp_path / "legacy-history.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE messages (
            id INTEGER PRIMARY KEY, thread_id TEXT, sender TEXT, content TEXT,
            message_timestamp TEXT, seq INTEGER
        )"""
    )
    conn.execute(
        "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
        ("thread-legacy", "Customer", "Mình muốn hỏi lớp", "Sep 10, 2026", 1),
    )
    conn.commit()
    conn.close()

    def legacy_connection():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(seeker_tools, "get_db_connection", legacy_connection)
    result = seeker_tools.get_thread_messages("thread-legacy")

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["messages"] == [{
        "sender": "Customer", "content": "Mình muốn hỏi lớp",
        "timestamp": "Sep 10, 2026", "message_at": None,
        "message_at_approx": None, "seq": 1, "source_id": None,
        "sender_confidence": "unknown", "raw_timestamp": None,
        "day_context": None, "time_precision": "unknown",
        "reply_to_message_id": None, "quoted_sender": None,
        "quoted_sender_confidence": "unknown", "quoted_text": None,
        "sender_evidence": None, "quote_evidence": None,
    }]
    assert result["reaction_events"] == []


def test_reaction_only_observation_is_persisted_without_synthetic_message(tmp_path):
    from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record

    db_path = str(tmp_path / "reaction-only.db")
    conn = _connection(db_path)
    record = enrich_thread_record(
        build_thread_record("page-1", {"name": "Hà", "text": "Hà"}),
        [{
            "sender": "Unknown", "text": "", "source_id": "reaction-control-1",
            "reactions": [{
                "actor": "unknown", "emoji": "👍", "target_type": "unknown",
                "target_message_id": None, "parse_confidence": "unknown",
            }],
        }],
        extract_user_info, detect_city,
    )

    result = persist_thread_record(conn, record, detect_city)

    assert result["messages_added"] == 0
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id=?", (record.thread_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM crawled_message_reactions WHERE thread_id=?", (record.thread_id,)).fetchone()[0] == 1
    conn.close()
