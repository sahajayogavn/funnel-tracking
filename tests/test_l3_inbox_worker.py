"""Unit tests for the Phase 1 inbox parallel-fetch refactor:
discover_threads() -> on_task dispatch, process_thread_task() outcome
mapping, and ThreadTask/ThreadResult JSON-safety.

# code:test-inbox-parallel-fetch-001:thread-worker
"""
import json
import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.browser.l3_inbox import discover_threads
from fb_pipeline.browser.inbox.thread_locator import LocateResult
from fb_pipeline.browser.inbox.thread_worker import ThreadWorkerDeps, process_thread_task
from fb_pipeline.contracts.l1_inbox import ThreadRecord, detect_city, extract_user_info
from fb_pipeline.contracts.l1_inbox_tasks import ThreadResult, ThreadTask
from fb_pipeline.persistence.l4_sqlite_store import setup_database


class _Mouse:
    def move(self, *_args):
        pass

    def wheel(self, *_args):
        pass


class _Page:
    def __init__(self):
        self.mouse = _Mouse()
        self.url = "https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326"

    def goto(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_selector(self, *_args, **_kwargs):
        pass

    def evaluate(self, *_args, **_kwargs):
        return "test"


class _Logger:
    def info(self, _msg):
        pass

    def warning(self, _msg):
        pass

    def error(self, _msg):
        pass


def _vt(name, ordinal, item_id, absolute_top):
    return {
        "name": name,
        "text": f"{name}\nHi there\nToday",
        "sidebarTimeText": "Today",
        "domIndex": ordinal,
        "selectedItemId": item_id,
        "absoluteTop": absolute_top,
    }


class TestDiscoverThreadsDispatch(unittest.TestCase):
    """(a) discover_threads calls on_task in order and before the next scroll."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_on_task_dispatched_per_round_before_next_scroll(self):
        page = _Page()
        logger = _Logger()

        round1 = [_vt("User A", 0, "a1", 100), _vt("User B", 1, "a2", 200)]
        round2 = [_vt("User C", 0, "a3", 300)]
        round3 = []

        events = []

        def fake_scroll(_page, _logger, scroll_round, timeout_ms=60000):
            events.append(("scroll", scroll_round))
            return {"elapsed_ms": 10}

        with patch("fb_pipeline.browser.l3_inbox.wait_for_inbox_shell", return_value=""), \
             patch("fb_pipeline.browser.l3_inbox.wait_for_initial_threads", return_value={"elapsed_ms": 0}), \
             patch("fb_pipeline.browser.l3_inbox.extract_visible_threads", side_effect=[round1, round2, round3]), \
             patch("fb_pipeline.browser.l3_inbox.scroll_sidebar_and_wait", side_effect=fake_scroll):
            result = discover_threads(
                page, "1548373332058326", "7d", max_threads=10, conn=self.conn, logger=logger,
                record_fetch=lambda *a, **k: None,
                skip_navigation=True, force_refresh=True, allow_early_exit=True,
                target_total_messages=None,
                on_task=lambda task: events.append(("task", task.ordinal)),
            )

        self.assertFalse(result["early_exit"])
        # Round 1's two tasks are dispatched, in order, before the first scroll.
        self.assertEqual(events[0], ("task", 0))
        self.assertEqual(events[1], ("task", 1))
        self.assertEqual(events[2], ("scroll", 1))
        # Round 2's task is dispatched before the second scroll.
        self.assertEqual(events[3], ("task", 2))
        self.assertEqual(events[4], ("scroll", 2))

    def test_commits_dispatched_batch_before_scrolling(self):
        """Stage-2 writers must not wait for Stage 1's full-range transaction."""
        class _TrackingConnection:
            def __init__(self, inner):
                self.inner = inner
                self.commits = 0

            def commit(self):
                self.commits += 1
                return self.inner.commit()

            def __getattr__(self, name):
                return getattr(self.inner, name)

        raw_conn = sqlite3.connect(":memory:")
        raw_conn.row_factory = sqlite3.Row
        setup_database(raw_conn)
        conn = _TrackingConnection(raw_conn)
        page = _Page()

        def fake_scroll(_page, _logger, scroll_round, timeout_ms=60000):
            self.assertGreaterEqual(conn.commits, 1)
            return {"elapsed_ms": 0}

        with patch("fb_pipeline.browser.l3_inbox.wait_for_inbox_shell", return_value=""), \
             patch("fb_pipeline.browser.l3_inbox.wait_for_initial_threads", return_value={"elapsed_ms": 0}), \
             patch("fb_pipeline.browser.l3_inbox.extract_visible_threads", side_effect=[[_vt("User A", 0, "a1", 100)], []]), \
             patch("fb_pipeline.browser.l3_inbox.scroll_sidebar_and_wait", side_effect=fake_scroll):
            discover_threads(
                page, "1548373332058326", "7d", max_threads=10, conn=conn, logger=_Logger(),
                record_fetch=lambda *a, **k: None,
                skip_navigation=True, force_refresh=True, allow_early_exit=True,
                target_total_messages=None, on_task=lambda _task: None,
            )

        raw_conn.close()


class TestProcessThreadTaskOutcomes(unittest.TestCase):
    """(b) process_thread_task status mapping for 0-message vs successful threads."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()

    def _make_task(self, name="User A", thread_id="page1_abc123"):
        record = ThreadRecord(
            page_id="page1",
            thread_id=thread_id,
            thread_name=name,
            preview_text="Hi there",
            thread_lines=[name, "Hi there"],
            dom_index=0,
            sidebar_time_text="Today",
            sidebar_identity_key=f"{name}||Hi there||Today",
            selected_item_id="",
            fb_url="",
        )
        return ThreadTask(ordinal=0, record=record, absolute_top=0, psid_hint="", is_new=True)

    def _deps(self):
        return ThreadWorkerDeps(
            extract_ad_id_labels=lambda _page: [],
            extract_user_info=extract_user_info,
            detect_city=detect_city,
        )

    def test_zero_messages_maps_to_no_messages(self):
        task = self._make_task()
        page = _Page()
        logger = _Logger()
        locate_result = LocateResult(
            clicked=True, method="sidebar_identity", attempts=1,
            prev_fb_url="", pre_click_fingerprint="",
        )
        with patch("fb_pipeline.browser.inbox.thread_worker.locate_thread_in_sidebar", return_value=locate_result), \
             patch("fb_pipeline.browser.inbox.thread_worker.verify_thread_switch", return_value=("fb_url_1", True)), \
             patch("fb_pipeline.browser.inbox.thread_worker.extract_ad_context", return_value=""), \
             patch("fb_pipeline.browser.inbox.thread_worker.scroll_up_message_panel", return_value=0), \
             patch("fb_pipeline.browser.inbox.thread_worker.extract_thread_messages", return_value=[]):
            result = process_thread_task(page, self.conn, task, self._deps(), logger)

        self.assertIsInstance(result, ThreadResult)
        self.assertEqual(result.status, "no_messages")
        self.assertEqual(result.messages_added, 0)

    def test_successful_thread_maps_to_persisted_with_messages_added(self):
        task = self._make_task(name="User B", thread_id="page1_def456")
        page = _Page()
        logger = _Logger()
        locate_result = LocateResult(
            clicked=True, method="sidebar_identity", attempts=1,
            prev_fb_url="", pre_click_fingerprint="",
        )
        js_messages = [
            {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
            {"sender": "Page", "text": "Chào bạn", "timestamp": "Today"},
        ]
        with patch("fb_pipeline.browser.inbox.thread_worker.locate_thread_in_sidebar", return_value=locate_result), \
             patch("fb_pipeline.browser.inbox.thread_worker.verify_thread_switch", return_value=("fb_url_2", True)), \
             patch("fb_pipeline.browser.inbox.thread_worker.extract_ad_context", return_value=""), \
             patch("fb_pipeline.browser.inbox.thread_worker.scroll_up_message_panel", return_value=0), \
             patch("fb_pipeline.browser.inbox.thread_worker.extract_thread_messages", return_value=js_messages):
            result = process_thread_task(page, self.conn, task, self._deps(), logger, is_first_thread=True)

        self.assertEqual(result.status, "persisted")
        self.assertEqual(result.messages_added, 2)
        self.assertTrue(result.thread_id.startswith("page1_"))

        row = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id = ?", (result.thread_id,)
        ).fetchone()
        self.assertEqual(row[0], 2)

    def test_click_verify_failure_maps_to_click_verify_failed(self):
        task = self._make_task(name="User C", thread_id="page1_ghi789")
        page = _Page()
        logger = _Logger()
        locate_result = LocateResult(
            clicked=False, method="sidebar_identity", attempts=150,
            prev_fb_url="", pre_click_fingerprint="",
        )
        with patch("fb_pipeline.browser.inbox.thread_worker.locate_thread_in_sidebar", return_value=locate_result):
            result = process_thread_task(page, self.conn, task, self._deps(), logger)

        self.assertEqual(result.status, "click_verify_failed")
        self.assertEqual(result.thread_id, "")


class TestTaskResultLogDicts(unittest.TestCase):
    """(c) ThreadTask/ThreadResult to_log_dict() is JSON-serialisable."""

    def test_thread_task_to_log_dict_is_json_serialisable(self):
        record = ThreadRecord(
            page_id="page1",
            thread_id="page1_abc",
            thread_name="User A",
            preview_text="Hi",
            thread_lines=["User A", "Hi"],
            dom_index=0,
        )
        task = ThreadTask(ordinal=0, record=record, absolute_top=100.0, psid_hint="", is_new=True)
        payload = json.dumps(task.to_log_dict())
        decoded = json.loads(payload)
        self.assertEqual(decoded["record"]["thread_id"], "page1_abc")
        self.assertEqual(decoded["ordinal"], 0)

    def test_thread_result_to_log_dict_is_json_serialisable(self):
        result = ThreadResult(ordinal=0, thread_id="page1_abc", status="persisted", messages_added=3)
        payload = json.dumps(result.to_log_dict())
        decoded = json.loads(payload)
        self.assertEqual(decoded["status"], "persisted")
        self.assertEqual(decoded["messages_added"], 3)


if __name__ == '__main__':
    unittest.main()
