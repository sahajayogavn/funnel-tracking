"""
Warm-up tools for MAS Route 2 — Proactive dormant seeker nurturing.
code:agent-mas-001:warmup-tools

Tools for finding dormant seekers, checking warmup history,
selecting strategies by journey stage, and logging campaigns.
"""
import os
import sys
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("mas.warmup_tools")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection


# --- Warmup Strategy Definitions ---
# code:agent-mas-001:warmup-strategies
STAGE_ALIASES = {
    "user": "Intake",
    "intake": "Intake",
    "follower": "Seeker",
    "engaged": "Seeker",
    "curious seeker": "Seeker",
    "seeker": "Seeker",
    "registered": "Registered",
    "seeker public program": "Public Program Seeker",
    "seeker_public_program": "Public Program Seeker",
    "public program seeker": "Public Program Seeker",
    "attending": "Public Program Seeker",
    "deep learner": "18-Week Seeker",
    "seeker 18 weeks": "18-Week Seeker",
    "seeker_18_weeks": "18-Week Seeker",
    "18 week seeker": "18-Week Seeker",
    "18-week seeker": "18-Week Seeker",
    "seed": "18-Week Seeker",
    "sahaja yogi": "18-Week Seeker",
    "sahaja_yogi": "18-Week Seeker",
    "sahaja yogi dedicated": "18-Week Seeker",
    "sahaja_yogi_dedicated": "18-Week Seeker",
    "sahaja mahayogi": "18-Week Seeker",
    "sahaja_mahayogi": "18-Week Seeker",
}


WARMUP_STRATEGIES = {
    "Intake": {
        "min_days": 3, "max_days": 14,
        "type": "gentle_reminder",
        "template": (
            "Xin chào bạn! Chúng tôi có các lớp thiền Sahaja Yoga "
            "MIỄN PHÍ hàng tuần. Bạn có muốn tham gia không? 🧘"
        ),
    },
    "Seeker": {
        "min_days": 3, "max_days": 14,
        "type": "gentle_reminder",
        "template": (
            "Chào bạn! Rất vui được biết bạn quan tâm đến thiền. "
            "Chúng tôi có lớp thiền miễn phí sắp tới, bạn có muốn "
            "biết thêm chi tiết không? 🌸"
        ),
    },
    "Public Program Seeker": {
        "min_days": 7, "max_days": 30,
        "type": "tip_share",
        "template": (
            "Chào bạn! Bạn có thể thử thiền tại nhà mỗi sáng "
            "chỉ 10-15 phút — ngồi thoải mái, đặt tay lên đùi, "
            "và cảm nhận sự bình an bên trong. 🌿"
        ),
    },
    "18-Week Seeker": {
        "min_days": 14, "max_days": 45,
        "type": "check_in",
        "template": (
            "Chào bạn! Thực hành thiền của bạn gần đây thế nào? "
            "Nếu có bất kỳ câu hỏi nào, đừng ngại hỏi nhé! 💚"
        ),
    },
    "Registered": {
        "min_days": 1, "max_days": 7,
        "type": "class_reminder",
        "template": (
            "Nhắc nhẹ: Lớp thiền sắp tới đang chờ bạn! "
            "Bạn có thể đến được không? 📅"
        ),
    },
}


# code:tool-stage-001
# code:tool-event-001
def find_dormant_seekers(page_id: str = None, min_days: int = 3,
                          max_seekers: int = 10) -> dict:
    """Find seekers who haven't interacted in at least min_days.

    Args:
        page_id: Facebook Page ID (currently unused — filters by time only).
        min_days: Minimum days since last interaction.
        max_seekers: Max number of seekers to return.

    Returns:
        dict: Status, count, and list of dormant seekers.
    """
    try:
        cutoff = (datetime.now() - timedelta(days=min_days)).isoformat()
        seekers = []

        conn = get_db_connection()
        try:
            dm_rows = conn.execute('''
                SELECT thread_id, thread_name, city, lead_stage,
                       last_interaction, first_seen,
                       julianday('now') - julianday(last_interaction) AS days_dormant,
                       temperature, last_warmup_at, warmup_count, cool_step
                FROM users
                WHERE last_interaction < ?
                AND lead_stage NOT IN ('spam', 'unsubscribed')
                ORDER BY last_interaction ASC
                LIMIT ?
            ''', (cutoff, max_seekers)).fetchall()
        finally:
            conn.close()

        seekers.extend({
            "thread_id": r["thread_id"],
            "name": r["thread_name"],
            "city": r["city"],
            "lead_stage": r["lead_stage"] or "Intake",
            "last_interaction": r["last_interaction"],
            "first_seen": r["first_seen"],
            "days_dormant": int(r["days_dormant"]) if r["days_dormant"] else 0,
            "temperature": r["temperature"],
            "last_warmup_at": r["last_warmup_at"],
            "warmup_count": r["warmup_count"],
            "cool_step": r["cool_step"],
            "source": "inbox",
        } for r in dm_rows)

        conn2 = get_comment_db_connection()
        try:
            comment_rows = conn2.execute('''
                SELECT fb_user_id, commenter_name, city, lead_stage,
                       last_interaction, first_seen,
                       julianday('now') - julianday(last_interaction) AS days_dormant,
                       temperature, last_warmup_at, warmup_count, cool_step
                FROM comment_users
                WHERE last_interaction < ?
                AND lead_stage NOT IN ('spam', 'unsubscribed')
                ORDER BY last_interaction ASC
                LIMIT ?
            ''', (cutoff, max_seekers)).fetchall()
        finally:
            conn2.close()

        seekers.extend({
            "thread_id": f"comment_{r['fb_user_id']}" if r["fb_user_id"] else f"comment_name_{r['commenter_name']}",
            "name": r["commenter_name"],
            "city": r["city"],
            "lead_stage": r["lead_stage"] or "Intake",
            "last_interaction": r["last_interaction"],
            "first_seen": r["first_seen"],
            "days_dormant": int(r["days_dormant"]) if r["days_dormant"] else 0,
            "temperature": r["temperature"],
            "last_warmup_at": r["last_warmup_at"],
            "warmup_count": r["warmup_count"],
            "cool_step": r["cool_step"],
            "source": "comment",
        } for r in comment_rows)

        seekers.sort(key=lambda seeker: seeker.get("last_interaction") or "")
        seekers = seekers[:max_seekers]

        return {"status": "success", "seekers": seekers, "count": len(seekers)}
    except Exception as e:
        logger.error(f"find_dormant_seekers failed: {e}")
        return {"status": "error", "error": str(e)}


