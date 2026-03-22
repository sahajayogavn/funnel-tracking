import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_database
from tools.fetch_fb_messages import _scrape_inbox, fetch_messages
from tools.fb_browser_bootstrap import AuthorizedSession


class TestFetchMessagesCDP(unittest.TestCase):
    """Tests for the CDP credential capture flow."""

    @patch('tools.l5_fetch_fb_messages.attach_to_authorized_session')
    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_fetch_messages_cdp_flow(self, mock_sync_playwright, mock_exists, mock_attach):
        mock_exists.return_value = False

        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.content.return_value = "<html>Mock DOM</html>"
        mock_attach.return_value = AuthorizedSession(
            browser=mock_browser,
            context=mock_context,
            page=mock_page,
            cdp_url="http://127.0.0.1:9222",
            page_id="123",
            inbox_url="https://business.facebook.com/latest/inbox/all?asset_id=123",
            created_tab=True,
        )

        result = fetch_messages("123", "test_cred")

        self.assertEqual(result["success"], True)
        self.assertEqual(result["method"], "cdp_capture")
        mock_attach.assert_called_once()


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

        patcher = patch('fb_pipeline.persistence.l4_sqlite_store.sqlite3.connect', side_effect=patched_connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch('tools.l5_fetch_fb_messages._scrape_inbox')
    @patch('tools.l5_fetch_fb_messages.attach_to_authorized_session')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_cdp_direct_calls_scrape_inbox(self, mock_sync_playwright, mock_attach, mock_scrape):
        """CDP direct mode connects via CDP and calls _scrape_inbox."""
        self._patch_sqlite()
        mock_scrape.return_value = {"new_threads": 3, "new_messages": 10, "skipped_threads": 0}

        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_attach.return_value = AuthorizedSession(
            browser=mock_browser,
            context=mock_context,
            page=mock_page,
            cdp_url="http://127.0.0.1:9222",
            page_id="123",
            inbox_url="https://business.facebook.com/latest/inbox/all?asset_id=123",
            created_tab=True,
        )
        mock_context.pages = [mock_page]

        result = fetch_messages("123", "test_cred", time_range="7d", force_refresh=True, use_cdp=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "cdp_direct")
        mock_scrape.assert_called_once()
        mock_browser.close.assert_not_called()
        mock_attach.assert_called_once()

    @patch('tools.l5_fetch_fb_messages._scrape_inbox')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
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

        patcher = patch('fb_pipeline.persistence.l4_sqlite_store.sqlite3.connect', side_effect=patched_connect)
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
        url_state = {"url": "https://business.facebook.com/latest/inbox/all?asset_id=123"}
        type(mock_page).url = property(lambda self: url_state["url"])
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

        call_count = {"collect": 0, "fingerprint": 0, "scroll_info": 0}

        def evaluate_side_effect(script, args=None):
            if isinstance(args, dict) and "name" in args:
                return True
            if isinstance(args, str):
                return True
            if "innerText" in script and "substring(0, 200)" in script:
                call_count["fingerprint"] += 1
                if call_count["fingerprint"] <= 1:
                    return ""
                return "New thread messages here"
            if "scrollHeight" in script and "scrollableTag" in script:
                return {"count": len(js_messages), "scrollHeight": 500, "scrollTop": 0, "scrollableTag": "DIV"}
            if "scrollTop" in script and "dispatchEvent" in script:
                return None
            if "Message list container" in script and "x1y1aw1k" in script:
                return js_messages
            elif "Xem bài viết" in script:
                return ad_context
            elif "ad_id" in script and "innerText" in script:
                return ""
            elif "_5_n1" in script and "innerText" in script and "lines" in script:
                call_count["collect"] += 1
                if call_count["collect"] <= 1:
                    return mock_thread_data
                return []
            return ""

        mock_page.evaluate.side_effect = evaluate_side_effect

        original_click = mock_thread.click

        def click_side_effect(*args, **kwargs):
            url_state["url"] = "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=9876"
            return original_click(*args, **kwargs)

        mock_thread.click = click_side_effect

        return mock_p, mock_page

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_extracts_structured_messages(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        mock_p, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "headless_fetch")
        self.assertEqual(result["data"]["stats"]["new_threads"], 1)
        self.assertEqual(result["data"]["stats"]["new_messages"], 2)

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_no_messages(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=[])
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_messages"], 0)

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_with_ad_context(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        ad_text = "Thiền miễn phí tại Hà Nội"
        messages = [{"sender": "Customer", "text": "0912345678", "timestamp": "Sat 7:19 PM"}]
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=messages, ad_context=ad_text)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_messages"], 1)

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
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


if __name__ == '__main__':
    unittest.main()
