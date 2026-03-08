import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import sqlite3
import tempfile
import shutil
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.fetch_comments import (
    parse_page_id, parse_post_id, fetch_comments, should_fetch, extract_user_info,
    detect_city, get_comments_by_post, get_comment_users,
    setup_comment_database, get_db_connection
)


class TestParsePageId(unittest.TestCase):
    def test_parse_page_id_raw(self):
        self.assertEqual(parse_page_id("12345"), "12345")

    def test_parse_page_id_url(self):
        url = "https://business.facebook.com/latest/inbox/facebook?asset_id=1548373332058326&mailbox_id=&selected_item_id=4490615344500762&thread_type=FB_PAGE_POST"
        self.assertEqual(parse_page_id(url), "1548373332058326")

    def test_parse_page_id_fallback(self):
        self.assertEqual(parse_page_id("not-a-url-or-number"), "not-a-url-or-number")


class TestParsePostId(unittest.TestCase):
    def test_parse_post_id_from_url(self):
        url = "https://business.facebook.com/latest/inbox/facebook?asset_id=1548373332058326&selected_item_id=4490615344500762&thread_type=FB_PAGE_POST"
        self.assertEqual(parse_post_id(url), "4490615344500762")

    def test_parse_post_id_raw(self):
        self.assertEqual(parse_post_id("4490615344500762"), "4490615344500762")


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
        self.assertTrue(should_fetch("page123", self.conn))

    def test_should_fetch_stale_cache(self):
        """Last fetch > 1 hour ago → should fetch."""
        stale_time = (datetime.now() - timedelta(hours=2)).isoformat()
        self.conn.execute(
            "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, 2, 10)",
            ("page123", stale_time)
        )
        self.conn.commit()
        self.assertTrue(should_fetch("page123", self.conn))

    def test_should_not_fetch_fresh_cache(self):
        """Last fetch < 1 hour ago → should NOT fetch."""
        fresh_time = (datetime.now() - timedelta(minutes=30)).isoformat()
        self.conn.execute(
            "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, 2, 10)",
            ("page123", fresh_time)
        )
        self.conn.commit()
        self.assertFalse(should_fetch("page123", self.conn))


class TestExtractUserInfoComments(unittest.TestCase):
    """Tests for phone/email extraction from comments."""

    def test_extract_phone(self):
        comments = [{"comment_text": "Tôi muốn tham gia, SĐT 0935539464"}]
        info = extract_user_info(comments)
        self.assertEqual(info["phone"], "0935539464")

    def test_extract_email(self):
        comments = [{"comment_text": "Email tôi là ni8745@gmail.com"}]
        info = extract_user_info(comments)
        self.assertEqual(info["email"], "ni8745@gmail.com")

    def test_extract_both(self):
        comments = [
            {"comment_text": "Liên hệ 0912345678"},
            {"comment_text": "hoặc email abc@test.com"},
        ]
        info = extract_user_info(comments)
        self.assertEqual(info["phone"], "0912345678")
        self.assertEqual(info["email"], "abc@test.com")

    def test_no_info_found(self):
        comments = [{"comment_text": "Xin chào"}, {"comment_text": "Tôi muốn tham gia"}]
        info = extract_user_info(comments)
        self.assertIsNone(info["phone"])
        self.assertIsNone(info["email"])


class TestDetectCityComments(unittest.TestCase):
    """Tests for city detection from comment text."""

    def test_detect_hanoi(self):
        self.assertEqual(detect_city("Lớp thiền tại Hà Nội"), "Hà Nội")

    def test_detect_hcm(self):
        self.assertEqual(detect_city("Tôi ở TP.HCM"), "TP. Hồ Chí Minh")

    def test_detect_danang(self):
        self.assertEqual(detect_city("Đà Nẵng có lớp không"), "Đà Nẵng")

    def test_detect_unknown(self):
        self.assertEqual(detect_city("Tôi muốn tham gia"), "Unknown")


class TestFetchCommentsCDP(unittest.TestCase):
    """Tests for the CDP credential capture flow."""

    @patch('tools.fetch_comments.os.path.exists')
    @patch('tools.fetch_comments.sync_playwright')
    def test_fetch_comments_cdp_flow(self, mock_sync_playwright, mock_exists):
        mock_exists.return_value = False

        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p

        mock_browser = MagicMock()
        mock_p.chromium.connect_over_cdp.return_value = mock_browser

        mock_context = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_page = MagicMock()
        mock_context.pages = []
        mock_context.new_page.return_value = mock_page
        mock_page.content.return_value = "<html>Mock DOM</html>"

        result = fetch_comments("123", "test_cred")

        self.assertEqual(result["success"], True)
        self.assertEqual(result["method"], "cdp_capture")


