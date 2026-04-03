import logging
from datetime import datetime, timedelta

logger = logging.getLogger("scheduler_core")

# code:tool-scheduler-001:decision-core

TEMPERATURE_THRESHOLDS = {
    "Follower": {"hot": 3, "warm": 7, "cool": 21},
    "Curious Seeker": {"hot": 3, "warm": 7, "cool": 14},
    "Registered": {"hot": 2, "warm": 5, "cool": 14},
    "Deep Learner": {"hot": 7, "warm": 14, "cool": 28},
    "Sahaja Yogi": {"hot": 14, "warm": 30, "cool": 90},
}

COOL_SEQUENCE_TEMPLATES = {
    1: "Chào bạn, lâu rồi mình không thấy bạn. Hy vọng bạn khỏe 🙏",
    2: "Mình chia sẻ bạn mẹo thiền 5 phút mỗi sáng giúp tỉnh táo cả ngày 🌿",
    3: "Cuối tuần này có Thiền Âm nhạc tại {city}, hoàn toàn miễn phí. Bạn muốn tham gia không?",
}

STAGE_TO_STRATEGY_STAGE = {
    "intake": "Follower",
    "user": "Follower",
    "seeker": "Curious Seeker",
    "registered": "Registered",
    "public program seeker": "Registered",
    "seeker public program": "Registered",
    "seeker_public_program": "Registered",
    "18-week seeker": "Deep Learner",
    "18 week seeker": "Deep Learner",
    "seeker_18_weeks": "Deep Learner",
    "seed": "Sahaja Yogi",
    "sahaja yogi": "Sahaja Yogi",
    "sahaja_yogi": "Sahaja Yogi",
    "sahaja yogi dedicated": "Sahaja Yogi",
    "sahaja_yogi_dedicated": "Sahaja Yogi",
    "sahaja mahayogi": "Sahaja Yogi",
    "sahaja_mahayogi": "Sahaja Yogi",
}

def _normalize_strategy_stage(lead_stage: str | None) -> str:
    normalized = " ".join((lead_stage or "").strip().replace("_", " ").replace("-", " ").lower().split())
    return STAGE_TO_STRATEGY_STAGE.get(normalized, "Follower")

