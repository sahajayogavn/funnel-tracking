#!/usr/bin/env python3
"""
Backfill `messages.kind`, `messages.message_at` and recompute
`users.last_interaction` from genuine customer turns only.
code:inbox-msg-kind-001:migration

Usage:
    .venv/bin/python tools/l5_migrate_messages_kind.py            # dry-run report
    .venv/bin/python tools/l5_migrate_messages_kind.py --apply    # write changes

Idempotent: rows already classified are re-evaluated, so re-running after a
pattern update is safe.  A summary is appended to logs/migrate_messages_kind.log.
"""
import argparse
import logging
import os
import sys
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.contracts.l1_message_kind import classify_message_kind, KIND_MESSAGE
from fb_pipeline.contracts.l1_message_time import resolve_message_at
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, setup_database

os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, "logs", "migrate_messages_kind.log")),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("migrate_messages_kind")


def run(apply: bool) -> dict:
    conn = get_db_connection()
    setup_database(conn, logger)
    rows = conn.execute(
        "SELECT id, thread_id, sender, content, message_timestamp, timestamp, kind, message_at FROM messages"
    ).fetchall()

    kind_counter = Counter()
    kind_changes = 0
    time_filled = 0
    time_approx = 0
    time_missing = 0
    updates = []
    for row in rows:
        new_kind = classify_message_kind(row["content"])
        kind_counter[new_kind] += 1
        if new_kind != (row["kind"] or KIND_MESSAGE):
            kind_changes += 1
        message_at, approx = resolve_message_at(row["message_timestamp"], row["timestamp"])
        if message_at:
            time_filled += 1
            time_approx += 1 if approx else 0
        else:
            time_missing += 1
        updates.append((new_kind, message_at, 1 if approx else 0, row["id"]))

    summary = {
        "rows": len(rows),
        "kind_distribution": dict(kind_counter),
        "kind_changes": kind_changes,
        "message_at_filled": time_filled,
        "message_at_approx": time_approx,
        "message_at_missing": time_missing,
        "applied": apply,
    }

    if apply:
        conn.executemany(
            "UPDATE messages SET kind=?, message_at=?, message_at_approx=? WHERE id=?", updates
        )
        # Recompute last_interaction from the newest genuine customer turn that
        # has an absolute time. Threads without one keep their current value.
        cur = conn.execute(
            """
            UPDATE users SET last_interaction = (
                SELECT MAX(m.message_at) FROM messages m
                WHERE m.thread_id = users.thread_id AND m.sender='Customer' AND m.kind='message'
                  AND m.message_at IS NOT NULL
            )
            WHERE EXISTS (
                SELECT 1 FROM messages m
                WHERE m.thread_id = users.thread_id AND m.sender='Customer' AND m.kind='message'
                  AND m.message_at IS NOT NULL
            )
            """
        )
        summary["users_last_interaction_recomputed"] = cur.rowcount
        conn.commit()
    conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run report)")
    args = parser.parse_args()
    summary = run(apply=args.apply)
    for key, value in summary.items():
        logger.info("%s: %s", key, value)
    print(summary)


if __name__ == "__main__":
    main()
