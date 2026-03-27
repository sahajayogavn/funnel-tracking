"""
Seeker CRM tools for ADK agents.
code:agent-mas-001:seeker-tools

These tools allow ADK agents to query the FrankenSQLite database
for seeker information, journey stages, and conversation history.

Uses the shared get_db_connection() from fb_pipeline.persistence.l4_sqlite_store
to ensure consistent FrankenSQLite access.
"""
import os
import sys
import logging

logger = logging.getLogger("mas.seeker_tools")

# Add project root to path so we can import from tools/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection


def lookup_seeker(thread_id: str) -> dict:
    """Look up a seeker's profile by their thread ID in the CRM database.

    Args:
        thread_id: The thread ID from Facebook inbox (format: pageId_hash).

    Returns:
        dict: Seeker profile with name, phone, email, city, lead_stage,
              or a 'not_found' status if no record exists.
    """
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT thread_name, phone, email, fb_url, city, lead_stage, "
            "first_seen, last_interaction FROM users WHERE thread_id = ?",
            (thread_id,)
        ).fetchone()
        conn.close()

        if row:
            return {
                "status": "found",
                "name": row["thread_name"],
                "phone": row["phone"],
                "email": row["email"],
                "city": row["city"],
                "lead_stage": row["lead_stage"] or "Intake",
                "first_seen": row["first_seen"],
                "last_interaction": row["last_interaction"],
            }
        return {"status": "not_found", "thread_id": thread_id}
    except Exception as e:
        logger.error(f"Seeker lookup failed: {e}")
        return {"status": "error", "error": str(e)}


def get_thread_messages(thread_id: str, limit: int = 20) -> dict:
    """Get the most recent messages from a specific thread.

    Args:
        thread_id: The thread ID to fetch messages for.
        limit: Maximum number of messages to return (default 20).

    Returns:
        dict: Status and list of messages with sender, content, timestamp.
    """
    try:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT sender, content, message_timestamp FROM messages "
            "WHERE thread_id = ? ORDER BY seq DESC LIMIT ?",
            (thread_id, limit)
        ).fetchall()
        conn.close()

        messages = [
            {"sender": r["sender"], "content": r["content"],
             "timestamp": r["message_timestamp"]}
            for r in reversed(rows)
        ]
        return {"status": "success", "messages": messages, "count": len(messages)}
    except Exception as e:
        logger.error(f"Message fetch failed: {e}")
        return {"status": "error", "error": str(e)}


def find_unreplied_threads(page_id: str, limit: int = 10) -> dict:
    """Find threads whose latest customer message has not been acknowledged.

    Args:
        page_id: The Facebook Page ID to search threads for.
        limit: Maximum number of actionable threads to return.

    Returns:
        dict: Status and list of actionable thread IDs and names.
    """
    try:
        conn = get_db_connection()

        rows = conn.execute('''
            WITH thread_latest_seq AS (
                SELECT thread_id, MAX(seq) as max_seq
                FROM messages
                GROUP BY thread_id
            ),
            latest_message_details AS (
                SELECT
                    m.thread_id,
                    m.sender,
                    m.message_timestamp,
                    m.timestamp AS recorded_at
                FROM messages m
                JOIN thread_latest_seq tls ON m.thread_id = tls.thread_id AND m.seq = tls.max_seq
            ),
            latest_acknowledgements AS (
                SELECT
                    ar.thread_id,
                    MAX(ar.customer_message_timestamp) AS latest_acknowledged_customer_message_timestamp
                FROM auto_replies ar
                WHERE ar.customer_message_timestamp IS NOT NULL
                GROUP BY ar.thread_id
            )
            SELECT
                t.id,
                t.thread_name
            FROM threads t
            JOIN latest_message_details lmd ON lmd.thread_id = t.id
            LEFT JOIN latest_acknowledgements la ON la.thread_id = t.id
            WHERE t.page_id = ?
              AND lmd.sender IN ('Customer', 'Auto_Page')
              AND (
                    la.latest_acknowledged_customer_message_timestamp IS NULL
                    OR lmd.message_timestamp != la.latest_acknowledged_customer_message_timestamp
              )
            ORDER BY lmd.recorded_at DESC
            LIMIT ?
        ''', (page_id, limit)).fetchall()
        conn.close()

        threads = [{"thread_id": r["id"], "thread_name": r["thread_name"]}
                   for r in rows]
        return {"status": "success", "threads": threads, "count": len(threads)}
    except Exception as e:
        logger.error(f"Unreplied threads query failed: {e}")
        return {"status": "error", "error": str(e)}
