"""Stage 1 "already fetched?" decision matrix.

# code:test-validation-001:stage1-sync-skip
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from fb_pipeline.browser.inbox.thread_list_parser import extract_visible_threads, parse_sidebar_time_token
from fb_pipeline.browser.l3_inbox import discover_threads
from fb_pipeline.inbox.l3_pipeline import canonical_thread_id, normalize_preview_text
from fb_pipeline.inbox.l3_sync_decision import FETCH, SKIP, decide_by_fetched_marker, is_out_of_order
from fb_pipeline.persistence.l4_sqlite_store import setup_database

NOW = datetime(2026, 9, 21, 10, 0, 0)  # Monday


def _decide(token_now, fetched_token, fetched_at, *, preview_now="xinchao", preview_db="xinchao",
            source="utime", now=NOW, **kw):
    return decide_by_fetched_marker(
        token_now=token_now, source_now=source, preview_norm_now=preview_now,
        fetched_token=fetched_token, fetched_preview_norm=preview_db, fetched_at=fetched_at,
        now=now, **kw,
    )


class TestDecisionMatrix(unittest.TestCase):
    # --- undecided -> legacy preview fallback
    def test_no_marker_is_undecided(self):
        self.assertIsNone(_decide("Aug 6", None, None).action)

    def test_scan_source_is_undecided(self):
        d = _decide("Aug 6", "Aug 6", "2026-09-16 17:20:07", source="scan")
        self.assertIsNone(d.action)
        self.assertEqual(d.reason, "time_source_ambiguous")

    def test_out_of_order_is_undecided(self):
        d = _decide("Aug 6", "Aug 6", "2026-09-16 17:20:07", out_of_order=True)
        self.assertIsNone(d.action)

    def test_unknown_token_is_undecided(self):
        self.assertIsNone(_decide("!!??", "Aug 6", "2026-09-16 17:20:07").action)

    # --- identical tokens
    def test_same_month_day_skips_regardless_of_preview(self):
        d = _decide("Aug 6", "Aug 6", "2026-09-16 17:20:07", preview_now="banner", preview_db="xinchao")
        self.assertEqual((d.action, d.reason), (SKIP, "token_match"))

    def test_same_slash_date_skips(self):
        self.assertEqual(_decide("10/3/25", "10/3/25", "2026-09-16 17:20:07").action, SKIP)

    def test_same_absolute_date_with_year_skips(self):
        self.assertEqual(_decide("May 29, 2025", "May 29, 2025", "2026-09-16 17:20:07").action, SKIP)

    def test_same_clock_today_requires_same_preview(self):
        fetched_at = "2026-09-21 09:00:00"
        self.assertEqual(_decide("8:56 AM", "8:56 AM", fetched_at).action, SKIP)
        d = _decide("8:56 AM", "8:56 AM", fetched_at, preview_now="tinmoi")
        self.assertEqual((d.action, d.reason), (FETCH, "preview_changed"))

    # --- equivalent tokens whose shape changed with the passage of time
    def test_weekday_stored_then_rendered_as_date_is_equivalent(self):
        # Stored "Tue" on Thu 2026-09-17 -> Tue 2026-09-15.  Eight days later
        # Meta renders that same message as "Sep 15".
        d = _decide("Sep 15", "Tue", "2026-09-17 12:00:00", now=datetime(2026, 9, 25, 10, 0))
        self.assertEqual(d.action, SKIP)

    def test_clock_stored_then_rendered_as_yesterday_is_equivalent(self):
        d = _decide("Yesterday 8:56 PM", "8:56 PM", "2026-09-20 21:30:00")
        self.assertEqual(d.action, SKIP)

    def test_clock_stored_then_rendered_as_weekday_is_equivalent(self):
        # "8:56 PM" stored on Sat 2026-09-19; on Mon it renders as "Sat".
        d = _decide("Sat", "8:56 PM", "2026-09-19 21:30:00")
        self.assertEqual(d.action, SKIP)

    # --- token moved forward -> fetch
    def test_newer_token_fetches(self):
        d = _decide("Sep 20", "Aug 6", "2026-09-16 17:20:07")
        self.assertEqual((d.action, d.reason), (FETCH, "token_changed"))

    def test_new_clock_today_fetches(self):
        d = _decide("9:30 AM", "8:56 AM", "2026-09-21 09:00:00")
        self.assertEqual(d.action, FETCH)

    def test_weekday_moved_fetches(self):
        # Stored "Tue" (2026-09-15); now shows "Sat" (2026-09-19).
        d = _decide("Sat", "Tue", "2026-09-17 12:00:00")
        self.assertEqual(d.action, FETCH)

    # --- exact epoch from <abbr data-utime>
    def test_same_utime_skips_without_preview_or_token_check(self):
        d = _decide("Tue", "Aug 6", "2026-09-16 17:20:07", preview_now="x", preview_db="y",
                    utime_now_ms=1789480610443, fetched_utime_ms=1789480610443)
        self.assertEqual((d.action, d.reason), (SKIP, "utime_match"))

    def test_changed_utime_fetches_even_when_token_equal(self):
        d = _decide("Tue", "Tue", "2026-09-17 12:00:00", now=datetime(2026, 9, 18, 10, 0),
                    utime_now_ms=1789480610443, fetched_utime_ms=1789400000000)
        self.assertEqual((d.action, d.reason), (FETCH, "utime_changed"))

    def test_missing_utime_on_either_side_falls_back_to_token(self):
        d = _decide("Aug 6", "Aug 6", "2026-09-16 17:20:07", source="utime", utime_now_ms=1789480610443, fetched_utime_ms=None)
        self.assertEqual((d.action, d.reason), (SKIP, "token_match"))

    # --- --refresh-older-than
    def test_stale_marker_forces_fetch(self):
        d = _decide("Aug 6", "Aug 6", "2026-09-01 00:00:00", refresh_older_than_days=7)
        self.assertEqual((d.action, d.reason), (FETCH, "marker_stale"))
        self.assertEqual(_decide("Aug 6", "Aug 6", "2026-09-01 00:00:00", refresh_older_than_days=30).action, SKIP)


class TestWeekdayParser(unittest.TestCase):
    def test_bare_weekday_resolves_to_most_recent_past_day(self):
        now = datetime(2026, 9, 21, 10, 0)  # Monday
        self.assertEqual(parse_sidebar_time_token("Tue", now)["parsed_at"], "2026-09-15")
        self.assertEqual(parse_sidebar_time_token("Tuesday", now)["days_ago"], 6)
        self.assertEqual(parse_sidebar_time_token("Sun", now)["parsed_at"], "2026-09-20")
        # Today's weekday never renders as a bare name -> previous week.
        self.assertEqual(parse_sidebar_time_token("Mon", now)["parsed_at"], "2026-09-14")


class TestSlashDateParser(unittest.TestCase):
    def test_slash_dates_are_month_first(self):
        now = datetime(2026, 9, 21, 10, 0)
        self.assertEqual(parse_sidebar_time_token("12/29/24", now)["parsed_at"], "2024-12-29")
        self.assertEqual(parse_sidebar_time_token("3/8/25", now)["parsed_at"], "2025-03-08")
        self.assertEqual(parse_sidebar_time_token("10/3/25", now)["parsed_at"], "2025-10-03")
        # A dd/mm rendering is still recovered when the first field cannot be a month.
        self.assertEqual(parse_sidebar_time_token("29/12/24", now)["parsed_at"], "2024-12-29")


class TestOutOfOrder(unittest.TestCase):
    def test_newer_than_predecessor_by_more_than_a_day_is_flagged(self):
        self.assertTrue(is_out_of_order(date(2026, 8, 6), date(2026, 9, 14)))
        self.assertFalse(is_out_of_order(date(2026, 9, 14), date(2026, 9, 13)))
        self.assertFalse(is_out_of_order(date(2026, 9, 14), date(2026, 9, 15)))  # weekday resolution slack
        self.assertFalse(is_out_of_order(None, date(2026, 9, 14)))


# ---------------------------------------------------------------------------
# Stage 1 integration with the fetched marker
# ---------------------------------------------------------------------------
PAGE = "1548373332058326"
PSID = "100052384037366"


class _Page:
    def goto(self, *a, **k): pass
    def wait_for_timeout(self, _ms): pass


class _Logger:
    def __init__(self): self.lines = []
    def info(self, m): self.lines.append(("info", m))
    def warning(self, m): self.lines.append(("warning", m))
    def error(self, m): self.lines.append(("error", m))
    def debug(self, m): self.lines.append(("debug", m))


def _card(name, preview, time_text, source="utime", utime_ms=None):
    return {
        "name": name, "text": f"{name}\n{preview}\n{time_text}", "previewText": preview,
        "sidebarTimeText": time_text, "sidebarTimeSource": source, "sidebarTimestampMs": utime_ms,
        "sidebarIdentityKey": f"{name} || {preview} || {time_text}",
        "selectedItemId": "", "fbUrl": "", "domIndex": 0, "absoluteTop": 0,
    }


class TestStage1FetchedMarker(unittest.TestCase):
    NAME = "Phạm Thịnh"

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)
        self.tid = canonical_thread_id(PAGE, PSID)
        self.conn.execute(
            "INSERT INTO threads (id, page_id, thread_name, last_synced_time, fetched_sidebar_token, "
            "fetched_sidebar_kind, fetched_preview_norm, fetched_at) VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?)",
            (self.tid, PAGE, self.NAME, "Aug 6", "month_day", normalize_preview_text("Em cảm ơn ạ"), "2026-09-16 17:20:07"),
        )
        self.conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)", (self.tid, self.NAME, PSID))
        self.conn.execute(
            "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, 'Customer', 'Em cảm ơn ạ', '', 0)",
            (self.tid,),
        )
        self.conn.commit()
        self.logger = _Logger()

    def _discover(self, cards, **kw):
        tasks = []
        with patch("fb_pipeline.browser.l3_inbox.wait_for_inbox_shell", return_value=""), \
             patch("fb_pipeline.browser.l3_inbox.wait_for_initial_threads",
                   return_value={"count": len(cards), "elapsed_ms": 1, "fingerprint": "fp"}), \
             patch("fb_pipeline.browser.l3_inbox.reset_sidebar_to_top", return_value={"found": False}), \
             patch("fb_pipeline.browser.l3_inbox.extract_visible_threads", side_effect=[cards, cards, []]), \
             patch("fb_pipeline.browser.l3_inbox.validate_quick_fetch_cache", return_value=False), \
             patch("fb_pipeline.browser.l3_inbox.scroll_sidebar_and_wait", return_value={"elapsed_ms": 0}):
            out = discover_threads(
                _Page(), PAGE, "720d", 50, self.conn, self.logger, lambda *a, **k: None,
                skip_navigation=True, allow_early_exit=False, target_total_messages=None,
                on_task=tasks.append, **kw,
            )
        return out["stats"], tasks

    def test_same_token_skips_even_when_preview_differs(self):
        # The preview now shows a system banner; the token proves nothing new.
        stats, tasks = self._discover([_card(self.NAME, "Phạm Thịnh replied to an ad.", "Aug 6")])
        self.assertEqual(tasks, [])
        self.assertEqual(stats["skip_reasons"], {"token:token_match": 1})

    def test_utime_match_skips_and_utime_change_fetches(self):
        self.conn.execute("UPDATE threads SET fetched_sidebar_utime_ms=1789480610443 WHERE id=?", (self.tid,))
        self.conn.commit()
        stats, tasks = self._discover([_card(self.NAME, "banner", "Tue", source="utime", utime_ms=1789480610443.0)])
        self.assertEqual(tasks, [])
        self.assertEqual(stats["skip_reasons"], {"token:utime_match": 1})
        stats, tasks = self._discover([_card(self.NAME, "Em cảm ơn ạ", "Aug 6", source="utime", utime_ms=1789999999000.0)])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(stats["fetch_reasons"], {"utime_changed": 1})

    def test_newer_token_fetches(self):
        stats, tasks = self._discover([_card(self.NAME, "Em cảm ơn ạ", "Sep 20")])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(stats["fetch_reasons"], {"token_changed": 1})

    def test_scan_source_falls_back_to_preview_match(self):
        stats, tasks = self._discover([_card(self.NAME, "Em cảm ơn ạ", "Aug 6", source="scan")])
        self.assertEqual(tasks, [])
        self.assertEqual(stats["skip_reasons"], {"preview:time_source_ambiguous": 1})

    def test_refresh_flag_fetches_everything(self):
        stats, tasks = self._discover([_card(self.NAME, "Em cảm ơn ạ", "Aug 6")], force_refresh=True)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(stats["fetch_reasons"], {"force_refresh": 1})

    def test_refresh_older_than_fetches_stale_marker(self):
        stats, tasks = self._discover([_card(self.NAME, "Em cảm ơn ạ", "Aug 6")], refresh_older_than_days=1)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(stats["fetch_reasons"], {"marker_stale": 1})

    def test_skip_refreshes_marker_shape(self):
        # Stored as a clock yesterday; today Meta renders the same message as
        # "Yesterday 8:56 PM".  Equivalent -> skip, and the marker is rewritten
        # in its current shape.
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 21:30:00")
        self.conn.execute("UPDATE threads SET fetched_sidebar_token='8:56 PM', fetched_sidebar_kind='time_today', "
                          "fetched_at=? WHERE id=?", (yesterday, self.tid))
        self.conn.commit()
        stats, tasks = self._discover([_card(self.NAME, "Em cảm ơn ạ", "Yesterday 8:56 PM")])
        self.assertEqual(tasks, [])
        self.assertEqual(stats["skip_reasons"], {"token:token_match": 1})
        row = self.conn.execute("SELECT fetched_sidebar_token, fetched_sidebar_kind FROM threads WHERE id=?", (self.tid,)).fetchone()
        self.assertEqual(tuple(row), ("Yesterday 8:56 PM", "relative_day_time"))

    def test_you_preview_with_unknown_sender_row_still_skips(self):
        # Legacy rows carry sender='Unknown'; only a positive Customer row
        # proves the Page reply was never stored.
        self.conn.execute("UPDATE threads SET fetched_sidebar_token=NULL, fetched_at=NULL WHERE id=?", (self.tid,))
        self.conn.execute("UPDATE messages SET sender='Unknown', content='Hẹn gặp bạn' WHERE thread_id=?", (self.tid,))
        self.conn.commit()
        stats, tasks = self._discover([_card(self.NAME, "You: Hẹn gặp bạn", "Aug 6")])
        self.assertEqual(tasks, [])
        self.assertEqual(stats["skip_reasons"], {"preview:no_fetched_marker": 1})

    def test_you_preview_with_customer_row_fetches(self):
        self.conn.execute("UPDATE threads SET fetched_sidebar_token=NULL, fetched_at=NULL WHERE id=?", (self.tid,))
        self.conn.execute("UPDATE messages SET content='Hẹn gặp bạn' WHERE thread_id=?", (self.tid,))
        self.conn.commit()
        stats, tasks = self._discover([_card(self.NAME, "You: Hẹn gặp bạn", "Aug 6")])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(stats["fetch_reasons"], {"page_reply_missing": 1})

    def test_out_of_order_card_is_not_trusted(self):
        older = _card("Ánh Trịnh", "xin chào", "Aug 6")
        newer = dict(_card(self.NAME, "Em cảm ơn ạ", "Sep 14"), domIndex=1, absoluteTop=100)
        stats, tasks = self._discover([older, newer])
        self.assertEqual(stats["threads_time_out_of_order"], 1)
        self.assertTrue(any("sidebar_time_out_of_order" in m for lvl, m in self.logger.lines if lvl == "warning"))
        # Sep 14 != Aug 6 marker, but the token is untrusted -> preview fallback
        # (preview matches) -> skip via preview, not via token.
        self.assertIn("preview:time_out_of_order", stats["skip_reasons"])


# ---------------------------------------------------------------------------
# Browser-executed extractor: timestamp is read from the card tail
# ---------------------------------------------------------------------------
_MACOS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture(scope="module")
def dom_page():
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    executable = os.environ.get("PARSER_TEST_CHROME", _MACOS_CHROME)
    if not Path(executable).exists():
        pytest.skip("DOM extractor test requires local Chrome; set PARSER_TEST_CHROME")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
        except PlaywrightError as exc:  # pragma: no cover
            pytest.skip(f"unable to launch local Chrome: {exc}")
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def _card_html(name, preview, abbr_text=None, utime=None, title=None, chips=()):
    """Mimic Meta's sidebar card: name, preview, screen-reader span + abbr, chips."""
    time_html = ""
    if abbr_text:
        # Meta lays both out as separate innerText lines.
        time_html = (f'<span class="accessible_elem" style="display:block">{title or abbr_text}</span>'
                     f'<abbr class="timestamp" style="display:block" title="{title or abbr_text}" data-utime="{utime}">{abbr_text}</abbr>')
    chips_html = "".join(f"<div>{c}</div>" for c in chips)
    return (f'<div role="listitem" style="height:60px"><div>{name}</div><div class="_4ik4 _4ik5">{preview}</div>'
            f'{time_html}{chips_html}</div>')


