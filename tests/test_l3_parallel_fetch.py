import logging
"""Unit tests for the Phase 3 inbox parallel-fetch orchestrator/worker pool.

Fakes only: no real playwright, no live Chrome, no real DB file (in-memory
sqlite via ``fb_pipeline.persistence.l4_sqlite_store.setup_database``).

# code:test-inbox-parallel-fetch-001:orchestrator
# code:test-inbox-parallel-fetch-001:worker-main
"""
import os
import queue
import sqlite3
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.browser.inbox.thread_worker import ThreadWorkerDeps
from fb_pipeline.contracts.l1_inbox import ThreadRecord
from fb_pipeline.contracts.l1_inbox_tasks import ThreadResult, ThreadTask
from fb_pipeline.inbox import l3_parallel_fetch as parallel_fetch
from fb_pipeline.inbox.l3_parallel_fetch import MessageCounter, run_parallel_fetch, worker_main
from fb_pipeline.persistence.l4_sqlite_store import setup_database
from fb_pipeline.session.l2_facebook_block_gate import FacebookBlockGate, FacebookTemporaryBlockError


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, msg):
        self.lines.append(("info", msg))

    def warning(self, msg):
        self.lines.append(("warning", msg))

    def error(self, msg):
        self.lines.append(("error", msg))

    def debug(self, msg):
        self.lines.append(("debug", msg))


class _Page:
    """Fake page: only needs wait_for_timeout/evaluate for Stage-2 sidebar reset."""

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, *_args, **_kwargs):
        return {"found": False}


def _record(name="User", ordinal=0, page_id="123"):
    return ThreadRecord(
        page_id=page_id,
        thread_id=f"{page_id}_{name}",
        thread_name=name,
        preview_text="hi",
        thread_lines=["hi"],
        dom_index=ordinal,
    )


def _task(name="User", ordinal=0, psid_hint="", attempt=1):
    return ThreadTask(
        ordinal=ordinal,
        record=_record(name, ordinal),
        absolute_top=0.0,
        psid_hint=psid_hint,
        is_new=True,
        attempt=attempt,
    )


def _legacy_stats():
    return {
        "new_threads": 0, "new_messages": 0, "skipped_threads": 0, "threads_seen": 0,
        "threads_processed": 0, "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0,
        "processed_thread_ids": [], "threads_skipped_click_verify": 0,
        "sidebar_scrolls": 0, "sidebar_wait_ms": 0,
    }


class TestMessageCounter(unittest.TestCase):
    def test_add_reaches_target(self):
        counter = MessageCounter(target=5)
        self.assertFalse(counter.add(3))
        self.assertTrue(counter.add(2))

    def test_no_target_never_reaches(self):
        counter = MessageCounter(target=None)
        self.assertFalse(counter.add(1000))


