import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_database, should_fetch, log_mas_decision


class TestShouldFetch(unittest.TestCase):
    """Tests for the 1-hour cache logic."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp_dir, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_should_fetch_no_log(self):
        """First run: no log → should fetch."""
        self.assertTrue(should_fetch("page123", self.conn))

    def test_should_fetch_stale_cache(self):
        """Last fetch > 1 hour ago → should fetch."""
        stale_time = (datetime.now() - timedelta(hours=2)).isoformat()
        self.conn.execute(
            "INSERT INTO fetch_log (page_id, fetched_at, threads_found, messages_found) VALUES (?, ?, 5, 10)",
            ("page123", stale_time)
        )
        self.conn.commit()
        self.assertTrue(should_fetch("page123", self.conn))

    def test_should_not_fetch_fresh_cache(self):
        """Last fetch < 1 hour ago → should NOT fetch."""
        fresh_time = (datetime.now() - timedelta(minutes=30)).isoformat()
        self.conn.execute(
            "INSERT INTO fetch_log (page_id, fetched_at, threads_found, messages_found) VALUES (?, ?, 5, 10)",
            ("page123", fresh_time)
        )
        self.conn.commit()
        self.assertFalse(should_fetch("page123", self.conn))


class TestUserAdIdsDB(unittest.TestCase):
    """Tests for user_ad_ids and ad_posts junction tables."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp_dir, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_insert_user_ad_id(self):
        self.conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '6930299765389')")
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM user_ad_ids WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["ad_id"], "6930299765389")

    def test_unique_constraint(self):
        self.conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '6930299765389')")
        self.conn.commit()
        self.conn.execute("INSERT OR IGNORE INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '6930299765389')")
        self.conn.commit()
        count = self.conn.execute("SELECT COUNT(*) as cnt FROM user_ad_ids WHERE thread_id = 't1'").fetchone()["cnt"]
        self.assertEqual(count, 1)

    def test_multiple_ads_per_user(self):
        self.conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '111111')")
        self.conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '222222')")
        self.conn.commit()
        count = self.conn.execute("SELECT COUNT(*) as cnt FROM user_ad_ids WHERE thread_id = 't1'").fetchone()["cnt"]
        self.assertEqual(count, 2)

    def test_ad_posts_insert_and_city(self):
        self.conn.execute(
            "INSERT INTO ad_posts (ad_id, ad_content, city) VALUES (?, ?, ?)",
            ("6930299765389", "Lớp thiền tại Hà Nội...", "Hà Nội")
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM ad_posts WHERE ad_id = '6930299765389'").fetchone()
        self.assertEqual(row["city"], "Hà Nội")
        self.assertIn("Hà Nội", row["ad_content"])


class TestMasSchemaMigrations(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp_dir, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_setup_database_adds_last_synced_at_and_auto_reply_columns(self):
        self.conn.execute(
            '''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT UNIQUE,
                thread_name TEXT,
                phone TEXT,
                email TEXT,
                fb_url TEXT,
                city TEXT DEFAULT 'Unknown',
                lead_stage TEXT DEFAULT 'Intake',
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        self.conn.execute(
            '''
            CREATE TABLE auto_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                reply_text TEXT NOT NULL,
                agent_name TEXT DEFAULT 'responder',
                confidence REAL DEFAULT 1.0,
                escalated BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        self.conn.commit()

        setup_database(self.conn)

        user_cols = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(users)").fetchall()
        }
        auto_reply_cols = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(auto_replies)").fetchall()
        }
        self.assertIn("last_synced_at", user_cols)
        self.assertIn("dry_run", auto_reply_cols)
        self.assertIn("customer_message_timestamp", auto_reply_cols)
        self.assertIn("temperature", user_cols)
        self.assertIn("last_warmup_at", user_cols)
        self.assertIn("warmup_count", user_cols)
        self.assertIn("cool_step", user_cols)

    def test_setup_database_migrates_reactions_to_live_only_uniqueness(self):
        self.conn.execute(
            '''
            CREATE TABLE reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                reaction_type TEXT NOT NULL,
                agent_name TEXT DEFAULT 'reactor',
                dry_run BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(item_type, item_id)
            )
            '''
        )
        self.conn.execute(
            "INSERT INTO reactions (item_type, item_id, reaction_type, dry_run) VALUES ('message', '1', 'like', 1)"
        )
        self.conn.commit()

        setup_database(self.conn)

        self.conn.execute(
            "INSERT INTO reactions (item_type, item_id, reaction_type, dry_run) VALUES ('message', '1', 'love', 0)"
        )
        self.conn.commit()

        rows = self.conn.execute(
            "SELECT reaction_type, dry_run FROM reactions WHERE item_type = 'message' AND item_id = '1' ORDER BY id"
        ).fetchall()
        self.assertEqual([(row["reaction_type"], row["dry_run"]) for row in rows], [("like", 1), ("love", 0)])

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO reactions (item_type, item_id, reaction_type, dry_run) VALUES ('message', '1', 'care', 0)"
            )

    def test_auto_reply_rows_can_store_customer_message_timestamp(self):
        setup_database(self.conn)

        self.conn.execute(
            "INSERT INTO auto_replies (thread_id, reply_text, customer_message_timestamp) VALUES (?, ?, ?)",
            ("thread-1", "Draft reply", "2026-03-25T10:00:00"),
        )
        self.conn.commit()

        row = self.conn.execute(
            "SELECT thread_id, reply_text, customer_message_timestamp FROM auto_replies WHERE thread_id = ?",
            ("thread-1",),
        ).fetchone()
        self.assertEqual(row["thread_id"], "thread-1")
        self.assertEqual(row["reply_text"], "Draft reply")
        self.assertEqual(row["customer_message_timestamp"], "2026-03-25T10:00:00")

    def test_log_mas_decision_persists_payload_json(self):
        setup_database(self.conn)

        result = log_mas_decision(
            page_id="page123",
            route="warmup",
            subject_type="thread",
            subject_id="thread_1",
            decision="blocked",
            reason="pending_inbox_reply",
            dry_run=True,
            payload={"temperature": "cool", "days_dormant": 9},
            conn=self.conn,
        )

        self.assertEqual(result["status"], "logged")
        row = self.conn.execute(
            "SELECT route, subject_type, subject_id, decision, reason, dry_run, payload_json FROM mas_decisions WHERE id = ?",
            (result["decision_id"],),
        ).fetchone()
        self.assertEqual(row["route"], "warmup")
        self.assertEqual(row["subject_type"], "thread")
        self.assertEqual(row["subject_id"], "thread_1")
        self.assertEqual(row["decision"], "blocked")
        self.assertEqual(row["reason"], "pending_inbox_reply")
        self.assertEqual(row["dry_run"], 1)
        self.assertIn('"temperature": "cool"', row["payload_json"])


if __name__ == '__main__':
    unittest.main()
