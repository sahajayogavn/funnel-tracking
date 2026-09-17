"""Unit tests for the Phase 2 inbox parallel-fetch locate ladder:
L0 direct-URL locate/verify matrix, L0->L1 ladder fall-through, the L1
progressive-scroll overshoot stop, and psid-hint resolution.

# code:test-inbox-parallel-fetch-001:locator
"""
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.browser.inbox.thread_locator import (
    LocateResult,
    locate_thread,
    locate_thread_direct,
    locate_thread_in_sidebar,
    normalize_preview_for_match,
)
from fb_pipeline.contracts.l1_inbox import ThreadRecord
from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask
from fb_pipeline.persistence.l4_sqlite_store import resolve_psid_hint, setup_database


PAGE_ID = "1548373332058326"


class _Logger:
    def info(self, _msg):
        pass

    def warning(self, _msg):
        pass

    def error(self, _msg):
        pass


class _Mouse:
    def move(self, *_args):
        pass

    def wheel(self, *_args):
        pass


def _make_task(name="User A", psid="999000111", preview_text="Hi there", psid_hint=""):
    record = ThreadRecord(
        page_id=PAGE_ID,
        thread_id=f"{PAGE_ID}_abc123",
        thread_name=name,
        preview_text=preview_text,
        thread_lines=[name, preview_text],
        dom_index=0,
        sidebar_time_text="Today",
        sidebar_identity_key=f"{name}||{preview_text}||Today",
        selected_item_id=psid,
        fb_url="",
    )
    return ThreadTask(ordinal=0, record=record, absolute_top=0, psid_hint=psid_hint, is_new=False)


class _DirectPage:
    """Fake page for locate_thread_direct: records goto() calls and lets the
    test control what the post-navigation URL and header text look like."""

    def __init__(self, goto_url_override=None, header_text="User A"):
        self.url = f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE_ID}"
        self.goto_calls = []
        self.goto_url_override = goto_url_override
        self.header_text = header_text
        self.mouse = _Mouse()

    def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)
        self.url = self.goto_url_override if self.goto_url_override is not None else url

    def wait_for_selector(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, *_args, **_kwargs):
        return self.header_text


class TestLocateThreadDirectMatrix(unittest.TestCase):
    """(a) L0 direct-URL accept/reject matrix."""

    def test_url_name_and_preview_all_ok_accepts(self):
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        page = _DirectPage(header_text="User A")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Hi there", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertTrue(result.clicked)
        self.assertEqual(result.method, "direct_url")
        self.assertEqual(len(page.goto_calls), 1)
        self.assertIn("selected_item_id=999000111", page.goto_calls[0])

    def test_generic_inbox_header_is_neutral_and_accepts_on_url_and_preview(self):
        # Retrospective [2026-09-16]: fresh navigation renders h1 "Inbox".
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        page = _DirectPage(header_text="Inbox")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Hi there", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertTrue(result.clicked)
        self.assertEqual(result.method, "direct_url")

    def test_generic_inbox_header_still_rejects_on_preview_mismatch(self):
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        page = _DirectPage(header_text="Inbox")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Completely different", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertFalse(result.clicked)

    def test_url_missing_psid_after_navigation_rejects(self):
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        # Simulate Facebook rejecting the direct URL and redirecting away
        # from the requested selected_item_id.
        page = _DirectPage(
            goto_url_override=f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE_ID}",
            header_text="User A",
        )
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Hi there", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertFalse(result.clicked)
        self.assertEqual(result.method, "direct_url")

    def test_header_name_mismatch_rejects(self):
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        page = _DirectPage(header_text="Someone Else Entirely")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Hi there", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertFalse(result.clicked)
        self.assertEqual(result.method, "direct_url")

    def test_preview_mismatch_rejects(self):
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        page = _DirectPage(header_text="User A")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Completely unrelated content", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertFalse(result.clicked)
        self.assertEqual(result.method, "direct_url")

    def test_no_messages_extracted_rejects(self):
        task = _make_task(name="User A", psid="999000111", preview_text="Hi there")
        page = _DirectPage(header_text="User A")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertFalse(result.clicked)

    def test_no_psid_returns_clicked_false_without_calling_goto(self):
        task = _make_task(name="User A", psid="", preview_text="Hi there", psid_hint="")
        page = _DirectPage(header_text="User A")
        logger = _Logger()

        result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertFalse(result.clicked)
        self.assertEqual(result.method, "direct_url")
        self.assertEqual(page.goto_calls, [])

    def test_psid_hint_is_used_when_selected_item_id_is_empty(self):
        task = _make_task(name="User A", psid="", preview_text="Hi there", psid_hint="555222333")
        page = _DirectPage(header_text="User A")
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_thread_messages",
            return_value=[{"sender": "Customer", "text": "Hi there", "timestamp": "Today"}],
        ):
            result = locate_thread_direct(page, PAGE_ID, task, logger)

        self.assertTrue(result.clicked)
        self.assertIn("selected_item_id=555222333", page.goto_calls[0])


