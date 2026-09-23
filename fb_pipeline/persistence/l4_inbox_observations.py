"""Durable unresolved evidence, deliberately separate from actor-bearing history."""
import hashlib
import json
from datetime import datetime, timezone


def save_unresolved_observation(conn, record, recipient_id, messages, issues):
    if not str(record.page_id).isdigit() or not str(recipient_id).isdigit():
        raise ValueError("unverified_observation_identity")
    canonical_id = record.page_id + "_" + hashlib.sha256(recipient_id.encode()).hexdigest()[:16]
    if record.thread_id != canonical_id or not record.thread_name.strip():
        raise ValueError("observation_thread_identity_mismatch")
    payload = json.dumps({"messages": messages, "issues": issues}, ensure_ascii=False, sort_keys=True)
    observation_id = hashlib.sha256(
        (record.page_id + ":" + recipient_id + ":" + payload).encode()
    ).hexdigest()
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        # Identity was verified before this branch. Message incompleteness must
        # not hide an otherwise identified seeker from the dashboard. Do not
        # stamp fetched_at or last_interaction: neither message coverage nor
        # the time of a customer turn has been established.
        thread = conn.execute(
            "INSERT INTO threads (id,page_id,thread_name,last_synced_time,inbox_sort_index) "
            "VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "thread_name=excluded.thread_name, last_synced_time=excluded.last_synced_time, "
            "inbox_sort_index=COALESCE(excluded.inbox_sort_index,threads.inbox_sort_index) "
            "WHERE threads.page_id=excluded.page_id RETURNING id",
            (record.thread_id, record.page_id, record.thread_name, observed_at,
             getattr(record, "dom_index", None)),
        ).fetchone()
        if thread is None:
            raise ValueError("stored_thread_page_conflict")
        seeker = conn.execute(
            "INSERT INTO users (thread_id,thread_name,fb_url,city,lead_stage,last_interaction,last_synced_at) "
            "VALUES (?,?,?,'Unknown','Intake',NULL,?) "
            "ON CONFLICT(thread_id) DO UPDATE SET thread_name=excluded.thread_name, "
            "fb_url=excluded.fb_url,last_synced_at=excluded.last_synced_at "
            "WHERE users.fb_url IS NULL OR users.fb_url='' OR users.fb_url=excluded.fb_url RETURNING id",
            (record.thread_id, record.thread_name, recipient_id, observed_at),
        ).fetchone()
        if seeker is None:
            raise ValueError("stored_seeker_recipient_conflict")
        conn.execute(
            "INSERT INTO inbox_fetch_observations "
            "(observation_id,page_id,recipient_id,thread_id,thread_name,observed_at,payload_json) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(observation_id) DO NOTHING",
            (observation_id, record.page_id, recipient_id, record.thread_id, record.thread_name,
             observed_at, payload),
        )
        conn.execute("UPDATE threads SET fetch_history_complete=0 WHERE id=?", (record.thread_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return observation_id
