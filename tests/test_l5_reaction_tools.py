"""
Tests for MAS Route 1 — Reaction tools.
code:test-mas-reaction-001

Tests find_unreacted_items, log_reaction with seeded DB data.
"""
import os
import sys
import sqlite3
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Create a seeded in-memory DB with messages and comments."""
    db_path = str(tmp_path / "frankensqlite.db")
    monkeypatch.setattr(
        "fb_pipeline.persistence.l4_sqlite_store.get_db_connection",
        lambda *a, **kw: _create_seeded_conn(db_path)
    )
    monkeypatch.setattr(
        "fb_pipeline.persistence.l4_sqlite_store.get_comment_db_connection",
        lambda *a, **kw: _create_seeded_comment_conn(db_path)
    )
    # Also patch at import target for tools that import directly
    monkeypatch.setattr(
        "adk_agents.tools.l5_reaction_tools.get_db_connection",
        lambda *a, **kw: _create_seeded_conn(db_path)
    )
    monkeypatch.setattr(
        "adk_agents.tools.l5_reaction_tools.get_comment_db_connection",
        lambda *a, **kw: _create_seeded_comment_conn(db_path)
    )
    return db_path


def _create_seeded_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    setup_database(conn)

    # Seed threads and messages
    conn.execute(
        "INSERT OR IGNORE INTO threads (id, page_id, thread_name) VALUES (?, ?, ?)",
        ("thread_001", "119587786260266", "Test User")
    )
    conn.execute(
        "INSERT OR IGNORE INTO messages (id, thread_id, sender, content, message_timestamp, seq) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (1, "thread_001", "Customer", "Cảm ơn bạn rất nhiều!", "2026-03-22T10:00:00", 1)
    )
    conn.execute(
        "INSERT OR IGNORE INTO messages (id, thread_id, sender, content, message_timestamp, seq) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (2, "thread_001", "Customer", "Xin chào", "2026-03-22T10:05:00", 2)
    )
    conn.commit()
    return conn


def _create_seeded_comment_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from fb_pipeline.persistence.l4_sqlite_store import setup_comment_database, setup_database
    setup_database(conn)
    setup_comment_database(conn)

    # Seed posts and comments
    conn.execute(
        "INSERT OR IGNORE INTO posts (id, page_id, post_name) VALUES (?, ?, ?)",
        ("post_001", "119587786260266", "Test Post")
    )
    conn.execute(
        "INSERT OR IGNORE INTO comments (id, post_id, commenter_name, comment_text, comment_timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (100, "post_001", "Commenter A", "Tuyệt vời!", "2026-03-22T11:00:00")
    )
    conn.commit()
    return conn


class TestFindUnreactedItems:
    def test_finds_unreacted_messages(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import find_unreacted_items
        result = find_unreacted_items("119587786260266")
        assert result["status"] == "success"
        assert result["count"] >= 2
        # All items should be messages or comments
        types = {item["item_type"] for item in result["items"]}
        assert types <= {"message", "comment"}

    def test_finds_unreacted_comments(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import find_unreacted_items
        result = find_unreacted_items("119587786260266")
        comment_items = [i for i in result["items"] if i["item_type"] == "comment"]
        assert len(comment_items) >= 1

    def test_excludes_already_reacted(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import find_unreacted_items, log_reaction
        # Live reaction suppresses future selection
        log_reaction("message", "1", "love", dry_run=False)
        result = find_unreacted_items("119587786260266")
        msg_ids = [i["item_id"] for i in result["items"] if i["item_type"] == "message"]
        assert "1" not in msg_ids

    def test_dry_run_reaction_does_not_suppress_live_selection(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import find_unreacted_items, log_reaction
        log_reaction("message", "1", "love", dry_run=True)
        result = find_unreacted_items("119587786260266")
        msg_ids = [i["item_id"] for i in result["items"] if i["item_type"] == "message"]
        assert "1" in msg_ids


class TestLogReaction:
    def test_logs_successfully(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import log_reaction
        result = log_reaction("message", "1", "love")
        assert result["status"] == "logged"
        assert result["reaction_type"] == "love"
        assert result["dry_run"] is True

    def test_dedup_on_same_item(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import log_reaction
        log_reaction("message", "1", "love")
        # Second insert should be ignored (INSERT OR IGNORE)
        result = log_reaction("message", "1", "care")
        assert result["status"] == "logged"

    def test_different_items_ok(self, seeded_db):
        from adk_agents.tools.l5_reaction_tools import log_reaction
        r1 = log_reaction("message", "1", "love")
        r2 = log_reaction("comment", "100", "like")
        assert r1["status"] == "logged"
        assert r2["status"] == "logged"
