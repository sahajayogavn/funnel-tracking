import os
import sys
import sqlite3
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
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

    monkeypatch.setattr("adk_agents.tools.l5_event_tools.get_db_connection", _get_conn)
    monkeypatch.setattr("adk_agents.tools.l5_event_tools.get_comment_db_connection", _get_comment_conn)

    now = datetime.now()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO events (id, name, city, event_date, description) VALUES (?, ?, ?, ?, ?)",
        (1, "Thiền Âm nhạc", "Hà Nội", (now + timedelta(days=3)).strftime("%Y-%m-%d"), "Đêm thiền và âm nhạc chữa lành"),
    )
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, lead_stage, last_interaction, first_seen, temperature, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("thread_music", "Music Lover", "Hà Nội", "Seeker", (now - timedelta(days=1)).isoformat(), (now - timedelta(days=30)).isoformat(), "warm", 0, 0),
    )
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, lead_stage, last_interaction, first_seen, temperature, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("thread_registered", "Registered User", "Hà Nội", "Seeker_Public_Program", (now - timedelta(days=2)).isoformat(), (now - timedelta(days=40)).isoformat(), "warm", 1, 0),
    )
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, lead_stage, last_interaction, first_seen, temperature, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("thread_generic", "Generic Seeker", "Hà Nội", "Seeker", (now - timedelta(days=3)).isoformat(), (now - timedelta(days=20)).isoformat(), "warm", 0, 0),
    )
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, lead_stage, last_interaction, first_seen, temperature, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("thread_hcm", "Other City", "TP. Hồ Chí Minh", "Seeker", (now - timedelta(days=1)).isoformat(), (now - timedelta(days=10)).isoformat(), "warm", 0, 0),
    )

    messages = [
        ("thread_music", "Customer", "Mình thích thiền âm nhạc và chữa lành bằng music", now.isoformat(), 1),
        ("thread_music", "Customer", "Có workshop âm nhạc nào ở Hà Nội không?", now.isoformat(), 2),
        ("thread_registered", "Customer", "Mình muốn đăng ký lớp thiền miễn phí", now.isoformat(), 1),
        ("thread_generic", "Customer", "Mình đang tìm hiểu về thiền cơ bản", now.isoformat(), 1),
    ]
    for thread_id, sender, content, timestamp, seq in messages:
        conn.execute(
            "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
            (thread_id, sender, content, timestamp, seq),
        )
    conn.commit()
    conn.close()

    comment_conn = _get_comment_conn()
    comment_conn.execute(
        "INSERT INTO comment_users (post_id, commenter_name, fb_user_id, city, lead_stage, last_interaction, first_seen, temperature, last_warmup_at, warmup_count, cool_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "post_1",
            "Comment Prospect",
            "fb_comment_1",
            "Hà Nội",
            "Seeker",
            (now - timedelta(days=4)).isoformat(),
            (now - timedelta(days=50)).isoformat(),
            "warm",
            None,
            0,
            0,
        ),
    )
    comment_conn.commit()
    comment_conn.close()

    return db_path


class TestInterestTargeting:
    def test_interest_scoring_prefers_music_and_healing_matches(self, seeded_db):
        from adk_agents.tools.l5_event_tools import _score_seeker_interest

        score = _score_seeker_interest(
            [
                {"content": "Mình thích thiền âm nhạc"},
                {"content": "music healing workshop nghe hay quá"},
                {"content": "chữa lành với âm nhạc"},
            ],
            "Thiền Âm nhạc chữa lành",
        )

        assert score >= 5

    def test_unknown_event_type_falls_back_to_meditation_keywords(self, seeded_db):
        from adk_agents.tools.l5_event_tools import _score_seeker_interest

        score = _score_seeker_interest(
            [{"content": "Mình muốn học thiền căn bản"}],
            "Ngày hội cộng đồng",
        )

        assert score == 1

    def test_find_target_seekers_sorts_by_stage_priority_then_interest(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event

        result = find_target_seekers_for_event(event_id=1, city="Hà Nội", max_seekers=10)

        assert result["status"] == "success"
        thread_ids = [seeker["thread_id"] for seeker in result["seekers"]]
        assert thread_ids[0] == "thread_registered"
        assert thread_ids.index("thread_music") < thread_ids.index("thread_generic")

    def test_find_target_seekers_includes_interest_scores_for_dm_users(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event

        result = find_target_seekers_for_event(event_id=1, city="Hà Nội", max_seekers=10)
        music = next(seeker for seeker in result["seekers"] if seeker["thread_id"] == "thread_music")
        generic = next(seeker for seeker in result["seekers"] if seeker["thread_id"] == "thread_generic")

        assert music["interest_score"] > generic["interest_score"]
        assert music["source"] == "inbox"

    def test_comment_users_are_kept_as_low_interest_targets_without_messages(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event

        result = find_target_seekers_for_event(event_id=1, city="Hà Nội", max_seekers=10)
        comment_target = next(seeker for seeker in result["seekers"] if seeker["thread_id"] == "comment_fb_comment_1")

        assert comment_target["interest_score"] == 0
        assert comment_target["source"] == "comment"

    def test_live_event_log_suppresses_repeat_targeting_but_dry_run_does_not(self, seeded_db):
        from adk_agents.tools.l5_event_tools import find_target_seekers_for_event, log_event_campaign

        log_event_campaign(1, "thread_music", "Music Lover", "dry run", dry_run=True)
        dry_run_result = find_target_seekers_for_event(event_id=1, city="Hà Nội", max_seekers=10)
        assert "thread_music" in [seeker["thread_id"] for seeker in dry_run_result["seekers"]]

        log_event_campaign(1, "thread_music", "Music Lover", "live send", dry_run=False)
        live_result = find_target_seekers_for_event(event_id=1, city="Hà Nội", max_seekers=10)
        assert "thread_music" not in [seeker["thread_id"] for seeker in live_result["seekers"]]
