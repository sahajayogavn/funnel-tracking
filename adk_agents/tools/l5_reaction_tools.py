"""
Reaction tools for MAS Route 1 — React to messages and comments.
code:agent-mas-001:reaction-tools

Tools for finding unreacted items (messages/comments) and logging
reactions applied by the Reactor agent.
"""
import os
import sys
import logging

logger = logging.getLogger("mas.reaction_tools")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection


def find_unreacted_items(page_id: str, limit: int = 20) -> dict:
    """Find messages and comments that have not been reacted to yet.

    Args:
        page_id: The Facebook Page ID.
        limit: Maximum items to return.

    Returns:
        dict: Status, count, and list of unreacted items.
    """
    items = []
    try:
        # Find unreacted inbox messages (customer messages only)
        conn = get_db_connection()
        msg_rows = conn.execute('''
            SELECT m.id, m.thread_id, m.content, m.sender, m.message_timestamp,
                   t.thread_name
            FROM messages m
            JOIN threads t ON m.thread_id = t.id
            WHERE t.page_id = ?
            AND m.sender = 'Customer'
            AND NOT EXISTS (
                SELECT 1 FROM reactions r
                WHERE r.item_type = 'message'
                AND r.item_id = CAST(m.id AS TEXT)
            )
            ORDER BY m.id DESC
            LIMIT ?
        ''', (page_id, limit)).fetchall()
        conn.close()

        for r in msg_rows:
            items.append({
                "item_type": "message",
                "item_id": str(r["id"]),
                "thread_id": r["thread_id"],
                "content": r["content"],
                "sender": r["sender"],
                "timestamp": r["message_timestamp"],
                "thread_name": r["thread_name"],
            })

        # Find unreacted comments
        conn2 = get_comment_db_connection()
        cmt_rows = conn2.execute('''
            SELECT c.id, c.post_id, c.commenter_name, c.comment_text,
                   c.comment_timestamp
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            WHERE p.page_id = ?
            AND NOT EXISTS (
                SELECT 1 FROM reactions r
                WHERE r.item_type = 'comment'
                AND r.item_id = CAST(c.id AS TEXT)
            )
            ORDER BY c.id DESC
            LIMIT ?
        ''', (page_id, limit)).fetchall()
        conn2.close()

        for r in cmt_rows:
            items.append({
                "item_type": "comment",
                "item_id": str(r["id"]),
                "post_id": r["post_id"],
                "content": r["comment_text"],
                "sender": r["commenter_name"],
                "timestamp": r["comment_timestamp"],
            })

        return {"status": "success", "items": items, "count": len(items)}
    except Exception as e:
        logger.error(f"find_unreacted_items failed: {e}")
        return {"status": "error", "error": str(e)}


def log_reaction(item_type: str, item_id: str, reaction_type: str,
                 agent_name: str = "reactor", dry_run: bool = True) -> dict:
    """Log a reaction decision to FrankenSQLite.

    Args:
        item_type: 'message' or 'comment'.
        item_id: The ID of the item reacted to.
        reaction_type: Reaction type (like, love, care, haha, wow, sad, angry).
        agent_name: Name of the agent that made the decision.
        dry_run: If True, logs as dry-run (not actually applied).

    Returns:
        dict: Status of the logging operation.
    """
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT OR IGNORE INTO reactions "
            "(item_type, item_id, reaction_type, agent_name, dry_run) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_type, item_id, reaction_type, agent_name, dry_run)
        )
        conn.commit()
        conn.close()
        return {"status": "logged", "item_type": item_type, "item_id": item_id,
                "reaction_type": reaction_type, "dry_run": dry_run}
    except Exception as e:
        logger.error(f"log_reaction failed: {e}")
        return {"status": "error", "error": str(e)}


def apply_reaction_via_cdp(page, item_type: str, item_id: str,
                            reaction_type: str, dry_run: bool = True) -> bool:
    """Apply a reaction via CDP DOM click.

    Args:
        page: Playwright page connected via CDP.
        item_type: 'message' or 'comment'.
        item_id: ID of the item to react to.
        reaction_type: Reaction to apply (like, love, care, etc.).
        dry_run: If True, log but don't click.

    Returns:
        bool: True if reaction was applied (or would be in dry-run).
    """
    if dry_run:
        logger.info(f"[DRY-RUN] Would apply {reaction_type} to {item_type} {item_id}")
        return True

    # CDP reaction application — selectors may need updating if FB changes UI
    # For messages: hover over message bubble → click reaction button
    # For comments: hover over comment → click like/reaction button
    logger.warning(
        f"[LIVE] CDP reaction for {item_type} {item_id} "
        f"({reaction_type}) — not yet implemented. Logged only."
    )
    return False
