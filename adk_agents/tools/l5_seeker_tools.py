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
import json

logger = logging.getLogger("mas.seeker_tools")

# Add project root to path so we can import from tools/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
from fb_pipeline.contracts.l1_message_kind import canonical_sender_for_actor


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
            "SELECT thread_name, phone, email, fb_url, city, lead_stage, program_code, temperature, "
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
                "program_code": row["program_code"],
                "temperature": row["temperature"],
                "first_seen": row["first_seen"],
                "last_interaction": row["last_interaction"],
            }
        return {"status": "not_found", "thread_id": thread_id}
    except Exception as e:
        logger.error(f"Seeker lookup failed: {e}")
        return {"status": "error", "error": str(e)}


def get_thread_messages(thread_id: str, limit: int = 150) -> dict:
    """Get source-aware history for one thread without inventing attribution.

    Args:
        thread_id: The thread ID to fetch messages for.
        limit: DB query limit (default 150). Will dynamically cap around 3500 chars.

    Returns:
        dict: ``messages`` contains bodies, source evidence, and a compact
        reaction annotation attached only to the message Facebook targeted.

        Older read-only snapshots can predate the evidence migration.  Missing
        columns/tables are represented as unknown/empty evidence rather than
        making the whole history unreadable or inferring values from content.
    """
    conn = None
    try:
        conn = get_db_connection()
        message_columns = _table_columns(conn, "messages")
        # code:inbox-msg-kind-001 — system banners/reactions never become
        # message text supplied to the model.  Column expressions deliberately
        # cover old readonly DB snapshots that lack notation migration fields.
        message_fields = (
            ("source_id", "NULL"),
            ("sender", "'Unknown'"),
            ("sender_confidence", "'unknown'"),
            ("content", "''"),
            ("message_timestamp", "NULL"),
            ("message_at", "NULL"),
            ("message_at_approx", "NULL"),
            ("raw_timestamp", "NULL"),
            ("day_context", "NULL"),
            ("time_precision", "'unknown'"),
            ("reply_to_message_id", "NULL"),
            ("quoted_sender", "NULL"),
            ("quoted_sender_confidence", "'unknown'"),
            ("quoted_text", "NULL"),
            ("sender_evidence", "NULL"),
            ("quote_evidence", "NULL"),
            ("reaction_annotation_json", "'[]'"),
            ("seq", "0"),
            ("id", "0"),
        )
        message_select = ", ".join(
            _column_or_default("m", message_columns, field, fallback)
            for field, fallback in message_fields
        )
        kind_clause = "AND m.kind = 'message'" if "kind" in message_columns else ""
        order_column = "m.seq" if "seq" in message_columns else "m.id"
        rows = conn.execute(
            f"SELECT {message_select} FROM messages m "
            f"WHERE m.thread_id = ? {kind_clause} "
            f"ORDER BY {order_column} DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()

        messages = []
        total_chars = 0
        for r in rows:
            content = r["content"] or ""
            # Calculate char cost, add buffer for JSON structure
            char_cost = len(content) + 50
            if total_chars + char_cost > 3500 and total_chars > 0:
                break
            messages.append({
                # A legacy CSS-derived Page/Customer label is not safe input
                # when it also contains a merged quote/reaction fragment.
                # The shared contract downgrades precisely that known-corrupt
                # shape without blanketing all readable legacy history.
                "sender": canonical_sender_for_actor(dict(r)),
                "content": content,
                "timestamp": r["message_timestamp"],
                "message_at": r["message_at"],
                "message_at_approx": r["message_at_approx"],
                "seq": r["seq"],
                "source_id": r["source_id"],
                "sender_confidence": r["sender_confidence"],
                "raw_timestamp": r["raw_timestamp"],
                "day_context": r["day_context"],
                "time_precision": r["time_precision"],
                "reply_to_message_id": r["reply_to_message_id"],
                "quoted_sender": r["quoted_sender"],
                "quoted_sender_confidence": r["quoted_sender_confidence"],
                "quoted_text": r["quoted_text"],
                "sender_evidence": r["sender_evidence"],
                "quote_evidence": r["quote_evidence"],
                "reactions": _decode_reaction_annotation(r["reaction_annotation_json"]),
            })
            total_chars += char_cost

        # Reverse so the order is chronological (Oldest -> Newest)
        messages.reverse()
        return {
            "status": "success",
            "messages": messages,
            "count": len(messages),
        }
    except Exception as e:
        logger.error(f"Message fetch failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn is not None:
            conn.close()


def _table_columns(conn, table_name: str) -> set[str]:
    """Return a table's columns without assuming an evidence migration ran."""
    # Table names are module constants, not user input.  PRAGMA cannot bind a
    # table identifier; keep the allow-list here so this helper remains safe.
    if table_name != "messages":
        raise ValueError(f"Unsupported table: {table_name}")
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _column_or_default(table_alias: str, columns: set[str], column: str, fallback: str) -> str:
    """Build one stable output alias for a nullable legacy schema field."""
    if column in columns:
        return f"{table_alias}.{column} AS {column}"
    return f"{fallback} AS {column}"


def _decode_reaction_annotation(value) -> list[dict]:
    """Return only the stable annotation schema from a DB value."""
    try:
        items = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def find_unreplied_threads(page_id: str, limit: int = 10) -> dict:
    """Find threads whose latest genuine customer message has not been acknowledged.

    Only rows with ``kind='message'`` count (code:inbox-msg-kind-001); a
    re-scraped "replied to an ad." banner cannot make a thread look unreplied.

    Args:
        page_id: The Facebook Page ID to search threads for.
        limit: Maximum number of actionable threads to return.

    Returns:
        dict: Status and list of actionable thread IDs and names.
    """
    try:
        conn = get_db_connection()

        rows = conn.execute('''
            WITH last AS (
                SELECT thread_id, MAX(seq) AS max_seq FROM messages
                WHERE kind = 'message' GROUP BY thread_id
            ),
            lm AS (
                SELECT m.thread_id, m.sender, m.seq, m.message_timestamp, m.timestamp AS recorded_at 
                FROM messages m JOIN last ON last.thread_id=m.thread_id AND last.max_seq=m.seq
            ),
            proposed AS (
                SELECT thread_id, MAX(CAST(json_extract(payload_json,'$.last_message_seq') AS INTEGER)) AS seq
                FROM telegram_hitl_queue WHERE route='inbox' GROUP BY thread_id
            )
            , scheduled AS (
                SELECT thread_id, last_message_seq AS seq
                FROM inbox_mas_processed_messages
            )
            SELECT t.id, t.thread_name, lm.sender AS latest_sender, lm.seq AS latest_seq
            FROM lm JOIN threads t ON t.id=lm.thread_id
            LEFT JOIN proposed p ON p.thread_id=lm.thread_id
            LEFT JOIN scheduled s ON s.thread_id=lm.thread_id
            -- An unresolved actor with a non-empty body is deliberately
            -- surfaced to the deterministic gate.  It must become a
            -- needs-review record, never an automatic customer draft; leaving
            -- it out here would turn parser uncertainty into a silent skip.
            WHERE t.page_id=? AND lm.sender IN ('Customer', 'Unknown')
              AND NULLIF(TRIM((SELECT content FROM messages
                               WHERE thread_id=lm.thread_id AND seq=lm.seq)), '') IS NOT NULL
              AND (p.seq IS NULL OR p.seq < lm.seq)
              AND (s.seq IS NULL OR s.seq < lm.seq)
            ORDER BY t.inbox_sort_index LIMIT ?;
        ''', (page_id, limit)).fetchall()
        conn.close()

        threads = [{"thread_id": r["id"], "thread_name": r["thread_name"],
                    "latest_sender": r["latest_sender"], "latest_seq": r["latest_seq"]}
                   for r in rows]
        return {"status": "success", "threads": threads, "count": len(threads)}
    except Exception as e:
        logger.error(f"Unreplied threads query failed: {e}")
        return {"status": "error", "error": str(e)}


def claim_scheduled_inbox_message(thread_id: str, expected_message_seq: int | None = None) -> bool:
    """Atomically mark the latest customer turn as consumed by scheduled MAS.

    This deliberately happens *before* an LLM call.  The marker suppresses
    repeat scheduled generation for every downstream outcome: awaiting a human
    reply, approved/rejected draft, escalation, timeout, or agent error.  The
    manual dashboard MAS path does not call this function, so an operator can
    explicitly regenerate a selected thread.  A newer customer turn replaces
    the sequence and becomes eligible normally.
    """
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT MAX(seq) AS seq FROM messages "
            "WHERE thread_id=? AND sender='Customer' AND kind='message'",
            (thread_id,),
        ).fetchone()
        seq = row["seq"] if row else None
        if seq is None:
            conn.rollback()
            return False
        # Do not claim a newer turn that arrived after the caller assembled
        # its prompt.  It must be read in a later cycle with its own context.
        if expected_message_seq is not None and int(seq) != int(expected_message_seq):
            conn.rollback()
            return False
        cursor = conn.execute(
            """INSERT INTO inbox_mas_processed_messages
                   (thread_id, last_message_seq, processed_at, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(thread_id) DO UPDATE SET
                   last_message_seq=excluded.last_message_seq,
                   updated_at=CURRENT_TIMESTAMP
               WHERE inbox_mas_processed_messages.last_message_seq < excluded.last_message_seq""",
            (thread_id, int(seq)),
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
