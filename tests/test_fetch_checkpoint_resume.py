"""Crash recovery and coordinated worker queue regressions (no browser)."""
import dataclasses
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from fb_pipeline.inbox.l3_fetch_checkpoint import FetchCheckpoint
from fb_pipeline.browser.inbox.thread_worker import ThreadWorkerDeps
from fb_pipeline.inbox import l3_parallel_fetch as fetch
from fb_pipeline.contracts.l1_inbox import ThreadRecord
from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask, ThreadResult


def task(n):
    return ThreadTask(n, ThreadRecord('123', str(n), f'User {n}', '', [], n), 0, '', True)


def test_checkpoint_survives_crash_tail_and_excludes_committed_and_review(tmp_path):
    cp = FetchCheckpoint(tmp_path, {'page_id': '123'})
    for n in range(4):
        cp.add_task(task(n))
    cp.add_result(ThreadResult(0, '0', 'persisted', messages_added=10))
    cp.add_result(ThreadResult(1, '', 'click_verify_failed'))
    cp.add_result(ThreadResult(2, '', 'needs_review'))
    path = cp.path
    with pytest.raises(BlockingIOError):
        FetchCheckpoint(tmp_path, {'page_id': '123'}, resume=path)
    cp.close()
    with path.open('ab') as out:
        out.write(b'{"kind":')
    cp = FetchCheckpoint(tmp_path, {'page_id': '123'}, resume=path)
    assert [t.ordinal for t in cp.pending()] == [1, 3]
    cp.add_result(ThreadResult(1, '1', 'persisted'))
    cp.close()
    cp = FetchCheckpoint(tmp_path, {'page_id': '123'}, resume=path)
    assert [t.ordinal for t in cp.pending()] == [3]
    cp.close()
    with pytest.raises(ValueError, match='parameters differ'):
        FetchCheckpoint(tmp_path, {'page_id': '456'}, resume=path)


def test_resume_skips_discovery_and_persisted_tasks(tmp_path):
    metadata = dict(page_id='123', time_range='1080d', max_threads=100,
                    force_refresh=False, refresh_older_than_days=None,
                    allow_early_exit=True, target_total_messages=None)
    cp = FetchCheckpoint(tmp_path, metadata)
    cp.add_task(task(0))
    cp.add_task(task(1))
    cp.add_result(ThreadResult(0, '0', 'persisted', messages_added=10))
    stats = {k: ([] if k == 'processed_thread_ids' else 0) for k in fetch._LEGACY_STATS_KEYS}
    cp.append({'kind': 'discovery', 'value': dict(early_exit=False, stats=stats, existing_message_count=0)})
    path = cp.path
    cp.close()
    processed = []

    def worker(index, page_id, url, tasks, results, stop, deps, memory, logger, **kwargs):
        while True:
            t = tasks.get()
            if t is None:
                return
            processed.append(t.ordinal)
            results.put(ThreadResult(t.ordinal, str(t.ordinal), 'persisted', messages_added=5))

    with patch.object(fetch, 'discover_threads', side_effect=AssertionError('must not rediscover')):
        result = fetch.run_parallel_fetch(MagicMock(), '123', '1080d', 100, MagicMock(),
                                         MagicMock(), MagicMock(), ThreadWorkerDeps(None, None, None),
                                         workers=2, inbox_url='url', worker_loop=worker,
                                         resume_run=str(path), assignment_log_dir=str(tmp_path))
    assert processed == [1]
    assert result['fetch_complete']
    assert result['threads_processed'] == 2
    assert result['durable_pending'] == 0
    assert result['terminal_results'] == 2


def test_stopped_worker_does_not_consume_backlog():
    tasks = queue.Queue()
    for n in range(20):
        tasks.put(task(n))
    stop = threading.Event()
    stop.set()
    with patch.object(fetch, 'sync_playwright'):
        fetch.worker_main(1, '123', 'url', tasks, queue.Queue(), stop, MagicMock(), None,
                          MagicMock(), session_factory=MagicMock(), connection_factory=MagicMock())
    assert tasks.qsize() == 20
    assert tasks.unfinished_tasks == 20


def test_worker_waits_for_late_retry_from_inflight_peer():
    tasks = queue.Queue()
    tasks.put(task(0))
    active = tasks.get()  # Simulate another worker still processing after discovery ends.
    done = threading.Event()
    done.set()
    results = queue.Queue()
    processed = threading.Event()
    def process(*args, **kwargs):
        processed.set()
        return ThreadResult(0, '0', 'persisted')
    with patch.object(fetch, 'sync_playwright'), patch.object(fetch, '_call_process_thread_task', side_effect=process), patch.object(fetch, '_restamp_role'), patch.object(fetch, '_close_worker_tab'):
        worker = threading.Thread(target=fetch.worker_main, args=(1, '123', 'url', tasks, results,
                                  threading.Event(), MagicMock(), None, MagicMock()),
                                  kwargs=dict(session_factory=MagicMock(), connection_factory=MagicMock(),
                                              discovery_done=done, retry_q=tasks))
        worker.start()
        time.sleep(0.2)
        assert worker.is_alive()
        tasks.put(dataclasses.replace(active, attempt=2, failed_by='worker:2'))
        tasks.task_done()
        worker.join(3)
        assert not worker.is_alive()
    assert processed.is_set()
    assert results.get_nowait().status == 'persisted'
    assert tasks.unfinished_tasks == 0


def test_quality_reject_records_result_and_continues_remaining_tasks():
    tasks, results = queue.Queue(), queue.Queue()
    tasks.put(task(0))
    tasks.put(task(1))
    stop, done = threading.Event(), threading.Event()
    done.set()
    rejection = ThreadResult(0, '', 'needs_review', error='{"code": "fetch_evidence_needs_review"}')
    success = ThreadResult(1, '1', 'persisted')
    with patch.object(fetch, 'sync_playwright'), patch.object(fetch, '_call_process_thread_task', side_effect=[rejection, success]), patch.object(fetch, '_restamp_role'), patch.object(fetch, '_close_worker_tab'):
        fetch.worker_main(1, '123', 'url', tasks, results, stop, MagicMock(), None, MagicMock(),
                          session_factory=MagicMock(), connection_factory=MagicMock(),
                          discovery_done=done, retry_q=tasks)
    assert not stop.is_set()
    assert results.get_nowait().status == 'needs_review'
    assert results.get_nowait().status == 'persisted'
    assert tasks.empty()
