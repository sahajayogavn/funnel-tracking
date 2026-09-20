import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fb_pipeline.session.l2_facebook_block_gate import (
    FacebookBlockGate,
    FacebookTemporaryBlockError,
    detect_facebook_fetch_safety_issue,
    detect_facebook_temporary_block,
)


class _Body:
    def __init__(self, text):
        self.text = text

    def inner_text(self, timeout):
        return self.text


class _Page:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        assert selector == "body"
        return _Body(self.text)


class TestFacebookBlockGate(unittest.TestCase):
    def test_recognizes_meta_going_too_fast_warning(self):
        page = _Page("You’re Temporarily Blocked. It looks like you were misusing this feature by going too fast.")
        self.assertIn("Temporarily Blocked", detect_facebook_temporary_block(page))

    def test_normal_inbox_does_not_trip(self):
        self.assertEqual(detect_facebook_temporary_block(_Page("Inbox\nAlice\nHello")), "")

    def test_detects_login_checkpoint_access_and_meta_error_screens(self):
        cases = {
            "login_required": "Log in to Facebook to continue",
            "security_checkpoint": "Confirm your identity to continue this security check",
            "access_lost": "You don't have permission to access this content",
            "meta_error_page": "Something went wrong. Please try again later.",
        }
        for expected_reason, text in cases.items():
            with self.subTest(expected_reason=expected_reason):
                reason, evidence = detect_facebook_fetch_safety_issue(_Page(text))
                self.assertEqual(reason, expected_reason)
                self.assertEqual(evidence, text)

    def test_detects_visible_screen_that_has_no_inbox_shell(self):
        text = "Facebook profile preview " + ("public content " * 10)
        self.assertEqual(detect_facebook_fetch_safety_issue(_Page(text))[0], "visible_non_inbox_screen")

    def test_short_loading_screen_is_not_treated_as_a_block(self):
        self.assertEqual(detect_facebook_fetch_safety_issue(_Page("Loading…")), ("", ""))

    def test_gate_latches_first_block_and_remains_stopped(self):
        gate = FacebookBlockGate()
        with self.assertRaises(FacebookTemporaryBlockError):
            gate.trip_if_present(_Page("You've been temporarily blocked from using it."))
        self.assertTrue(gate.tripped)
        self.assertIn("stopped all fetching", gate.message)


class TestFetchBlockNotification(unittest.TestCase):
    @patch("tools.l5_telegram_hitl.send_telegram_notification")
    def test_notification_text_identifies_fetch_stop(self, notify):
        from tools.l5_fetch_fb_messages import _notify_facebook_block

        _notify_facebook_block("1548373332058326", "blocked")

        notify.assert_called_once()
        text = notify.call_args.args[0]
        self.assertIn("fetch STOPPED", text)
        self.assertIn("1548373332058326", text)
        self.assertIn("unsafe/non-Inbox", text)

    @patch("tools.l5_fetch_fb_messages.run_parallel_fetch")
    @patch("tools.l5_fetch_fb_messages.attach_to_authorized_session")
    @patch("tools.l5_fetch_fb_messages.get_db_connection")
    @patch("tools.l5_fetch_fb_messages.sync_playwright")
    @patch("tools.l5_telegram_hitl.send_telegram_notification")
    def test_blocked_startup_stops_before_fetch_or_worker_tabs(self, notify, playwright, db, attach, parallel):
        from tools.l5_fetch_fb_messages import _fetch_messages_impl

        page = _Page("You’re Temporarily Blocked. You’ve been temporarily blocked from using it.")
        session = MagicMock()
        session.page = page
        session.context.pages = [page]
        session.selected_existing_tab = True
        attach.return_value = session
        playwright.return_value.__enter__.return_value = MagicMock()

        result = _fetch_messages_impl("1548373332058326", "default", use_cdp=True, workers=4)

        self.assertEqual(result["error"], "facebook_temporarily_blocked")
        parallel.assert_not_called()
        notify.assert_called_once()
        session.close_page.assert_called_once()
