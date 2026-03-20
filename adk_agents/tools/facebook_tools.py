"""
Facebook inbox tools for ADK agents (CDP-based).
code:agent-mas-001:facebook-tools

These tools handle Facebook inbox interactions via CDP at port 9222.
NO Graph API is used — Facebook closed it for inbox access.

Uses the shared get_db_connection() from fetch_fb_messages.py
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

from tools.fetch_fb_messages import get_db_connection


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
    try:
        # Find the reply input box (FB Business Suite)
        reply_selector = (
            'div[aria-label*="Reply"], '
            'div[aria-label*="Nhắn tin"], '
            'div[aria-label*="Trả lời"], '
            'div[role="textbox"][contenteditable="true"]'
        )

        reply_box = page.wait_for_selector(reply_selector, timeout=8000)
        if not reply_box:
            logger.warning("Reply box not found")
            return False

        # Click to focus
        reply_box.click()
        page.wait_for_timeout(500)

        # Type the reply with human-like delay
        page.keyboard.type(reply_text, delay=30)
        page.wait_for_timeout(500)

        if dry_run:
            logger.info(f"[DRY-RUN] Reply typed (NOT sent): {reply_text[:80]}...")
            return True
        else:
            # Press Enter to send
            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)
            logger.info(f"[LIVE] Reply sent: {reply_text[:80]}...")
            return True

    except Exception as e:
        logger.error(f"Reply failed: {e}")
        return False


def navigate_to_thread(page, page_id: str, thread_name: str) -> bool:
    """Navigate to a specific thread in FB Business Suite inbox.

    Args:
        page: Playwright page object.
        page_id: Facebook Page ID.
        thread_name: Name of the thread to navigate to.

    Returns:
        bool: True if thread was found and clicked.
    """
    try:
        inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

        # Check if already on inbox page
        if "inbox" not in page.url:
            page.goto(inbox_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

        # Wait for thread list
        page.wait_for_selector('div._ikh', timeout=10000)
        page.wait_for_timeout(1000)

        # Find and click the thread by name
        thread_el = page.locator('div._ikh').filter(has_text=thread_name).first
        if thread_el:
            thread_el.click(force=True, timeout=5000)
            page.wait_for_timeout(2000)
            logger.info(f"Navigated to thread: {thread_name}")
            return True

        logger.warning(f"Thread '{thread_name}' not found in visible list")
        return False

    except Exception as e:
        logger.error(f"Navigation failed: {e}")
        return False


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