class TestWorkerMain(unittest.TestCase):
    """(3) re-queue on TargetClosedError once, then error on second."""

    def test_requeue_once_then_error(self):
        task_q = queue.Queue()
        result_q = queue.Queue()
        stop_event = threading.Event()
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        call_count = {"n": 0}

        def fake_process(page, conn, task, deps, logger, is_first_thread=False, page_id=""):
            call_count["n"] += 1
            raise parallel_fetch.TargetClosedError("tab closed")

        class _Session:
            page = _Page()

        def session_factory(playwright, page_id, inbox_url, worker_index):
            return _Session()

        def connection_factory(memory_dir):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            setup_database(conn)
            return conn

        task_q.put(_task("A", 0))
        task_q.put(None)

        with patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process), \
             patch.object(parallel_fetch, "sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = object()
            worker_main(
                1, "123", "inbox_url", task_q, result_q, stop_event, deps,
                memory_dir=None, logger=_Logger(),
                session_factory=session_factory, connection_factory=connection_factory,
            )

        # attempt=1 (TargetClosed) -> requeue attempt=2 -> attempt=2 (TargetClosed) -> error result.
        self.assertEqual(call_count["n"], 2)
        results = []
        while True:
            try:
                results.append(result_q.get_nowait())
            except queue.Empty:
                break
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "error")
        self.assertEqual(results[0].worker, "worker:1")

    def test_temporary_block_stops_worker_without_requeue(self):
        task_q, result_q = queue.Queue(), queue.Queue()
        task_q.put(_task("A", 0))
        task_q.put(None)
        stop_event = threading.Event()
        gate = FacebookBlockGate()
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None,
                                block_gate=gate)

        class _Session:
            page = _Page()

        with patch.object(parallel_fetch, "process_thread_task",
                          side_effect=FacebookTemporaryBlockError("blocked")), \
             patch.object(parallel_fetch, "sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = object()
            worker_main(1, "123", "inbox_url", task_q, result_q, stop_event, deps,
                        memory_dir=None, logger=_Logger(), session_factory=lambda *_: _Session(),
                        connection_factory=lambda _: sqlite3.connect(":memory:"))

        result = result_q.get_nowait()
        self.assertEqual(result.status, "facebook_temporarily_blocked")
        self.assertTrue(stop_event.is_set())
        self.assertFalse(result.requeued)


class TestRunParallelFetchDispatch(unittest.TestCase):
    """(1) tasks are dispatched to workers BEFORE Stage 1 finishes."""

    def test_commits_orchestrator_connection_before_workers_and_stage1(self):
        """A pending PostgreSQL transaction must not block parallel UPSERTs."""
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

        def fake_worker_loop(*args, **kwargs):
            task_q = args[3]
            while task_q.get() is not None:
                pass

        def fake_discover(page, page_id, time_range, max_threads, received_conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            self.assertIs(received_conn, conn)
            self.assertGreaterEqual(conn.commits, 1)
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)
        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}):
            run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, _Logger(), lambda *a, **k: None, deps,
                workers=2, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=fake_worker_loop, assignment_log_dir=None,
            )
        self.assertGreaterEqual(conn.commits, 1)
        raw_conn.close()

    def test_dispatch_before_stage1_completes(self):
        received_event = threading.Event()
        release_event = threading.Event()
        seen_ordinals = []

        def fake_worker_loop(worker_index, page_id, inbox_url, task_q, result_q, stop_event,
                              deps, memory_dir, logger, session_factory=None,
                              connection_factory=None, counter=None):
            while True:
                task = task_q.get()
                if task is None:
                    break
                seen_ordinals.append(task.ordinal)
                received_event.set()
                release_event.wait(timeout=5)
                result_q.put(ThreadResult(ordinal=task.ordinal, thread_id=task.record.thread_id,
                                           status="persisted", messages_added=1, worker=f"worker:{worker_index}"))

        def fake_discover(page, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            on_task(_task("A", 0))
            on_task(_task("B", 1))
            on_task(_task("C", 2))
            # Block until a worker has actually received at least one task,
            # proving dispatch happens before Stage 1 (this function) returns.
            self.assertTrue(received_event.wait(timeout=5))
            release_event.set()
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)
        logger = _Logger()

        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}):
            stats = run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, logger, lambda *a, **k: None, deps,
                workers=2, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=fake_worker_loop, assignment_log_dir=None,
            )

        self.assertGreaterEqual(len(seen_ordinals), 1)
        self.assertEqual(stats["workers"], 2)
        conn.close()


class TestRunParallelFetchSentinels(unittest.TestCase):
    """(2) sentinel shutdown: N-1 workers each get exactly one None and exit."""

    def test_each_worker_gets_one_sentinel(self):
        sentinel_counts = {}
        lock = threading.Lock()

        def fake_worker_loop(worker_index, page_id, inbox_url, task_q, result_q, stop_event,
                              deps, memory_dir, logger, session_factory=None,
                              connection_factory=None, counter=None):
            count = 0
            while True:
                task = task_q.get()
                if task is None:
                    count += 1
                    break
                result_q.put(ThreadResult(ordinal=task.ordinal, thread_id=task.record.thread_id,
                                           status="persisted", messages_added=1, worker=f"worker:{worker_index}"))
            with lock:
                sentinel_counts[worker_index] = count

        def fake_discover(page, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            for i in range(5):
                on_task(_task(f"T{i}", i))
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}):
            stats = run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, _Logger(), lambda *a, **k: None, deps,
                workers=4, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=fake_worker_loop, assignment_log_dir=None,
            )

        self.assertEqual(len(sentinel_counts), 3)
        for count in sentinel_counts.values():
            self.assertEqual(count, 1)
        self.assertEqual(stats["workers"], 4)
        conn.close()


