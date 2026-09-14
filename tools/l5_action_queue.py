"""Durable human-in-the-loop queues for every outbound Facebook action.

MAS is permitted to *propose* work only.  The first non-terminal item in a
queue blocks later items, so approval preserves FIFO delivery rather than
letting a newer proposal overtake an older decision.
"""
import json
from typing import Any, Optional

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

QUEUE_TYPES = {
    "reply_message",
    "reply_comment",
    "proactive_comment",
    "proactive_message",
}
TERMINAL_STATUSES = {"executed", "rejected", "failed"}


def enqueue_action(*, queue_type: str, page_id: str, target_type: str,
                   target_id: Optional[str], target_name: Optional[str],
                   action_text: Optional[str] = None,
                   reaction_type: Optional[str] = None,
                   payload: Optional[dict[str, Any]] = None) -> int:
    if queue_type not in QUEUE_TYPES:
        raise ValueError(f"Unknown queue type: {queue_type}")
    if not action_text and not reaction_type:
        raise ValueError("An action must contain action_text or reaction_type")
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO action_queue
               (queue_type, page_id, target_type, target_id, target_name,
                action_text, reaction_type, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (queue_type, page_id, target_type, target_id, target_name,
             action_text, reaction_type, json.dumps(payload or {}, ensure_ascii=False)),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def approve_action(queue_id: int, source: str) -> bool:
    """Approve only a pending proposal; execution happens in a separate job."""
    conn = get_db_connection()
    try:
        changed = conn.execute(
            """UPDATE action_queue
               SET status='approved', approval_source=?, approved_at=datetime('now'), updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (source, queue_id),
        ).rowcount
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def reject_action(queue_id: int, source: str, reason: str = "") -> bool:
    conn = get_db_connection()
    try:
        changed = conn.execute(
            """UPDATE action_queue
               SET status='rejected', approval_source=?, error_text=?, updated_at=datetime('now')
               WHERE id=? AND status IN ('pending','approved')""",
            (source, reason, queue_id),
        ).rowcount
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def claim_next_action(queue_type: str) -> Optional[dict[str, Any]]:
    """Atomically claim the head of one queue, but never skip an undecided head."""
    if queue_type not in QUEUE_TYPES:
        raise ValueError(f"Unknown queue type: {queue_type}")
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        head = conn.execute(
            """SELECT * FROM action_queue WHERE queue_type=?
               AND status NOT IN ('executed','rejected','failed') ORDER BY id LIMIT 1""",
            (queue_type,),
        ).fetchone()
        if not head or head["status"] != "approved":
            conn.commit()
            return None
        changed = conn.execute(
            """UPDATE action_queue SET status='executing', claimed_at=datetime('now'), updated_at=datetime('now')
               WHERE id=? AND status='approved'""", (head["id"],)
        ).rowcount
        conn.commit()
        if not changed:
            return None
        result = dict(head)
        result["status"] = "executing"
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return result
    finally:
        conn.close()


def finish_action(queue_id: int, error: Optional[str] = None) -> None:
    conn = get_db_connection()
    try:
        status = "failed" if error else "executed"
        conn.execute(
            """UPDATE action_queue SET status=?, error_text=?, executed_at=datetime('now'), updated_at=datetime('now')
               WHERE id=? AND status='executing'""", (status, error, queue_id)
        )
        conn.commit()
    finally:
        conn.close()
