import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_database, should_fetch


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


if __name__ == '__main__':
    unittest.main()
