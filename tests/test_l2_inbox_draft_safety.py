import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.browser.l2_actions import send_reply_via_cdp


class TestL2InboxDraftSafety(unittest.TestCase):
    def _build_page(self):
        page = MagicMock()
        reply_box = MagicMock()
        page.wait_for_selector.return_value = reply_box
        return page, reply_box

    def test_send_reply_types_draft_without_pressing_enter_in_dry_run(self):
        page, reply_box = self._build_page()

        result = send_reply_via_cdp(page, "Hello seeker", dry_run=True)

        self.assertTrue(result)
        page.wait_for_selector.assert_called_once()
        reply_box.click.assert_called_once_with()
        page.keyboard.type.assert_called_once_with("Hello seeker", delay=5)
        page.keyboard.press.assert_not_called()

    def test_send_reply_types_draft_without_pressing_enter_when_live_requested(self):
        page, _ = self._build_page()

        result = send_reply_via_cdp(page, "Still only a draft", dry_run=False)

        self.assertTrue(result)
        page.keyboard.type.assert_called_once_with("Still only a draft", delay=5)
        page.keyboard.press.assert_not_called()


if __name__ == "__main__":
    unittest.main()
