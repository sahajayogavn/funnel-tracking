#!/usr/bin/env python3
"""
# code:tool-dedup-001
Deduplication tool for the users table in FrankenSQLite.

Merges same-user records that share the same fb_url but appear under
different display names (a known Facebook behaviour where the same person
messages a page from renamed or aliased accounts).

Usage:
    python tools/dedup_users.py --dry-run          # preview (default)
    python tools/dedup_users.py --execute           # perform merge
    python tools/dedup_users.py --execute --db path # custom DB path
"""

import argparse
import logging
import os
import sqlite3
import sys

logger = logging.getLogger("dedup_users")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "memory", "agent_memory", "frankensqlite.db"
)


# code:tool-dedup-001:score
def score_user(row: dict) -> tuple:
    """Return a stable sort key for keeper selection.

    Higher score = more complete record. Among equal scores, the earliest
    first_seen wins. If first_seen also ties, use thread_id as a final
    deterministic tiebreak so keeper selection stays stable across processes.
    """
    s = 0
    if row.get("phone"):
        s += 1
    if row.get("email"):
        s += 1
    if row.get("city") and row["city"] != "Unknown":
        s += 1
    if row.get("lead_stage") and row["lead_stage"] != "Intake":
        s += 1
    return (-s, row.get("first_seen") or "", row.get("thread_id") or "")


# code:tool-dedup-001:find-duplicates
def find_duplicate_groups(conn: sqlite3.Connection) -> list[list[dict]]:
    """Return a list of groups, each group is a list of user dicts sharing
    the same non-empty fb_url."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT fb_url, COUNT(*) as cnt
        FROM users
        WHERE fb_url IS NOT NULL AND fb_url != ''
        GROUP BY fb_url
        HAVING cnt > 1
        ORDER BY cnt DESC
        """
    ).fetchall()

    groups = []
    for r in rows:
        members = conn.execute(
            "SELECT * FROM users WHERE fb_url = ? ORDER BY first_seen",
            (r["fb_url"],),
        ).fetchall()
        groups.append([dict(m) for m in members])
    return groups


# code:tool-dedup-001:merge
def merge_group(conn: sqlite3.Connection, group: list[dict], dry_run: bool) -> dict:
    """Merge a duplicate group.  Returns a summary dict."""
    # Pick keeper using a stable ordering rule.
    scored = sorted(group, key=score_user)
    keeper = scored[0]
    dupes = scored[1:]

    summary = {
        "keeper_name": keeper["thread_name"],
        "keeper_thread_id": keeper["thread_id"],
        "fb_url": keeper["fb_url"],
        "removed": [],
    }

    for dupe in dupes:
        summary["removed"].append(
            {"name": dupe["thread_name"], "thread_id": dupe["thread_id"]}
        )

        if dry_run:
            continue

        dupe_tid = dupe["thread_id"]
        keeper_tid = keeper["thread_id"]

        # 1. Re-point messages (handle UNIQUE constraint conflicts with OR IGNORE)
        conn.execute(
            "UPDATE OR IGNORE messages SET thread_id = ? WHERE thread_id = ?",
            (keeper_tid, dupe_tid),
        )
        # Delete any remaining messages that couldn't be re-pointed (exact duplicates)
        conn.execute("DELETE FROM messages WHERE thread_id = ?", (dupe_tid,))

        # 2. Re-point user_ad_ids
        conn.execute(
            "UPDATE OR IGNORE user_ad_ids SET thread_id = ? WHERE thread_id = ?",
            (keeper_tid, dupe_tid),
        )
        conn.execute("DELETE FROM user_ad_ids WHERE thread_id = ?", (dupe_tid,))

        # 3. Backfill empty fields on keeper from dupe
        backfills = {}
        if not keeper.get("phone") and dupe.get("phone"):
            backfills["phone"] = dupe["phone"]
        if not keeper.get("email") and dupe.get("email"):
            backfills["email"] = dupe["email"]
        if (not keeper.get("city") or keeper["city"] == "Unknown") and dupe.get(
            "city"
        ) and dupe["city"] != "Unknown":
            backfills["city"] = dupe["city"]

        if backfills:
            sets = ", ".join(f"{k} = ?" for k in backfills)
            conn.execute(
                f"UPDATE users SET {sets} WHERE thread_id = ?",
                (*backfills.values(), keeper_tid),
            )
            # Update in-memory keeper so subsequent dupes see the merged data
            keeper.update(backfills)

        # 4. Delete dupe thread
        conn.execute("DELETE FROM threads WHERE id = ?", (dupe_tid,))

        # 5. Delete dupe user
        conn.execute("DELETE FROM users WHERE thread_id = ?", (dupe_tid,))

    if not dry_run:
        conn.commit()

    return summary


# code:tool-dedup-001:main
def run_dedup(db_path: str = DB_PATH, dry_run: bool = True) -> dict:
    """Run deduplication.  Returns stats dict."""
    conn = sqlite3.connect(db_path)
    groups = find_duplicate_groups(conn)

    stats = {
        "duplicate_groups": len(groups),
        "records_removed": 0,
        "dry_run": dry_run,
        "merges": [],
    }

    if not groups:
        logger.info("No duplicate fb_url groups found. Nothing to do.")
        conn.close()
        return stats

    for group in groups:
        summary = merge_group(conn, group, dry_run)
        stats["merges"].append(summary)
        stats["records_removed"] += len(summary["removed"])

        action = "WOULD MERGE" if dry_run else "MERGED"
        removed_names = ", ".join(r["name"] for r in summary["removed"])
        logger.info(
            f"{action}: Keep '{summary['keeper_name']}' "
            f"(fb_url={summary['fb_url']}), remove: [{removed_names}]"
        )

    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate users table by fb_url"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview merges without changing the database (default)",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Perform the merge",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help="Path to FrankenSQLite database",
    )
    args = parser.parse_args()

    dry_run = not args.execute
    stats = run_dedup(db_path=args.db, dry_run=dry_run)

    import json
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
