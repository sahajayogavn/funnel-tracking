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
    "Registered": 0,
    "Public Program Seeker": 0,
    "Seeker": 1,
    "18-Week Seeker": 2,
    "Intake": 3,
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
        # Search DM users (inbox) by city
        conn = get_db_connection()
        dm_rows = conn.execute('''
            SELECT u.thread_id, u.thread_name, u.city, u.lead_stage, u.last_interaction
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
                seekers.append({
                    "thread_id": r["thread_id"],
                    "name": r["thread_name"],
                    "city": r["city"],
                    "lead_stage": normalized_stage,
                    "source": "inbox",
                    "last_interaction": r["last_interaction"],
                })
                seen_thread_ids.add(r["thread_id"])

        # Search comment users by city (they may not have DM threads)
        conn2 = get_comment_db_connection()
        cmt_rows = conn2.execute('''
            SELECT cu.commenter_name, cu.city, cu.fb_user_id, cu.lead_stage, cu.last_interaction
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
                    "source": "comment",
                    "last_interaction": r["last_interaction"],
                })
                seen_thread_ids.add(pseudo_thread)

        seekers.sort(
            key=lambda seeker: (
                EVENT_STAGE_PRIORITIES.get(seeker["lead_stage"], 99),
                seeker.get("last_interaction") or "",
            ),
            reverse=False,
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