def was_recently_warmed_up(thread_id: str, days: int = 7) -> bool:
    """Check if a seeker was warmed up within the last N days.

    Args:
        thread_id: The seeker's thread ID.
        days: Lookback window in days.

    Returns:
        bool: True if warmup was sent within the window.
    """
    try:
        conn = get_db_connection()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM warmup_campaigns "
            "WHERE thread_id = ? AND sent_at > ? AND COALESCE(dry_run, 1) = 0",
            (thread_id, cutoff)
        ).fetchone()
        conn.close()
        return row["cnt"] > 0 if row else False
    except Exception as e:
        logger.error(f"was_recently_warmed_up check failed: {e}")
        return False


def normalize_lead_stage(lead_stage: str | None) -> str:
    """Normalize stage aliases from MAS strategy, web UI, and legacy DB values."""
    if not lead_stage:
        return "Intake"

    normalized = " ".join(
        lead_stage.strip().replace("-", " ").replace("_", " ").lower().split()
    )
    return STAGE_ALIASES.get(normalized, lead_stage)


def select_warmup_strategy(lead_stage: str, days_dormant: int) -> dict:
    """Select a warmup strategy based on journey stage and dormancy.

    Args:
        lead_stage: The seeker's current journey stage.
        days_dormant: Number of days since last interaction.

    Returns:
        dict: Strategy with type and template, or None if not applicable.
    """
    normalized_stage = normalize_lead_stage(lead_stage)
    strategy = WARMUP_STRATEGIES.get(normalized_stage)
    if not strategy:
        # Fallback to Intake strategy for unknown stages
        strategy = WARMUP_STRATEGIES["Intake"]

    if days_dormant < strategy["min_days"]:
        return None  # Too soon
    if days_dormant > strategy["max_days"]:
        # Very dormant — use gentle reminder regardless of stage
        return {
            "type": "re_engagement",
            "template": (
                "Chào bạn! Lâu rồi chúng tôi chưa nghe tin bạn. "
                "Nếu bạn vẫn quan tâm đến thiền, chúng tôi luôn chào đón bạn "
                "tham gia các lớp MIỄN PHÍ! 🙏"
            ),
        }

    return {"type": strategy["type"], "template": strategy["template"]}


def log_warmup_campaign(thread_id: str, seeker_name: str = None,
                         strategy_type: str = None, message_text: str = "",
                         dry_run: bool = True) -> dict:
    """Log a warmup campaign attempt to FrankenSQLite.

    Args:
        thread_id: The seeker's thread ID.
        seeker_name: Display name of the seeker.
        strategy_type: Warmup strategy used.
        message_text: The generated warmup message.
        dry_run: If True, logged as dry-run.

    Returns:
        dict: Status of the logging operation.
    """
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO warmup_campaigns "
            "(thread_id, seeker_name, journey_stage, strategy_type, message_text, dry_run) "
            "VALUES (?, ?, (SELECT lead_stage FROM users WHERE thread_id = ?), ?, ?, ?)",
            (thread_id, seeker_name, thread_id, strategy_type, message_text, dry_run)
        )
        conn.commit()
        conn.close()
        return {"status": "logged", "thread_id": thread_id,
                "strategy_type": strategy_type, "dry_run": dry_run}
    except Exception as e:
        logger.error(f"log_warmup_campaign failed: {e}")
        return {"status": "error", "error": str(e)}
