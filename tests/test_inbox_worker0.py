"""Worker 0 fetches inline, checkpoints identity, and drains retired peers."""
import json
import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from fb_pipeline.browser.inbox.thread_worker import ThreadWorkerDeps, process_thread_task
from fb_pipeline.browser.inbox.thread_locator import LocateResult, locate_thread_in_sidebar
from fb_pipeline.contracts.l1_inbox import ThreadRecord
from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask, ThreadResult
from fb_pipeline.inbox import l3_parallel_fetch as fetch
from fb_pipeline.inbox.l3_fetch_checkpoint import FetchCheckpoint


def task(n, known=False):
    record = ThreadRecord('123', f'provisional-{n}', f'User {n}', 'hi', [], n,
                          sidebar_identity_key=f'card-{n}', selected_item_id=str(900+n) if known else '')
    return ThreadTask(n, record, 28000+n*80, '', True)


def discovery_stats():
    return dict(early_exit=False, existing_message_count=0,
                stats={k: ([] if k == 'processed_thread_ids' else 0) for k in fetch._LEGACY_STATS_KEYS})


def page():
    p = MagicMock()
    p.is_closed.return_value = False
    return p


def test_single_tab_fetches_before_next_discovery_and_saves_id(tmp_path):
    events = []
    p = page()
    def process(passed_page, conn, t, deps, logger, **kwargs):
        assert passed_page is p
        assert kwargs['discovery_viewport']
        assert threading.current_thread() is threading.main_thread()
        events.append(('fetch', t.ordinal))
        t.record.selected_item_id = str(900+t.ordinal)
        deps.on_identity(t)
        return ThreadResult(t.ordinal, t.record.thread_id, 'persisted', messages_added=1)
    def discover(*args, on_task, **kwargs):
        for n in range(3):
            events.append(('discover', n))
            on_task(task(n))
        return discovery_stats()
    with patch.object(fetch, 'discover_threads', side_effect=discover), patch.object(fetch, 'process_thread_task', side_effect=process), patch.object(fetch.threading, 'Thread', side_effect=AssertionError('no extra thread')):
        result = fetch.run_parallel_fetch(p, '123', '7d', 10, MagicMock(), MagicMock(), MagicMock(),
                    ThreadWorkerDeps(None, None, None), workers=1, inbox_url='url', assignment_log_dir=str(tmp_path))
    assert events == [(kind, n) for n in range(3) for kind in ('discover', 'fetch')]
    assert result['fetch_complete'] and result['threads_processed'] == 3
    assert {r['worker'] for r in result['assignments']} == {'worker:0'}
    entries = [json.loads(line) for line in open(result['checkpoint_path'])]
    for n in range(3):
        assert any(e['kind'] == 'task' and e['value']['ordinal'] == n and
                   e['value']['record']['selected_item_id'] == str(900+n) for e in entries)


def test_identity_is_checkpointed_before_history_scan(tmp_path):
    cp = FetchCheckpoint(tmp_path, {'page_id': '123'})
    t = task(0)
    cp.add_task(t)
    deps = ThreadWorkerDeps(None, None, None, on_identity=cp.add_task)
    def history(*args):
        assert cp.tasks[0]['record']['selected_item_id'] == '9876'
        raise RuntimeError('tab lost during history')
    with patch('fb_pipeline.browser.inbox.thread_worker.locate_thread_in_sidebar', return_value=LocateResult(True, 'sidebar_identity', 1, '', '')) as locate, patch('fb_pipeline.browser.inbox.thread_worker.verify_thread_switch', return_value=('9876', True)), patch('fb_pipeline.browser.inbox.thread_worker.extract_ad_context', return_value=''), patch('fb_pipeline.browser.inbox.thread_worker.scroll_up_message_panel', side_effect=history):
        with pytest.raises(RuntimeError, match='history'):
            process_thread_task(page(), MagicMock(), t, deps, MagicMock(), page_id='123', discovery_viewport=True)
        assert locate.call_args.kwargs == {'visible_only': True}
    path = cp.path
    cp.close()
    cp = FetchCheckpoint(tmp_path, {'page_id': '123'}, resume=path)
    assert list(cp.pending())[0].record.selected_item_id == '9876'
    cp.close()


def test_visible_only_lookup_never_scrolls_or_navigates():
    p = page()
    p.evaluate.return_value = False
    with patch('fb_pipeline.browser.inbox.thread_locator.scroll_sidebar_and_wait', side_effect=AssertionError('must preserve discovery cursor')):
        result = locate_thread_in_sidebar(p, task(0), MagicMock(), visible_only=True)
    assert not result.clicked and result.attempts == 3
    p.goto.assert_not_called()
    assert not any('scrollTop = pos' in c.args[0] for c in p.evaluate.call_args_list)


