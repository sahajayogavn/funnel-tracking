"""
Event tools for MAS Route 3 — City-targeted event advertising.
code:agent-mas-001:event-tools

Tools for managing events, finding target seekers by city,
and logging event campaign messages.
"""
import os
import sys
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("mas.event_tools")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection
from adk_agents.tools.l5_warmup_tools import normalize_lead_stage


EVENT_STAGE_PRIORITIES = {
    "18-Week Seeker": 4,
    "Registered": 3,
    "Public Program Seeker": 3,
    "Seeker": 2,
    "Intake": 1,
}

# code:tool-event-001:interest-scoring
INTEREST_KEYWORDS = {
    "music": ["âm nhạc", "am nhac", "music", "nhạc", "concert"],
    "healing": ["trị liệu", "tri lieu", "healing", "chữa lành", "wellness", "sức khỏe", "suc khoe"],
    "class": ["lớp", "lop", "khóa", "khoa", "học", "hoc", "zoom", "online", "offline", "đăng ký", "dang ky"],
    "meditation": ["thiền", "thien", "meditate", "meditation"],
}


def create_event(name: str, city: str, event_date: str,
                  description: str = None) -> dict:
    """Create a new event in the events catalog.

    Args:
        name: Event name (e.g., "Lớp Thiền Miễn Phí Hà Nội").
        city: City where the event takes place.
        event_date: Date string (ISO format or human-readable).
        description: Optional longer description.

    Returns:
        dict: Status and the created event's ID.
    """
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            "INSERT INTO events (name, city, event_date, description) "
            "VALUES (?, ?, ?, ?)",
            (name, city, event_date, description)
        )
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {"status": "created", "event_id": event_id}
    except Exception as e:
        logger.error(f"create_event failed: {e}")
        return {"status": "error", "error": str(e)}


def get_upcoming_events(city: str = None, days_ahead: int = 14) -> dict:
    """Get upcoming events, optionally filtered by city.

    Args:
        city: Optional city filter.
        days_ahead: Number of days to look ahead.

    Returns:
        dict: Status, count, and list of upcoming events.
    """
    try:
        conn = get_db_connection()
        now = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        if city:
            rows = conn.execute(
                "SELECT id, name, city, event_date, description, created_at "
                "FROM events WHERE city = ? AND event_date BETWEEN ? AND ? "
                "ORDER BY event_date ASC",
                (city, now, future)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, city, event_date, description, created_at "
                "FROM events WHERE event_date BETWEEN ? AND ? "
                "ORDER BY event_date ASC",
                (now, future)
            ).fetchall()
        conn.close()

        events = [{
            "id": r["id"],
            "name": r["name"],
            "city": r["city"],
            "event_date": r["event_date"],
            "description": r["description"],
            "created_at": r["created_at"],
        } for r in rows]

        return {"status": "success", "events": events, "count": len(events)}
    except Exception as e:
        logger.error(f"get_upcoming_events failed: {e}")
        return {"status": "error", "error": str(e)}


# code:tool-stage-001
# code:tool-event-001:interest-scoring
def _score_seeker_interest(messages: list[dict], event_type: str) -> int:
    event_type_text = (event_type or "").lower()
    keyword_buckets = []

    if any(token in event_type_text for token in ["âm nhạc", "am nhac", "music"]):
        keyword_buckets.extend(["music", "healing"])
    if any(token in event_type_text for token in ["trị liệu", "tri lieu", "healing", "wellness"]):
        keyword_buckets.extend(["healing", "meditation"])
    if any(token in event_type_text for token in ["lớp", "lop", "khóa", "khoa", "4 tuần", "4 tuan", "18 tuần", "18 tuan", "online"]):
        keyword_buckets.extend(["class", "meditation"])

    if not keyword_buckets:
        keyword_buckets.append("meditation")

    keywords = []
    for bucket in keyword_buckets:
        keywords.extend(INTEREST_KEYWORDS.get(bucket, []))

    score = 0
    for message in messages:
        content = (message.get("content") or "").lower()
        for keyword in keywords:
            if keyword in content:
                score += 1
    return score