class TestFetchCommentsHeadless(unittest.TestCase):
    """Tests for the headless fetch flow with comment extraction."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _patch_sqlite(self):
        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        original_connect = sqlite3.connect
        def patched_connect(path, *args, **kwargs):
            if "frankensqlite" in str(path):
                return original_connect(tmp_db, *args, **kwargs)
            return original_connect(path, *args, **kwargs)
        patcher = patch('tools.fetch_comments.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _setup_mock_playwright(self, mock_sync_playwright, mock_exists,
                                post_text="Post Title\nSome preview text.",
                                js_comments=None):
        mock_exists.return_value = True
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_p.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_page.url = "https://business.facebook.com/latest/inbox/facebook?asset_id=123&selected_item_id=456&thread_type=FB_PAGE_POST"
        mock_page.frames = [MagicMock()]
        mock_page.content.return_value = "<html>Mock DOM</html>"

        mock_post_locator = MagicMock()
        mock_post = MagicMock()
        mock_post.inner_text.return_value = post_text
        mock_post_locator.count.return_value = 1
        mock_post_locator.nth.return_value = mock_post
        mock_page.locator.return_value = mock_post_locator

        if js_comments is None:
            js_comments = [
                {"commenter_name": "Nguyễn Văn A", "comment_text": "Lớp học có mất phí không?", "timestamp": "2d", "profile_url": "https://facebook.com/nguyen.a", "fb_user_id": "nguyen.a", "is_reply": False},
                {"commenter_name": "Trần Thị B", "comment_text": "Tôi muốn tham gia, SĐT 0912345678", "timestamp": "1d", "profile_url": "https://facebook.com/profile.php?id=12345", "fb_user_id": "12345", "is_reply": False},
            ]

        # Build mock post thread data for scroll loop
        mock_post_data = [{
            "domIndex": 0,
            "name": post_text.split('\n')[0].strip(),
            "text": post_text,
            "lines": [l.strip() for l in post_text.split('\n') if l.strip()] + ["Today"]
        }]

        # Mock mouse for wheel scrolling
        mock_mouse = MagicMock()
        mock_page.mouse = mock_mouse

        call_count = {"collect": 0}

        def evaluate_side_effect(script, args=None):
            if "_ikh" in script and "innerText" in script and "lines" in script:
                # Collect-visible call in scroll loop
                call_count["collect"] += 1
                if call_count["collect"] <= 1:
                    return mock_post_data
                return []  # no more posts on subsequent rounds
            elif "role" in script and "article" in script:
                # Comment extraction JS
                return js_comments
            return ""
        mock_page.evaluate.side_effect = evaluate_side_effect

        return mock_p, mock_page

    @patch('tools.fetch_comments.os.path.exists')
    @patch('tools.fetch_comments.sync_playwright')
    def test_headless_extracts_comments(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        mock_p, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists)
        result = fetch_comments("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "headless_fetch")
        self.assertEqual(result["data"]["stats"]["new_posts"], 1)
        self.assertEqual(result["data"]["stats"]["new_comments"], 2)

    @patch('tools.fetch_comments.os.path.exists')
    @patch('tools.fetch_comments.sync_playwright')
    def test_headless_no_comments(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_comments=[])
        result = fetch_comments("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_comments"], 0)

    @patch('tools.fetch_comments.os.path.exists')
    @patch('tools.fetch_comments.sync_playwright')
    def test_headless_with_phone_in_comment(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        comments = [
            {"commenter_name": "Test User", "comment_text": "SĐT: 0935539464", "timestamp": "1d", "profile_url": "", "fb_user_id": "", "is_reply": False}
        ]
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_comments=comments)
        result = fetch_comments("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_comments"], 1)

    @patch('tools.fetch_comments.os.path.exists')
    @patch('tools.fetch_comments.sync_playwright')
    def test_cache_hit_skips_browser(self, mock_sync_playwright, mock_exists):
        """When cache is fresh and --refresh is NOT set, browser should NOT launch."""
        self._patch_sqlite()
        mock_exists.return_value = True
        # Pre-populate the comment_fetch_log with a fresh entry
        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        conn = sqlite3.connect(tmp_db)
        setup_comment_database(conn)
        fresh_time = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, 2, 20)",
            ("123", fresh_time)
        )
        conn.commit()
        conn.close()

        result = fetch_comments("123", "test_cred", force_refresh=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "cache_hit")
        # Browser should NOT have been launched
        mock_sync_playwright.return_value.__enter__.return_value.chromium.launch.assert_not_called()


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
        patcher = patch('tools.fetch_comments.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Populate test data
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
        patcher = patch('tools.fetch_comments.sqlite3.connect', side_effect=patched_connect)
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
