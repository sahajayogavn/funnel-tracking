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

from fb_pipeline.browser.l2_actions import (
    navigate_to_thread as shared_navigate_to_thread, 
    send_reply_via_cdp as shared_send_reply_via_cdp,
    commit_reply_via_cdp as shared_commit_reply_via_cdp,
    clear_composer_via_cdp as shared_clear_composer_via_cdp
)
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection


# code:agent-mas-001:send-reply-cdp
def send_reply_via_cdp(page, reply_text: str, dry_run: bool = True) -> bool:
    """Draft a reply in the active FB inbox thread via CDP.

    This wrapper is draft-only for inbox MAS callers. It types the message into
    the composer and never exposes send semantics, even if legacy callers pass
    ``dry_run=False``.

    Args:
        page: Playwright page object (connected via CDP).
        reply_text: The text to type as a reply draft.
        dry_run: Legacy compatibility flag. Ignored for inbox drafting.

    Returns:
        bool: True if reply text was drafted successfully.
    """
    return shared_send_reply_via_cdp(page, reply_text, dry_run=True)


def commit_reply_via_cdp(page) -> bool:
    """Hit Enter to send the drafted reply."""
    return shared_commit_reply_via_cdp(page)

def clear_composer_via_cdp(page) -> bool:
    """Clear the composer content."""
    return shared_clear_composer_via_cdp(page)


def navigate_to_thread(page, page_id: str, thread_name: str, thread_id: str = None) -> bool:
    """Navigate to a specific thread in FB Business Suite inbox.

    Args:
        page: Playwright page object.
        page_id: Facebook Page ID.
        thread_name: Name of the thread to navigate to.
        thread_id: Optional exact thread ID to navigate via URL parameter.

    Returns:
        bool: True if thread was found and clicked.
    """
    return shared_navigate_to_thread(page, page_id, thread_name, thread_id)


def log_auto_reply(thread_id: str, reply_text: str, agent_name: str = "responder",
                   escalated: bool = False, dry_run: bool = True,
                   customer_message_timestamp: str | None = None) -> dict:
    """Log an auto-generated reply draft to FrankenSQLite.

    Args:
        thread_id: The thread this draft belongs to.
        reply_text: The generated reply text.
        agent_name: Which agent created this draft (default: responder).
        escalated: Whether this was escalated to human review.
        dry_run: Legacy compatibility flag retained for existing schema usage.
        customer_message_timestamp: Latest customer-message boundary acknowledged
            by this drafted reply.

    Returns:
        dict: Status of the logging operation.
    """
    conn = None
    try:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO auto_replies (thread_id, reply_text, agent_name, escalated, dry_run, customer_message_timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, reply_text, agent_name, escalated, dry_run, customer_message_timestamp)
            )
        except Exception as exc:
            if "customer_message_timestamp" not in str(exc):
                raise
            logger.warning("auto_replies missing customer_message_timestamp; logging draft without boundary")
            conn.execute(
                "INSERT INTO auto_replies (thread_id, reply_text, agent_name, escalated, dry_run) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, reply_text, agent_name, escalated, dry_run)
            )
        conn.commit()
        return {
            "status": "logged",
            "thread_id": thread_id,
            "dry_run": dry_run,
            "customer_message_timestamp": customer_message_timestamp,
        }
    except Exception as e:
        logger.error(f"Failed to log reply draft: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn is not None:
            conn.close()
