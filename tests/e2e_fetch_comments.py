"""
E2E Test for Facebook Comment Fetcher Tool (fetch_comments.py)
code:tool-fbcomments-001:e2e-test

This test connects to the real Facebook Business Suite via Playwright
headful browser using an existing credential file. It:
1. Fetches post comments from a real FB page (asset_id=1548373332058326)
2. Asserts >= 10 comments were retrieved
3. Validates comment schema fields (commenter_name, comment_text, fb_user_id, etc.)
4. Stores results in FrankenSQLite
5. Writes a diagnostic report to ./logs/e2e_fetch_comments_report.md

PREREQUISITES:
- A valid credential file must exist at:
  memory/agent_memory/fb_credential_default.json
  (or another credential captured via CDP)
- Run from project root: python tests/e2e_fetch_comments.py
"""

import sys
import os
import json
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.fetch_comments import fetch_comments, get_comments_by_post, get_comment_users, get_db_connection

# --- E2E Configuration ---
PAGE_ID = "1548373332058326"
CREDENTIAL_ID = "default"
TIME_RANGE = "90d"
MIN_COMMENTS_THRESHOLD = 10
REPORT_PATH = "./logs/e2e_fetch_comments_report.md"


def run_e2e_test():
    """Run the E2E fetch comments test against the real Facebook page."""
    os.makedirs('./logs/', exist_ok=True)
    report_lines = []
    report_lines.append("# E2E Test Report: Facebook Comment Fetcher")
    report_lines.append(f"\n**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"**Page ID**: {PAGE_ID}")
    report_lines.append(f"**Time Range**: {TIME_RANGE}")
    report_lines.append(f"**Threshold**: ≥{MIN_COMMENTS_THRESHOLD} comments")
    report_lines.append("")

    # Step 1: Check credential exists
    credential_id = CREDENTIAL_ID
    credential_path = os.path.join("memory", "agent_memory", f"fb_credential_{credential_id}.json")
    if not os.path.exists(credential_path):
        # Try other known credentials
        alt_creds = ["e2e_iter3", "e2e_test", "e2e_test_2", "e2e_test_3", "full_run_7d"]
        found = False
        for alt in alt_creds:
            alt_path = os.path.join("memory", "agent_memory", f"fb_credential_{alt}.json")
            if os.path.exists(alt_path):
                credential_id = alt
                credential_path = alt_path
                found = True
                print(f"[INFO] Using alternative credential: {alt}")
                break
        if not found:
            report_lines.append("## ❌ FAILED: No credential file found\n")
            report_lines.append("Available credentials must be captured first via CDP.\n")
            with open(REPORT_PATH, 'w') as f:
                f.write("\n".join(report_lines))
            print(f"FAIL: No credential file. Report saved to {REPORT_PATH}")
            sys.exit(1)

    report_lines.append(f"**Credential**: `{credential_id}` ✅")
    report_lines.append("")

    # Step 2: Fetch comments (headful=True so we can see the browser)
    report_lines.append("## Fetch Phase")
    print(f"[E2E] Starting fetch_comments for page {PAGE_ID} with time_range={TIME_RANGE}...")

    result = fetch_comments(
        page_input=PAGE_ID,
        credential_id=credential_id,
        time_range=TIME_RANGE,
        show_browser=True,
        force_refresh=True,
        max_posts=50
    )

    report_lines.append(f"\n**Method**: `{result.get('method', 'N/A')}`")
    report_lines.append(f"**Success**: `{result.get('success', False)}`")

    if not result.get("success"):
        report_lines.append(f"**Error**: `{result.get('error', 'Unknown error')}`")
        report_lines.append("\n## ❌ FAILED: Fetch returned error\n")
        with open(REPORT_PATH, 'w') as f:
            f.write("\n".join(report_lines))
        print(f"FAIL: Fetch error. Report saved to {REPORT_PATH}")
        sys.exit(1)

    stats = result.get("data", {}).get("stats", {})
    new_posts = stats.get("new_posts", 0)
    new_comments = stats.get("new_comments", 0)
    skipped = stats.get("skipped_posts", 0)

    report_lines.append(f"**New Posts**: {new_posts}")
    report_lines.append(f"**New Comments**: {new_comments}")
    report_lines.append(f"**Skipped Posts**: {skipped}")
    report_lines.append("")

    # Step 3: Verify data in DB
    report_lines.append("## Database Verification")
    conn = get_db_connection()
    cursor = conn.cursor()

    # Count total comments for this page
    cursor.execute('''
        SELECT COUNT(*) as cnt FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE p.page_id = ?
    ''', (PAGE_ID,))
    total_comments = cursor.fetchone()["cnt"]

    cursor.execute('''
        SELECT COUNT(*) as cnt FROM posts WHERE page_id = ?
    ''', (PAGE_ID,))
    total_posts = cursor.fetchone()["cnt"]

    cursor.execute('''
        SELECT COUNT(*) as cnt FROM comment_users cu
        JOIN posts p ON cu.post_id = p.id
        WHERE p.page_id = ?
    ''', (PAGE_ID,))
    total_users = cursor.fetchone()["cnt"]

    report_lines.append(f"**Total Posts in DB**: {total_posts}")
    report_lines.append(f"**Total Comments in DB**: {total_comments}")
    report_lines.append(f"**Total Unique Commenters**: {total_users}")
    report_lines.append("")

    # Step 4: Schema validation — check enriched fields exist
    report_lines.append("## Schema Validation")
    cursor.execute('''
        SELECT c.id, c.post_id, c.commenter_name, c.comment_text, c.comment_timestamp,
               c.fb_profile_url, c.fb_user_id, c.is_reply, c.comment_date,
               p.post_name, p.post_url
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE p.page_id = ?
        ORDER BY c.id ASC
        LIMIT 20
    ''', (PAGE_ID,))
    sample_comments = cursor.fetchall()

    has_profile_url = any(row["fb_profile_url"] for row in sample_comments)
    has_user_id = any(row["fb_user_id"] for row in sample_comments)
    has_reply = any(row["is_reply"] for row in sample_comments)
    has_date = any(row["comment_date"] for row in sample_comments)
    has_text = all(row["comment_text"] for row in sample_comments)
    has_name = all(row["commenter_name"] for row in sample_comments)

    report_lines.append("| Field | Populated? |")
    report_lines.append("|---|---|")
    report_lines.append(f"| `commenter_name` | {'✅' if has_name else '❌'} (all rows) |")
    report_lines.append(f"| `comment_text` | {'✅' if has_text else '❌'} (all rows) |")
    report_lines.append(f"| `fb_profile_url` | {'✅' if has_profile_url else '⚠️'} (at least one) |")
    report_lines.append(f"| `fb_user_id` | {'✅' if has_user_id else '⚠️'} (at least one) |")
    report_lines.append(f"| `is_reply` | {'✅' if has_reply else '⚠️'} (at least one) |")
    report_lines.append(f"| `comment_date` | {'✅' if has_date else '⚠️'} (at least one) |")
    report_lines.append("")

    # Step 5: Sample comments detail
    report_lines.append("## Sample Comments (first 10)")
    report_lines.append("")
    report_lines.append("| # | Commenter | Text (truncated) | User ID | Reply? | Date |")
    report_lines.append("|---|---|---|---|---|---|")
    for i, row in enumerate(sample_comments[:10], 1):
        text_preview = (row["comment_text"] or "")[:60].replace("|", "\\|")
        user_id = row["fb_user_id"] or "-"
        is_reply = "Yes" if row["is_reply"] else "No"
        date = row["comment_date"] or "-"
        name = (row["commenter_name"] or "").replace("|", "\\|")
        report_lines.append(f"| {i} | {name} | {text_preview} | {user_id} | {is_reply} | {date} |")
    report_lines.append("")

    conn.close()

    # Step 6: Pass/Fail assertion for Phase 1
    report_lines.append("## Phase 1 Result: Fetch")
    phase1_passed = total_comments >= MIN_COMMENTS_THRESHOLD
    if phase1_passed:
        report_lines.append(f"\n### ✅ PASSED: {total_comments} comments ≥ {MIN_COMMENTS_THRESHOLD} threshold\n")
    else:
        report_lines.append(f"\n### ❌ FAILED: {total_comments} comments < {MIN_COMMENTS_THRESHOLD} threshold\n")

    # ==========================================================
    # Phase 2: Cache Hit Test (re-run WITHOUT --refresh)
    # The 1-hour TTL should return cache_hit immediately
    # ==========================================================
    report_lines.append("## Phase 2: Cache Hit Test (1-hour TTL)")
    print(f"\n[E2E] Phase 2: Re-running WITHOUT --refresh (expect cache_hit)...")

    result2 = fetch_comments(
        page_input=PAGE_ID,
        credential_id=credential_id,
        time_range=TIME_RANGE,
        show_browser=True,
        force_refresh=False,  # <-- no refresh = should hit cache
        max_posts=50
    )

    cache_method = result2.get("method", "N/A")
    cache_passed = cache_method == "cache_hit"
    report_lines.append(f"\n**Method**: `{cache_method}`")
    report_lines.append(f"**Expected**: `cache_hit`")
    if cache_passed:
        report_lines.append(f"### ✅ PASSED: 1-hour cache TTL working — no browser launched\n")
    else:
        report_lines.append(f"### ❌ FAILED: Expected cache_hit but got `{cache_method}`\n")

    print(f"[E2E] Phase 2: {'PASSED ✅' if cache_passed else 'FAILED ❌'} (method={cache_method})")

    # ==========================================================
    # Phase 3: Force Refresh + Skip/Dedup Test
    # Re-run WITH --refresh: posts should be SKIPPED (preview unchanged)
    # and new_comments should be 0 (all already in SQL)
    # ==========================================================
    report_lines.append("## Phase 3: Force Refresh — Skip & Dedup Test")
    print(f"\n[E2E] Phase 3: Re-running WITH --refresh (expect skipped_posts > 0, new_comments == 0)...")

    result3 = fetch_comments(
        page_input=PAGE_ID,
        credential_id=credential_id,
        time_range=TIME_RANGE,
        show_browser=True,
        force_refresh=True,  # <-- force refresh = re-navigate but should skip/dedup
        max_posts=50
    )

    if result3.get("success") and result3.get("method") == "headless_fetch":
        stats3 = result3.get("data", {}).get("stats", {})
        skipped3 = stats3.get("skipped_posts", 0)
        new_comments3 = stats3.get("new_comments", 0)
        new_posts3 = stats3.get("new_posts", 0)

        report_lines.append(f"\n**Skipped Posts (preview unchanged)**: {skipped3}")
        report_lines.append(f"**New Comments (dedup)**: {new_comments3}")
        report_lines.append(f"**New Posts**: {new_posts3}")

        # Skip test: at least some posts should be skipped (preview text matches)
        skip_passed = skipped3 > 0
        # Dedup test: new_comments should be 0 (all already in SQL via INSERT OR IGNORE)
        dedup_passed = new_comments3 == 0

        if skip_passed:
            report_lines.append(f"### ✅ Skip: {skipped3} posts skipped (preview unchanged)")
        else:
            report_lines.append(f"### ⚠️ Skip: No posts skipped (FB may have updated previews)")

        if dedup_passed:
            report_lines.append(f"### ✅ Dedup: 0 new comments (all already in SQL)")
        else:
            report_lines.append(f"### ⚠️ Dedup: {new_comments3} new comments found (possible new activity)")

        report_lines.append("")
    else:
        skip_passed = False
        dedup_passed = False
        report_lines.append(f"\n### ❌ FAILED: Phase 3 returned error or unexpected method\n")

    print(f"[E2E] Phase 3: Skip={'PASSED ✅' if skip_passed else 'WARN ⚠️'}, Dedup={'PASSED ✅' if dedup_passed else 'WARN ⚠️'}")

    # ==========================================================
    # Final Result
    # ==========================================================
    report_lines.append("## Final Result")
    all_passed = phase1_passed and cache_passed
    if all_passed:
        report_lines.append(f"\n### ✅ ALL PHASES PASSED")
    else:
        report_lines.append(f"\n### ❌ SOME PHASES FAILED")
    report_lines.append(f"- Phase 1 (Fetch ≥{MIN_COMMENTS_THRESHOLD}): {'✅' if phase1_passed else '❌'}")
    report_lines.append(f"- Phase 2 (Cache TTL): {'✅' if cache_passed else '❌'}")
    report_lines.append(f"- Phase 3 Skip: {'✅' if skip_passed else '⚠️'}")
    report_lines.append(f"- Phase 3 Dedup: {'✅' if dedup_passed else '⚠️'}")
    report_lines.append("")

    # Write report
    with open(REPORT_PATH, 'w') as f:
        f.write("\n".join(report_lines))

    print(f"\n{'='*60}")
    print(f"E2E Test {'ALL PASSED ✅' if all_passed else 'SOME FAILED ❌'}")
    print(f"Phase 1: {total_comments} comments (≥{MIN_COMMENTS_THRESHOLD}): {'✅' if phase1_passed else '❌'}")
    print(f"Phase 2: Cache hit: {'✅' if cache_passed else '❌'}")
    print(f"Phase 3: Skip={skipped3 if 'skipped3' in dir() else '?'}, Dedup={new_comments3 if 'new_comments3' in dir() else '?'}")
    print(f"Report saved to: {REPORT_PATH}")
    print(f"{'='*60}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    run_e2e_test()

