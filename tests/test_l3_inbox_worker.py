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
            {"sender": "Customer", "text": "Xin chào", "timestamp": "Sep 10, 2026 9:00 AM", "day_context": "2026-09-10", "time_precision": "date_time", "source_id": "m1", "sender_confidence": "explicit", "sender_evidence": "Lan sent a message"},
            {"sender": "Page", "text": "Chào bạn", "timestamp": "Sep 10, 2026 9:01 AM", "day_context": "2026-09-10", "time_precision": "date_time", "source_id": "m2", "sender_confidence": "explicit", "sender_evidence": "You sent"},
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


# code:test-validation-001:partial-admission
class TestPartialAdmission(unittest.TestCase):
    """Evidenced messages are persisted while unresolved bubbles are quarantined;
    a contradiction still rejects the whole thread."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)
        self.page_id = "1548373332058326"
        self.psid = "100001005716854"

    def tearDown(self):
        self.conn.close()

    def _task(self):
        from fb_pipeline.inbox.l3_pipeline import canonical_thread_id
        record = ThreadRecord(
            page_id=self.page_id, thread_id=canonical_thread_id(self.page_id, self.psid),
            thread_name="Hung Bui", preview_text="Hi", thread_lines=["Hung Bui", "Hi"], dom_index=0,
            sidebar_time_text="Today", sidebar_identity_key="k", selected_item_id=self.psid, fb_url="",
        )
        return ThreadTask(ordinal=0, record=record, absolute_top=0, psid_hint=self.psid, is_new=True)

    def _run(self, messages):
        locate_result = LocateResult(clicked=True, method="direct_url", attempts=1, prev_fb_url="", pre_click_fingerprint="")
        deps = ThreadWorkerDeps(extract_ad_id_labels=lambda _p: [], extract_user_info=extract_user_info, detect_city=detect_city)
        class _CapturingLogger:
            def __init__(self): self.lines = []
            def info(self, m): self.lines.append(("info", m))
            def warning(self, m): self.lines.append(("warning", m))
            def error(self, m): self.lines.append(("error", m))
            def debug(self, m): self.lines.append(("debug", m))
        logger = _CapturingLogger()
        with patch("fb_pipeline.browser.inbox.thread_worker.locate_thread_in_sidebar", return_value=locate_result), \
             patch("fb_pipeline.browser.inbox.thread_worker.verify_thread_switch", return_value=(self.psid, True)), \
             patch("fb_pipeline.browser.inbox.thread_worker.extract_ad_context", return_value=""), \
             patch("fb_pipeline.browser.inbox.thread_worker.scroll_up_message_panel", return_value=0), \
             patch("fb_pipeline.browser.inbox.thread_worker.extract_thread_messages", return_value=messages), \
             patch("fb_pipeline.browser.inbox.thread_worker.save_integrity_report", return_value="/dev/null"):
            return process_thread_task(_Page(), self.conn, self._task(), deps, logger), logger

    @staticmethod
    def _msg(text, sender, sid, conf="structural", evidence="layout=row-reverse", **kw):
        base = {"sender": sender, "text": text, "body": text, "timestamp": "Sep 10, 2026, 9:00 AM",
                "day_context": "2026-09-10", "time_precision": "date_time", "source_id": sid,
                "sender_confidence": conf, "sender_evidence": evidence, "reactions": []}
        base.update(kw)
        return base

    def test_structural_evidence_is_admitted_and_unresolved_bubbles_quarantined(self):
        messages = [
            {"sender": "Unknown", "text": "Hung Bui replied to an ad.", "body": "Hung Bui replied to an ad.",
             "timestamp": "", "day_context": None, "time_precision": "unknown", "source_id": None,
             "sender_confidence": "unknown", "sender_evidence": None, "reactions": []},
            self._msg("Xin chào", "Customer", "m1", evidence="avatar-alt=Hung Bui"),
            self._msg("Chào bạn", "Page", "m2"),
            self._msg("Hỏi chi tiết", "Unknown", None, conf="unknown", evidence=None),  # quick-reply chip, no id
        ]
        result, logger = self._run(messages)
        self.assertEqual(result.status, "persisted")
        self.assertEqual(result.messages_added, 2)
        rows = self.conn.execute("SELECT sender, sender_confidence, content FROM messages WHERE thread_id=? ORDER BY seq",
                                 (result.thread_id,)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Customer", "structural", "Xin chào"), ("Page", "structural", "Chào bạn")])
        obs = self.conn.execute("SELECT COUNT(*) FROM inbox_fetch_observations").fetchone()[0]
        self.assertEqual(obs, 1)
        self.assertTrue(any("quarantined 1 unresolved" in m for _, m in logger.lines))

    def test_nothing_evidenced_is_needs_review(self):
        result, _ = self._run([self._msg("Chào bạn", "Unknown", "m2", conf="unknown", evidence=None)])
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_contradiction_rejects_whole_thread(self):
        messages = [self._msg("A", "Page", "dup"), self._msg("B", "Page", "dup")]
        result, _ = self._run(messages)
        self.assertEqual(result.status, "error")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_system_ad_event_survives_unresolved_human_message(self):
        event = self._msg('Hung Bui replied to an ad.', 'System', None,
                          kind='system_banner', source_links=[{'url': 'https://www.facebook.com/123/posts/456'}])
        unresolved = self._msg('Hello', 'Unknown', 'm1', conf='unknown', evidence=None)
        result, _ = self._run([event, unresolved])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)
        row = self.conn.execute('SELECT target_type,target_id FROM inbox_system_events').fetchone()
        self.assertEqual(tuple(row), ('post', '456'))
        self.assertEqual(self.conn.execute('SELECT fetch_history_complete FROM threads WHERE id=?', (result.thread_id,)).fetchone()[0], 0)

    def test_partial_fetch_keeps_marker_but_records_incomplete_history(self):
        # code:inbox-sync-skip-001:incomplete-history-marker
        # The card marker is recorded so Stage 1 can bound re-opens of a
        # permanently incomplete thread; the incompleteness itself is kept.
        result, _ = self._run([self._msg('Hello', 'Page', 'm1'),
                               self._msg('Unresolved', 'Unknown', 'm2', conf='unknown', evidence=None)])
        row = self.conn.execute('SELECT fetched_at, fetch_history_complete FROM threads WHERE id=?', (result.thread_id,)).fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1], 0)


# code:test-validation-001:msg-order-resequence
class TestResequenceByTime(unittest.TestCase):
    def test_backfilled_older_turn_moves_before_newer_rows_and_untimed_rows_do_not_jump(self):
        import sqlite3
        from fb_pipeline.inbox.l3_pipeline import resequence_thread_by_time
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, thread_id TEXT, sender TEXT, content TEXT, "
                     "message_timestamp TEXT, seq INTEGER, message_at TEXT, time_precision TEXT, "
                     "UNIQUE(thread_id, sender, content, message_timestamp, seq))")
        rows = [  # (content, seq, message_at, precision) as stored after an append-only crawl
            ("Chào Dinh", 0, "2026-09-20 15:40:06", "date_time"),
            ("Học phí ?", 1, "2026-09-20 15:40:07", "date_time"),
            ("banner", 2, None, "unknown"),
            ("Hoàn toàn miễn phí", 3, "2026-09-20 16:26:14", "date_time"),
            ("[attachment]", 4, "2026-09-20 15:40:07", "date_time"),   # admitted on a later crawl
        ]
        for content, seq, at, prec in rows:
            conn.execute("INSERT INTO messages (thread_id, sender, content, message_timestamp, seq, message_at, time_precision) "
                         "VALUES ('t', 'Page', ?, 'x', ?, ?, ?)", (content, seq, at, prec))
        changed = resequence_thread_by_time(conn.cursor(), "t")
        order = [r[0] for r in conn.execute("SELECT content FROM messages WHERE thread_id='t' ORDER BY seq").fetchall()]
        self.assertEqual(order, ["Chào Dinh", "Học phí ?", "banner", "[attachment]", "Hoàn toàn miễn phí"])
        self.assertEqual(changed, 2)
        self.assertEqual(resequence_thread_by_time(conn.cursor(), "t"), 0)
