"""
Seeker CRM tools for ADK agents.
code:agent-mas-001:seeker-tools

These tools allow ADK agents to query the FrankenSQLite database
for seeker information, journey stages, and conversation history.

Uses the shared get_db_connection() from fetch_fb_messages.py
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

from tools.fetch_fb_messages import get_db_connection


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
    """Find threads that have new customer messages with no auto-reply.

    Args:
        page_id: The Facebook Page ID to search threads for.
        limit: Maximum number of unreplied threads to return.

    Returns:
        dict: Status and list of unreplied thread IDs and names.
    """
    try:
        conn = get_db_connection()
        # Ensure auto_replies table exists (FrankenSQLite schema extension)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS auto_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                reply_text TEXT NOT NULL,
                agent_name TEXT DEFAULT 'responder',
                confidence REAL DEFAULT 1.0,
                escalated BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

        rows = conn.execute('''
            SELECT DISTINCT t.id, t.thread_name
            FROM threads t
            JOIN messages m ON m.thread_id = t.id
            WHERE t.page_id = ?
            AND m.sender = 'Customer'
            AND NOT EXISTS (
                SELECT 1 FROM auto_replies ar
                WHERE ar.thread_id = t.id
                AND ar.created_at > datetime(m.timestamp, '-1 hour')
            )
            ORDER BY m.timestamp DESC
            LIMIT ?
        ''', (page_id, limit)).fetchall()
        conn.close()

        threads = [{"thread_id": r["id"], "thread_name": r["thread_name"]}
                   for r in rows]
        return {"status": "success", "threads": threads, "count": len(threads)}
    except Exception as e:
        logger.error(f"Unreplied threads query failed: {e}")
        return {"status": "error", "error": str(e)}
