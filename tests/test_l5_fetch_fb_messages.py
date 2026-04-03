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
                {"sender": "Customer", "text": "Lớp học có mất phí không?", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Sat 7:19 PM"},
                {"sender": "Page", "text": "Dạ hoàn toàn miễn phí ạ", "htmlStr": "<div>...</div>", "bg": "rgb(0, 132, 255)", "timestamp": "Sat 7:19 PM"},
            ]

        mock_thread_data = [{
            "domIndex": 0,
            "name": thread_text.split('\n')[0].strip(),
            "text": thread_text,
            "lines": [l.strip() for l in thread_text.split('\n') if l.strip()] + ["Today"],
            "previewText": "This is a preview.",
            "sidebarTimeText": "Today",
            "sidebarTimeKind": "today",
            "sidebarIdentityKey": "thread-1",
            "selectedItemId": "9876",
        }]

        mock_mouse = MagicMock()
        mock_page.mouse = mock_mouse

        call_count = {"collect": 0, "fingerprint": 0, "scroll_info": 0, "sidebar_snapshot": 0}

        def evaluate_side_effect(script, args=None):
            if "scrollIntoView" in script and "c.click()" in script:
                return True
            if isinstance(args, dict) and args.get("threadSelector"):
                if "pickTimeToken" in script:
                    call_count["collect"] += 1
                    if call_count["collect"] <= 1:
                        return mock_thread_data
                    return []
                call_count["sidebar_snapshot"] += 1
                if call_count["sidebar_snapshot"] == 1:
                    return {"count": 1, "loadingCount": 1, "fingerprint": "fp-a"}
                return {"count": 1, "loadingCount": 0, "fingerprint": "fp-a"}
            if isinstance(args, dict) and "name" in args:
                return True
            if isinstance(args, str):
                return True
            if "return (r.innerText || \"\").substring(0, 200)" in script or "return (main.innerText || \"\").substring(0, 500)" in script or "let main_text = main.innerText || \"\";" in script:
                if "querySelector('div[role=\"main\"]')" in script:
                    return "Test User\nThis is a mock header."
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
            return ""

        mock_page.evaluate.side_effect = evaluate_side_effect

        original_click = mock_thread.click

        def click_side_effect(*args, **kwargs):
            url_state["url"] = "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=9876"
            return original_click(*args, **kwargs)

        mock_thread.click = click_side_effect

        return mock_p, mock_page, call_count

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_extracts_structured_messages(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        mock_p, _, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "headless_fetch")
        self.assertEqual(result["data"]["stats"]["new_threads"], 1)
        self.assertEqual(result["data"]["stats"]["new_messages"], 2)

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_no_messages(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        _, _, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=[])
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_messages"], 0)

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_deduplicates_overlapping_messages(self, mock_sync_playwright, mock_exists):
        """Test the monotonic sequence deduplication suffix-matching algorithm."""
        self._patch_sqlite()
        messages_run_1 = [
            {"sender": "Customer", "text": "Hello", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "1:00 PM"},
            {"sender": "Page", "text": "Hi", "htmlStr": "<div>...</div>", "bg": "rgb(0, 132, 255)", "timestamp": "1:01 PM"},
        ]
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=messages_run_1)
        result1 = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result1["success"])
        self.assertEqual(result1["data"]["stats"]["new_messages"], 2)

        # Simulating run 2 where an extra message is loaded from the top (user scrolled higher in DOM?) 
        # Actually our algorithm suffix matches so let's simulate a NEW message arriving at the bottom!
        messages_run_2 = [
            {"sender": "Customer", "text": "Hello", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "1:00 PM"},
            {"sender": "Page", "text": "Hi", "htmlStr": "<div>...</div>", "bg": "rgb(0, 132, 255)", "timestamp": "1:01 PM"},
            {"sender": "Customer", "text": "Pushed a new message", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "1:05 PM"},
        ]
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, thread_text="Test User\nPushed a new message", js_messages=messages_run_2)
        result2 = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result2["success"])
        # Only the net-new message should be imported!
        self.assertEqual(result2["data"]["stats"]["new_messages"], 1)

        tmp_db = os.path.join(self._tmp_dir, "frankensqlite.db")
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT content, seq FROM messages ORDER BY seq ASC")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], "Hello")
        self.assertEqual(rows[0][1], 0)
        self.assertEqual(rows[2][0], "Pushed a new message")
        self.assertEqual(rows[2][1], 2)
        conn.close()

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_with_ad_context(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        ad_text = "Thiền miễn phí tại Hà Nội"
        messages = [{"sender": "Customer", "text": "0912345678", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Sat 7:19 PM"}]
        _, _, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_messages=messages, ad_context=ad_text)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_messages"], 1)

    @patch('tools.l5_fetch_fb_messages.os.path.exists')
    @patch('tools.l5_fetch_fb_messages.sync_playwright')
    def test_headless_uses_single_sidebar_scroll_and_waits_for_load(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        _, mock_page, call_count = self._setup_mock_playwright(mock_sync_playwright, mock_exists)
        result = fetch_messages("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        sidebar_moves = [call for call in mock_page.mouse.move.call_args_list if call.args == (200, 400)]
        self.assertGreaterEqual(len(sidebar_moves), 1)
        evaluate_calls = [call.args[0] for call in mock_page.evaluate.call_args_list if call.args and isinstance(call.args[0], str)]
        self.assertTrue(any("scrollIntoView" in script for script in evaluate_calls))
        self.assertGreaterEqual(call_count["sidebar_snapshot"], 2)
        wait_calls = [call.args[0] for call in mock_page.wait_for_timeout.call_args_list if call.args]
        self.assertIn(1000, wait_calls)


if __name__ == '__main__':
    unittest.main()
