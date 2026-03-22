import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_database
from tools.fetch_fb_messages import (
    fetch_message_by_user,
    get_list_unique_user,
    get_user_ad_ids,
    propagate_city_from_ads,
    resolve_ad_posts,
)


class TestGetUserAdIds(unittest.TestCase):
    """Tests for the get_user_ad_ids DB-only action."""

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
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'preview', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city, last_interaction) VALUES ('t1', 'User A', 'Unknown', datetime('now'))")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '6930299765389')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '6892367141614')")
        conn.execute("INSERT INTO ad_posts (ad_id, ad_content, city) VALUES ('6930299765389', 'Lớp tại Hà Nội', 'Hà Nội')")
        conn.commit()
        conn.close()

    def test_get_user_ad_ids(self):
        self._patch_db()
        result = get_user_ad_ids("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        ad_ids = [a["ad_id"] for a in result["associations"]]
        self.assertIn("6930299765389", ad_ids)
        self.assertIn("6892367141614", ad_ids)

    def test_get_user_ad_ids_empty(self):
        self._patch_db()
        result = get_user_ad_ids("nonexistent_page")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)


class TestResolveAdPosts(unittest.TestCase):
    """Tests for the resolve_ad_posts action (DB-only strategy 1)."""

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
        return tmp_db, original_connect

    def test_resolve_from_stored_content(self):
        """Strategy 1: resolve city from already-stored ad_content."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Unknown')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '111111')")
        conn.execute("INSERT INTO ad_posts (ad_id, ad_content, city) VALUES ('111111', 'Lớp thiền miễn phí tại Đà Nẵng', 'Unknown')")
        conn.commit()
        conn.close()

        result = resolve_ad_posts("page1", use_cdp=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["resolved"], 1)

        conn2 = orig_connect(tmp_db)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT city FROM users WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["city"], "Đà Nẵng")
        conn2.close()

    def test_all_already_resolved(self):
        """No unresolved ads → quick return."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Hà Nội')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '111111')")
        conn.execute("INSERT INTO ad_posts (ad_id, ad_content, city) VALUES ('111111', 'Hà Nội content', 'Hà Nội')")
        conn.commit()
        conn.close()

        result = resolve_ad_posts("page1", use_cdp=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["resolved"], 0)


class TestGetListUniqueUser(unittest.TestCase):
    """Tests for the DB-only user listing action."""

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
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'preview', datetime('now'))")
        conn.execute("INSERT INTO threads VALUES ('t2', 'page1', 'User B', 'preview', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, phone, city, last_interaction) VALUES ('t1', 'User A', '0911111111', 'Hà Nội', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, phone, city, last_interaction) VALUES ('t2', 'User B', '0922222222', 'Đà Nẵng', datetime('now'))")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Customer', 'Hello', 'Today')")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Page', 'Hi!', 'Today')")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t2', 'Customer', 'Xin chào', 'Today')")
        conn.commit()
        conn.close()

    def test_list_users(self):
        self._patch_db()
        result = get_list_unique_user("page1", "7d")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["users"][0]["phone"], "0911111111")
        self.assertEqual(result["users"][1]["city"], "Đà Nẵng")


class TestFetchMessageByUser(unittest.TestCase):
    """Tests for the DB-only user message lookup action."""

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
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'preview', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, phone, email, city, last_interaction) VALUES ('t1', 'User A', '0911111111', 'a@b.com', 'Hà Nội', datetime('now'))")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Customer', 'Hello', 'Today')")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Page', 'Hi!', 'Today')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '6930299765389')")
        conn.commit()
        conn.close()

    def test_lookup_by_thread_id(self):
        self._patch_db()
        result = fetch_message_by_user("page1", "t1")
        self.assertTrue(result["success"])
        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(result["user"]["name"], "User A")
        self.assertIn("6930299765389", result["user"]["ad_ids"])

    def test_lookup_by_phone(self):
        self._patch_db()
        result = fetch_message_by_user("page1", "0911111111")
        self.assertTrue(result["success"])
        self.assertEqual(len(result["messages"]), 2)

    def test_lookup_by_email(self):
        self._patch_db()
        result = fetch_message_by_user("page1", "a@b.com")
        self.assertTrue(result["success"])
        self.assertEqual(result["user"]["phone"], "0911111111")

    def test_user_not_found(self):
        self._patch_db()
        result = fetch_message_by_user("page1", "nonexistent")
        self.assertFalse(result["success"])


class TestPropagateCityFromAds(unittest.TestCase):
    """Tests for the propagate_city_from_ads batch action."""

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
        return tmp_db, original_connect

    def test_propagate_from_resolved_ad(self):
        """Users with resolved ad city get updated."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Unknown')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '111111')")
        conn.execute("INSERT INTO ad_posts (ad_id, ad_content, city) VALUES ('111111', 'Lớp thiền tại Đà Nẵng', 'Đà Nẵng')")
        conn.commit()
        conn.close()

        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_ads"], 1)

        conn2 = orig_connect(tmp_db)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT city FROM users WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["city"], "Đà Nẵng")
        conn2.close()

    def test_unknown_ad_city_not_updated(self):
        """Users with 'Unknown' ad city remain unchanged."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Unknown')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '222222')")
        conn.execute("INSERT INTO ad_posts (ad_id, ad_content, city) VALUES ('222222', 'Generic ad', 'Unknown')")
        conn.commit()
        conn.close()

        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_ads"], 0)

    def test_no_ad_association_unchanged(self):
        """Users without ad associations remain unchanged."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Unknown')")
        conn.commit()
        conn.close()

        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_ads"], 0)
        self.assertEqual(result["total_updated"], 0)

    def test_multiple_users_same_ad(self):
        """Multiple users sharing the same ad_id all get updated."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO threads VALUES ('t2', 'page1', 'User B', 'y', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Unknown')")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t2', 'User B', 'Unknown')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t1', '333333')")
        conn.execute("INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t2', '333333')")
        conn.execute("INSERT INTO ad_posts (ad_id, ad_content, city) VALUES ('333333', 'Thiền tại Hà Nội', 'Hà Nội')")
        conn.commit()
        conn.close()

        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_ads"], 2)

        conn2 = orig_connect(tmp_db)
        conn2.row_factory = sqlite3.Row
        rows = conn2.execute("SELECT city FROM users ORDER BY thread_id").fetchall()
        self.assertEqual(rows[0]["city"], "Hà Nội")
        self.assertEqual(rows[1]["city"], "Hà Nội")
        conn2.close()

    def test_fallback_message_city_detection(self):
        """Strategy 2: Detect city from Page messages when no ad data."""
        tmp_db, orig_connect = self._patch_db()
        conn = orig_connect(tmp_db)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'x', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, city) VALUES ('t1', 'User A', 'Unknown')")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Page', 'Địa chỉ: 02 Xô Viết Nghệ Tĩnh, Bình Thạnh', 'Today')")
        conn.commit()
        conn.close()

        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_messages"], 1)

        conn2 = orig_connect(tmp_db)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT city FROM users WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["city"], "TP. Hồ Chí Minh")
        conn2.close()


if __name__ == '__main__':
    unittest.main()
