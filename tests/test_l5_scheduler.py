"""
Tests for MAS Unified Scheduler.
code:test-mas-scheduler-001

Tests schedule registration, route enable/disable, and scheduler loop logic.
"""
import os
import sys
from datetime import datetime

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def clear_schedule():
    """Clear all scheduled jobs before each test."""
    import schedule
    schedule.clear()
    yield
    schedule.clear()


class TestSetupSchedule:
    def test_all_routes_register_jobs(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes={"react", "reply", "warmup", "event"},
            fetch_interval=15, warmup_time="09:00", event_time="10:00"
        )
        assert len(registered) >= 4
        assert len(schedule.get_jobs()) >= 4

    def test_single_route_registers_correct_jobs(self):
        from tools.l5_scheduler import setup_schedule
        import schedule
        registered = setup_schedule(
            page_id="119587786260266", dry_run=True,
            routes={"warmup"},
            fetch_interval=15, warmup_time="09:00", event_time="10:00"
        )
        assert len(registered) == 1
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
        assert len(registered) == 2
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
        assert len(registered) == 0
        assert len(schedule.get_jobs()) == 0

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


class TestGracefulShutdown:
    def test_signal_handler_sets_flag(self):
        import tools.l5_scheduler as sched
        sched._shutdown_requested = False
        sched._signal_handler(2, None)  # SIGINT
        assert sched._shutdown_requested is True
        # Reset
        sched._shutdown_requested = False