def _make_symmetric_worker_loop(fake_process):
    """A ``worker_loop`` that routes through the SAME (patched) module-level
    ``process_thread_task`` the orchestrator's own Stage-2 pass uses, so a
    task's outcome never depends on which "worker" (background thread or the
    orchestrator itself) happens to drain it. Never touches playwright or a
    real DB connection.
    """

    def loop(worker_index, page_id, inbox_url, task_q, result_q, stop_event,
             deps, memory_dir, logger, session_factory=None, connection_factory=None,
             counter=None):
        while True:
            task = task_q.get()
            if task is None:
                break
            if stop_event.is_set():
                continue
            result = fake_process(None, None, task, deps, logger, is_first_thread=False, page_id=page_id)
            result.worker = f"worker:{worker_index}"
            result_q.put(result)
            if counter is not None and counter.add(result.messages_added):
                stop_event.set()

    return loop


class TestRunParallelFetchTargetMessages(unittest.TestCase):
    """(4) target_total_messages sets stop_event; remaining tasks are abandoned.

    The orchestrator's own Stage-2 pass and the background worker both route
    through the same patched ``process_thread_task``, so results are
    deterministic regardless of which one drains a given task.
    """

    def test_target_reached_stops_and_abandons(self):
        def fake_process(page, conn, task, deps, logger, is_first_thread=False, page_id=""):
            return ThreadResult(ordinal=task.ordinal, thread_id=task.record.thread_id,
                                 status="persisted", messages_added=5)

        def fake_discover(page, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            for i in range(10):
                on_task(_task(f"T{i}", i))
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}), \
             patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process):
            stats = run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, _Logger(), lambda *a, **k: None, deps,
                workers=2, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=5, memory_dir=None,
                worker_loop=_make_symmetric_worker_loop(fake_process), assignment_log_dir=None,
            )

        self.assertLessEqual(stats["tasks_dispatched"], 10)
        self.assertLess(stats["threads_processed"], 10)
        self.assertEqual(
            stats["tasks_abandoned"],
            stats["tasks_dispatched"] - stats["threads_processed"],
        )
        conn.close()


class TestRunParallelFetchAggregation(unittest.TestCase):
    """(5) stats aggregation matches scrape_inbox semantics; ordinal order; locate_methods."""

    def test_aggregation_keys_and_order(self):
        def fake_process(page, conn, task, deps, logger, is_first_thread=False, page_id=""):
            if task.ordinal == 1:
                return ThreadResult(ordinal=1, thread_id="", status="click_verify_failed",
                                     locate_method="sidebar_identity")
            return ThreadResult(ordinal=task.ordinal, thread_id=f"tid-{task.ordinal}",
                                 status="persisted", messages_added=2, locate_method="direct_url")

        def fake_discover(page, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            # Dispatch out of "natural" completion order relative to ordinal 2 vs 0
            # to prove aggregation sorts processed_thread_ids by ordinal, not
            # arrival order.
            on_task(_task("C", 2))
            on_task(_task("A", 0))
            on_task(_task("B", 1))
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        record_fetch_calls = []

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}), \
             patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process):
            stats = run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, _Logger(),
                lambda *a, **k: record_fetch_calls.append(a), deps,
                workers=2, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=_make_symmetric_worker_loop(fake_process), assignment_log_dir=None,
            )

        self.assertEqual(stats["processed_thread_ids"], ["tid-0", "tid-2"])
        self.assertEqual(stats["threads_processed"], 2)
        self.assertEqual(stats["new_messages"], 4)
        self.assertEqual(stats["threads_skipped_click_verify"], 1)
        self.assertEqual(stats["locate_methods"], {"direct_url": 2, "sidebar_identity": 1})
        # The failed ordinal was handed off once and failed again on retry.
        self.assertEqual(stats["tasks_requeued"], 1)
        self.assertEqual([f["inbox_index"] for f in stats["failed_threads"]], [2])
        self.assertEqual(len(record_fetch_calls), 1)
        conn.close()


