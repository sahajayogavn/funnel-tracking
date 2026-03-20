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
    setup_database, get_db_connection, _scrape_inbox,
    parse_ad_ids, get_user_ad_ids, resolve_ad_posts,
    propagate_city_from_ads
)


class TestParsePageId(unittest.TestCase):
    def test_parse_page_id_raw(self):
        self.assertEqual(parse_page_id("12345"), "12345")
        
    def test_parse_page_id_url(self):
        url = "https://business.facebook.com/latest/inbox/all/?nav_ref=manage_page_ap_plus_inbox_message_button&asset_id=1548373332058326&business_id="
        self.assertEqual(parse_page_id(url), "1548373332058326")
        
    def test_parse_page_id_fallback(self):
        self.assertEqual(parse_page_id("not-a-url-or-number"), "not-a-url-or-number")


class TestParseAdIds(unittest.TestCase):
    """Tests for ad_id pattern extraction."""
    
    def test_single_ad_id(self):
        text = "∞ ad_id.6930299765389"
        self.assertEqual(parse_ad_ids(text), ["6930299765389"])
    
    def test_multiple_ad_ids(self):
        text = "Intake ad_id.6930299765389 ad_id.6892367141614 messenger_ads"
        result = parse_ad_ids(text)
        self.assertEqual(len(result), 2)
        self.assertIn("6930299765389", result)
        self.assertIn("6892367141614", result)
    
    def test_no_ad_ids(self):
        text = "Intake messenger_ads some other label"
        self.assertEqual(parse_ad_ids(text), [])
    
    def test_partial_match_ignored(self):
        text = "ad_id.123"  # too short (< 5 digits)
        self.assertEqual(parse_ad_ids(text), [])
    
    def test_ad_id_without_dot(self):
        text = "ad_id6930299765389"
        self.assertEqual(parse_ad_ids(text), ["6930299765389"])
    
    def test_ad_id_from_log_line(self):
        text = "Intake \u200b ad_id.6892367141614"
        self.assertEqual(parse_ad_ids(text), ["6892367141614"])


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
        patcher = patch('tools.fetch_fb_messages.sqlite3.connect', side_effect=patched_connect)
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
        patcher = patch('tools.fetch_fb_messages.sqlite3.connect', side_effect=patched_connect)
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
        
        # Check user city was updated
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


class TestFetchMessagesCDPDirect(unittest.TestCase):
    """Tests for the --cdp direct scraping flow (Mode 1)."""

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

    @patch('tools.fetch_fb_messages._scrape_inbox')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_cdp_direct_calls_scrape_inbox(self, mock_sync_playwright, mock_scrape):
        """CDP direct mode connects via CDP and calls _scrape_inbox."""
        self._patch_sqlite()
        mock_scrape.return_value = {"new_threads": 3, "new_messages": 10, "skipped_threads": 0}

        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_p.chromium.connect_over_cdp.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.contexts = [mock_context]
        mock_page = MagicMock()
        mock_context.pages = [mock_page]

        result = fetch_messages("123", "test_cred", time_range="7d",
                                force_refresh=True, use_cdp=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "cdp_direct")
        mock_scrape.assert_called_once()
        mock_browser.close.assert_not_called()

    @patch('tools.fetch_fb_messages._scrape_inbox')
    @patch('tools.fetch_fb_messages.sync_playwright')
    def test_cdp_direct_cache_hit(self, mock_sync_playwright, mock_scrape):
        """CDP direct mode also respects the cache."""
        self._patch_sqlite()
        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        conn = sqlite3.connect(tmp_db)
        setup_database(conn)
        conn.execute(
            "INSERT INTO fetch_log (page_id, fetched_at, threads_found, messages_found) VALUES (?, ?, 5, 20)",
            ("123", datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

        result = fetch_messages("123", "test_cred", force_refresh=False, use_cdp=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "cache_hit")
        mock_scrape.assert_not_called()


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
        
        mock_thread_data = [{
            "index": 0,
            "name": thread_text.split('\n')[0].strip(),
            "text": thread_text,
            "lines": [l.strip() for l in thread_text.split('\n') if l.strip()] + ["Today"]
        }]
        
        mock_mouse = MagicMock()
        mock_page.mouse = mock_mouse
        
        call_count = {"collect": 0}
        
        def evaluate_side_effect(script, args=None):
            if isinstance(args, dict) and "name" in args:
                return True
            if isinstance(args, str):
                return True
            if "Message list container" in script:
                return js_messages
            elif "Xem bài viết" in script:
                return ad_context
            elif "ad_id" in script and "innerText" in script:
                return ""
            elif "_ikh" in script and "innerText" in script and "lines" in script:
                call_count["collect"] += 1
                if call_count["collect"] <= 1:
                    return mock_thread_data
                return []
            elif "scrollTop" in script and "c.scrollTop = 0" in script:
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
        patcher = patch('tools.fetch_fb_messages.sqlite3.connect', side_effect=patched_connect)
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
        
        from tools.fetch_fb_messages import propagate_city_from_ads
        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_ads"], 1)
        
        # Verify DB was updated
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
        
        from tools.fetch_fb_messages import propagate_city_from_ads
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
        
        from tools.fetch_fb_messages import propagate_city_from_ads
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
        
        from tools.fetch_fb_messages import propagate_city_from_ads
        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_ads"], 2)
        
        # Verify both users updated
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
        
        from tools.fetch_fb_messages import propagate_city_from_ads
        result = propagate_city_from_ads("page1")
        self.assertTrue(result["success"])
        self.assertEqual(result["from_messages"], 1)
        
        # Verify city detected from message
        conn2 = orig_connect(tmp_db)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT city FROM users WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["city"], "TP. Hồ Chí Minh")
        conn2.close()


if __name__ == '__main__':
    unittest.main()
