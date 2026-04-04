import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
from fb_pipeline.browser.l3_inbox import _parse_sidebar_time_token, _sidebar_loading_count, scrape_inbox
from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record
from fb_pipeline.persistence.l4_sqlite_store import setup_database
from fb_pipeline.browser.inbox.thread_detail_parser import extract_thread_messages

class TestThreadDetailParser(unittest.TestCase):
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

        # The script utilizes mouse move and scrollIntoView
        self.assertIn((200, 400), page.mouse.moves)
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
        self.assertEqual(enriched.city, "Hà Nội")
        self.assertEqual(len(enriched.messages), 2)
        self.assertEqual(enriched.messages[0].seq, 0)
        self.assertEqual(enriched.mas_handoff.fb_url, "selected123")
        self.assertEqual(enriched.mas_handoff.seeker.city, "Hà Nội")
        self.assertEqual(enriched.mas_handoff.ad_ids, ["ad_1"])

    # Gate 3: code:test-validation-001:l1-to-l4
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
        self.assertEqual(result["city"], "Hà Nội")

        thread = self.conn.execute("SELECT * FROM threads WHERE id = ?", (thread_record.thread_id,)).fetchone()
        self.assertEqual(thread["page_id"], "page1")

        user = self.conn.execute("SELECT * FROM users WHERE thread_id = ?", (thread_record.thread_id,)).fetchone()
        self.assertEqual(user["fb_url"], "selected456")
        self.assertEqual(user["city"], "Hà Nội")
        self.assertIsNotNone(user["last_synced_at"])

        ad = self.conn.execute("SELECT * FROM ad_posts WHERE ad_id = '6930299765389'").fetchone()
        self.assertEqual(ad["city"], "Hà Nội")

        msgs = self.conn.execute("SELECT content, seq FROM messages WHERE thread_id = ? ORDER BY seq", (thread_record.thread_id,)).fetchall()
        self.assertIn("--- [AD SOURCE]: Thiền miễn phí tại Hà Nội ---", msgs[0]["content"])
        self.assertEqual(msgs[1]["seq"], 1)

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


if __name__ == '__main__':
    unittest.main()
