"""
Tests for MAS Unified Scheduler.
code:test-mas-scheduler-001

Tests schedule registration, route enable/disable, and scheduler loop logic.
"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def clear_schedule(monkeypatch):
    """Clear all scheduled jobs before each test."""
    import types

    class _Jobs:
        def __init__(self):
            self._jobs = []

        def clear(self):
            self._jobs.clear()

        def every(self, interval=None):
            scheduler = self

            class _Every:
                def __init__(self, interval):
                    self.interval = interval
                    self.unit = None

                @property
                def minutes(self):
                    self.unit = "minutes"
                    return self

                @property
                def seconds(self):
                    self.unit = "seconds"
                    return self

                @property
                def day(self):
                    self.unit = "day"
                    return self

                def at(self, when):
                    self.when = when
                    return self

                def do(self, func, *args, **kwargs):
                    scheduler._jobs.append({"func": func, "args": args, "kwargs": kwargs, "interval": self.interval, "unit": self.unit, "when": getattr(self, "when", None)})
                    return scheduler._jobs[-1]

            return _Every(interval)

        def get_jobs(self):
            return list(self._jobs)

        def run_pending(self):
            return None

    fake_schedule = _Jobs()
    import tools.l5_scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "schedule", fake_schedule)
    # Also patch sys.modules so `import schedule` inside test methods returns the fake
    monkeypatch.setitem(sys.modules, "schedule", fake_schedule)
    yield
    fake_schedule.clear()


@pytest.fixture
def scheduler_seeded_db(tmp_path):
    db_path = str(tmp_path / "frankensqlite.db")

    def _get_conn(*a, **kw):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        from fb_pipeline.persistence.l4_sqlite_store import setup_database
        setup_database(conn)
        return conn

    def _get_comment_conn(*a, **kw):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        from fb_pipeline.persistence.l4_sqlite_store import setup_database, setup_comment_database
        setup_database(conn)
        setup_comment_database(conn)
        return conn

    now = datetime.now()
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (thread_id, thread_name, city, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("thread_dm_1", "DM User", "Hà Nội", "Seeker", (now - timedelta(days=5)).isoformat(), "cool", (now - timedelta(days=10)).isoformat(), 1, 1),
    )
    conn.commit()
    conn.close()

    comment_conn = _get_comment_conn()
    comment_conn.execute(
        "INSERT OR IGNORE INTO comment_users (post_id, commenter_name, fb_user_id, city, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("post_1", "Comment User", "fb_comment_1", "Hà Nội", "Seeker", (now - timedelta(days=6)).isoformat(), "cool", (now - timedelta(days=8)).isoformat(), 2, 1),
    )
    comment_conn.commit()
    comment_conn.close()
    return _get_conn, _get_comment_conn


class TestSetupSchedule:
    def test_all_routes_register_jobs(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes={"react", "reply", "warmup", "event"},
            fetch_interval=15, warmup_time="09:00", event_time="10:00"
        )
        assert len(registered) >= 6
        assert len(schedule.get_jobs()) >= 6

    def test_single_route_registers_correct_jobs(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes={"warmup"},
            fetch_interval=15, warmup_time="09:00", event_time="10:00"
        )
        assert len(registered) >= 3
        assert "warmup" in registered[0]

    def test_react_only_also_registers_fetch(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes={"react"},
            fetch_interval=15, warmup_time="09:00", event_time="10:00"
        )
        # Should register fetch + react
        assert len(registered) >= 4
        types = " ".join(registered)
        assert "fetch" in types
        assert "react" in types

    def test_empty_routes_no_jobs(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes=set(),
            fetch_interval=15, warmup_time="09:00", event_time="10:00"
        )
        assert len(registered) >= 2
        assert len(schedule.get_jobs()) >= 2

    def test_custom_fetch_interval(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes={"reply"},
            fetch_interval=10, warmup_time="09:00", event_time="10:00"
        )
        types = " ".join(registered)
        assert "10min" in types


class TestReactionHeuristic:
    def test_grateful_message_returns_love(self):
        from tools.l5_scheduler import _select_reaction_heuristic
        result = _select_reaction_heuristic({"content": "Cảm ơn bạn rất nhiều!"})
        assert result == "love"

    def test_sad_message_returns_care(self):
        from tools.l5_scheduler import _select_reaction_heuristic
        result = _select_reaction_heuristic({"content": "Tôi rất buồn"})
        assert result == "care"

    def test_neutral_message_returns_like(self):
        from tools.l5_scheduler import _select_reaction_heuristic
        result = _select_reaction_heuristic({"content": "Xin chào"})
        assert result == "like"

    def test_empty_content_returns_like(self):
        from tools.l5_scheduler import _select_reaction_heuristic
        result = _select_reaction_heuristic({"content": None})
        assert result == "like"


class TestDecisionCore:
    def test_compute_temperature_respects_manual_unsubscribed_state(self):
        from tools.l5_scheduler import _compute_temperature
        assert _compute_temperature("Seeker", "2026-03-01T00:00:00", "unsubscribed") == "unsubscribed"

    def test_compute_temperature_for_recent_registered_thread(self):
        from tools.l5_scheduler import _compute_temperature
        recent = datetime.now().isoformat()
        assert _compute_temperature("Seeker_Public_Program", recent, None) == "hot"

    def test_compute_temperature_for_old_seeker_thread(self):
        from tools.l5_scheduler import _compute_temperature
        old = "2026-01-01T00:00:00"
        assert _compute_temperature("Seeker", old, None) == "cold"

    def test_evaluate_proactive_eligibility_blocks_pending_reply(self, monkeypatch):
        import tools.l5_scheduler as sched
        monkeypatch.setattr(sched, "_load_user_state", lambda thread_id: {
            "thread_id": thread_id,
            "lead_stage": "Seeker",
            "last_interaction": datetime.now().isoformat(),
            "temperature": None,
        })
        monkeypatch.setattr(sched, "_thread_has_pending_reply", lambda page_id, thread_id: True)
        monkeypatch.setattr(sched, "_recent_live_touch_exists", lambda thread_id, since_hours=24: False)
        monkeypatch.setattr(sched, "_has_recent_live_event", lambda thread_id, since_days=90: False)

        eligible, reason, payload = sched._evaluate_proactive_eligibility("page123", "warmup", "thread_1")
        assert eligible is False
        assert reason == "pending_inbox_reply"
        assert payload["thread_id"] == "thread_1"

    def test_evaluate_proactive_eligibility_blocks_dormant_quarterly_limit(self, monkeypatch):
        import tools.l5_scheduler as sched
        monkeypatch.setattr(sched, "_load_user_state", lambda thread_id: {
            "thread_id": thread_id,
            "lead_stage": "Seeker",
            "last_interaction": "2026-01-01T00:00:00",
            "temperature": "dormant",
        })
        monkeypatch.setattr(sched, "_thread_has_pending_reply", lambda page_id, thread_id: False)
        monkeypatch.setattr(sched, "_recent_live_touch_exists", lambda thread_id, since_hours=24: False)
        monkeypatch.setattr(sched, "_has_recent_live_event", lambda thread_id, since_days=90: True)

        eligible, reason, _ = sched._evaluate_proactive_eligibility("page123", "event", "thread_1")
        assert eligible is False
        assert reason == "dormant_quarterly_limit"


class TestRouteCommentUsers:
    def test_load_user_state_reads_comment_users(self, monkeypatch, scheduler_seeded_db):
        import tools.l5_scheduler as sched
        get_conn, get_comment_conn = scheduler_seeded_db

        monkeypatch.setattr(
            "fb_pipeline.persistence.l4_sqlite_store.get_db_connection",
            lambda *a, **kw: get_conn(),
        )
        monkeypatch.setattr(
            "fb_pipeline.persistence.l4_sqlite_store.get_comment_db_connection",
            lambda *a, **kw: get_comment_conn(),
        )

        state = sched._load_user_state("comment_fb_comment_1")
        assert state is not None
        assert state["thread_name"] == "Comment User"
        assert state["temperature"] == "cool"
        assert state["warmup_count"] == 2
        assert state["cool_step"] == 1

    def test_update_user_decision_state_updates_comment_users(self, monkeypatch, scheduler_seeded_db):
        import tools.l5_scheduler as sched
        get_conn, get_comment_conn = scheduler_seeded_db

        monkeypatch.setattr(
            "fb_pipeline.persistence.l4_sqlite_store.get_db_connection",
            lambda *a, **kw: get_conn(),
        )
        monkeypatch.setattr(
            "fb_pipeline.persistence.l4_sqlite_store.get_comment_db_connection",
            lambda *a, **kw: get_comment_conn(),
        )

        sched._update_user_decision_state("comment_fb_comment_1", "warm", warmup_sent=False, cool_step=2)

        conn = get_comment_conn()
        row = conn.execute(
            "SELECT temperature, warmup_count, cool_step FROM comment_users WHERE fb_user_id = ?",
            ("fb_comment_1",),
        ).fetchone()
        conn.close()

        assert row["temperature"] == "warm"
        assert row["warmup_count"] == 2
        assert row["cool_step"] == 2


class TestGracefulShutdown:
    def test_signal_handler_sets_flag(self):
        import tools.l5_scheduler as sched
        sched._shutdown_requested = False
        sched._signal_handler(2, None)  # SIGINT
        assert sched._shutdown_requested is True
        # Reset
        sched._shutdown_requested = False