class TestNormalizePreviewForMatch(unittest.TestCase):
    def test_strips_ad_source_prefix_and_you_prefix(self):
        raw = "---[AD SOURCE]: Ad 123---You: Hello There!"
        self.assertEqual(normalize_preview_for_match(raw), "hellothere")

    def test_empty_string_normalises_to_empty(self):
        self.assertEqual(normalize_preview_for_match(""), "")


class TestLocateThreadLadderFallThrough(unittest.TestCase):
    """(b) locate_thread falls through L0 -> L1 and reports L1's method."""

    def test_ladder_reports_l1_method_when_l0_fails(self):
        task = _make_task()
        page = _DirectPage()
        logger = _Logger()

        l1_result = LocateResult(
            clicked=True, method="sidebar_identity", attempts=7,
            prev_fb_url="", pre_click_fingerprint="",
        )
        l0_result = LocateResult(
            clicked=False, method="direct_url", attempts=1,
            prev_fb_url="", pre_click_fingerprint="",
        )

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.locate_thread_direct",
            return_value=l0_result,
        ) as mock_l0, patch(
            "fb_pipeline.browser.inbox.thread_locator.locate_thread_in_sidebar",
            return_value=l1_result,
        ) as mock_l1:
            result = locate_thread(page, PAGE_ID, task, logger)

        mock_l0.assert_called_once()
        mock_l1.assert_called_once()
        self.assertTrue(result.clicked)
        self.assertEqual(result.method, "sidebar_identity")

    def test_ladder_returns_l0_result_without_calling_l1_when_l0_succeeds(self):
        task = _make_task()
        page = _DirectPage()
        logger = _Logger()

        l0_result = LocateResult(
            clicked=True, method="direct_url", attempts=1,
            prev_fb_url="", pre_click_fingerprint="",
        )

        with patch(
            "fb_pipeline.browser.inbox.thread_locator.locate_thread_direct",
            return_value=l0_result,
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.locate_thread_in_sidebar",
        ) as mock_l1:
            result = locate_thread(page, PAGE_ID, task, logger)

        mock_l1.assert_not_called()
        self.assertTrue(result.clicked)
        self.assertEqual(result.method, "direct_url")


class _SidebarPage:
    """Fake page for locate_thread_in_sidebar: the identity-match JS never
    finds the card (clicked stays False), so the loop reaches the
    progressive-scroll branch after 3 quick attempts."""

    def __init__(self):
        self.url = f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE_ID}"
        self.mouse = _Mouse()

    def evaluate(self, *_args, **_kwargs):
        # Used for: prev_fb_url parse (not evaluate), pre_click_fingerprint,
        # the identity-match click JS, and _thread_panel_loading_count.
        # None of these need to succeed for this test -- return falsy so the
        # click loop never reports success and the loading-count check sees 0.
        return False

    def wait_for_timeout(self, _ms):
        pass