def _cards_html(*cards):
    return f'<div style="height:200px;overflow:auto">{"".join(cards)}</div>'


def test_extractor_reads_abbr_utime_and_strips_screen_reader_duplicate(dom_page):
    dom_page.set_content(_cards_html(
        _card_html("Dinh Nguyen Thi", "You: Hoàn toàn miễn phí bạn à.", "Sun", "1789896374.244", "Sunday",
                   chips=("Intake", "\u200b", "ad_id....")),
        _card_html("Phạm Thịnh", "Chị hẹn ngày Aug 6 nhé", "Sep 14", "1789000000.5", "September 14"),
    ))
    threads = extract_visible_threads(dom_page)
    by_name = {t["name"]: t for t in threads}
    dinh = by_name["Dinh Nguyen Thi"]
    assert (dinh["sidebarTimeText"], dinh["sidebarTimeSource"]) == ("Sun", "utime")
    assert dinh["sidebarTimestampMs"] == 1789896374244.0
    assert "Sunday" not in dinh["previewText"] and "Sun" not in dinh["previewText"].split()
    assert dinh["previewText"].startswith("You: Hoàn toàn miễn phí bạn à.")
    thinh = by_name["Phạm Thịnh"]
    assert thinh["sidebarTimeText"] == "Sep 14"
    assert thinh["previewText"] == "Chị hẹn ngày Aug 6 nhé"


def test_extractor_without_abbr_falls_back_to_scan_and_flags_it(dom_page):
    dom_page.set_content(_cards_html(
        '<div role="listitem" style="height:60px"><div>Hoà Thu</div><div>Chị hẹn ngày Aug 6 nhé</div><div>Sep 14</div></div>'
    ))
    t = extract_visible_threads(dom_page)[0]
    # Forward scan picks the date inside the preview -> exactly why it is flagged.
    assert (t["sidebarTimeText"], t["sidebarTimeSource"], t["sidebarTimestampMs"]) == ("Sep 14", "scan", None)
