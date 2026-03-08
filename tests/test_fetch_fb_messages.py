import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import sqlite3
import tempfile
import shutil
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.fetch_fb_messages import (
    parse_page_id, fetch_messages, should_fetch, extract_user_info,
    detect_city, get_list_unique_user, fetch_message_by_user,
    setup_database, get_db_connection
)


class TestParsePageId(unittest.TestCase):
    def test_parse_page_id_raw(self):
        self.assertEqual(parse_page_id("12345"), "12345")
        
    def test_parse_page_id_url(self):
        url = "https://business.facebook.com/latest/inbox/all/?nav_ref=manage_page_ap_plus_inbox_message_button&asset_id=1548373332058326&business_id="
        self.assertEqual(parse_page_id(url), "1548373332058326")
        
    def test_parse_page_id_fallback(self):
        self.assertEqual(parse_page_id("not-a-url-or-number"), "not-a-url-or-number")


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


class TestExtractUserInfo(unittest.TestCase):
    """Tests for phone/email extraction from messages."""
    
    def test_extract_phone_from_customer(self):
        messages = [
            {"sender": "Page", "content": "Bạn vui lòng gửi SĐT"},
            {"sender": "Customer", "content": "0935539464"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertEqual(info["phone"], "0935539464")
    
    def test_extract_email_from_customer(self):
        messages = [
            {"sender": "Customer", "content": "ni8745@gmail.com"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertEqual(info["email"], "ni8745@gmail.com")
    
    def test_extract_both(self):
        messages = [
            {"sender": "Customer", "content": "Trần thị hoài thương"},
            {"sender": "Customer", "content": "ni8745@gmail.com"},
            {"sender": "Customer", "content": "0935539464"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertEqual(info["phone"], "0935539464")
        self.assertEqual(info["email"], "ni8745@gmail.com")
    
    def test_no_info_found(self):
        messages = [
            {"sender": "Customer", "content": "Xin chào"},
            {"sender": "Page", "content": "Chào bạn!"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertIsNone(info["phone"])
        self.assertIsNone(info["email"])


class TestDetectCity(unittest.TestCase):
    """Tests for city detection from ad content."""
    
    def test_detect_hanoi(self):
        ad = "Lớp thiền miễn phí tại số 40 Vương Thừa Vũ, Hà Nội"
        self.assertEqual(detect_city(ad, []), "Hà Nội")
    
    def test_detect_hcm(self):
        ad = "Thiền Sahaja Yoga tại TP.HCM"
        self.assertEqual(detect_city(ad, []), "TP. Hồ Chí Minh")
    
    def test_detect_danang(self):
        ad = "Lớp thiền tại Đà Nẵng"
        self.assertEqual(detect_city(ad, []), "Đà Nẵng")
    
    def test_detect_nghean(self):
        ad = "Lớp tại Nghệ An"
        self.assertEqual(detect_city(ad, []), "Nghệ An")
    
    def test_detect_haiphong(self):
        ad = "Thiền tại Hải Phòng"
        self.assertEqual(detect_city(ad, []), "Hải Phòng")
    
    def test_detect_from_page_message(self):
        msgs = [{"sender": "Page", "content": "Địa chỉ: 02 Xô Viết Nghệ Tĩnh, Bình Thạnh"}]
        self.assertEqual(detect_city("", msgs), "TP. Hồ Chí Minh")
    
    def test_detect_unknown(self):
        self.assertEqual(detect_city("Lớp thiền", []), "Unknown")


class TestFetchMessagesCDP(unittest.TestCase):
    """Tests for the CDP credential capture flow."""

    @patch('tools.fetch_fb_messages.os.path.exists')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_fetch_messages_cdp_flow(self, mock_sync_playwright, mock_exists):
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
        
        result = fetch_messages("123", "test_cred")
        
        self.assertEqual(result["success"], True)
        self.assertEqual(result["method"], "cdp_capture")


class TestFetchMessagesHeadless(unittest.TestCase):
    """Tests for the headless fetch flow with structured message extraction."""

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
        patcher = patch('tools.fetch_fb_messages.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _setup_mock_playwright(self, mock_sync_playwright, mock_exists, 
                                thread_text="Test User\nThis is a preview.",
                                js_messages=None, ad_context=""):
        mock_exists.return_value = True
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_p.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_page.url = "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=9876"
        mock_page.frames = [MagicMock()]
        mock_page.content.return_value = "<html>Mock DOM</html>"
        
        mock_thread_locator = MagicMock()
        mock_thread = MagicMock()
        mock_thread.inner_text.return_value = thread_text
        mock_thread_locator.count.return_value = 1
        mock_thread_locator.nth.return_value = mock_thread
        mock_page.locator.return_value = mock_thread_locator
        
        if js_messages is None:
            js_messages = [
                {"sender": "Customer", "text": "Lớp học có mất phí không?", "timestamp": "Sat 7:19 PM"},
                {"sender": "Page", "text": "Dạ hoàn toàn miễn phí ạ", "timestamp": "Sat 7:19 PM"},
            ]
        
        # Build mock thread data for scroll-wait-check loop
        mock_thread_data = [{
            "index": 0,
            "name": thread_text.split('\n')[0].strip(),
            "text": thread_text,
            "lines": [l.strip() for l in thread_text.split('\n') if l.strip()] + ["Today"]
        }]
        
        # Mock mouse for wheel scrolling
        mock_mouse = MagicMock()
        mock_page.mouse = mock_mouse
        
        call_count = {"collect": 0}
        
        def evaluate_side_effect(script, args=None):
            if isinstance(args, dict) and "name" in args:
                # Scroll-to-position-and-click call: always succeed
                return True
            if isinstance(args, str):
                # Retry click call with thread name: always succeed
                return True
            if "Message list container" in script:
                return js_messages
            elif "Xem bài viết" in script:
                return ad_context
            elif "_ikh" in script and "innerText" in script and "lines" in script:
                # Collect-visible call in scroll loop
                call_count["collect"] += 1
                if call_count["collect"] <= 1:
                    return mock_thread_data
                return []  # no more threads on subsequent rounds
            elif "scrollTop" in script and "c.scrollTop = 0" in script:
                # Scroll-back-to-top
                return None
            return ""
        mock_page.evaluate.side_effect = evaluate_side_effect
        
        return mock_p, mock_page

    @patch('tools.fetch_fb_messages.os.path.exists')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_headless_extracts_structured_messages(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        mock_p, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "headless_fetch")
        self.assertEqual(result["data"]["stats"]["new_threads"], 1)
        self.assertEqual(result["data"]["stats"]["new_messages"], 2)

    @patch('tools.fetch_fb_messages.os.path.exists')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_headless_no_messages(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=[])
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_messages"], 0)

    @patch('tools.fetch_fb_messages.os.path.exists')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_headless_with_ad_context(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        ad_text = "Thiền miễn phí tại Hà Nội"
        messages = [{"sender": "Customer", "text": "0912345678", "timestamp": "Sat 7:19 PM"}]
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=messages, ad_context=ad_text)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_messages"], 1)

    @patch('tools.fetch_fb_messages.os.path.exists')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_cache_hit_skips_browser(self, mock_sync_playwright, mock_exists):
        """When cache is fresh and --refresh is NOT set, browser should NOT launch."""
        self._patch_sqlite()
        mock_exists.return_value = True
        # Pre-populate the fetch_log with a fresh entry
        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        conn = sqlite3.connect(tmp_db)
        setup_database(conn)
        fresh_time = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO fetch_log (page_id, fetched_at, threads_found, messages_found) VALUES (?, ?, 2, 20)",
            ("123", fresh_time)
        )
        conn.commit()
        conn.close()
        
        result = fetch_messages("123", "test_cred", force_refresh=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "cache_hit")
        # Browser should NOT have been launched
        mock_sync_playwright.return_value.__enter__.return_value.chromium.launch.assert_not_called()


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
        patcher = patch('tools.fetch_fb_messages.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Populate test data
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
        patcher = patch('tools.fetch_fb_messages.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn = original_connect(tmp_db)
        setup_database(conn)
        conn.execute("INSERT INTO threads VALUES ('t1', 'page1', 'User A', 'preview', datetime('now'))")
        conn.execute("INSERT INTO users (thread_id, thread_name, phone, email, city, last_interaction) VALUES ('t1', 'User A', '0911111111', 'a@b.com', 'Hà Nội', datetime('now'))")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Customer', 'Hello', 'Today')")
        conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp) VALUES ('t1', 'Page', 'Hi!', 'Today')")
        conn.commit()
        conn.close()

    def test_lookup_by_thread_id(self):
        self._patch_db()
        result = fetch_message_by_user("page1", "t1")
        self.assertTrue(result["success"])
        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(result["user"]["name"], "User A")

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


if __name__ == '__main__':
    unittest.main()