def test_worker0_drains_resume_when_all_peers_retire(tmp_path):
    metadata = dict(page_id='123', time_range='7d', max_threads=10, force_refresh=False,
                    refresh_older_than_days=None, allow_early_exit=True, target_total_messages=None)
    cp = FetchCheckpoint(tmp_path, metadata)
    for n in range(4):
        cp.add_task(task(n, known=True))
    cp.add_result(ThreadResult(0, 'saved', 'persisted'))
    cp.append({'kind': 'discovery', 'value': discovery_stats()})
    path = cp.path
    cp.close()
    def process(p, conn, t, deps, logger, **kwargs):
        assert not kwargs.get('discovery_viewport')
        return ThreadResult(t.ordinal, str(t.ordinal), 'persisted')
    def retired(*args, discovery_done=None, **kwargs):
        return
    with patch.object(fetch, 'discover_threads', side_effect=AssertionError('no rediscovery')), patch.object(fetch, 'process_thread_task', side_effect=process):
        result = fetch.run_parallel_fetch(page(), '123', '7d', 10, MagicMock(), MagicMock(), MagicMock(),
                    ThreadWorkerDeps(None, None, None), workers=3, inbox_url='url', resume_run=str(path), assignment_log_dir=str(tmp_path), worker_loop=retired)
    assert result['fetch_complete']
    assert result['threads_processed'] == 4 and result['durable_pending'] == 0
    assert [r['worker'] for r in result['assignments'] if r['inbox_index'] > 1] == ['worker:0'] * 3


def test_worker0_and_background_tabs_share_known_id_tasks(tmp_path):
    calls = []
    def process(p, conn, t, deps, logger, **kwargs):
        calls.append((t.ordinal, threading.current_thread().name))
        return ThreadResult(t.ordinal, str(t.ordinal), 'persisted')
    def background(index, pid, url, tasks, results, stop, deps, memory, logger, discovery_done=None, **kwargs):
        while True:
            try:
                t = tasks.get(timeout=.01)
            except queue.Empty:
                if discovery_done.is_set() and tasks.unfinished_tasks == 0:
                    return
                continue
            try:
                r = process(None, None, t, deps, logger)
                r.worker = f'worker:{index}'
                results.put(r)
            finally:
                tasks.task_done()
    def discover(*args, on_task, **kwargs):
        for n in range(6):
            on_task(task(n, known=True))
        # Ensure peers have handled their dispatched share before the drain.
        # timeout avoids hiding a scheduler deadlock in this regression.
        import time
        deadline = time.monotonic() + 2
        while len(calls) < 6 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(calls) == 6
        return discovery_stats()
    with patch.object(fetch, 'discover_threads', side_effect=discover), patch.object(fetch, 'process_thread_task', side_effect=process):
        result = fetch.run_parallel_fetch(page(), '123', '7d', 10, MagicMock(), MagicMock(), MagicMock(),
                    ThreadWorkerDeps(None, None, None), workers=3, inbox_url='url', assignment_log_dir=str(tmp_path), worker_loop=background)
    assert result['fetch_complete']
    assert len(calls) == 6
    assert any(name == threading.main_thread().name for _, name in calls)
    assert any(name.startswith('inbox-worker-') for _, name in calls)


@pytest.mark.parametrize('args,expected', [([], 1), (['--worker', '2'], 2), (['--workers', '3'], 3)])
def test_cli_defaults_and_alias(args, expected):
    import tools.l5_fetch_fb_messages as cli
    with patch('sys.argv', ['fetch', '--pageId', '123', *args]), patch.object(cli, 'fetch_messages', return_value={'success': True}) as call:
        cli.main()
    assert call.call_args.kwargs['workers'] == expected


@pytest.mark.parametrize('stop', [False, True])
def test_discovery_tracks_resolved_identity_and_honors_worker0_stop(stop):
    import sqlite3
    from contextlib import ExitStack
    from fb_pipeline.browser import l3_inbox
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    card = dict(name='User A', text='User A\nhi\nToday', sidebarTimeText='Today',
                domIndex=0, sidebarIdentityKey='original-card', selectedItemId='', absoluteTop=0)
    seen = []
    def on_task(t):
        seen.append(t)
        t.record.selected_item_id = '900'
        return not stop
    with ExitStack() as stack:
        stack.enter_context(patch.object(l3_inbox, 'wait_for_inbox_shell', return_value=''))
        stack.enter_context(patch.object(l3_inbox, 'wait_for_initial_threads', return_value={'elapsed_ms': 0}))
        stack.enter_context(patch.object(l3_inbox, 'reset_sidebar_to_top', return_value={'found': True, 'after': 0}))
        stack.enter_context(patch.object(l3_inbox, 'extract_visible_threads', side_effect=[[card], [dict(card, selectedItemId='900')], []]))
        scroll = stack.enter_context(patch.object(l3_inbox, 'scroll_sidebar_and_wait', return_value={'elapsed_ms': 0}))
        result = l3_inbox.discover_threads(page(), '123', '7d', 10, conn, MagicMock(), MagicMock(),
                                           force_refresh=True, skip_navigation=True, on_task=on_task)
    conn.close()
    assert len(seen) == 1
    assert result['interrupted'] is stop
    if stop:
        scroll.assert_not_called()
    else:
        assert result['stats']['threads_skipped_duplicate'] == 1
