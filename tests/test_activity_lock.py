"""Tests for fb_pipeline/session/l2_activity_lock.py.

# code:test-validation-001:activity-lock
"""
import json
import logging
import os
import time
from unittest.mock import patch

import pytest

import fb_pipeline.session.l2_activity_lock as al


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "LOCK_DIR", tmp_path)
    return tmp_path


def _age_lock(lock: al.ActivityLock, seconds: float) -> None:
    """Rewrite the lock as if its last heartbeat happened ``seconds`` ago."""
    data = json.loads(lock.path.read_text())
    data["heartbeat_at"] = time.time() - seconds
    lock.path.write_text(json.dumps(data))
    old = time.time() - seconds
    os.utime(lock.path, (old, old))


def _foreign_lock(role: str, page_id: str, **kw) -> al.ActivityLock:
    """Write a lock that looks like it belongs to another (live) process."""
    with patch.object(al.os, "getpid", return_value=999999):
        return al.ActivityLock(role, page_id, **kw).acquire()


def test_acquire_release_roundtrip(lock_dir):
    lock = al.ActivityLock(al.ROLE_FETCH_CLI, "p1", label="t").acquire()
    info = al.read_activity(al.ROLE_FETCH_CLI, "p1")
    assert info and info.pid == os.getpid() and info.pid_alive
    lock.release()
    assert al.read_activity(al.ROLE_FETCH_CLI, "p1") is None


def test_own_pid_is_not_a_conflict(lock_dir):
    with al.ActivityLock(al.ROLE_SCHEDULER_BROWSER, "p1"):
        assert al.is_active(al.ROLE_SCHEDULER_BROWSER, "p1") is None
        assert al.is_active(al.ROLE_SCHEDULER_BROWSER, "p1", ignore_own_pid=False) is not None


def test_dead_pid_is_ignored_and_cleaned(lock_dir):
    lock = al.ActivityLock(al.ROLE_FETCH_CLI, "p1").acquire()
    with patch.object(al, "_pid_alive", return_value=False):
        assert al.is_active(al.ROLE_FETCH_CLI, "p1", ignore_own_pid=False) is None
    assert not lock.path.exists()


def test_stale_heartbeat_is_ignored_but_log_activity_keeps_alive(lock_dir, tmp_path):
    log_file = tmp_path / "x.log"
    log_file.write_text("a")
    old = time.time() - 1000
    with patch.object(al, "_pid_alive", return_value=True):
        lock = _foreign_lock(al.ROLE_FETCH_CLI, "p1", log_paths=[str(log_file)])
        _age_lock(lock, 1000)
        os.utime(log_file, (old, old))
        assert al.is_active(al.ROLE_FETCH_CLI, "p1", stale_after=180) is None
        # A fresh log write (process still logging) counts as liveness even
        # though the heartbeat itself is old.
        lock = _foreign_lock(al.ROLE_FETCH_CLI, "p1", log_paths=[str(log_file)])
        _age_lock(lock, 1000)
        log_file.write_text("ab")
        assert al.is_active(al.ROLE_FETCH_CLI, "p1", stale_after=180) is not None


def test_heartbeat_log_handler_refreshes(lock_dir):
    lock = al.ActivityLock(al.ROLE_FETCH_CLI, "p1", heartbeat_min_interval=0).acquire()
    _age_lock(lock, 500)
    assert al.read_activity(al.ROLE_FETCH_CLI, "p1").idle_seconds > 400
    logger = logging.getLogger("test_heartbeat")
    handler = al.HeartbeatLogHandler(lock)
    logger.addHandler(handler)
    try:
        logger.warning("still alive")
    finally:
        logger.removeHandler(handler)
    assert lock.held, "logging must not release the activity lock (Handler.lock name clash)"
    assert al.read_activity(al.ROLE_FETCH_CLI, "p1").idle_seconds < 5
    lock.release()


def test_scheduler_cycle_skips_while_cli_fetch_active(lock_dir):
    logger = logging.getLogger("test_sched")
    with patch.object(al, "_pid_alive", return_value=True):
        _foreign_lock(al.ROLE_FETCH_CLI, "p1")
        with al.scheduler_browser_cycle("[FETCH]", "p1", logger) as may_run:
            assert may_run is False
    assert al.read_activity(al.ROLE_SCHEDULER_BROWSER, "p1") is None


def test_scheduler_cycle_holds_lock_when_free(lock_dir):
    logger = logging.getLogger("test_sched2")
    with al.scheduler_browser_cycle("[REPLY]", "p1", logger) as may_run:
        assert may_run is True
        assert al.read_activity(al.ROLE_SCHEDULER_BROWSER, "p1").label == "[REPLY]"
    assert al.read_activity(al.ROLE_SCHEDULER_BROWSER, "p1") is None


def test_wait_until_idle_times_out(lock_dir):
    logger = logging.getLogger("test_wait")
    with patch.object(al, "_pid_alive", return_value=True), patch.object(al.time, "sleep"):
        _foreign_lock(al.ROLE_SCHEDULER_BROWSER, "p1")
        assert al.wait_until_idle(al.ROLE_SCHEDULER_BROWSER, "p1", timeout=0, logger=logger) is False