class TestRunParallelFetchEarlyExit(unittest.TestCase):
    """(8) early_exit from discover returns the legacy dict keys."""

    def test_early_exit_returns_legacy_keys(self):
        early_stats = {
            "new_threads": 0, "new_messages": 0, "skipped_threads": 3,
            "threads_seen": 3, "threads_processed": 0, "processed_thread_ids": [],
            "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0,
            "threads_skipped_click_verify": 0, "sidebar_scrolls": 0, "sidebar_wait_ms": 5,
            "method": "dynamic_cache_hit",
        }

        def fake_discover(page, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            return {"early_exit": True, "stats": dict(early_stats)}

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover):
            stats = run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, _Logger(), lambda *a, **k: None, deps,
                workers=3, inbox_url="inbox_url", skip_navigation=True, force_refresh=False,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=lambda *a, assignment_log_dir=None, **k: None,
            )

        for key, value in early_stats.items():
            self.assertEqual(stats[key], value)
        self.assertEqual(stats["workers"], 3)
        self.assertEqual(stats["tasks_dispatched"], 0)
        self.assertEqual(stats["tasks_abandoned"], 0)
        conn.close()


class TestCliWorkersOne(unittest.TestCase):
    """(6) workers==1 in fetch_messages calls _scrape_inbox and never starts threads."""

    def test_workers_one_never_touches_run_parallel_fetch(self):
        import tools.l5_fetch_fb_messages as cli

        def boom(*args, **kwargs):
            raise AssertionError("run_parallel_fetch must not be called when workers=1")

        with patch.object(cli, "run_parallel_fetch", side_effect=boom), \
             patch.object(cli, "attach_to_authorized_session") as mock_attach, \
             patch.object(cli, "_scrape_inbox", return_value={"new_threads": 0, "new_messages": 0,
                                                                "skipped_threads": 0, "threads_seen": 0,
                                                                "processed_thread_ids": []}) as mock_scrape, \
             patch.object(cli, "sync_playwright") as mock_sp, \
             patch.object(cli, "get_db_connection", return_value=sqlite3.connect(":memory:")):
            from unittest.mock import MagicMock
            mock_p = MagicMock()
            mock_sp.return_value.__enter__.return_value = mock_p
            mock_session = MagicMock()
            mock_session.selected_existing_tab = True
            mock_session.context.pages = [MagicMock()]
            mock_attach.return_value = mock_session

            result = cli.fetch_messages("123", "test_cred", use_cdp=True, workers=1)

        self.assertTrue(result["success"])
        mock_scrape.assert_called_once()


class TestCliWorkersClamp(unittest.TestCase):
    """(7) --workers clamps 0->1 and values above 4->4."""

    def test_clamp(self):
        import tools.l5_fetch_fb_messages as cli
        self.assertEqual(cli._clamp_workers(0, _Logger()), 1)
        self.assertEqual(cli._clamp_workers(20, _Logger()), 4)
        self.assertEqual(cli._clamp_workers(4, _Logger()), 4)
        self.assertEqual(cli._clamp_workers(1, _Logger()), 1)
        self.assertEqual(cli._clamp_workers(8, _Logger()), 4)


if __name__ == '__main__':
    unittest.main()


