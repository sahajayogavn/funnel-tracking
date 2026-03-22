import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.persistence.l4_sqlite_store import setup_comment_database
from tools.fetch_comments import fetch_comments
from tools.fb_browser_bootstrap import AuthorizedSession


class TestFetchCommentsCDP(unittest.TestCase):
    """Tests for the CDP credential capture flow."""

    @patch('tools.l5_fetch_comments.attach_to_authorized_session')
    @patch('tools.l5_fetch_comments.os.path.exists')
    @patch('tools.l5_fetch_comments.sync_playwright')
    def test_fetch_comments_cdp_flow(self, mock_sync_playwright, mock_exists, mock_attach):
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
            inbox_url="https://business.facebook.com/latest/inbox/facebook?asset_id=123&thread_type=FB_PAGE_POST",
            created_tab=True,
        )

        result = fetch_comments("123", "test_cred")

        self.assertEqual(result["success"], True)
        self.assertEqual(result["method"], "cdp_capture")
        mock_attach.assert_called_once()


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

        patcher = patch('fb_pipeline.persistence.l4_sqlite_store.sqlite3.connect', side_effect=patched_connect)
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

        mock_post_data = [{
            "domIndex": 0,
            "name": post_text.split('\n')[0].strip(),
            "text": post_text,
            "lines": [l.strip() for l in post_text.split('\n') if l.strip()] + ["Today"]
        }]

        mock_mouse = MagicMock()
        mock_page.mouse = mock_mouse

        call_count = {"collect": 0}

        def evaluate_side_effect(script, args=None):
            if "._5_n1" in script and "innerText" in script and "lines" in script:
                call_count["collect"] += 1
                if call_count["collect"] <= 1:
                    return mock_post_data
                return []
            elif "role" in script and "article" in script:
                return js_comments
            return ""

        mock_page.evaluate.side_effect = evaluate_side_effect

        return mock_p, mock_page

    @patch('tools.l5_fetch_comments.os.path.exists')
    @patch('tools.l5_fetch_comments.sync_playwright')
    def test_headless_extracts_comments(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        mock_p, _ = self._setup_mock_playwright(mock_sync_playwright, mock_exists)
        result = fetch_comments("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "headless_fetch")
        self.assertEqual(result["data"]["stats"]["new_posts"], 1)
        self.assertEqual(result["data"]["stats"]["new_comments"], 2)

    @patch('tools.l5_fetch_comments.os.path.exists')
    @patch('tools.l5_fetch_comments.sync_playwright')
    def test_headless_no_comments(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_comments=[])
        result = fetch_comments("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_comments"], 0)

    @patch('tools.l5_fetch_comments.os.path.exists')
    @patch('tools.l5_fetch_comments.sync_playwright')
    def test_headless_with_phone_in_comment(self, mock_sync_playwright, mock_exists):
        self._patch_sqlite()
        comments = [
            {"commenter_name": "Test User", "comment_text": "SĐT: 0935539464", "timestamp": "1d", "profile_url": "", "fb_user_id": "", "is_reply": False}
        ]
        self._setup_mock_playwright(mock_sync_playwright, mock_exists, js_comments=comments)
        result = fetch_comments("123", "test_cred", force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["new_comments"], 1)

    @patch('tools.l5_fetch_comments.os.path.exists')
    @patch('tools.l5_fetch_comments.sync_playwright')
    def test_cache_hit_skips_browser(self, mock_sync_playwright, mock_exists):
        """When cache is fresh and --refresh is NOT set, browser should NOT launch."""
        self._patch_sqlite()
        mock_exists.return_value = True
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
        mock_sync_playwright.return_value.__enter__.return_value.chromium.launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
