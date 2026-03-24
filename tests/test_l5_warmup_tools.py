"""
Tests for MAS Route 2 — Warm-up tools.
code:test-mas-warmup-001

Tests find_dormant_seekers, was_recently_warmed_up, select_warmup_strategy,
and log_warmup_campaign with seeded DB data.
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
    """Create a seeded DB with users at various dormancy levels."""
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
        "adk_agents.tools.l5_warmup_tools.get_db_connection", _get_conn
    )
    monkeypatch.setattr(
        "adk_agents.tools.l5_warmup_tools.get_comment_db_connection", _get_comment_conn
    )

    # Seed users with various dormancy
    conn = _get_conn()
    now = datetime.now()
    users = [
        ("thread_active", "Active User", "Hà Nội", "Seeker",
         (now - timedelta(days=1)).isoformat()),
        ("thread_dormant_5d", "Dormant 5d", "Đà Nẵng", "Seeker",
         (now - timedelta(days=5)).isoformat()),
        ("thread_dormant_10d", "Dormant 10d", "Hà Nội", "Public Program Seeker",
         (now - timedelta(days=10)).isoformat()),
        ("thread_dormant_30d", "Dormant 30d", "TP. Hồ Chí Minh", "18-Week Seeker",
         (now - timedelta(days=30)).isoformat()),
        ("thread_spam", "Spam User", "Unknown", "spam",
         (now - timedelta(days=100)).isoformat()),
    ]
    for tid, name, city, stage, interaction in users:
        conn.execute(
            "INSERT OR IGNORE INTO users (thread_id, thread_name, city, lead_stage, "
            "last_interaction, first_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (tid, name, city, stage, interaction, (now - timedelta(days=60)).isoformat())
        )
    conn.commit()
    conn.close()

    comment_conn = _get_comment_conn()
    comment_conn.execute(
        "INSERT OR IGNORE INTO comment_users (post_id, commenter_name, fb_user_id, city, lead_stage, last_interaction, first_seen, temperature, last_warmup_at, warmup_count, cool_step) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "post_1",
            "Dormant Comment 8d",
            "fb_commenter_1",
            "Hà Nội",
            "Seeker",
            (now - timedelta(days=8)).isoformat(),
            (now - timedelta(days=90)).isoformat(),
            "cool",
            (now - timedelta(days=9)).isoformat(),
            2,
            1,
        )
    )
    comment_conn.commit()
    comment_conn.close()
    return db_path


class TestFindDormantSeekers:
    def test_finds_dormant_seekers(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import find_dormant_seekers
        result = find_dormant_seekers(min_days=3)
        assert result["status"] == "success"
        assert result["count"] >= 2  # At least dormant_5d and dormant_10d
        names = [s["name"] for s in result["seekers"]]
        assert "Active User" not in names

    def test_excludes_spam(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import find_dormant_seekers
        result = find_dormant_seekers(min_days=1)
        names = [s["name"] for s in result["seekers"]]
        assert "Spam User" not in names

    def test_respects_limit(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import find_dormant_seekers
        result = find_dormant_seekers(min_days=3, max_seekers=1)
        assert result["count"] == 1

    def test_includes_comment_users_with_scheduler_fields(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import find_dormant_seekers
        result = find_dormant_seekers(min_days=3, max_seekers=10)
        comment_seeker = next(s for s in result["seekers"] if s["thread_id"] == "comment_fb_commenter_1")
        assert comment_seeker["source"] == "comment"
        assert comment_seeker["temperature"] == "cool"
        assert comment_seeker["warmup_count"] == 2
        assert comment_seeker["cool_step"] == 1


class TestWasRecentlyWarmedUp:
    def test_not_warmed_up(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import was_recently_warmed_up
        assert was_recently_warmed_up("thread_dormant_5d", days=7) is False

    def test_recently_warmed_up(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import log_warmup_campaign, was_recently_warmed_up
        log_warmup_campaign("thread_dormant_5d", "Dormant 5d", "gentle_reminder", "Hello!", dry_run=False)
        assert was_recently_warmed_up("thread_dormant_5d", days=7) is True

    def test_dry_run_warmup_does_not_block_live_eligibility(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import log_warmup_campaign, was_recently_warmed_up
        log_warmup_campaign("thread_dormant_5d", "Dormant 5d", "gentle_reminder", "Hello!", dry_run=True)
        assert was_recently_warmed_up("thread_dormant_5d", days=7) is False


class TestSelectWarmupStrategy:
    def test_seeker_in_range(self):
        from adk_agents.tools.l5_warmup_tools import select_warmup_strategy
        result = select_warmup_strategy("Seeker", 5)
        assert result is not None
        assert result["type"] == "gentle_reminder"
        assert "template" in result

    def test_too_soon(self):
        from adk_agents.tools.l5_warmup_tools import select_warmup_strategy
        result = select_warmup_strategy("Seeker", 1)
        assert result is None

    def test_very_dormant_fallback(self):
        from adk_agents.tools.l5_warmup_tools import select_warmup_strategy
        result = select_warmup_strategy("Seeker", 100)
        assert result is not None
        assert result["type"] == "re_engagement"

    def test_public_program_seeker(self):
        from adk_agents.tools.l5_warmup_tools import select_warmup_strategy
        result = select_warmup_strategy("Public Program Seeker", 10)
        assert result is not None
        assert result["type"] == "tip_share"

    def test_unknown_stage_fallback(self):
        from adk_agents.tools.l5_warmup_tools import select_warmup_strategy
        result = select_warmup_strategy("Unknown Stage", 5)
        assert result is not None  # Falls back to Intake strategy


class TestNormalizeLeadStage:
    def test_normalizes_journey_engine_registered_stage(self):
        from adk_agents.tools.l5_warmup_tools import normalize_lead_stage
        assert normalize_lead_stage("Seeker_Public_Program") == "Public Program Seeker"

    def test_normalizes_journey_engine_deep_learner_stage(self):
        from adk_agents.tools.l5_warmup_tools import normalize_lead_stage
        assert normalize_lead_stage("Seeker_18_Weeks") == "18-Week Seeker"

    def test_normalizes_yogi_stages_to_18_week_strategy(self):
        from adk_agents.tools.l5_warmup_tools import normalize_lead_stage
        assert normalize_lead_stage("Seed") == "18-Week Seeker"
        assert normalize_lead_stage("Sahaja_Yogi") == "18-Week Seeker"

    def test_select_strategy_accepts_journey_engine_aliases(self):
        from adk_agents.tools.l5_warmup_tools import select_warmup_strategy
        result = select_warmup_strategy("Seeker_Public_Program", 10)
        assert result is not None
        assert result["type"] == "tip_share"


class TestLogWarmupCampaign:
    def test_logs_successfully(self, seeded_db):
        from adk_agents.tools.l5_warmup_tools import log_warmup_campaign
        result = log_warmup_campaign(
            thread_id="thread_dormant_5d",
            seeker_name="Dormant 5d",
            strategy_type="gentle_reminder",
            message_text="Test warmup message"
        )
        assert result["status"] == "logged"
        assert result["dry_run"] is True
