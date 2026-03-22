import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools.fb_browser_bootstrap import (
    attach_to_authorized_session,
    sanitize_storage_state_file,
    FacebookAuthorizationError,
    PageAccessError,
    CDPConnectionError,
)


class TestBrowserBootstrap(unittest.TestCase):
    def test_attach_returns_authorized_session_with_new_tab(self):
        mock_playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_existing_page = MagicMock()
        mock_new_page = MagicMock()
        mock_new_page.url = "https://business.facebook.com/latest/inbox/all?asset_id=123"
        mock_new_page.content.return_value = "<html>Inbox</html>"

        mock_playwright.chromium.connect_over_cdp.return_value = mock_browser
        mock_browser.contexts = [mock_context]
        mock_context.pages = [mock_existing_page]
        mock_context.new_page.return_value = mock_new_page

        session = attach_to_authorized_session(
            mock_playwright,
            "123",
            "https://business.facebook.com/latest/inbox/all?asset_id=123",
        )

        self.assertIs(session.page, mock_new_page)
        self.assertTrue(session.created_tab)
        self.assertFalse(session.selected_existing_tab)
        mock_playwright.chromium.connect_over_cdp.assert_called_once()
        mock_new_page.goto.assert_called_once()

    def test_attach_reuses_existing_tab_when_requested(self):
        mock_playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.url = "https://business.facebook.com/latest/inbox/all?asset_id=123"
        mock_page.content.return_value = "<html>Inbox</html>"

        mock_playwright.chromium.connect_over_cdp.return_value = mock_browser
        mock_browser.contexts = [mock_context]
        mock_context.pages = [mock_page]

        session = attach_to_authorized_session(
            mock_playwright,
            "123",
            "https://business.facebook.com/latest/inbox/all?asset_id=123",
            prefer_new_tab=False,
        )

        self.assertIs(session.page, mock_page)
        self.assertFalse(session.created_tab)
        self.assertTrue(session.selected_existing_tab)
        mock_context.new_page.assert_not_called()

    def test_attach_fails_when_login_page_detected(self):
        mock_playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.url = "https://www.facebook.com/login"

        mock_playwright.chromium.connect_over_cdp.return_value = mock_browser
        mock_browser.contexts = [mock_context]
        mock_context.pages = [mock_page]

        with self.assertRaises(FacebookAuthorizationError):
            attach_to_authorized_session(
                mock_playwright,
                "123",
                "https://business.facebook.com/latest/inbox/all?asset_id=123",
                prefer_new_tab=False,
            )

    def test_attach_fails_when_wrong_asset_id_opened(self):
        mock_playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.url = "https://business.facebook.com/latest/inbox/all?asset_id=999"
        mock_page.content.return_value = "<html>Inbox</html>"

        mock_playwright.chromium.connect_over_cdp.return_value = mock_browser
        mock_browser.contexts = [mock_context]
        mock_context.pages = [mock_page]

        with self.assertRaises(PageAccessError):
            attach_to_authorized_session(
                mock_playwright,
                "123",
                "https://business.facebook.com/latest/inbox/all?asset_id=123",
                prefer_new_tab=False,
            )

    def test_attach_fails_when_cdp_unavailable(self):
        mock_playwright = MagicMock()
        mock_playwright.chromium.connect_over_cdp.side_effect = RuntimeError("boom")

        with self.assertRaises(CDPConnectionError):
            attach_to_authorized_session(
                mock_playwright,
                "123",
                "https://business.facebook.com/latest/inbox/all?asset_id=123",
            )

    def test_sanitize_storage_state_file_keeps_only_fb_domains(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                f.write(
                    '{"cookies": ['
                    '{"domain": ".facebook.com", "name": "a", "expires": -1},'
                    '{"domain": ".messenger.com", "name": "b", "expires": 123},'
                    '{"domain": ".example.com", "name": "c", "expires": 456}'
                    ']}'
                )

            sanitize_storage_state_file(path)

            with open(path, "r") as f:
                content = f.read()

            self.assertIn('.facebook.com', content)
            self.assertIn('.messenger.com', content)
            self.assertNotIn('.example.com', content)
            self.assertNotIn('"expires": -1', content)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
