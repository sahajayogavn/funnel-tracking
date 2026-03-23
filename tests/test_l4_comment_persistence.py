import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_comment_database, should_fetch_comments


class TestCommentShouldFetch(unittest.TestCase):
    """Tests for the 1-hour cache logic for comments."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp_dir, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        setup_comment_database(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_should_fetch_no_log(self):
        """First run: no log → should fetch."""
        self.assertTrue(should_fetch_comments("page123", self.conn))

    def test_should_fetch_stale_cache(self):
        """Last fetch > 1 hour ago → should fetch."""
        stale_time = (datetime.now() - timedelta(hours=2)).isoformat()
        self.conn.execute(
            "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, 2, 10)",
            ("page123", stale_time)
        )
        self.conn.commit()
        self.assertTrue(should_fetch_comments("page123", self.conn))

    def test_should_not_fetch_fresh_cache(self):
        """Last fetch < 1 hour ago → should NOT fetch."""
        fresh_time = (datetime.now() - timedelta(minutes=30)).isoformat()
        self.conn.execute(
            "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, 2, 10)",
            ("page123", fresh_time)
        )
        self.conn.commit()
        self.assertFalse(should_fetch_comments("page123", self.conn))


class TestCommentSchemaMigrations(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp_dir, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_setup_comment_database_adds_last_synced_at_column(self):
        self.conn.execute(
            '''
            CREATE TABLE comment_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT,
                commenter_name TEXT,
                fb_user_id TEXT,
                fb_profile_url TEXT,
                phone TEXT,
                email TEXT,
                city TEXT DEFAULT 'Unknown',
                lead_stage TEXT DEFAULT 'Intake',
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, commenter_name)
            )
            '''
        )
        self.conn.commit()

        setup_comment_database(self.conn)

        cols = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(comment_users)").fetchall()
        }
        self.assertIn("last_synced_at", cols)


if __name__ == '__main__':
    unittest.main()
