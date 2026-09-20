"""Orchestrator + worker pool for the inbox parallel-fetch pipeline.

Implements docs/architect/inbox-fetch-pipeline.md sections 3, 6, 7 and 8.
``--workers 1`` stays on the legacy sequential ``scrape_inbox`` path (see
``tools/l5_fetch_fb_messages.py``); this module is only exercised for
``--workers >= 2``.

# code:inbox-parallel-fetch-001:orchestrator
"""
import collections
import dataclasses
import inspect
import json
from datetime import datetime
import os
import queue
import threading
import time
from typing import Callable, Optional

from playwright.sync_api import sync_playwright

from fb_pipeline.browser.inbox.scroll_helpers import reset_sidebar_to_top
from fb_pipeline.browser.inbox.thread_worker import ThreadWorkerDeps, process_thread_task
from fb_pipeline.browser.l3_inbox import discover_threads
from fb_pipeline.contracts.l1_inbox_tasks import ThreadResult, ThreadTask
from fb_pipeline.contracts.l1_session import WORKER_TAB_ROLE_PREFIX
from fb_pipeline.persistence.db import connect as connect_database

try:  # pragma: no cover - exact import path depends on installed playwright
    from playwright._impl._errors import TargetClosedError
except Exception:  # pragma: no cover
    class TargetClosedError(Exception):
        """Fallback stand-in when playwright's internal error type moves."""


_LEGACY_STATS_KEYS = (
    "new_threads", "new_messages", "skipped_threads", "threads_seen",
    "threads_processed", "threads_skipped_duplicate", "threads_skipped_cutoff",
    "processed_thread_ids", "threads_skipped_click_verify",
    "sidebar_scrolls", "sidebar_wait_ms",
)


def _is_target_closed(exc: Exception) -> bool:
    return isinstance(exc, TargetClosedError) or exc.__class__.__name__ == "TargetClosedError"


# code:inbox-parallel-fetch-001:requeue
# Retrospective [2026-09-17, 90d run]: worker:4's sidebar wedged at
# scrollTop=39705 after thread #499. It kept pulling tasks and failed 153
# threads in a row at ~6 s each while the four healthy tabs persisted theirs,
# so 204/754 threads (27%) were never saved. Two guards now apply:
#   * a failed locate is re-queued once onto ``retry_q`` for a *different*
#     consumer (``ThreadTask.failed_by``);
#   * each consumer tracks its failure streak: at ``RECOVER`` it reloads its
#     inbox tab (fresh virtualized sidebar); at ``RETIRE`` it stops taking
#     tasks so it can no longer starve the pool.
MAX_TASK_ATTEMPTS = 2
WORKER_FAILURE_STREAK_RECOVER = 3
WORKER_FAILURE_STREAK_RETIRE = 6
_FAILED_STATUSES = ("click_verify_failed", "locate_failed", "error")


class _WorkerHealth:
    """Consecutive-failure circuit breaker shared by workers and the orchestrator."""

    def __init__(self, recover_at: int = WORKER_FAILURE_STREAK_RECOVER,
                 retire_at: int = WORKER_FAILURE_STREAK_RETIRE):
        self.streak = 0
        self.recoveries = 0
        self.recover_at = recover_at
        self.retire_at = retire_at

    def record(self, status: str) -> Optional[str]:
        """Return ``"recover"``, ``"retire"`` or ``None`` after *status*."""
        if status not in _FAILED_STATUSES:
            self.streak = 0
            return None
        self.streak += 1
        if self.streak >= self.retire_at:
            return "retire"
        if self.streak == self.recover_at:
            return "recover"
        return None