# code:test-inbox-parallel-fetch-001:assignment-log
class TestAssignmentLog(unittest.TestCase):
    def test_writes_run_header_and_one_row_per_result(self):
        import json, os, tempfile
        from fb_pipeline.inbox.l3_parallel_fetch import _aggregate_stats, _write_assignment_log
        from fb_pipeline.contracts.l1_inbox_tasks import ThreadResult

        results = [
            ThreadResult(ordinal=1, thread_id="t2", status="persisted", messages_added=2,
                         locate_method="direct_url", elapsed_ms=10, worker="worker:1", thread_name="B"),
            ThreadResult(ordinal=0, thread_id="t1", status="click_verify_failed",
                         locate_method="sidebar_identity", elapsed_ms=20, worker="orchestrator", thread_name="A"),
        ]
        stats = {}
        _aggregate_stats(stats, results, 2, 2, 100, 200)
        self.assertEqual([a["thread_name"] for a in stats["assignments"]], ["A", "B"])
        self.assertEqual(stats["assignments"][0]["worker"], "orchestrator")

        with tempfile.TemporaryDirectory() as d:
            path = _write_assignment_log(stats, "123", logging.getLogger("t"), log_dir=d)
            lines = [json.loads(l) for l in open(path, encoding="utf-8")]
        self.assertEqual(lines[0]["id"], "logs:inbox-parallel-fetch-001:run")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1]["thread_name"], "A")
        self.assertEqual(lines[2]["locate_method"], "direct_url")


class _ClosablePage(_Page):
    """_Page that records ``on('close')`` handlers and can be closed by a test."""

    def __init__(self):
        self._closed = False
        self._handlers = []
        self.context = type("_Ctx", (), {"pages": [self, object()]})()

    def on(self, event, handler):
        if event == "close":
            self._handlers.append(handler)

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True
        for h in list(self._handlers):
            h(self)


class TestOrchestratorTabClosed(unittest.TestCase):
    """# code:test-validation-001:orchestrator-close
    Retrospective [2026-09-16]: when the orchestrator tab is closed mid-run it
    must stop taking Stage 2 tasks instead of failing every queued thread."""

    def test_orchestrator_stops_and_workers_drain(self):
        page = _ClosablePage()
        orchestrator_calls = {"n": 0}

        def fake_process(p, conn, task, deps, logger, is_first_thread=False, page_id=""):
            if p is page:
                orchestrator_calls["n"] += 1
                raise RuntimeError("Target page, context or browser has been closed")
            return ThreadResult(ordinal=task.ordinal, thread_id=task.record.thread_id,
                                 status="persisted", messages_added=1)

        def fake_discover(p, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            for i in range(20):
                on_task(_task(f"T{i}", i))
            page.close()  # scheduler navigated/closed our tab during Stage 1
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}), \
             patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process):
            stats = run_parallel_fetch(
                page, "123", "7d", 50, conn, _Logger(), lambda *a, **k: None, deps,
                workers=2, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=_make_symmetric_worker_loop(fake_process), assignment_log_dir=None,
            )

        self.assertEqual(orchestrator_calls["n"], 0, "dead orchestrator must not attempt tasks")
        self.assertEqual(stats["threads_processed"], 20)
        self.assertEqual(stats["tasks_abandoned"], 0)
        conn.close()


class TestWorkerTabCleanup(unittest.TestCase):
    """# code:test-validation-001:tab-cleanup
    Worker role tabs spawned for a run are closed when the worker drains its queue."""

    def _run(self, session):
        task_q = queue.Queue()
        task_q.put(None)

        def connection_factory(memory_dir):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            setup_database(conn)
            return conn

        with patch.object(parallel_fetch, "sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = object()
            worker_main(
                1, "123", "inbox_url", task_q, queue.Queue(), threading.Event(),
                ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None),
                memory_dir=None, logger=_Logger(),
                session_factory=lambda *a, **k: session, connection_factory=connection_factory,
            )

    def test_worker_role_tab_is_closed(self):
        page = _ClosablePage()
        session = type("_S", (), {"page": page, "tab_role": "scan_inbox_worker:1", "context": page.context})()
        self._run(session)
        self.assertTrue(page.is_closed())

    def test_non_worker_tab_is_left_open(self):
        page = _ClosablePage()
        session = type("_S", (), {"page": page, "tab_role": "scan_inbox", "context": page.context})()
        self._run(session)
        self.assertFalse(page.is_closed())

    def test_last_tab_is_never_closed(self):
        page = _ClosablePage()
        page.context.pages = [page]
        session = type("_S", (), {"page": page, "tab_role": "scan_inbox_worker:1", "context": page.context})()
        self._run(session)
        self.assertFalse(page.is_closed())


