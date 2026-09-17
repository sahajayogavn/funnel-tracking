"""Cross-process browser activity locks.

# code:inbox-activity-lock-001

Two independent automation clients drive the same CDP Chrome: the interactive
CLI fetch (``tools/l5_fetch_fb_messages.py``) and the scheduler's browser
cycles (``tools/l5_scheduler.py``). Retrospective [2026-09-16]: a scheduler
REPLY cycle attached mid-way through a 90d CLI fetch, navigated the shared
inbox tab, and the fetch orchestrator lost its page (503 threads failed).

Each side publishes a small JSON lock file under ``logs/locks/`` while it owns
the browser. Liveness is decided by *activity*, not by the file's existence:

* the owning pid must still be alive, and
* the most recent of (heartbeat timestamp, mtime of the owner's log files)
  must be younger than ``stale_after`` seconds.

``HeartbeatLogHandler`` refreshes the heartbeat on every log record the owner
emits, so a long-running fetch that keeps logging is always seen as alive, while
a crashed/killed process (dead pid, or no log output) is ignored automatically.
Locks held by the *current* pid are never treated as a conflict, so a scheduler
cycle that calls ``fetch_messages`` in-process does not block on itself.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

LOCK_DIR = Path(os.environ.get("FB_ACTIVITY_LOCK_DIR", "./logs/locks"))
DEFAULT_STALE_AFTER_S = 180.0

ROLE_FETCH_CLI = "inbox_fetch_cli"
ROLE_SCHEDULER_BROWSER = "scheduler_browser"


def _lock_path(role: str, page_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{role}.{page_id}")
    return LOCK_DIR / f"{safe}.json"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class ActivityInfo:
    role: str
    page_id: str
    pid: int
    label: str
    started_at: float
    heartbeat_at: float
    log_paths: list[str]
    last_activity_at: float
    pid_alive: bool

    @property
    def idle_seconds(self) -> float:
        return max(0.0, time.time() - self.last_activity_at)

    def describe(self) -> str:
        return (f"{self.role} pid={self.pid} ({self.label or '-'}) "
                f"last activity {self.idle_seconds:.0f}s ago, pid_alive={self.pid_alive}")


# code:inbox-activity-lock-001:read
def read_activity(role: str, page_id: str) -> Optional[ActivityInfo]:
    """Return the published lock for ``role``/``page_id`` or ``None``."""
    path = _lock_path(role, page_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    pid = int(data.get("pid", 0) or 0)
    heartbeat_at = float(data.get("heartbeat_at", 0) or 0)
    log_paths = [str(p) for p in data.get("log_paths", []) or []]
    last = heartbeat_at
    for log_path in log_paths:
        try:
            last = max(last, os.path.getmtime(log_path))
        except OSError:
            continue
    try:
        last = max(last, path.stat().st_mtime)
    except OSError:
        pass
    return ActivityInfo(
        role=role, page_id=page_id, pid=pid, label=str(data.get("label", "")),
        started_at=float(data.get("started_at", 0) or 0), heartbeat_at=heartbeat_at,
        log_paths=log_paths, last_activity_at=last, pid_alive=_pid_alive(pid),
    )


# code:inbox-activity-lock-001:is-active
def is_active(role: str, page_id: str, stale_after: float = DEFAULT_STALE_AFTER_S,
              ignore_own_pid: bool = True) -> Optional[ActivityInfo]:
    """Return the ``ActivityInfo`` if another live, recently-active process holds
    ``role`` for ``page_id``; otherwise ``None`` (stale files are removed)."""
    info = read_activity(role, page_id)
    if info is None:
        return None
    if ignore_own_pid and info.pid == os.getpid():
        return None
    if not info.pid_alive or info.idle_seconds > stale_after:
        with contextlib.suppress(OSError):
            _lock_path(role, page_id).unlink()
        return None
    return info


# code:inbox-activity-lock-001:wait
def wait_until_idle(role: str, page_id: str, timeout: float, logger: logging.Logger,
                    poll_interval: float = 10.0,
                    stale_after: float = DEFAULT_STALE_AFTER_S) -> bool:
    """Block until no other live process holds ``role``. Returns ``False`` on
    timeout (the caller decides whether to abort)."""
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        info = is_active(role, page_id, stale_after=stale_after)
        if info is None:
            if announced:
                logger.info("[activity-lock] %s is idle; continuing.", role)
            return True
        if time.monotonic() >= deadline:
            logger.error("[activity-lock] gave up waiting %.0fs for %s", timeout, info.describe())
            return False
        if not announced:
            logger.info("[activity-lock] waiting for %s (poll every %.0fs, timeout %.0fs)",
                        info.describe(), poll_interval, timeout)
            announced = True
        time.sleep(poll_interval)


class ActivityLock:
    """Publish ``role`` ownership for ``page_id`` from this process.

    # code:inbox-activity-lock-001:lock
    """

    def __init__(self, role: str, page_id: str, label: str = "",
                 log_paths: Iterable[str] = (), heartbeat_min_interval: float = 2.0):
        self.role = role
        self.page_id = page_id
        self.label = label
        self.log_paths = [str(p) for p in log_paths]
        self.path = _lock_path(role, page_id)
        self._min_interval = heartbeat_min_interval
        self._last_write = 0.0
        self._started_at = 0.0
        self.held = False

    def _write(self) -> None:
        now = time.time()
        payload = {
            "role": self.role, "page_id": self.page_id, "pid": os.getpid(),
            "label": self.label, "started_at": self._started_at,
            "heartbeat_at": now, "log_paths": self.log_paths,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)
        self._last_write = now

    def acquire(self) -> "ActivityLock":
        self._started_at = time.time()
        self._write()
        self.held = True
        return self

    def heartbeat(self, force: bool = False) -> None:
        if not self.held:
            return
        if force or (time.time() - self._last_write) >= self._min_interval:
            with contextlib.suppress(OSError):
                self._write()

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            info = read_activity(self.role, self.page_id)
            if info is None or info.pid == os.getpid():
                self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "ActivityLock":
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()


class HeartbeatLogHandler(logging.Handler):
    """Refresh ``lock`` whenever the owner logs — "still logging" == "still alive".

    # code:inbox-activity-lock-001:heartbeat-handler
    """

    def __init__(self, lock: ActivityLock, level: int = logging.DEBUG):
        super().__init__(level=level)
        # NOTE: must not be named ``self.lock`` — ``logging.Handler`` owns that
        # attribute (its RLock) and calls ``self.lock.acquire()/release()``
        # around every ``emit``; shadowing it would release the activity lock
        # on each log record.
        self.activity_lock = lock

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            self.activity_lock.heartbeat()
        except Exception:
            pass


@contextlib.contextmanager
def hold_activity(role: str, page_id: str, logger: logging.Logger, label: str = "",
                  log_paths: Iterable[str] = ()):
    """Context manager: acquire ``role`` and heartbeat it from ``logger``'s records."""
    lock = ActivityLock(role, page_id, label=label, log_paths=log_paths).acquire()
    handler = HeartbeatLogHandler(lock)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield lock
    finally:
        root.removeHandler(handler)
        lock.release()


# code:inbox-activity-lock-001:scheduler-guard
@contextlib.contextmanager
def scheduler_browser_cycle(job_name: str, page_id: str, logger: logging.Logger,
                            stale_after: float = DEFAULT_STALE_AFTER_S):
    """Guard a scheduler job that needs the browser.

    Yields ``True`` when the job may run (and holds ``scheduler_browser`` for its
    duration), or ``False`` when a live CLI fetch owns the browser — the job
    must then skip this tick and try again on the next schedule.
    """
    other = is_active(ROLE_FETCH_CLI, page_id, stale_after=stale_after)
    if other is not None:
        logger.info("[activity-lock] %s skipped: %s", job_name, other.describe())
        yield False
        return
    with hold_activity(ROLE_SCHEDULER_BROWSER, page_id, logger, label=job_name,
                       log_paths=["./logs/scheduler.log"]):
        yield True
