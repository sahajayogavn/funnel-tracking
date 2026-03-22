import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_comment_database
from tools.fetch_comments import get_comment_users, get_comments_by_post


class TestGetCommentsByPost(unittest.TestCase):
    """Tests for the DB-only comment lookup action."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _patch_db(self):
        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        original_connect = sqlite3.connect

        def patched_connect(path, *args, **kwargs):
            if "frankensqlite" in str(path):
                return original_connect(tmp_db, *args, **kwargs)
            return original_connect(path, *args, **kwargs)

        patcher = patch('fb_pipeline.persistence.l4_sqlite_store.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn = original_connect(tmp_db)
        setup_comment_database(conn)
        conn.execute("INSERT INTO posts VALUES ('p1', 'page1', 'Post A', 'http://fb.com/p1', 'preview', datetime('now'))")
        conn.execute("INSERT INTO posts VALUES ('p2', 'page1', 'Post B', 'http://fb.com/p2', 'preview', datetime('now'))")
        conn.execute("INSERT INTO comments (post_id, commenter_name, comment_text, comment_timestamp) VALUES ('p1', 'User A', 'Hello', 'Today')")
        conn.execute("INSERT INTO comments (post_id, commenter_name, comment_text, comment_timestamp) VALUES ('p1', 'User B', 'Great!', 'Today')")
        conn.execute("INSERT INTO comments (post_id, commenter_name, comment_text, comment_timestamp) VALUES ('p2', 'User C', 'Nice post', 'Yesterday')")
        conn.commit()
        conn.close()

    def test_get_all_comments(self):
        self._patch_db()
        result = get_comments_by_post("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 3)

    def test_get_comments_by_specific_post(self):
        self._patch_db()
        result = get_comments_by_post("page1", "p1")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["comments"][0]["commenter_name"], "User A")

    def test_get_comments_no_results(self):
        self._patch_db()
        result = get_comments_by_post("page1", "nonexistent")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)


class TestGetCommentUsers(unittest.TestCase):
    """Tests for the DB-only commenter listing action."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _patch_db(self):
        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        original_connect = sqlite3.connect

        def patched_connect(path, *args, **kwargs):
            if "frankensqlite" in str(path):
                return original_connect(tmp_db, *args, **kwargs)
            return original_connect(path, *args, **kwargs)

        patcher = patch('fb_pipeline.persistence.l4_sqlite_store.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn = original_connect(tmp_db)
        setup_comment_database(conn)
        conn.execute("INSERT INTO posts VALUES ('p1', 'page1', 'Post A', 'http://fb.com/p1', 'preview', datetime('now'))")
        conn.execute("INSERT INTO comment_users (post_id, commenter_name, fb_user_id, fb_profile_url, phone, city, last_interaction) VALUES ('p1', 'User A', 'user.a', 'https://fb.com/user.a', '0911111111', 'Hà Nội', datetime('now'))")
        conn.execute("INSERT INTO comment_users (post_id, commenter_name, fb_user_id, fb_profile_url, phone, city, last_interaction) VALUES ('p1', 'User B', 'user.b', 'https://fb.com/user.b', '0922222222', 'Đà Nẵng', datetime('now'))")
        conn.execute("INSERT INTO comments (post_id, commenter_name, comment_text, comment_timestamp) VALUES ('p1', 'User A', 'Hello', 'Today')")
        conn.execute("INSERT INTO comments (post_id, commenter_name, comment_text, comment_timestamp) VALUES ('p1', 'User B', 'Great!', 'Today')")
        conn.commit()
        conn.close()

    def test_list_comment_users(self):
        self._patch_db()
        result = get_comment_users("page1", "7d")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["users"][0]["phone"], "0911111111")
        self.assertEqual(result["users"][1]["city"], "Đà Nẵng")


if __name__ == '__main__':
    unittest.main()