# code:test-validation-001:parallel-requeue
class TestRequeueAndCircuitBreaker(unittest.TestCase):
    """Bug A (90d run 2026-09-17): a wedged worker must neither keep the
    tasks it fails nor keep draining the shared queue."""

    @staticmethod
    def _conn(_memory_dir=None):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        return conn

    def _session(self, name):
        page = _ClosablePage()
        page.gotos = []
        page.goto = lambda url, **k: page.gotos.append(url)
        return type("_S", (), {"page": page, "tab_role": f"scan_inbox_worker:{name}", "context": page.context})()

    def test_failed_task_is_requeued_to_another_worker(self):
        task_q, result_q, retry_q = queue.Queue(), queue.Queue(), queue.Queue()
        stop_event = threading.Event()
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)
        seen = []

        def fake_process(page, conn, task, deps, logger, is_first_thread=False, page_id=""):
            seen.append((task.record.thread_name, task.attempt, task.failed_by))
            if task.attempt == 1:
                return ThreadResult(ordinal=task.ordinal, thread_id="", status="click_verify_failed",
                                     locate_method="sidebar_identity")
            return ThreadResult(ordinal=task.ordinal, thread_id="tid", status="persisted",
                                 messages_added=1, locate_method="direct_url")

        task_q.put(_task("A", 0))
        task_q.put(None)
        s1 = self._session(1)
        with patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process), \
             patch.object(parallel_fetch, "sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = object()
            worker_main(1, "123", "inbox_url", task_q, result_q, stop_event, deps,
                        memory_dir=None, logger=_Logger(),
                        session_factory=lambda *a, **k: s1, connection_factory=self._conn,
                        retry_q=retry_q)

        # worker:1 failed it, handed it off, and must NOT retry its own reject.
        self.assertEqual(seen, [("A", 1, "")])
        first = result_q.get_nowait()
        self.assertEqual(first.status, "click_verify_failed")
        self.assertTrue(first.requeued)
        retry = retry_q.get_nowait()
        self.assertEqual((retry.attempt, retry.failed_by), (2, "worker:1"))
        retry_q.put(retry)

        # A second worker drains the retry before exiting.
        task_q.put(None)
        s2 = self._session(2)
        with patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process), \
             patch.object(parallel_fetch, "sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = object()
            worker_main(2, "123", "inbox_url", task_q, result_q, stop_event, deps,
                        memory_dir=None, logger=_Logger(),
                        session_factory=lambda *a, **k: s2, connection_factory=self._conn,
                        retry_q=retry_q)
        self.assertEqual(seen[-1], ("A", 2, "worker:1"))
        second = result_q.get_nowait()
        self.assertEqual((second.status, second.worker, second.attempt), ("persisted", "worker:2", 2))
        self.assertTrue(retry_q.empty())

    def test_worker_reloads_then_retires_and_leaves_queue(self):
        task_q, result_q, retry_q = queue.Queue(), queue.Queue(), queue.Queue()
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)

        def fake_process(page, conn, task, deps, logger, is_first_thread=False, page_id=""):
            return ThreadResult(ordinal=task.ordinal, thread_id="", status="click_verify_failed",
                                locate_method="sidebar_identity")

        n_tasks = 20
        for i in range(n_tasks):
            task_q.put(_task(f"T{i}", i))
        task_q.put(None)
        session = self._session(4)
        log = _Logger()
        with patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}), \
             patch.object(parallel_fetch, "sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = object()
            worker_main(4, "123", "inbox_url", task_q, result_q, threading.Event(), deps,
                        memory_dir=None, logger=log,
                        session_factory=lambda *a, **k: session, connection_factory=self._conn,
                        retry_q=retry_q)

        # Reloaded once at streak 3, retired at streak 6: only 6 tasks consumed.
        self.assertEqual(session.page.gotos, ["inbox_url"])
        results = []
        while not result_q.empty():
            results.append(result_q.get_nowait())
        self.assertEqual(len(results), parallel_fetch.WORKER_FAILURE_STREAK_RETIRE)
        self.assertEqual(task_q.qsize(), n_tasks - parallel_fetch.WORKER_FAILURE_STREAK_RETIRE + 1)
        self.assertEqual(retry_q.qsize(), parallel_fetch.WORKER_FAILURE_STREAK_RETIRE)
        self.assertTrue(any("retiring" in m for lvl, m in log.lines if lvl == "error"))
        self.assertTrue(session.page.is_closed())

    def test_orchestrator_takes_retries_after_workers_exit(self):
        """End-to-end: a worker that fails everything hands tasks to the
        orchestrator, which persists them; final stats count each thread once."""
        attempts = []

        def fake_process(page, conn, task, deps, logger, is_first_thread=False, page_id=""):
            attempts.append((task.ordinal, task.attempt))
            if task.attempt == 1:
                return ThreadResult(ordinal=task.ordinal, thread_id="", status="click_verify_failed",
                                     locate_method="sidebar_identity")
            return ThreadResult(ordinal=task.ordinal, thread_id=f"tid-{task.ordinal}",
                                 status="persisted", messages_added=1, locate_method="direct_url")

        def worker_loop(worker_index, page_id, inbox_url, task_q, result_q, stop_event,
                        deps, memory_dir, logger, session_factory=None, connection_factory=None,
                        counter=None, retry_q=None):
            while True:
                task = task_q.get()
                if task is None:
                    break
                r = fake_process(None, None, task, deps, logger)
                r.worker = f"worker:{worker_index}"
                r.thread_name = task.record.thread_name
                r.attempt = task.attempt
                parallel_fetch._requeue_failed(task, r, retry_q, r.worker, logger)
                result_q.put(r)

        def fake_discover(page, page_id, time_range, max_threads, conn, logger, record_fetch,
                          *, skip_navigation, force_refresh, allow_early_exit,
                          target_total_messages, on_task):
            for i in range(3):
                on_task(_task(f"T{i}", i))
            time.sleep(0.2)  # let the worker fail them before the orchestrator's pass 1
            return {"early_exit": False, "stats": _legacy_stats(), "existing_message_count": 0}

        conn = self._conn()
        deps = ThreadWorkerDeps(extract_ad_id_labels=None, extract_user_info=None, detect_city=None)
        log = _Logger()
        with patch.object(parallel_fetch, "discover_threads", side_effect=fake_discover), \
             patch.object(parallel_fetch, "reset_sidebar_to_top", return_value={"found": False}), \
             patch.object(parallel_fetch, "process_thread_task", side_effect=fake_process):
            stats = run_parallel_fetch(
                _Page(), "123", "7d", 50, conn, log, lambda *a, **k: None, deps,
                workers=2, inbox_url="inbox_url", skip_navigation=True, force_refresh=True,
                allow_early_exit=True, target_total_messages=None, memory_dir=None,
                worker_loop=worker_loop, assignment_log_dir=None,
            )
        self.assertEqual(stats["threads_processed"], 3)
        self.assertEqual(stats["processed_thread_ids"], ["tid-0", "tid-1", "tid-2"])
        self.assertEqual(stats["threads_skipped_click_verify"], 0)
        self.assertEqual(stats["tasks_requeued"], 3)
        self.assertEqual(stats["tasks_recovered_by_retry"], 3)
        self.assertEqual(stats["failed_threads"], [])
        self.assertEqual(stats["tasks_abandoned"], 0)
        self.assertEqual(len(stats["assignments"]), 6)
        self.assertTrue(any("Stage 2 summary" in m for _, m in log.lines))
        conn.close()
