"""
Tests for MAS Route 3 — Event tools.
code:test-mas-event-001

Tests create_event, get_upcoming_events, find_target_seekers_for_event,
and log_event_campaign with seeded DB data.
"""
import os
import sys
import sqlite3
import pytest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Create a seeded DB with events and users."""
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

    monkeypatch.setattr(
        "adk_agents.tools.l5_event_tools.get_db_connection", _get_conn
    )
    monkeypatch.setattr(
        "adk_agents.tools.l5_event_tools.get_comment_db_connection", _get_comment_conn
    )

    # Seed users in different cities
    conn = _get_conn()
    now = datetime.now()
    users = [
        ("thread_hn1", "Hà Nội User 1", "Hà Nội", "Seeker",
         (now - timedelta(days=2)).isoformat()),
        ("thread_hn2", "Hà Nội User 2", "Hà Nội", "Seeker_Public_Program",
         (now - timedelta(days=5)).isoformat()),
        ("thread_hcm", "HCM User", "TP. Hồ Chí Minh", "Seeker",
         (now - timedelta(days=3)).isoformat()),
        ("thread_spam", "Spam User", "Hà Nội", "spam",
         (now - timedelta(days=10)).isoformat()),
    ]
    for tid, name, city, stage, interaction in users:
        conn.execute(
            "INSERT OR IGNORE INTO users (thread_id, thread_name, city, lead_stage, "
            "last_interaction, first_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (tid, name, city, stage, interaction, (now - timedelta(days=60)).isoformat())
        )

    # Seed an upcoming event
    future_date = (now + timedelta(days=5)).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO events (name, city, event_date, description) VALUES (?, ?, ?, ?)",
        ("Lớp Thiền Miễn Phí", "Hà Nội", future_date, "Free meditation class")
    )

    # Seed a past event
    past_date = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO events (name, city, event_date, description) VALUES (?, ?, ?, ?)",
        ("Old Event", "Hà Nội", past_date, "Already happened")
    )
    conn.commit()
    conn.close()
    return db_path


class TestCreateEvent:
    def test_creates_event(self, seeded_db):
        from adk_agents.tools.l5_event_tools import create_event
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        result = create_event("New Event", "Đà Nẵng", future, "A new event")
        assert result["status"] == "created"
        assert result["event_id"] > 0


class TestGetUpcomingEvents:
    def test_finds_upcoming_events(self, seeded_db):
        from adk_agents.tools.l5_event_tools import get_upcoming_events
        result = get_upcoming_events()
        assert result["status"] == "success"
        assert result["count"] >= 1
        names = [e["name"] for e in result["events"]]
        assert "Lớp Thiền Miễn Phí" in names

    def test_excludes_past_events(self, seeded_db):
        from adk_agents.tools.l5_event_tools import get_upcoming_events
        result = get_upcoming_events()
        names = [e["name"] for e in result["events"]]
        assert "Old Event" not in names

    def test_filter_by_city(self, seeded_db):
        from adk_agents.tools.l5_event_tools import get_upcoming_events
        result = get_upcoming_events(city="Đà Nẵng")
        assert result["count"] == 0  # No upcoming events in Đà Nẵng


class TestFindTargetSeekers:
    def test_finds_seekers_by_city(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event
        result = find_target_seekers_for_event(event_id=1, city="Hà Nội")
        assert result["status"] == "success"
        assert result["count"] >= 2
        names = [s["name"] for s in result["seekers"]]
        assert "Hà Nội User 1" in names
        assert "Hà Nội User 2" in names

    def test_normalizes_stage_aliases(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event
        result = find_target_seekers_for_event(event_id=1, city="Hà Nội")
        seeker = next(s for s in result["seekers"] if s["thread_id"] == "thread_hn2")
        assert seeker["lead_stage"] == "Public Program Seeker"

    def test_prioritizes_registered_before_seeker(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event
        result = find_target_seekers_for_event(event_id=1, city="Hà Nội")
        thread_ids = [s["thread_id"] for s in result["seekers"]]
        assert thread_ids.index("thread_hn2") < thread_ids.index("thread_hn1")

    def test_excludes_spam(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event
        result = find_target_seekers_for_event(event_id=1, city="Hà Nội")
        names = [s["name"] for s in result["seekers"]]
        assert "Spam User" not in names

    def test_excludes_already_notified(self, seeded_db):
        from adk_agents.tools.l5_event_tools import (
            find_target_seekers_for_event, log_event_campaign
        )
        log_event_campaign(1, "thread_hn1", "Hà Nội User 1", "Test message")
        result = find_target_seekers_for_event(event_id=1, city="Hà Nội")
        thread_ids = [s["thread_id"] for s in result["seekers"]]
        assert "thread_hn1" not in thread_ids

    def test_wrong_city_returns_empty(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event
        result = find_target_seekers_for_event(event_id=1, city="Đà Nẵng")
        assert result["count"] == 0


class TestLogEventCampaign:
    def test_logs_successfully(self, seeded_db):
        from adk_agents.tools.l5_event_tools import log_event_campaign
        result = log_event_campaign(
            event_id=1, thread_id="thread_hn1",
            seeker_name="Hà Nội User 1",
            message_text="Test notification"
        )
        assert result["status"] == "logged"
        assert result["dry_run"] is True
