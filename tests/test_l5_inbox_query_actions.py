import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from adk_agents.tools.l5_seeker_tools import claim_scheduled_inbox_message, find_unreplied_threads
from fb_pipeline.persistence.l4_sqlite_store import setup_database


class TestFindUnrepliedThreads(unittest.TestCase):
    """# Gate 4: code:test-validation-001:l4-to-l5"""
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp_dir, "frankensqlite.db")

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

        def _make_conn(*args, **kwargs):
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            setup_database(c)
            return c

        self.patcher = patch(
            'adk_agents.tools.l5_seeker_tools.get_db_connection',
            side_effect=_make_conn,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _insert_thread(self, thread_id: str, page_id: str = "page1", thread_name: str = "User A"):
        self.conn.execute(
            "INSERT INTO threads (id, page_id, thread_name, last_synced_time) VALUES (?, ?, ?, ?)",
            (thread_id, page_id, thread_name, "2026-03-25T09:00:00"),
        )

    def _insert_customer_message(self, thread_id: str, content: str, message_timestamp: str, recorded_at: str, seq: int):
        self.conn.execute(
            "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, timestamp) VALUES (?, 'Customer', ?, ?, ?, ?)",
            (thread_id, content, message_timestamp, seq, recorded_at),
        )

    def _insert_unknown_message(self, thread_id: str, content: str, seq: int):
        self.conn.execute(
            "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, timestamp) "
            "VALUES (?, 'Unknown', ?, '2026-03-25T10:00:00', ?, '2026-03-25T10:00:00')",
            (thread_id, content, seq),
        )

    def _insert_auto_reply_ack(self, thread_id: str, seq: int | None, dry_run: bool = True):
        import json
        payload = {"last_message_seq": seq} if seq is not None else {}
        self.conn.execute(
            "INSERT INTO telegram_hitl_queue (route, thread_id, telegram_message_id, payload_json, status, created_at, updated_at) VALUES (?, ?, 'msg1', ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("inbox", thread_id, json.dumps(payload)),
        )

    def test_selects_thread_when_latest_customer_message_has_no_acknowledgement(self):
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1",
            "Hello",
            "2026-03-25T10:00:00",
            "2026-03-25T10:00:00",
            1,
        )
        self.conn.commit()

        result = find_unreplied_threads("page1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["threads"][0]["thread_id"], "thread-1")

    def test_draft_acknowledgement_suppresses_repeat_drafting_even_when_dry_run_true(self):
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1",
            "Hello",
            "2026-03-25T10:00:00",
            "2026-03-25T10:00:00",
            1,
        )
        self._insert_auto_reply_ack("thread-1", 1, dry_run=True)
        self.conn.commit()

        result = find_unreplied_threads("page1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["threads"], [])

    def test_newer_customer_message_reopens_thread_after_prior_acknowledgement(self):
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1",
            "Hello",
            "2026-03-25T10:00:00",
            "2026-03-25T10:00:00",
            1,
        )
        self._insert_auto_reply_ack("thread-1", 1)
        self._insert_customer_message(
            "thread-1",
            "Following up",
            "2026-03-25T11:00:00",
            "2026-03-25T11:00:00",
            2,
        )
        self.conn.commit()

        result = find_unreplied_threads("page1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["threads"][0]["thread_id"], "thread-1")

    def test_old_acknowledgement_does_not_suppress_newer_customer_message(self):
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1",
            "First",
            "2026-03-25T09:00:00",
            "2026-03-25T09:00:00",
            1,
        )
        self._insert_customer_message(
            "thread-1",
            "Second",
            "2026-03-25T12:00:00",
            "2026-03-25T12:00:00",
            2,
        )
        self._insert_auto_reply_ack("thread-1", 1)
        self.conn.commit()

        result = find_unreplied_threads("page1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["threads"][0]["thread_id"], "thread-1")

    def test_rows_without_customer_message_timestamp_do_not_create_false_suppression(self):
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1",
            "Hello",
            "2026-03-25T10:00:00",
            "2026-03-25T10:00:00",
            1,
        )
        self._insert_auto_reply_ack("thread-1", None)
        self.conn.commit()

        result = find_unreplied_threads("page1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["threads"][0]["thread_id"], "thread-1")

    def test_latest_unknown_body_is_selected_for_review_not_silently_ignored(self):
        """The runner's deterministic state gate will make this needs_review.

        Selection here only makes the uncertainty observable; it does not make
        the row eligible for claim or an LLM draft.
        """
        self._insert_thread("thread-unknown")
        self._insert_unknown_message("thread-unknown", "Lan sent a message", 1)
        self.conn.commit()

        result = find_unreplied_threads("page1")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["threads"][0]["thread_id"], "thread-unknown")
        self.assertEqual(result["threads"][0]["latest_sender"], "Unknown")

    def test_empty_unknown_row_is_not_an_actionable_review_candidate(self):
        self._insert_thread("thread-empty-unknown")
        self._insert_unknown_message("thread-empty-unknown", "   ", 1)
        self.conn.commit()

        self.assertEqual(find_unreplied_threads("page1")["count"], 0)

    def test_scheduler_claim_suppresses_repeat_mas_after_an_agent_error(self):
        """The claim is written before an LLM call, so a 15-minute retry does
        not pay for the same customer turn after a downstream MAS failure."""
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1", "Hello", "2026-03-25T10:00:00", "2026-03-25T10:00:00", 1,
        )
        self.conn.commit()

        self.assertTrue(claim_scheduled_inbox_message("thread-1"))
        self.assertEqual(find_unreplied_threads("page1")["count"], 0)
        # A new customer response is a new unit of work and reopens the thread.
        self._insert_customer_message(
            "thread-1", "Following up", "2026-03-25T11:00:00", "2026-03-25T11:00:00", 2,
        )
        self.conn.commit()
        self.assertEqual(find_unreplied_threads("page1")["count"], 1)

    def test_scheduler_claim_does_not_consume_a_newer_unread_customer_turn(self):
        self._insert_thread("thread-1")
        self._insert_customer_message(
            "thread-1", "First", "2026-03-25T10:00:00", "2026-03-25T10:00:00", 1,
        )
        self._insert_customer_message(
            "thread-1", "Newer", "2026-03-25T10:01:00", "2026-03-25T10:01:00", 2,
        )
        self.conn.commit()

        self.assertFalse(claim_scheduled_inbox_message("thread-1", expected_message_seq=1))
        self.assertEqual(find_unreplied_threads("page1")["count"], 1)


if __name__ == '__main__':
    unittest.main()
