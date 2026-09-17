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


# code:bug-action-queue-duplicate-proposal-001:dedup-guard
def active_proposal_status(target_id: Optional[str], queue_type: str, payload_type: Optional[str] = None) -> Optional[str]:
    """Status ('pending'/'approved'/'executing') of the live proposal for
    `target_id` in `queue_type`, or None when there is none."""
    if not target_id:
        return None
    conn = get_db_connection()
    try:
        query = (
            "SELECT status FROM action_queue WHERE target_id = ? AND queue_type = ? "
            "AND status NOT IN ('executed', 'rejected', 'failed')"
        )
        params: list[Any] = [target_id, queue_type]
        if payload_type:
            query += " AND json_extract(payload_json, '$.type') = ?"
            params.append(payload_type)
        row = conn.execute(query + " ORDER BY id DESC LIMIT 1", params).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def has_active_proposal(target_id: Optional[str], queue_type: str, payload_type: Optional[str] = None) -> bool:
    """True if `target_id` already has a non-terminal (pending/approved/executing)
    proposal of the same kind. A target must never have two live proposals in the
    same queue_type (e.g. two 'reply_message' proposals), so every caller of
    `enqueue_action` for a single target must check this first."""
    return active_proposal_status(target_id, queue_type, payload_type) is not None


def enqueue_action(*, queue_type: str, page_id: str, target_type: str,
                   target_id: Optional[str], target_name: Optional[str],
                   action_text: Optional[str] = None,
                   reaction_type: Optional[str] = None,
                   payload: Optional[dict[str, Any]] = None) -> int:
    action_id, _ = _insert_action(
        queue_type=queue_type, page_id=page_id, target_type=target_type, target_id=target_id,
        target_name=target_name, action_text=action_text, reaction_type=reaction_type,
        payload=payload, replace_active=False,
    )
    return action_id


def replace_action(*, queue_type: str, page_id: str, target_type: str,
                   target_id: Optional[str], target_name: Optional[str],
                   action_text: Optional[str] = None,
                   reaction_type: Optional[str] = None,
                   payload: Optional[dict[str, Any]] = None,
                   source: str = "mas_regenerate") -> tuple[int, list[int]]:
    """Enqueue a fresh proposal that supersedes the target's current
    pending/approved one of the same kind, atomically (the unique index
    idx_action_queue_one_active_per_target forbids two live rows, so the old
    row is rejected and the new one inserted in one transaction). An
    `executing` row is never replaced: the worker already owns it, and the
    insert fails. Returns (new_id, superseded_ids)."""
    return _insert_action(
        queue_type=queue_type, page_id=page_id, target_type=target_type, target_id=target_id,
        target_name=target_name, action_text=action_text, reaction_type=reaction_type,
        payload=payload, replace_active=True, source=source,
    )


def _insert_action(*, queue_type: str, page_id: str, target_type: str,
                   target_id: Optional[str], target_name: Optional[str],
                   action_text: Optional[str], reaction_type: Optional[str],
                   payload: Optional[dict[str, Any]], replace_active: bool,
                   source: str = "mas_regenerate") -> tuple[int, list[int]]:
    if queue_type not in QUEUE_TYPES:
        raise ValueError(f"Unknown queue type: {queue_type}")
    if not action_text and not reaction_type:
        raise ValueError("An action must contain action_text or reaction_type")
    payload = payload or {}
    insert_sql = """INSERT INTO action_queue
                    (queue_type, page_id, target_type, target_id, target_name,
                     action_text, reaction_type, payload_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')"""
    insert_params = (queue_type, page_id, target_type, target_id, target_name,
                     action_text, reaction_type, json.dumps(payload, ensure_ascii=False))
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        superseded: list[int] = []
        if replace_active and target_id:
            superseded = [int(r["id"]) for r in conn.execute(
                """SELECT id FROM action_queue WHERE target_id = ? AND queue_type = ?
                   AND status IN ('pending', 'approved')
                   AND COALESCE(json_extract(payload_json, '$.type'), '') = ?""",
                (target_id, queue_type, payload.get("type") or ""),
            ).fetchall()]
            # Free the unique slot (idx_action_queue_one_active_per_target) before inserting.
            conn.executemany(
                "UPDATE action_queue SET status='rejected', approval_source=?, updated_at=datetime('now') WHERE id=?",
                [(source, old_id) for old_id in superseded],
            )
        new_id = int(conn.execute(insert_sql, insert_params).lastrowid)
        if superseded:
            conn.executemany(
                "UPDATE action_queue SET error_text=? WHERE id=?",
                [(f"superseded by #{new_id}", old_id) for old_id in superseded],
            )
        conn.commit()
        return new_id, superseded
    except Exception:
        conn.rollback()
        raise
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


def peek_next_approved(queue_type: str) -> Optional[dict[str, Any]]:
    """Read-only preview of what `claim_next_action` would pick up next, without
    claiming it. Used by dry-run execution to log intent without ever touching
    the live queue or opening a real browser session."""
    if queue_type not in QUEUE_TYPES:
        raise ValueError(f"Unknown queue type: {queue_type}")
    conn = get_db_connection()
    try:
        head = conn.execute(
            """SELECT * FROM action_queue WHERE queue_type=?
               AND status NOT IN ('executed','rejected','failed') ORDER BY id LIMIT 1""",
            (queue_type,),
        ).fetchone()
        if not head or head["status"] != "approved":
            return None
        return dict(head)
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