class TestSidebarOvershootStop(unittest.TestCase):
    """(c) the progressive sidebar walk stops on overshoot."""

    def test_overshoot_returns_clicked_false_with_sidebar_identity_method(self):
        task = _make_task(name="User A", psid="", preview_text="Hi there", psid_hint="")
        # Target thread is "Today" (days_ago == 0 via parse_sidebar_time_token).
        task.record.sidebar_time_text = "Today"
        page = _SidebarPage()
        logger = _Logger()

        old_token = (datetime.now() - timedelta(days=5)).strftime("%b %d, %Y, %I:%M %p")

        with patch(
            "fb_pipeline.browser.inbox.thread_locator._thread_panel_loading_count",
            return_value=0,
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.sidebar_loading_snapshot",
            return_value={"count": 3, "fingerprint": "same"},
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.scroll_sidebar_and_wait",
            return_value={"count": 3, "fingerprint": "same", "elapsed_ms": 10},
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_visible_threads",
            return_value=[{"sidebarTimeText": old_token}],
        ):
            result = locate_thread_in_sidebar(page, task, logger)

        self.assertFalse(result.clicked)
        self.assertEqual(result.method, "sidebar_identity")

    def test_unknown_time_tokens_never_trigger_overshoot(self):
        """A guarded parse failure (unrecognised token) must not raise or
        stop the loop early via the overshoot path; it just keeps retrying
        until the ordinary stagnant-retry stop takes over."""
        task = _make_task(name="User A", psid="", preview_text="Hi there", psid_hint="")
        task.record.sidebar_time_text = "not a real time token !!"
        page = _SidebarPage()
        logger = _Logger()

        with patch(
            "fb_pipeline.browser.inbox.thread_locator._thread_panel_loading_count",
            return_value=0,
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.sidebar_loading_snapshot",
            return_value={"count": 3, "fingerprint": "same"},
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.scroll_sidebar_and_wait",
            return_value={"count": 3, "fingerprint": "same", "elapsed_ms": 10},
        ), patch(
            "fb_pipeline.browser.inbox.thread_locator.extract_visible_threads",
            return_value=[{"sidebarTimeText": "also not a real time token !!"}],
        ):
            # Should terminate via the stagnant-retry stop (same fingerprint
            # every round), not raise, and not falsely report an overshoot
            # before the stagnant threshold is reached.
            result = locate_thread_in_sidebar(page, task, logger)

        self.assertFalse(result.clicked)
        self.assertEqual(result.method, "sidebar_identity")


class TestResolvePsidHint(unittest.TestCase):
    """(d) resolve_psid_hint uniqueness rule."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_zero_rows_returns_empty(self):
        self.assertEqual(resolve_psid_hint(self.conn, PAGE_ID, "Nobody"), "")

    def test_two_different_fb_urls_returns_empty(self):
        self.conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)",
            (f"{PAGE_ID}_aaa", "User A", "111111111111111"),
        )
        self.conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)",
            (f"{PAGE_ID}_bbb", "User A", "222222222222222"),
        )
        self.conn.commit()
        self.assertEqual(resolve_psid_hint(self.conn, PAGE_ID, "User A"), "")

    def test_single_row_returns_the_fb_url(self):
        self.conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)",
            (f"{PAGE_ID}_ccc", "User B", "333333333333333"),
        )
        self.conn.commit()
        self.assertEqual(resolve_psid_hint(self.conn, PAGE_ID, "User B"), "333333333333333")

    def test_only_matches_rows_for_the_requested_page_id(self):
        self.conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)",
            ("OTHERPAGE_ddd", "User C", "444444444444444"),
        )
        self.conn.commit()
        self.assertEqual(resolve_psid_hint(self.conn, PAGE_ID, "User C"), "")


if __name__ == '__main__':
    unittest.main()


# code:test-inbox-parallel-fetch-001:locator
class TestPreviewProbeMatching(unittest.TestCase):
    """Retrospective [2026-09-16]: quoted replies and sidebar label noise."""

    def test_quoted_reply_with_label_noise_matches(self):
        from fb_pipeline.browser.inbox.thread_locator import _preview_matches_any
        bubble = "họcphíquotedreplylinklớphọcmiễnphícônhécũngkhôngphảimuatàiliệuhaymuagìđâuạ"
        preview = "lớphọcmiễnphícônhécũngkhôngphảimuatàiliệuhaymuagìđâuạtueintakeadid"
        self.assertTrue(_preview_matches_any(preview, [bubble]))

    def test_unrelated_text_does_not_match(self):
        from fb_pipeline.browser.inbox.thread_locator import _preview_matches_any
        self.assertFalse(_preview_matches_any("xinchàotôimuốnđăngkýkhóathiền", ["cảmơnbạnđãliênhệvớichúngtôi"]))

    def test_short_preview_requires_prefix_match(self):
        from fb_pipeline.browser.inbox.thread_locator import _preview_matches_any
        self.assertTrue(_preview_matches_any("ok", ["okcảmơn"]))
        self.assertFalse(_preview_matches_any("ok", ["cảmơnok"]))
