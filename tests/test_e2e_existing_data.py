"""
E2E Existing Data Integrity Tests.
code:test-e2e-data-001

Read-only tests against the production FrankenSQLite database to validate
data integrity of the 92 existing threads.

Run:
    .venv/bin/python -m pytest tests/test_e2e_existing_data.py -v
"""
import os
import sys
import unittest

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "memory", "agent_memory", "frankensqlite.db")

skip_no_db = pytest.mark.skipif(
    not os.path.exists(DB_PATH),
    reason="Production frankensqlite.db not available"
)


def get_readonly_conn():
    import sqlite3
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@skip_no_db
class TestExistingDataIntegrity(unittest.TestCase):
    """Read-only integrity checks on the production DB."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_readonly_conn()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_thread_count_minimum(self):
        """DB has at least 90 threads."""
        count = self.conn.execute("SELECT COUNT(*) as cnt FROM threads").fetchone()["cnt"]
        self.assertGreaterEqual(count, 90, f"Expected ≥90 threads, got {count}")

    def test_message_count_minimum(self):
        """DB has at least 800 messages."""
        count = self.conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()["cnt"]
        self.assertGreaterEqual(count, 800, f"Expected ≥800 messages, got {count}")

    def test_all_users_have_thread_name(self):
        """All users have non-null thread_name."""
        rows = self.conn.execute(
            "SELECT thread_id FROM users WHERE thread_name IS NULL OR thread_name = ''"
        ).fetchall()
        self.assertEqual(len(rows), 0, f"Found {len(rows)} users with null thread_name")

    def test_all_messages_reference_valid_threads(self):
        """All messages reference thread IDs that exist in the threads table."""
        orphans = self.conn.execute("""
            SELECT COUNT(*) as cnt FROM messages m
            LEFT JOIN threads t ON t.id = m.thread_id
            WHERE t.id IS NULL
        """).fetchone()["cnt"]
        self.assertEqual(orphans, 0, f"Found {orphans} orphan messages")

    def test_top_thread_has_substantial_messages(self):
        """The thread with most messages has at least 25."""
        row = self.conn.execute("""
            SELECT t.thread_name, COUNT(m.id) as msg_count
            FROM threads t JOIN messages m ON m.thread_id = t.id
            GROUP BY t.id ORDER BY msg_count DESC LIMIT 1
        """).fetchone()
        self.assertGreaterEqual(
            row["msg_count"], 25,
            f"Top thread '{row['thread_name']}' has only {row['msg_count']} messages"
        )

    def test_mas_handoff_construction_for_existing_thread(self):
        """MasHandoff can be constructed from an existing thread's data."""
        from fb_pipeline.contracts.l1_inbox import (
            MasHandoff, SeekerInfo, InboxMessage, detect_city, extract_user_info,
        )

        # Get the thread with most messages
        thread = self.conn.execute("""
            SELECT t.id, t.thread_name, t.page_id, u.phone, u.city, u.fb_url
            FROM threads t JOIN users u ON u.thread_id = t.id
            ORDER BY (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) DESC
            LIMIT 1
        """).fetchone()

        msgs = self.conn.execute(
            "SELECT sender, content, message_timestamp, seq FROM messages "
            "WHERE thread_id = ? ORDER BY seq",
            (thread["id"],)
        ).fetchall()

        # Construct MAS handoff
        messages = [
            InboxMessage(
                sender=m["sender"],
                content=m["content"],
                message_timestamp=m["message_timestamp"] or "",
                seq=m["seq"],
            )
            for m in msgs
        ]

        seeker = SeekerInfo(
            name=thread["thread_name"],
            phone=thread["phone"],
            city=thread["city"] or "Unknown",
        )

        handoff = MasHandoff(
            thread_id=thread["id"],
            thread_name=thread["thread_name"],
            page_id=thread["page_id"],
            fb_url=thread["fb_url"] or "",
            seeker=seeker,
            messages=messages,
        )

        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.thread_name, thread["thread_name"])
        self.assertGreater(len(handoff.messages), 0)
        self.assertEqual(handoff.seeker.name, thread["thread_name"])

    def test_page_id_consistency(self):
        """All threads belong to the same page_id."""
        rows = self.conn.execute(
            "SELECT DISTINCT page_id FROM threads"
        ).fetchall()
        page_ids = [r["page_id"] for r in rows]
        self.assertEqual(len(page_ids), 1, f"Multiple page IDs found: {page_ids}")
        self.assertEqual(page_ids[0], "1548373332058326")


if __name__ == "__main__":
    unittest.main()
