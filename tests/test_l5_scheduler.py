"""
Tests for MAS Unified Scheduler.
code:test-mas-scheduler-001

Tests schedule registration, route enable/disable, and scheduler loop logic.
"""
import os
import sys
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


class TestGracefulShutdown:
    def test_signal_handler_sets_flag(self):
        import tools.l5_scheduler as sched
        sched._shutdown_requested = False
        sched._signal_handler(2, None)  # SIGINT
        assert sched._shutdown_requested is True
        # Reset
        sched._shutdown_requested = False
