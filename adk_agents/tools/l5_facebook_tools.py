"""
Facebook inbox tools for ADK agents (CDP-based).
code:agent-mas-001:facebook-tools

These tools handle Facebook inbox interactions via CDP at port 9222.
NO Graph API is used — Facebook closed it for inbox access.

Uses the shared SQLite store helpers from fb_pipeline.persistence.l4_sqlite_store
(FrankenSQLite) for all database operations.
"""
import os
import sys
import logging

logger = logging.getLogger("mas.facebook_tools")

# Add project root to path so we can import from tools/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.browser.l2_actions import navigate_to_thread as shared_navigate_to_thread, send_reply_via_cdp as shared_send_reply_via_cdp
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection


# code:agent-mas-001:send-reply-cdp
def send_reply_via_cdp(page, reply_text: str, dry_run: bool = True) -> bool:
    """Type a reply in the currently active FB inbox thread via CDP.

    In dry-run mode: types the message but does NOT press Enter.
    In live mode: types the message AND presses Enter to send.

    Args:
        page: Playwright page object (connected via CDP).
        reply_text: The text to type as a reply.
        dry_run: If True, type but don't send. If False, type and send.

    Returns:
        bool: True if reply was typed (and optionally sent) successfully.
    """
    return shared_send_reply_via_cdp(page, reply_text, dry_run=dry_run)


def navigate_to_thread(page, page_id: str, thread_name: str) -> bool:
    """Navigate to a specific thread in FB Business Suite inbox.

    Args:
        page: Playwright page object.
        page_id: Facebook Page ID.
        thread_name: Name of the thread to navigate to.

    Returns:
        bool: True if thread was found and clicked.
    """
    return shared_navigate_to_thread(page, page_id, thread_name)


def log_auto_reply(thread_id: str, reply_text: str, agent_name: str = "responder",
                   escalated: bool = False) -> dict:
    """Log an auto-generated reply to FrankenSQLite.

    Args:
        thread_id: The thread this reply belongs to.
        reply_text: The generated reply text.
        agent_name: Which agent created this reply (default: responder).
        escalated: Whether this was escalated to human review.

    Returns:
        dict: Status of the logging operation.
    """
    try:
        conn = get_db_connection()
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
        conn.execute(
            "INSERT INTO auto_replies (thread_id, reply_text, agent_name, escalated) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, reply_text, agent_name, escalated)
        )
        conn.commit()
        conn.close()
        return {"status": "logged", "thread_id": thread_id}
    except Exception as e:
        logger.error(f"Failed to log reply: {e}")
        return {"status": "error", "error": str(e)}