def _parse_db_time(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

def _compute_temperature(lead_stage: str | None, last_interaction: str | None, stored_temperature: str | None = None) -> str:
    if stored_temperature in {"dormant", "unsubscribed"}:
        return stored_temperature

    if lead_stage and lead_stage.lower() in {"spam", "unsubscribed"}:
        return "unsubscribed"

    interaction_at = _parse_db_time(last_interaction)
    if interaction_at is None:
        return stored_temperature or "warm"

    days_silent = max(0, (datetime.now() - interaction_at).days)
    thresholds = TEMPERATURE_THRESHOLDS[_normalize_strategy_stage(lead_stage)]
    if days_silent < thresholds["hot"]:
        return "hot"
    if days_silent < thresholds["warm"]:
        return "warm"
    if days_silent < thresholds["cool"]:
        return "cool"
    return "cold"

# code:tool-stage-001
# code:tool-event-001
def _load_user_state(thread_id: str) -> dict | None:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection

    if thread_id.startswith("comment_"):
        comment_key = thread_id[len("comment_"):]
        conn = get_comment_db_connection()
        try:
            row = conn.execute(
                "SELECT commenter_name AS thread_name, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step, city "
                "FROM comment_users WHERE fb_user_id = ?",
                (comment_key,),
            ).fetchone()
            if row:
                return {"thread_id": thread_id, **dict(row)}
            return None
        finally:
            conn.close()

    conn = get_db_connection(logger=logger)
    try:
        row = conn.execute(
            "SELECT thread_id, thread_name, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step, city "
            "FROM users WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def _thread_has_pending_reply(page_id: str, thread_id: str) -> bool:
    from adk_agents.tools.l5_seeker_tools import find_unreplied_threads

    result = find_unreplied_threads(page_id, limit=200)
    if result.get("status") != "success":
        return False
    return any(thread.get("thread_id") == thread_id for thread in result.get("threads", []))

def _recent_live_touch_exists(thread_id: str, since_hours: int = 24) -> bool:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

    cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
    conn = get_db_connection(logger=logger)
    try:
        checks = [
            ("SELECT 1 FROM warmup_campaigns WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND sent_at > ? LIMIT 1", (thread_id, cutoff)),
            ("SELECT 1 FROM event_campaigns WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND sent_at > ? LIMIT 1", (thread_id, cutoff)),
            ("SELECT 1 FROM auto_replies WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND created_at > ? LIMIT 1", (thread_id, cutoff)),
        ]
        for query, params in checks:
            if conn.execute(query, params).fetchone():
                return True
        return False
    finally:
        conn.close()

def _has_recent_live_event(thread_id: str, since_days: int = 90) -> bool:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
    conn = get_db_connection(logger=logger)
    try:
        row = conn.execute(
            "SELECT 1 FROM event_campaigns WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND sent_at > ? LIMIT 1",
            (thread_id, cutoff),
        ).fetchone()
        return row is not None
    finally:
        conn.close()

# code:tool-scheduler-001:cool-sequence
def _get_next_cool_step(user_state: dict) -> int | None:
    current_step = int(user_state.get("cool_step") or 0)
    return current_step + 1 if current_step < 3 else None

# code:tool-stage-001
# code:tool-scheduler-001:cool-sequence
def _update_user_decision_state(
    thread_id: str,
    temperature: str,
    warmup_sent: bool = False,
    cool_step: int | None = None,
):
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection

    is_comment_user = thread_id.startswith("comment_")
    if is_comment_user:
        conn = get_comment_db_connection()
        where_clause = "fb_user_id = ?"
        thread_key = thread_id[len("comment_"):]
        table_name = "comment_users"
    else:
        conn = get_db_connection(logger=logger)
        where_clause = "thread_id = ?"
        thread_key = thread_id
        table_name = "users"

    try:
        if warmup_sent:
            if cool_step is None:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ?, last_warmup_at = datetime('now'), warmup_count = COALESCE(warmup_count, 0) + 1 WHERE {where_clause}",
                    (temperature, thread_key),
                )
            else:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ?, last_warmup_at = datetime('now'), warmup_count = COALESCE(warmup_count, 0) + 1, cool_step = ? WHERE {where_clause}",
                    (temperature, cool_step, thread_key),
                )
        else:
            if cool_step is None:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ? WHERE {where_clause}",
                    (temperature, thread_key),
                )
            else:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ?, cool_step = ? WHERE {where_clause}",
                    (temperature, cool_step, thread_key),
                )
        conn.commit()
    finally:
        conn.close()

def _evaluate_proactive_eligibility(page_id: str, route: str, thread_id: str) -> tuple[bool, str, dict]:
    user_state = _load_user_state(thread_id)
    if not user_state:
        return False, "missing_user_state", {"thread_id": thread_id}

    temperature = _compute_temperature(
        user_state.get("lead_stage"),
        user_state.get("last_interaction"),
        user_state.get("temperature"),
    )
    payload = {
        "thread_id": thread_id,
        "lead_stage": user_state.get("lead_stage"),
        "temperature": temperature,
        "last_interaction": user_state.get("last_interaction"),
    }

    if (user_state.get("lead_stage") or "").lower() in {"spam", "unsubscribed"} or temperature == "unsubscribed":
        return False, "hard_stop_status", payload

    if route == "warmup" and temperature == "dormant":
        return False, "dormant_blocks_warmup", payload

    if _thread_has_pending_reply(page_id, thread_id):
        return False, "pending_inbox_reply", payload

    if _recent_live_touch_exists(thread_id, since_hours=24):
        return False, "recent_live_touch", payload

    if route == "event" and temperature == "dormant" and _has_recent_live_event(thread_id, since_days=90):
        return False, "dormant_quarterly_limit", payload

    return True, "eligible", payload