def _get_thread_messages_for_interest(thread_id: str, limit: int = 30) -> list[dict]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT sender, content, message_timestamp FROM messages WHERE thread_id = ? ORDER BY seq DESC, id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# code:tool-stage-001
# code:tool-event-001
def find_target_seekers_for_event(event_id: int, city: str,
                                   max_seekers: int = 20) -> dict:
    """Find seekers in a specific city who haven't been notified about this event.

    Searches both DM users and comment users, deduplicates, normalizes stage aliases,
    and prioritizes journey stages according to mas_strategy.

    Args:
        event_id: The event ID to advertise.
        city: City to match seekers against.
        max_seekers: Maximum seekers to return.

    Returns:
        dict: Status, count, and list of target seekers.
    """
    seekers = []
    seen_thread_ids = set()

    try:
        conn = get_db_connection()
        event_row = conn.execute(
            "SELECT name, description FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        event_type = " ".join(
            part for part in [event_row["name"], event_row["description"]] if event_row and part
        ) if event_row else ""

        # Search DM users (inbox) by city
        dm_rows = conn.execute('''
            SELECT u.thread_id, u.thread_name, u.city, u.lead_stage, u.last_interaction,
                   u.temperature, u.last_warmup_at, u.warmup_count, u.cool_step
            FROM users u
            WHERE u.city = ?
            AND u.lead_stage NOT IN ('spam', 'unsubscribed')
            AND NOT EXISTS (
                SELECT 1 FROM event_campaigns ec
                WHERE ec.event_id = ? AND ec.thread_id = u.thread_id
                AND COALESCE(ec.dry_run, 1) = 0
            )
            ORDER BY u.last_interaction DESC
            LIMIT ?
        ''', (city, event_id, max_seekers)).fetchall()
        conn.close()

        for r in dm_rows:
            if r["thread_id"] not in seen_thread_ids:
                normalized_stage = normalize_lead_stage(r["lead_stage"])
                messages = _get_thread_messages_for_interest(r["thread_id"])
                interest_score = _score_seeker_interest(messages, event_type)
                seekers.append({
                    "thread_id": r["thread_id"],
                    "name": r["thread_name"],
                    "city": r["city"],
                    "lead_stage": normalized_stage,
                    "lead_stage_priority": EVENT_STAGE_PRIORITIES.get(normalized_stage, 0),
                    "interest_score": interest_score,
                    "source": "inbox",
                    "last_interaction": r["last_interaction"],
                    "temperature": r["temperature"],
                    "last_warmup_at": r["last_warmup_at"],
                    "warmup_count": r["warmup_count"],
                    "cool_step": r["cool_step"],
                })
                seen_thread_ids.add(r["thread_id"])

        # Search comment users by city (they may not have DM threads)
        conn2 = get_comment_db_connection()
        cmt_rows = conn2.execute('''
            SELECT cu.commenter_name, cu.city, cu.fb_user_id, cu.lead_stage, cu.last_interaction,
                   cu.temperature, cu.last_warmup_at, cu.warmup_count, cu.cool_step
            FROM comment_users cu
            WHERE cu.city = ?
            AND cu.lead_stage NOT IN ('spam', 'unsubscribed')
            ORDER BY cu.last_interaction DESC
            LIMIT ?
        ''', (city, max_seekers)).fetchall()
        conn2.close()

        # Comment users don't have thread_ids, so we track by fb_user_id
        for r in cmt_rows:
            pseudo_thread = f"comment_{r['fb_user_id']}" if r["fb_user_id"] else None
            if pseudo_thread and pseudo_thread not in seen_thread_ids:
                normalized_stage = normalize_lead_stage(r["lead_stage"])
                seekers.append({
                    "thread_id": pseudo_thread,
                    "name": r["commenter_name"],
                    "city": r["city"],
                    "lead_stage": normalized_stage,
                    "lead_stage_priority": EVENT_STAGE_PRIORITIES.get(normalized_stage, 0),
                    "interest_score": 0,
                    "source": "comment",
                    "last_interaction": r["last_interaction"],
                    "temperature": r["temperature"],
                    "last_warmup_at": r["last_warmup_at"],
                    "warmup_count": r["warmup_count"],
                    "cool_step": r["cool_step"],
                })
                seen_thread_ids.add(pseudo_thread)

        seekers.sort(
            key=lambda seeker: (
                seeker.get("lead_stage_priority", 0),
                seeker.get("interest_score", 0),
                seeker.get("last_interaction") or "",
            ),
            reverse=True,
        )
        seekers = seekers[:max_seekers]

        return {"status": "success", "seekers": seekers, "count": len(seekers)}
    except Exception as e:
        logger.error(f"find_target_seekers_for_event failed: {e}")
        return {"status": "error", "error": str(e)}


def log_event_campaign(event_id: int, thread_id: str,
                        seeker_name: str = None, message_text: str = "",
                        dry_run: bool = True) -> dict:
    """Log an event campaign message to FrankenSQLite.

    Args:
        event_id: The event being advertised.
        thread_id: The target seeker's thread ID.
        seeker_name: Display name of the seeker.
        message_text: The generated event notification message.
        dry_run: If True, logged as dry-run.

    Returns:
        dict: Status of the logging operation.
    """
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO event_campaigns "
            "(event_id, thread_id, seeker_name, message_text, dry_run) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, thread_id, seeker_name, message_text, dry_run)
        )
        conn.commit()
        conn.close()
        return {"status": "logged", "event_id": event_id,
                "thread_id": thread_id, "dry_run": dry_run}
    except Exception as e:
        logger.error(f"log_event_campaign failed: {e}")
        return {"status": "error", "error": str(e)}
