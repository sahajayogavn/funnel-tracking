import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
from fb_pipeline.browser.l3_inbox import (
    _parse_sidebar_time_token,
    _sidebar_loading_count,
    _sidebar_snapshot_progressed,
    _thread_panel_loading_count,
    scrape_inbox,
)
from fb_pipeline.browser.inbox.scroll_helpers import reset_sidebar_to_top, scroll_sidebar_and_wait
from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record
from fb_pipeline.persistence.l4_sqlite_store import setup_database
from fb_pipeline.browser.inbox.thread_detail_parser import extract_thread_messages, verify_thread_switch
from fb_pipeline.browser.inbox.thread_list_parser import extract_visible_threads, is_conversation_name

class TestThreadDetailParser(unittest.TestCase):
    def test_stage2_sidebar_progress_requires_position_or_card_change(self):
        static = {"scrollTop": 0, "fingerprint": "same-cards"}
        self.assertFalse(_sidebar_snapshot_progressed(static, static))
        self.assertTrue(_sidebar_snapshot_progressed(static, {"scrollTop": 0, "fingerprint": "next-cards"}))
        self.assertTrue(_sidebar_snapshot_progressed(static, {"scrollTop": 300, "fingerprint": "same-cards"}))

    def test_sidebar_scroll_stops_on_static_loading_marker(self):
        class _Mouse:
            def move(self, *_args):
                pass

            def wheel(self, *_args):
                pass

        class _Page:
            mouse = _Mouse()

            def evaluate(self, *_args, **_kwargs):
                return {
                    "before": 0,
                    "after": 500,
                    "domMoved": True,
                    "targetX": 200,
                    "targetY": 300,
                    "targetHeight": 500,
                    "conversationCardCount": 12,
                }

            def wait_for_timeout(self, _ms):
                pass

        class _Logger:
            def info(self, _msg):
                pass

            def warning(self, _msg):
                pass

        static_loading = {
            "count": 12,
            "fingerprint": "same-cards",
            "loadingCount": 1,
            "globalLoadingCount": 0,
        }
        with patch(
            "fb_pipeline.browser.inbox.scroll_helpers.sidebar_loading_snapshot",
            return_value=static_loading,
        ):
            result = scroll_sidebar_and_wait(_Page(), _Logger(), scroll_round=1, timeout_ms=60000)

        self.assertTrue(result["stalled"])

    def test_thread_panel_loading_count_uses_message_panel_scope(self):
        class _Page:
            def evaluate(self, _script, _args):
                return 2

        self.assertEqual(_thread_panel_loading_count(_Page()), 2)

    def test_extract_visible_threads_excludes_filter_tabs(self):
        class _Page:
            def __init__(self):
                self.script = ""
            def evaluate(self, script, *_args, **_kwargs):
                self.script = script
                return []

        page = _Page()
        self.assertEqual(extract_visible_threads(page), [])
        self.assertIn("!el.closest('[role=\"tablist\"]')", page.script)

    def test_extract_thread_messages_includes_reactions(self):
        class _Page:
            def evaluate(self, script, *args, **kwargs):
                return [
                    {"text": "Hello\\n[Quoted Reply/Link]: :::REACTION_LOVE:::", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                ]
        page = _Page()
        messages = extract_thread_messages(page)
        self.assertEqual(len(messages), 1)
        self.assertIn(":::REACTION_LOVE:::", messages[0]["text"])
        
    def test_extract_thread_messages_ignores_system_buttons_with_zws(self):
        class _Page:
            def evaluate(self, script, *args, **kwargs):
                return [
                    {"text": "Hello", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                    {"text": "Close\u200b", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                    {"text": "\u200bĐóng\u200b", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                    {"text": "Previous\n[Quoted Reply/Link]: Close\n[Quoted Reply/Link]: Next", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                    {"text": "Improve AI response", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                    {"text": "Real message", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"},
                ]
        page = _Page()
        messages = extract_thread_messages(page)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["text"], "Hello")
        self.assertEqual(messages[1]["text"], "Real message")

    def test_verify_thread_switch_fallback_fb_url_when_missing_selected_item_id(self):
        class _Page:
            url = "https://business.facebook.com/latest/inbox/all?asset_id=123"
            def evaluate(self, script, *args, **kwargs):
                return "User A" # Returns header_text ensuring name matched
            def wait_for_timeout(self, ms):
                pass

        class _Logger:
            def info(self, msg): pass
            def error(self, msg): pass
            def warning(self, msg): pass

        class _ThreadRecord:
            selected_item_id = ""
            fb_url = "original_hovercard_fb_url"
            
        page = _Page()
        logger = _Logger()
        thread_record = _ThreadRecord()
        
        # Act
        # Facebook returns a URL without selected_item_id.
        fb_url, verified_status = verify_thread_switch(
            page, logger, name="User A", prev_fb_url="old", 
            pre_click_fingerprint="old_fp", is_first_thread=False, thread_record=thread_record
        )
        
        # Assert
        self.assertTrue(verified_status)
        self.assertEqual(fb_url, "original_hovercard_fb_url")

    def test_verify_thread_switch_first_thread_fallback(self):
        class _Page:
            url = "https://business.facebook.com/latest/inbox/all?asset_id=123"
            def evaluate(self, script, *args, **kwargs):
                return "User A"
            def wait_for_timeout(self, ms):
                pass
        
        class _Logger:
            def info(self, msg): pass
            def error(self, msg): pass
            def warning(self, msg): pass

        class _ThreadRecord:
            selected_item_id = ""
            fb_url = "hovercard_first_thread"

        page = _Page()
        logger = _Logger()
        thread_record = _ThreadRecord()

        # Act with is_first_thread=True
        fb_url, verified_status = verify_thread_switch(
            page, logger, name="User A", prev_fb_url="old", 
            pre_click_fingerprint="old_fp", is_first_thread=True, thread_record=thread_record
        )

        # Assert early return still sets fallback fb_url
        self.assertTrue(verified_status)
        self.assertEqual(fb_url, "hovercard_first_thread")

class TestInboxContracts(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()

    # Gate 1 & 2: code:test-validation-001:l2-to-l3 and code:test-validation-001:l3-to-l1
    def test_build_thread_record_parses_visible_thread(self):
        record = build_thread_record("page1", {
            "domIndex": 3,
            "name": " User A ",
            "text": " User A \nHello there\nToday ",
            "sidebarTimeText": "Today",
            "sidebarTimeKind": "today",
            "sidebarIdentityKey": "thread-a",
        })
        self.assertEqual(record.page_id, "page1")
        self.assertEqual(record.thread_name, "User A")
        self.assertEqual(record.preview_text, "Hello there")
        self.assertEqual(record.dom_index, 3)
        self.assertEqual(record.sidebar_time_text, "Today")
        self.assertEqual(record.sidebar_time_kind, "today")
        self.assertEqual(record.sidebar_identity_key, "thread-a")
        self.assertTrue(record.thread_id.startswith("page1_"))

    def test_build_thread_record_is_deterministic_and_distinguishes_same_name_threads(self):
        record_a1 = build_thread_record("page1", {
            "name": "User A",
            "text": "User A\nPreview one\nToday",
            "sidebarTimeText": "Today",
            "sidebarIdentityKey": "thread-a",
        })
        record_a2 = build_thread_record("page1", {
            "name": "User A",
            "text": "User A\nPreview one\nToday",
            "sidebarTimeText": "Today",
            "sidebarIdentityKey": "thread-a",
        })
        record_b = build_thread_record("page1", {
            "name": "User A",
            "text": "User A\nPreview two\nToday",
            "sidebarTimeText": "Today",
            "sidebarIdentityKey": "thread-b",
        })
        self.assertEqual(record_a1.thread_id, record_a2.thread_id)
        self.assertNotEqual(record_a1.thread_id, record_b.thread_id)

    def test_parse_sidebar_time_token_handles_supported_formats(self):
        now = __import__("datetime").datetime(2026, 4, 1)
        self.assertEqual(_parse_sidebar_time_token("Today", now)["days_ago"], 0)
        self.assertEqual(_parse_sidebar_time_token("Yesterday", now)["days_ago"], 1)
        self.assertEqual(_parse_sidebar_time_token("Today 8:56 PM", now)["parsed_at"], "2026-04-01 20:56:00")
        self.assertEqual(_parse_sidebar_time_token("Yesterday 8:56 PM", now)["parsed_at"], "2026-03-31 20:56:00")
        self.assertEqual(_parse_sidebar_time_token("Feb 6, 2026, 1:58 PM", now)["parsed_at"], "2026-02-06 13:58:00")
        self.assertEqual(_parse_sidebar_time_token("Mon", now)["kind"], "weekday")
        self.assertEqual(_parse_sidebar_time_token("Mar 15", now)["kind"], "month_day")
        self.assertEqual(_parse_sidebar_time_token("??", now)["kind"], "unknown")

    def test_sidebar_loading_count_prefers_container_scoped_spinner(self):
        self.assertEqual(
            _sidebar_loading_count({"hasContainer": True, "loadingCount": 0, "globalLoadingCount": 3}),
            0,
        )
        self.assertEqual(
            _sidebar_loading_count({"hasContainer": False, "loadingCount": 0, "globalLoadingCount": 3}),
            3,
        )

    def test_sidebar_scroll_uses_wheel_over_list_when_dom_scroll_does_not_move(self):
        # code:test-validation-001:l3-sidebar-wheel-fallback
        class _Mouse:
            def __init__(self):
                self.moves = []
                self.wheels = []

            def move(self, x, y):
                self.moves.append((x, y))

            def wheel(self, dx, dy):
                self.wheels.append((dx, dy))

        class _Page:
            def __init__(self):
                self.mouse = _Mouse()

            def evaluate(self, *_args, **_kwargs):
                return {
                    "before": 0,
                    "after": 0,
                    "domMoved": False,
                    "targetX": 210,
                    "targetY": 330,
                    "targetHeight": 618,
                    "conversationCardCount": 8,
                }

            def wait_for_timeout(self, _ms):
                pass

        class _Logger:
            def __init__(self):
                self.messages = []

            def info(self, message):
                self.messages.append(message)

            def warning(self, message):
                self.messages.append(message)

        snapshot = {
            "count": 8,
            "loadingCount": 0,
            "globalLoadingCount": 0,
            "hasContainer": True,
            "fingerprint": "same",
        }
        page = _Page()
        logger = _Logger()
        with patch("fb_pipeline.browser.inbox.scroll_helpers.sidebar_loading_snapshot", side_effect=[snapshot] * 4):
            scroll_sidebar_and_wait(page, logger, scroll_round=1, timeout_ms=1000, poll_ms=0)

        self.assertEqual(page.mouse.moves, [(210, 330)])
        self.assertEqual(page.mouse.wheels, [(0, 618)])
        self.assertTrue(any("sidebar_scroll_wheel_fallback" in message for message in logger.messages))

    def test_sidebar_reset_uses_tab_aware_conversation_scroller(self):
        # code:test-validation-001:l3-stage2-sidebar-reset
        class _Page:
            def evaluate(self, *_args, **_kwargs):
                return {
                    "before": 66451.25,
                    "after": 0,
                    "found": True,
                    "conversationCardCount": 14,
                }

        class _Logger:
            def __init__(self):
                self.messages = []

            def info(self, message):
                self.messages.append(message)

        logger = _Logger()
        result = reset_sidebar_to_top(_Page(), logger)

        self.assertTrue(result["found"])
        self.assertEqual(result["after"], 0)
        self.assertTrue(any("scrollTop=66451.25->0" in message for message in logger.messages))

    def test_scrape_inbox_performs_one_sidebar_scroll_and_one_wait_cycle(self):
        # code:test-validation-001:l3-sidebar-loading
        class _Mouse:
            def __init__(self):
                self.moves = []
                self.wheels = []

            def move(self, x, y):
                self.moves.append((x, y))

            def wheel(self, dx, dy):
                self.wheels.append((dx, dy))

        class _Page:
            def __init__(self):
                self.mouse = _Mouse()
                self.goto_calls = []
                self.wait_for_timeout_calls = []
                self.evaluate_calls = []
                self.url = "https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326"

            def evaluate(self, script, *args, **kwargs):
                self.evaluate_calls.append(script)
                if "config.threadSelector" in script: return {"count": 2, "loadingCount": 0, "globalLoadingCount": 0, "hasContainer": True, "fingerprint": "fp-eval"}
                if "sidebarIdentityKey" in script: return True
                if "querySelectorAll('.x14vqqas" in script or "results.push({htmlStr" in script: return [{"text": "Hello", "htmlStr": "<div>...</div>", "bg": "rgba(235, 235, 235, 1)", "timestamp": "Today"}]
                if "document.title" in script or "querySelectorAll('.xzsf02u" in script: return []
                return "test"

            def goto(self, url, wait_until=None, timeout=None):
                self.goto_calls.append((url, wait_until, timeout))

            def wait_for_timeout(self, ms):
                self.wait_for_timeout_calls.append(ms)

        class _Logger:
            def __init__(self):
                self.messages = []

            def info(self, msg):
                self.messages.append(("info", msg))

            def warning(self, msg):
                self.messages.append(("warning", msg))

        page = _Page()
        logger = _Logger()
        record_fetch_calls = []

        def _record_fetch(page_id, total_threads, new_messages, conn):
            record_fetch_calls.append((page_id, total_threads, new_messages))

        with patch("fb_pipeline.browser.l3_inbox.wait_for_inbox_shell", return_value=""), \
             patch("fb_pipeline.browser.l3_inbox.wait_for_initial_threads", return_value={
                 "count": 2, "elapsed_ms": 1500, "fingerprint": "fp-0",
             }), \
             patch("fb_pipeline.browser.l3_inbox.sidebar_loading_snapshot", return_value={
                 "count": 2,
                 "loadingCount": 0,
                 "globalLoadingCount": 0,
                 "hasContainer": True,
                 "fingerprint": "fp-1",
             }), \
             patch("fb_pipeline.browser.l3_inbox.extract_visible_threads", side_effect=[
                 [{"name": "test"}], # for first_glance_threads
                 [{"name": "test"}], # first round
                 []                  # second round (to break)
             ]), \
             patch("fb_pipeline.browser.l3_inbox.validate_quick_fetch_cache", return_value=False):
            stats = scrape_inbox(
                page=page,
                page_id="1548373332058326",
                time_range="7d",
                max_threads=5,
                conn=self.conn,
                logger=logger,
                record_fetch=_record_fetch,
                extract_ad_id_labels_arg=lambda _page: [],
                extract_user_info=extract_user_info,
                detect_city=detect_city,
            )

        # The script targets the resolved sidebar geometry before scrolling.
        self.assertTrue(page.mouse.moves)
        self.assertTrue(any("scrollIntoView" in script for script in page.evaluate_calls))
        self.assertEqual(record_fetch_calls, [("1548373332058326", 1, 1)])

    # Gate 2: code:test-validation-001:l3-to-l1
    def test_enrich_thread_record_builds_mas_payload(self):
        thread_record = build_thread_record("page1", {
            "name": "User A",
            "text": "User A\nPreview",
        })
        enriched = enrich_thread_record(
            thread_record,
            [
                {"sender": "Customer", "text": "0912345678", "timestamp": "Today"},
                {"sender": "Page", "text": "Lớp tại Hà Nội", "timestamp": "Today"},
                {"sender": "Customer", "text": "", "timestamp": "Today"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected123",
            ad_ids=["ad_1"],
        )
        self.assertEqual(enriched.user_info["phone"], "0912345678")
        self.assertIsNone(enriched.city)
        self.assertEqual(len(enriched.messages), 2)
        self.assertEqual(enriched.messages[0].seq, 0)
        self.assertEqual(enriched.mas_handoff.fb_url, "selected123")
        self.assertIsNone(enriched.mas_handoff.seeker.city)
        self.assertEqual(enriched.mas_handoff.ad_ids, ["ad_1"])

    # Gate 3: code:test-validation-001:l1-to-l4
    # code:test-validation-001:message-dedup-002
    def test_persist_is_idempotent_when_first_row_was_saved_as_auto_page(self):
        """Retrospective 2026-09-17: the AD SOURCE-prefixed first row is stored
        with sender Auto_Page while the scraper keeps reporting Page, so a
        second crawl re-inserted '<name> replied to an ad.' every run."""
        js_messages = [
            {"sender": "Page", "text": "User B replied to an ad.", "timestamp": "Today"},
            {"sender": "Customer", "text": "Cho em hỏi lịch học", "timestamp": "Today"},
            {"sender": "Page", "text": "Chúng tôi có thể giúp gì cho bạn?", "timestamp": "Today"},
        ]

        def _record():
            return enrich_thread_record(
                build_thread_record("page1", {"name": "User B", "text": "User B\nPreview"}),
                js_messages, extract_user_info, detect_city,
                ad_context="Thiền miễn phí tại Hà Nội", fb_url="psid-b",
            )

        first = persist_thread_record(self.conn, _record(), detect_city)
        self.assertEqual(first["messages_added"], 3)
        senders = [r[0] for r in self.conn.execute(
            "SELECT sender FROM messages WHERE thread_id = ? ORDER BY seq", (first["thread_id"],))]
        self.assertEqual(senders, ["Auto_Page", "Customer", "Auto_Page"])

        second = persist_thread_record(self.conn, _record(), detect_city)
        self.assertEqual(second["messages_added"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (first["thread_id"],)).fetchone()[0]
        self.assertEqual(count, 3)

    def test_persist_thread_record_writes_all_boundaries(self):
        thread_record = enrich_thread_record(
            build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
            [
                {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
                {"sender": "Page", "text": "Địa chỉ: 40 Vương Thừa Vũ", "timestamp": "Today"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected456",
            ad_ids=["6930299765389"],
        )
        result = persist_thread_record(self.conn, thread_record, detect_city)
        self.assertEqual(result["messages_added"], 2)
        self.assertEqual(result["ad_ids_count"], 1)
        self.assertIsNone(result["city"])

        thread = self.conn.execute("SELECT * FROM threads WHERE id = ?", (thread_record.thread_id,)).fetchone()
        self.assertEqual(thread["page_id"], "page1")
        self.assertEqual(thread["inbox_sort_index"], thread_record.dom_index)

        user = self.conn.execute("SELECT * FROM users WHERE thread_id = ?", (thread_record.thread_id,)).fetchone()
        self.assertEqual(user["fb_url"], "selected456")
        self.assertIsNone(user["city"])
        self.assertIsNotNone(user["last_synced_at"])

        ad = self.conn.execute("SELECT * FROM ad_posts WHERE ad_id = '6930299765389'").fetchone()
        self.assertEqual(ad["city"], "Hà Nội")

        msgs = self.conn.execute("SELECT content, seq FROM messages WHERE thread_id = ? ORDER BY seq", (thread_record.thread_id,)).fetchall()
        self.assertIn("--- [AD SOURCE]: Thiền miễn phí tại Hà Nội ---", msgs[0]["content"])
        self.assertEqual(msgs[1]["seq"], 1)

    # Gate 3: code:test-validation-001:l1-to-l4 (dedup stability)
    def test_persist_thread_record_dedups_literal_newline_and_late_reaction(self):
        def _record(js_messages):
            return enrich_thread_record(
                build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
                js_messages, extract_user_info, detect_city,
            )

        first = _record([
            {"sender": "Customer", "text": "Đăng ký\n[Quoted Reply/Link]: Em ở Đà Nẵng", "timestamp": "Fri 3:49 PM"},
            {"sender": "Page", "text": "Tụi mình có lớp sáng chủ nhật", "timestamp": "8:04 AM"},
        ])
        persist_thread_record(self.conn, first, detect_city)

        # Re-sync: same messages, but with a literal backslash-n (legacy parser
        # bug), a weekday-prefixed relative time, and a reaction added later.
        resync = _record([
            {"sender": "Customer", "text": "Đăng ký\\n[Quoted Reply/Link]: Em ở Đà Nẵng", "timestamp": "Fri 3:49 PM"},
            {"sender": "Page", "text": "Tụi mình có lớp sáng chủ nhật\n[Quoted Reply/Link]: :::REACTION_LOVE:::", "timestamp": "Mon 8:04 AM"},
        ])
        result = persist_thread_record(self.conn, resync, detect_city)

        self.assertEqual(result["messages_added"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (first.thread_id,)).fetchone()[0]
        self.assertEqual(count, 2)

    # Gate 3: code:test-validation-001:l1-to-l4 (CRM Timing State)
    def test_persist_thread_record_only_refreshes_last_synced_at_without_new_customer_message(self):
        thread_record = enrich_thread_record(
            build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
            [
                {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
                {"sender": "Page", "text": "Địa chỉ: 40 Vương Thừa Vũ", "timestamp": "Today"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected456",
            ad_ids=["6930299765389"],
        )
        persist_thread_record(self.conn, thread_record, detect_city)

        stale_interaction = "2000-01-01 00:00:00"
        stale_synced = "2000-01-01 00:00:00"
        self.conn.execute(
            "UPDATE users SET last_interaction = ?, last_synced_at = ? WHERE thread_id = ?",
            (stale_interaction, stale_synced, thread_record.thread_id),
        )
        self.conn.commit()

        resynced_record = enrich_thread_record(
            build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
            [
                {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
                {"sender": "Page", "text": "Địa chỉ: 40 Vương Thừa Vũ", "timestamp": "Today"},
                {"sender": "Page", "text": "Lớp tiếp theo vào Chủ Nhật", "timestamp": "Tomorrow"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected456",
            ad_ids=["6930299765389"],
        )
        persist_thread_record(self.conn, resynced_record, detect_city)

        user = self.conn.execute(
            "SELECT last_interaction, last_synced_at FROM users WHERE thread_id = ?",
            (thread_record.thread_id,),
        ).fetchone()
        self.assertEqual(user["last_interaction"], stale_interaction)
        self.assertNotEqual(user["last_synced_at"], stale_synced)

    def test_is_valid_timestamp_text_filters_noise(self):
        from fb_pipeline.browser.inbox.thread_detail_parser import is_valid_timestamp_text
        # Valid timestamps
        self.assertTrue(is_valid_timestamp_text("8:20 AM"))
        self.assertTrue(is_valid_timestamp_text("Feb 6, 2026, 1:58 PM"))
        self.assertTrue(is_valid_timestamp_text("9/5/18, 4:09 PM"))
        self.assertTrue(is_valid_timestamp_text("Today"))
        self.assertTrue(is_valid_timestamp_text("Yesterday"))
        # Invalid buttons/labels
        self.assertFalse(is_valid_timestamp_text("Hỏi chi tiết"))
        self.assertFalse(is_valid_timestamp_text("Thiền Sahaja Yoga Việt Nam"))
        self.assertFalse(is_valid_timestamp_text("zalo.me"))
        self.assertFalse(is_valid_timestamp_text("New Advanced Print FreeMeditation.pdf"))
        self.assertFalse(is_valid_timestamp_text("3.35 MiB"))
        self.assertFalse(is_valid_timestamp_text("Audio call"))
        self.assertFalse(is_valid_timestamp_text(""))

    def test_persist_thread_record_prioritizes_message_timestamp_and_avoids_future_times(self):
        import datetime as dt
        now = dt.datetime.now()
        thread_record = enrich_thread_record(
            build_thread_record("page1", {
                "name": "Khanh Van Quach",
                "text": "Khanh Van Quach\nThanks bạn",
                "sidebarTimeText": "Today",
                "sidebarTimeKind": "today",
            }),
            [
                {"sender": "Customer", "text": "Hà Nội", "timestamp": "Feb 6, 2026, 1:58 PM"},
                {"sender": "Customer", "text": "Thanks bạn", "timestamp": "8:20 AM"},
            ],
            extract_user_info,
            detect_city,
        )
        persist_thread_record(self.conn, thread_record, detect_city)
        user = self.conn.execute("SELECT * FROM users WHERE thread_id = ?", (thread_record.thread_id,)).fetchone()
        self.assertIsNotNone(user)
        # Should record exact 8:20:00, not 23:59:59
        self.assertIn("08:20:00", user["last_interaction"])
        # Must never be in the future
        user_dt = dt.datetime.strptime(user["last_interaction"], "%Y-%m-%d %H:%M:%S")
        self.assertLessEqual(user_dt, now)
        thread = self.conn.execute("SELECT last_message_at FROM threads WHERE id = ?", (thread_record.thread_id,)).fetchone()
        self.assertIn("08:20:00", thread["last_message_at"])


if __name__ == '__main__':
    unittest.main()