def _recover_tab(session, inbox_url: str, log) -> bool:
    """Reload the consumer's inbox tab so Meta rebuilds the virtualized sidebar."""
    page = getattr(session, "page", None)
    if page is None:
        return False
    try:
        page.goto(inbox_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        _restamp_role(session)
        try:
            reset_sidebar_to_top(page, log)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        log.warning(f"Recovered tab after failure streak (reloaded {inbox_url[:80]}).")
        return True
    except Exception as exc:
        log.error(f"Tab recovery failed: {exc}")
        return False


def _requeue_failed(task: ThreadTask, result: ThreadResult, retry_q, worker_name: str, log) -> bool:
    """Hand a failed task to another consumer once. Returns True when re-queued."""
    if retry_q is None or result.status not in _FAILED_STATUSES:
        return False
    if task.attempt >= MAX_TASK_ATTEMPTS:
        return False
    retry_q.put(dataclasses.replace(task, attempt=task.attempt + 1, failed_by=worker_name))
    result.requeued = True
    log.warning(
        f"Re-queued thread '{task.record.thread_name}' (inbox #{task.ordinal + 1}) after "
        f"status={result.status} for another worker (attempt {task.attempt + 1}/{MAX_TASK_ATTEMPTS})."
    )
    return True


def _accepts_kwarg(fn, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _next_retry(retry_q, worker_name: str):
    """Non-blocking pull from ``retry_q`` skipping tasks this consumer failed itself.

    A task we failed is put back for someone else and the drain stops (so a
    lone consumer never spins on its own rejects); the orchestrator's final
    pass ignores ``failed_by`` and takes everything.
    """
    if retry_q is None:
        return None
    try:
        task = retry_q.get_nowait()
    except queue.Empty:
        return None
    if task.failed_by == worker_name:
        retry_q.put(task)
        return None
    return task


class _PrefixedLogger:
    """Thin ``[worker:i]``-prefixed wrapper around any duck-typed logger."""

    def __init__(self, logger, prefix: str):
        self._logger = logger
        self._prefix = prefix

    def _emit(self, level: str, msg):
        fn = getattr(self._logger, level, None)
        if callable(fn):
            fn(f"{self._prefix} {msg}")

    def info(self, msg):
        self._emit("info", msg)

    def warning(self, msg):
        self._emit("warning", msg)

    def error(self, msg):
        self._emit("error", msg)

    def debug(self, msg):
        self._emit("debug", msg)


class MessageCounter:
    """Lock-guarded running total of persisted messages vs. an optional target.

    # code:inbox-parallel-fetch-001:stop-rules
    """

    def __init__(self, target: Optional[int] = None):
        self.lock = threading.Lock()
        self.total = 0
        self.target = target

    def add(self, n: int) -> bool:
        """Add ``n`` (may be 0) and return True if the target is now met."""
        with self.lock:
            if n:
                self.total += n
            return self.target is not None and self.total >= self.target


def _call_process_thread_task(page, conn, task: ThreadTask, deps: ThreadWorkerDeps, logger,
                               is_first_thread: bool, page_id: str) -> ThreadResult:
    """Call Phase 2's ``process_thread_task``, tolerating its pre-Phase-2 signature.

    Phase 2 adds a ``page_id`` kwarg; until it lands we fall back without it.
    """
    try:
        return process_thread_task(page, conn, task, deps, logger,
                                    is_first_thread=is_first_thread, page_id=page_id)
    except TypeError:
        return process_thread_task(page, conn, task, deps, logger, is_first_thread=is_first_thread)


def _resolve_psid_hint(conn, page_id: str, thread_name: str) -> str:
    """Call Phase 2's ``resolve_psid_hint`` if it exists yet, else return ""."""
    try:
        from fb_pipeline.persistence.l4_sqlite_store import resolve_psid_hint
    except ImportError:
        return ""
    try:
        return resolve_psid_hint(conn, page_id, thread_name) or ""
    except Exception:
        return ""


def _default_get_db_connection(memory_dir):
    """Open a worker connection without rerunning schema migrations.

    The orchestrator has already opened and initialized this database before
    it starts the pool.  Running DDL from every worker races on SQLite's
    exclusive schema lock and can terminate otherwise healthy workers.
    """
    return connect_database(memory_dir, initialize=False)


def _default_session_factory(playwright, page_id: str, inbox_url: str, worker_index: int):
    """Call Phase 2's ``attach_worker_session`` if it exists yet, else fall back
    to ``attach_to_authorized_session`` with an equivalent role tab."""
    try:
        from fb_pipeline.session.l2_bootstrap import attach_worker_session
    except ImportError:
        from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session
        return attach_to_authorized_session(
            playwright, page_id, inbox_url, prefer_new_tab=True,
            tab_role=f"scan_inbox_worker:{worker_index}",
        )
    return attach_worker_session(playwright, page_id, inbox_url, worker_index)


def worker_main(worker_index: int, page_id: str, inbox_url: str, task_q: "queue.Queue",
                 result_q: "queue.Queue", stop_event: threading.Event, deps: ThreadWorkerDeps,
                 memory_dir, logger, session_factory: Optional[Callable] = None,
                 connection_factory: Optional[Callable] = None,
                 counter: Optional[MessageCounter] = None,
                 retry_q: Optional["queue.Queue"] = None) -> None:
    """Own ``playwright``/CDP/tab/conn for this thread; pull tasks FIFO until a
    ``None`` sentinel; re-attach up to twice on ``TargetClosedError``; hand
    locate failures to ``retry_q`` and drain other workers' retries before
    exiting; reload the tab after 3 consecutive failures, retire after 6.

    # code:inbox-parallel-fetch-001:worker-main
    """
    log = _PrefixedLogger(logger, f"[worker:{worker_index}]")
    factory = session_factory or _default_session_factory
    make_conn = connection_factory or _default_get_db_connection

    with sync_playwright() as p:
        try:
            session = factory(p, page_id, inbox_url, worker_index)
            log.info(
                f"Attached tab created={getattr(session, 'created_tab', None)} "
                f"reused={getattr(session, 'selected_existing_tab', None)} url={getattr(session.page, 'url', '')[:120]}"
            )
            try:
                session.page.on("close", lambda _p, _log=log: _log.error("Worker tab received 'close' event"))
            except Exception:
                pass
        except Exception as exc:
            log.error(f"Failed to attach worker session: {exc}")
            session = None

        conn = make_conn(memory_dir)
        reattach_count = 0
        is_first_task = True
        worker_name = f"worker:{worker_index}"
        # Re-attach retries stay local to this worker instead of going back
        # onto the shared task_q, so a retry can never be starved by a
        # sentinel that is already queued ahead of it (sentinels are only
        # enqueued after Stage 1 finishes dispatching every real task).
        pending_retries: "collections.deque" = collections.deque()
        health = _WorkerHealth()
        retired = False
        draining_retries = False

        try:
            while True:
                from_shared_queue = False
                if pending_retries:
                    task = pending_retries.popleft()
                elif draining_retries:
                    task = _next_retry(retry_q, worker_name)
                    if task is None:
                        break
                else:
                    task = task_q.get()
                    from_shared_queue = True
                try:
                    if task is None:
                        # Sentinel: our share of Stage 1 is done. Before leaving,
                        # take over retries other consumers handed off.
                        draining_retries = True
                        continue
                    if stop_event.is_set():
                        continue

                    log.debug(json.dumps({
                        "id": "logs:inbox-parallel-fetch-001:task",
                        **task.to_log_dict(),
                    }))
                    log.info(
                        f"Picked thread '{task.record.thread_name}' (inbox #{task.ordinal + 1}, "
                        f"attempt {task.attempt}, hint={'psid' if (task.psid_hint or task.record.selected_item_id) else 'none'})"
                    )

                    t0 = time.monotonic()

                    if session is None:
                        result = ThreadResult(
                            ordinal=task.ordinal, thread_id="", status="error",
                            error="worker session unavailable",
                        )
                    else:
                        try:
                            result = _call_process_thread_task(
                                session.page, conn, task, deps, log, is_first_task, page_id
                            )
                            is_first_task = False
                        except Exception as exc:
                            if _is_target_closed(exc):
                                if reattach_count < 2:
                                    reattach_count += 1
                                    log.warning(
                                        f"Tab closed, re-attaching (attempt {reattach_count}/2): {exc}"
                                    )
                                    try:
                                        session = factory(p, page_id, inbox_url, worker_index)
                                        is_first_task = True
                                    except Exception as reattach_exc:
                                        log.error(f"Re-attach failed: {reattach_exc}")
                                        session = None
                                if task.attempt < 2:
                                    pending_retries.append(dataclasses.replace(task, attempt=task.attempt + 1))
                                    continue
                                result = ThreadResult(
                                    ordinal=task.ordinal, thread_id="", status="error", error=str(exc)
                                )
                            else:
                                result = ThreadResult(
                                    ordinal=task.ordinal, thread_id="", status="error", error=str(exc)
                                )

                    result.worker = worker_name
                    result.thread_name = task.record.thread_name
                    result.attempt = task.attempt
                    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
                    _requeue_failed(task, result, retry_q, worker_name, log)
                    result_q.put(result)
                    log.info(
                        f"Finished thread '{task.record.thread_name}' (inbox #{task.ordinal + 1}) "
                        f"status={result.status} via={result.locate_method or '-'} "
                        f"messages_added={result.messages_added} in {result.elapsed_ms}ms"
                        f"{' requeued=1' if result.requeued else ''}"
                    )
                    log.debug(json.dumps({
                        "id": "logs:inbox-parallel-fetch-001:result",
                        **result.to_log_dict(),
                    }))

                    if counter is not None and counter.add(result.messages_added):
                        stop_event.set()
                    _restamp_role(session)

                    action = health.record(result.status)
                    if action == "recover" and session is not None:
                        log.warning(
                            f"{health.streak} consecutive failures; reloading tab to reset the sidebar."
                        )
                        if _recover_tab(session, inbox_url, log):
                            is_first_task = True
                    elif action == "retire":
                        log.error(
                            f"{health.streak} consecutive failures after recovery; retiring this worker "
                            f"so remaining tasks go to healthy tabs."
                        )
                        retired = True
                        break
                finally:
                    if from_shared_queue:
                        task_q.task_done()
        finally:
            if retired:
                log.error("Worker retired early (circuit breaker).")
            try:
                conn.close()
            except Exception:
                pass
            _close_worker_tab(session, log)


def _page_is_closed(page) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return False


# code:inbox-parallel-fetch-001:tab-cleanup
def _close_worker_tab(session, log) -> None:
    """Close this worker's role tab once its queue is drained.

    Retrospective [2026-09-16]: role tabs were left open "for reuse", but the
    scheduler's own navigation wipes the DOM role marker, so every run created
    fresh tabs and Chrome accumulated 13+ inbox tabs. Close carefully: only a
    tab tagged with *our* worker role, never the last tab in the context
    (closing it would terminate the user's Chrome), and swallow CDP errors
    because the tab may already be gone.
    """
    if session is None:
        return
    page = getattr(session, "page", None)
    role = getattr(session, "tab_role", None) or ""
    if page is None or not role.startswith(WORKER_TAB_ROLE_PREFIX):
        return
    try:
        if _page_is_closed(page):
            return
        context = getattr(session, "context", None) or page.context
        if len(context.pages) <= 1:
            log.warning("Not closing worker tab: it is the last tab in the browser.")
            return
        page.close()
        log.info(f"Closed worker tab (role={role}).")
    except Exception as exc:
        log.warning(f"Could not close worker tab (role={role}): {exc}")


def _restamp_role(session) -> None:
    """L0 navigation wipes the DOM role marker; restore it so the tab is
    found again on the next run (see ``l2_bootstrap.stamp_tab_role``)."""
    if session is None:
        return
    try:
        from fb_pipeline.session.l2_bootstrap import stamp_tab_role
    except ImportError:
        return
    stamp_tab_role(getattr(session, "page", None), getattr(session, "tab_role", None))


# code:inbox-parallel-fetch-001:run-summary
def _log_stage2_summary(stats: dict, logger) -> None:
    """One INFO block at the end of Stage 2 so failures never have to be
    counted by grepping 35k lines of JSON again."""
    failed = stats.get("failed_threads") or []
    per_worker = ", ".join(
        f"{e['worker']}: ok={e['processed']} failed={e['failed']}" for e in stats.get("per_worker") or []
    )
    logger.info(
        f"Stage 2 summary: persisted={stats.get('threads_processed', 0)} "
        f"failed={len(failed)} requeued={stats.get('tasks_requeued', 0)} "
        f"recovered_by_retry={stats.get('tasks_recovered_by_retry', 0)} "
        f"abandoned={stats.get('tasks_abandoned', 0)} "
        f"locate={stats.get('locate_methods')} | {per_worker}"
    )
    if failed:
        preview = "; ".join(f"#{f['inbox_index']} {f['thread_name']} ({f['status']}, {f['worker']})" for f in failed[:25])
        logger.warning(f"Stage 2 failed threads ({len(failed)}): {preview}{' ...' if len(failed) > 25 else ''}")


# code:inbox-parallel-fetch-001:assignment-log
def _write_assignment_log(stats: dict, page_id: str, logger, log_dir: str = "./logs/parallel-fetch") -> str:
    """Persist one JSONL file per run listing which tab handled which thread,
    so a job can be reviewed later without grepping the main log."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"assignments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": "logs:inbox-parallel-fetch-001:run",
                "page_id": page_id,
                "workers": stats.get("workers"),
                "tasks_dispatched": stats.get("tasks_dispatched"),
                "tasks_abandoned": stats.get("tasks_abandoned"),
                "tasks_requeued": stats.get("tasks_requeued"),
                "tasks_recovered_by_retry": stats.get("tasks_recovered_by_retry"),
                "failed_threads": stats.get("failed_threads"),
                "locate_methods": stats.get("locate_methods"),
                "stage1_ms": stats.get("stage1_ms"),
                "stage2_ms": stats.get("stage2_ms"),
                "per_worker": stats.get("per_worker"),
            }, ensure_ascii=False) + "\n")
            for row in stats.get("assignments") or []:
                fh.write(json.dumps({"id": "logs:inbox-parallel-fetch-001:assignment", **row}, ensure_ascii=False) + "\n")
        stats["assignment_log"] = path
        logger.info(f"Assignment log written to {path}")
        return path
    except Exception as exc:
        logger.warning(f"Could not write assignment log: {exc}")
        return ""


def _drain_all(result_q: "queue.Queue") -> list:
    drained = []
    while True:
        try:
            drained.append(result_q.get_nowait())
        except queue.Empty:
            break
    return drained


def _final_results(results: list) -> list:
    """One result per ordinal: the persisted attempt if any, else the last attempt."""
    by_ordinal: dict = {}
    for r in results:
        cur = by_ordinal.get(r.ordinal)
        if cur is None or r.status == "persisted" or (cur.status != "persisted" and r.attempt >= cur.attempt):
            by_ordinal[r.ordinal] = r
    return sorted(by_ordinal.values(), key=lambda r: r.ordinal)


def _aggregate_stats(stats: dict, results: list, tasks_dispatched: int, workers: int,
                      stage1_ms: int, stage2_ms: int) -> dict:
    final = _final_results(results)
    persisted = [r for r in final if r.status == "persisted"]

    stats["threads_processed"] = stats.get("threads_processed", 0) + len(persisted)
    stats["processed_thread_ids"] = list(stats.get("processed_thread_ids") or []) + [
        r.thread_id for r in persisted
    ]
    stats["new_messages"] = stats.get("new_messages", 0) + sum(r.messages_added for r in persisted)
    stats["threads_skipped_click_verify"] = stats.get("threads_skipped_click_verify", 0) + sum(
        1 for r in final if r.status in ("click_verify_failed", "locate_failed")
    )
    # code:inbox-parallel-fetch-001:requeue
    stats["tasks_requeued"] = sum(1 for r in results if r.requeued)
    stats["tasks_recovered_by_retry"] = sum(
        1 for r in persisted if r.attempt > 1
    )
    stats["failed_threads"] = [
        {"inbox_index": r.ordinal + 1, "thread_name": r.thread_name, "worker": r.worker,
         "status": r.status, "attempts": r.attempt, "error": r.error}
        for r in final if r.status in _FAILED_STATUSES
    ]

    locate_methods: dict = {}
    for r in final:
        if r.locate_method:
            locate_methods[r.locate_method] = locate_methods.get(r.locate_method, 0) + 1

    per_worker_map: dict = {}
    for r in results:
        w = r.worker or "unknown"
        entry = per_worker_map.setdefault(
            w, {"worker": w, "processed": 0, "failed": 0, "errors": 0, "requeued": 0, "elapsed_ms": 0}
        )
        if r.status == "persisted":
            entry["processed"] += 1
        if r.status in _FAILED_STATUSES:
            entry["failed"] += 1
        if r.status == "error":
            entry["errors"] += 1
        if r.requeued:
            entry["requeued"] += 1
        entry["elapsed_ms"] += r.elapsed_ms

    stats["workers"] = workers
    stats["tasks_dispatched"] = tasks_dispatched
    stats["tasks_abandoned"] = max(0, tasks_dispatched - len(final))
    stats["locate_methods"] = locate_methods
    # code:inbox-parallel-fetch-001:assignment-log
    stats["assignments"] = [
        {
            "inbox_index": r.ordinal + 1,
            "thread_name": r.thread_name,
            "thread_id": r.thread_id,
            "worker": r.worker,
            "status": r.status,
            "locate_method": r.locate_method,
            "messages_added": r.messages_added,
            "elapsed_ms": r.elapsed_ms,
            "error": r.error,
            "attempt": r.attempt,
            "requeued": r.requeued,
        }
        for r in sorted(results, key=lambda r: (r.ordinal, r.attempt))
    ]
    stats["stage1_ms"] = stage1_ms
    stats["stage2_ms"] = stage2_ms
    stats["per_worker"] = sorted(per_worker_map.values(), key=lambda e: e["worker"])
    return stats


def run_parallel_fetch(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                        record_fetch, deps: ThreadWorkerDeps, *, workers: int, inbox_url: str,
                        skip_navigation: bool = False, force_refresh: bool = False,
                        allow_early_exit: bool = True, target_total_messages: Optional[int] = None,
                        memory_dir=None, session_factory: Optional[Callable] = None,
                        worker_loop: Optional[Callable] = None,
                        assignment_log_dir: Optional[str] = "./logs/parallel-fetch") -> dict:
    """Stage 1 (orchestrator, own tab) dispatches ``ThreadTask``s to ``workers - 1``
    background worker threads as they are discovered; after Stage 1 the
    orchestrator drains the same queue itself ("worker 0"); results are
    aggregated into the same stats keys ``scrape_inbox`` would have produced,
    plus the new parallel-fetch keys.

    # code:inbox-parallel-fetch-001:orchestrator
    """
    workers = max(1, workers)
    task_q: "queue.Queue" = queue.Queue()
    result_q: "queue.Queue" = queue.Queue()
    retry_q: "queue.Queue" = queue.Queue()
    stop_event = threading.Event()
    counter = MessageCounter(target=target_total_messages)

    tasks_dispatched = 0

    def _dispatch(task: ThreadTask) -> None:
        nonlocal tasks_dispatched
        if stop_event.is_set():
            return
        dispatch_task = task
        if not dispatch_task.psid_hint:
            hint = _resolve_psid_hint(conn, page_id, dispatch_task.record.thread_name)
            if hint:
                dispatch_task = dataclasses.replace(dispatch_task, psid_hint=hint)
        task_q.put(dispatch_task)
        tasks_dispatched += 1
        logger.info(
            f"[dispatch] queued thread '{dispatch_task.record.thread_name}' (inbox #{dispatch_task.ordinal + 1}, "
            f"hint={'psid' if (dispatch_task.psid_hint or dispatch_task.record.selected_item_id) else 'none'}) "
            f"queue_size={task_q.qsize()}"
        )

    loop_fn = worker_loop or worker_main
    loop_kwargs = {"session_factory": session_factory, "counter": counter}
    if _accepts_kwarg(loop_fn, "retry_q"):
        loop_kwargs["retry_q"] = retry_q
    worker_threads = []
    orchestrator_dead = threading.Event()

    def _on_orchestrator_close(_p):
        # code:inbox-parallel-fetch-001:orchestrator-close
        # Retrospective [2026-09-16]: a scheduler cycle navigated/closed the
        # shared inbox tab mid-run. The orchestrator kept dequeuing with a dead
        # page and failed 503 threads in one second. Flag it so Stage 2 leaves
        # the remaining queue to the worker tabs instead.
        logger.error("Orchestrator tab received 'close' event; orchestrator stops taking Stage 2 tasks.")
        orchestrator_dead.set()

    try:
        page.on("close", _on_orchestrator_close)
    except Exception:
        pass

    # The caller's orchestrator connection is also used by Stage 1 to resolve
    # PSID hints.  On PostgreSQL every such read lives in a transaction.  If a
    # previous setup write is still pending, leaving that transaction open
    # while workers begin their per-thread UPSERTs can make the entire pool
    # wait on its transaction ID.  Establish a clean boundary before any
    # background writer is started.
    conn.commit()

    for i in range(1, workers):
        th = threading.Thread(
            target=loop_fn,
            args=(i, page_id, inbox_url, task_q, result_q, stop_event, deps, memory_dir, logger),
            kwargs=loop_kwargs,
            name=f"inbox-worker-{i}",
            daemon=True,
        )
        worker_threads.append(th)
        th.start()

    stage1_start = time.monotonic()
    interrupted: Optional[BaseException] = None
    try:
        discovery = discover_threads(
            page, page_id, time_range, max_threads, conn, logger, record_fetch,
            skip_navigation=skip_navigation, force_refresh=force_refresh,
            allow_early_exit=allow_early_exit, target_total_messages=target_total_messages,
            on_task=_dispatch,
        )
    except KeyboardInterrupt as exc:
        interrupted = exc
        stop_event.set()
        discovery = {
            "early_exit": False,
            "stats": {key: ([] if key == "processed_thread_ids" else 0) for key in _LEGACY_STATS_KEYS},
            "existing_message_count": 0,
        }
    stage1_ms = int((time.monotonic() - stage1_start) * 1000)

    for _ in range(workers - 1):
        task_q.put(None)

    if discovery["early_exit"] or interrupted is not None:
        for th in worker_threads:
            th.join(timeout=120)
            if th.is_alive():
                logger.warning(f"Worker thread {th.name} did not exit within timeout.")
        stats = dict(discovery["stats"])
        all_results = _drain_all(result_q)
        _aggregate_stats(stats, all_results, tasks_dispatched, workers, stage1_ms, 0)
        if interrupted is not None:
            logger.warning(f"Parallel fetch interrupted; returning partial stats: {stats}")
            raise interrupted
        return stats

    stats = discovery["stats"]
    existing_message_count = discovery["existing_message_count"]
    if target_total_messages is not None and counter.add(existing_message_count):
        stop_event.set()

    stage2_start = time.monotonic()
    try:
        logger.info("Resetting sidebar scroll to top for Stage 2 (orchestrator worker)...")
        reset_sidebar_to_top(page, logger)
        page.wait_for_timeout(1500)
    except Exception as exc:
        logger.warning(f"Failed to run Stage 2 sidebar reset for orchestrator worker: {exc}")

    is_first_thread = True
    olog = _PrefixedLogger(logger, "[orchestrator]")
    ohealth = _WorkerHealth()
    orchestrator_retired = False

    def _orchestrator_alive() -> bool:
        return not (orchestrator_dead.is_set() or orchestrator_retired or _page_is_closed(page))

    def _orchestrator_process(task: ThreadTask) -> None:
        nonlocal is_first_thread, orchestrator_retired
        if stop_event.is_set():
            return
        olog.info(
            f"Picked thread '{task.record.thread_name}' (inbox #{task.ordinal + 1}, "
            f"attempt {task.attempt}, hint={'psid' if (task.psid_hint or task.record.selected_item_id) else 'none'})"
        )
        t0 = time.monotonic()
        try:
            result = _call_process_thread_task(page, conn, task, deps, olog, is_first_thread, page_id)
            is_first_thread = False
        except Exception as exc:
            result = ThreadResult(ordinal=task.ordinal, thread_id="", status="error", error=str(exc))
        result.worker = "orchestrator"
        result.thread_name = task.record.thread_name
        result.attempt = task.attempt
        result.elapsed_ms = int((time.monotonic() - t0) * 1000)
        # Only hand off while worker tabs are still alive to take it.
        if any(th.is_alive() for th in worker_threads):
            _requeue_failed(task, result, retry_q, "orchestrator", olog)
        result_q.put(result)
        olog.info(
            f"Finished thread '{task.record.thread_name}' (inbox #{task.ordinal + 1}) "
            f"status={result.status} via={result.locate_method or '-'} "
            f"messages_added={result.messages_added} in {result.elapsed_ms}ms"
            f"{' requeued=1' if result.requeued else ''}"
        )
        if counter.add(result.messages_added):
            stop_event.set()
        action = ohealth.record(result.status)
        if action == "recover":
            olog.warning(f"{ohealth.streak} consecutive failures; reloading orchestrator tab.")
            if _recover_tab(type("_S", (), {"page": page, "tab_role": None})(), inbox_url, olog):
                is_first_thread = True
        elif action == "retire":
            olog.error(f"{ohealth.streak} consecutive failures after recovery; orchestrator stops taking tasks.")
            orchestrator_retired = True

    # Pass 1: the shared Stage 1 queue (FIFO, sentinels come after every real task).
    while _orchestrator_alive():
        try:
            task = task_q.get_nowait()
        except queue.Empty:
            break
        if task is None:
            # Not ours: put it back for a real worker and stop, since sentinels
            # are only enqueued after every real task in FIFO order.
            task_q.put(None)
            break
        _orchestrator_process(task)
    if not _orchestrator_alive() and not orchestrator_retired:
        logger.warning("Orchestrator page is closed; leaving remaining Stage 2 tasks to worker tabs.")

    # Pass 2: retries handed off by failing consumers. Keep polling while any
    # worker is still running (it may still fail and re-queue); the
    # orchestrator is the last resort so it ignores ``failed_by``.
    # code:inbox-parallel-fetch-001:requeue
    while _orchestrator_alive():
        try:
            task = retry_q.get(timeout=1.0)
        except queue.Empty:
            if any(th.is_alive() for th in worker_threads):
                continue
            break
        _orchestrator_process(task)

    for th in worker_threads:
        th.join(timeout=120)
        if th.is_alive():
            logger.warning(f"Worker thread {th.name} did not exit within timeout.")

    stage2_ms = int((time.monotonic() - stage2_start) * 1000)

    all_results = _drain_all(result_q)
    _aggregate_stats(stats, all_results, tasks_dispatched, workers, stage1_ms, stage2_ms)
    _log_stage2_summary(stats, logger)

    if assignment_log_dir:
        _write_assignment_log(stats, page_id, logger, log_dir=assignment_log_dir)
    record_fetch(page_id, stats["new_threads"] + stats["skipped_threads"], stats["new_messages"], conn)

    if interrupted is not None:
        raise interrupted
    return stats


__all__ = [
    "MAX_TASK_ATTEMPTS",
    "MessageCounter",
    "WORKER_FAILURE_STREAK_RECOVER",
    "WORKER_FAILURE_STREAK_RETIRE",
    "run_parallel_fetch",
    "worker_main",
]
