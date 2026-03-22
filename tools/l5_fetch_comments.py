import json
import argparse
import sys
import logging
import os
from playwright.sync_api import sync_playwright

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.browser.l3_comments import scrape_comments_ui as shared_scrape_comments
from fb_pipeline.comments.l1_helpers import parse_page_id, extract_user_info, detect_city
from fb_pipeline.persistence.l4_sqlite_store import (
    get_comment_db_connection,
    record_comment_fetch,
    should_fetch_comments,
)
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session, sanitize_storage_state_file

# Ensure logs directory exists
os.makedirs('./logs/', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('./logs/fetch_comments.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("fetch_comments")

# --- Core scraping logic (shared by CDP and credential modes) ---
# code:tool-fbcomments-001:scrape-core

def _scrape_comments(page, page_id: str, time_range: str, max_posts: int, conn) -> dict:
    """Core comment scraping logic. Takes an already-navigated Playwright page."""
    return shared_scrape_comments(
        page,
        page_id,
        time_range,
        max_posts,
        conn,
        logger=logger,
        record_fetch=record_comment_fetch,
        extract_user_info=extract_user_info,
        detect_city=detect_city,
    )


def get_db_connection(memory_dir: str = None):
    return get_comment_db_connection(memory_dir)


# --- Action: fetch_comments (Playwright browser fetch) ---
# code:tool-fbcomments-001:main

def fetch_comments(page_input: str, credential_id: str, time_range: str = "7d",
                   show_browser: bool = True, force_refresh: bool = False,
                   max_posts: int = 50, use_cdp: bool = False) -> dict:
    """Fetch comments from Facebook Page posts using Playwright browser automation."""
    page_id = parse_page_id(page_input)
    logger.info(f"Using Page ID: {page_id}, Time Range: {time_range}")

    memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    credential_path = os.path.join(memory_dir, f"fb_credential_{credential_id}.json")

    inbox_url = f"https://business.facebook.com/latest/inbox/facebook?asset_id={page_id}&thread_type=FB_PAGE_POST"

    if use_cdp:
        logger.info("CDP Direct Mode: Connecting to Chrome at http://127.0.0.1:9222")
        conn = get_comment_db_connection(memory_dir)

        if not force_refresh and not should_fetch_comments(page_id, conn):
            last_row = conn.execute(
                "SELECT fetched_at, posts_found, comments_found FROM comment_fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
                (page_id,)
            ).fetchone()
            logger.info(f"Cache hit: Last fetch at {last_row['fetched_at']}. Use --refresh to force.")
            conn.close()
            return {
                "success": True, "method": "cache_hit",
                "message": f"Using cached data from {last_row['fetched_at']}. Use --refresh to force a new fetch.",
                "data": {"last_fetch": last_row["fetched_at"], "posts": last_row["posts_found"], "comments": last_row["comments_found"]}
            }

        with sync_playwright() as p:
            session = None
            try:
                session = attach_to_authorized_session(p, page_id, inbox_url)
                logger.info(f"Connected to CDP session. Opened new tab (total tabs: {len(session.context.pages)}).")

                stats = _scrape_comments(session.page, page_id, time_range, max_posts, conn)

                conn.close()
                session.close_page()
                logger.info(f"CDP Direct: Saved to FrankenSQLite. Stats: {stats}")
                return {"success": True, "method": "cdp_direct", "data": {"stats": stats}}
            except Exception as e:
                logger.error(f"CDP Direct comment scrape failed: {e}")
                conn.close()
                if session:
                    session.close_page()
                return {"success": False, "error": str(e)}

    with sync_playwright() as p:
        if not os.path.exists(credential_path):
            logger.info(f"Credential '{credential_id}' not found at {credential_path}.")
            logger.info("Attempting to connect to existing Chrome instance via CDP at http://127.0.0.1:9222")

            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                session = None
                try:
                    session = attach_to_authorized_session(p, page_id, inbox_url, prefer_new_tab=False)

                    diagnostic_dir = f"./logs/diagnostic/iteration-0001"
                    os.makedirs(diagnostic_dir, exist_ok=True)
                    with open(os.path.join(diagnostic_dir, "consoleLog.txt"), "a") as f:
                        f.write(f"Attempt {attempt}: Navigated to {inbox_url}\n")
                    with open(os.path.join(diagnostic_dir, "DOM.txt"), "w") as f:
                        f.write(session.page.content())

                    logger.info(f"Saving browser state to {credential_path}")
                    session.context.storage_state(path=credential_path)

                    try:
                        sanitize_storage_state_file(credential_path)
                        logger.info("Successfully sanitized saved CDP state.")
                    except Exception as ex:
                        logger.warning(f"Failed to sanitize cookie state: {ex}")

                    logger.info("Successfully fetched and saved authenticated browser state via CDP session.")
                    session.close_page()
                    session.browser.close()
                    return {"success": True, "method": "cdp_capture", "message": "Authenticated state saved. Run again to fetch headless."}
                except Exception as e:
                    logger.warning(f"Attempt {attempt} failed: {e}. Retrying Ralph loop...")
                    if session:
                        session.close_page()
                    if attempt == max_attempts:
                        logger.error(f"Failed to connect via CDP after {max_attempts} attempts.")
                        logger.error("Make sure Chrome is running with --remote-debugging-port=9222")
                        return {"success": False, "error": str(e)}
        else:
            conn = get_comment_db_connection(memory_dir)

            if not force_refresh and not should_fetch_comments(page_id, conn):
                last_row = conn.execute(
                    "SELECT fetched_at, posts_found, comments_found FROM comment_fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
                    (page_id,)
                ).fetchone()
                logger.info(f"Cache hit: Last fetch at {last_row['fetched_at']} ({last_row['posts_found']} posts, {last_row['comments_found']} comments). Use --refresh to force.")
                conn.close()
                return {
                    "success": True, "method": "cache_hit",
                    "message": f"Using cached data from {last_row['fetched_at']}. Use --refresh to force a new fetch.",
                    "data": {"last_fetch": last_row["fetched_at"], "posts": last_row["posts_found"], "comments": last_row["comments_found"]}
                }

            logger.info(f"Credential '{credential_id}' found. Launching {'headful' if show_browser else 'headless'} browser.")
            try:
                browser = p.chromium.launch(headless=not show_browser)
                context = browser.new_context(
                    storage_state=credential_path,
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()

                logger.info(f"Navigating to {inbox_url}")
                page.goto(inbox_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                stats = _scrape_comments(page, page_id, time_range, max_posts, conn)

                conn.close()
                logger.info(f"Storage: Saved output to FrankenSQLite DB. Stats: {stats}")

                context.close()
                browser.close()
                return {"success": True, "method": "headless_fetch", "data": {"stats": stats}}
            except Exception as e:
                logger.error(f"Error while fetching post comments: {e}")
                return {"success": False, "error": str(e)}


# --- Action: get_comments_by_post (DB-only) ---
# code:tool-fbcomments-001:get-comments

def get_comments_by_post(page_id: str, post_id: str = None) -> dict:
    """Get all comments, optionally filtered by post_id."""
    conn = get_comment_db_connection()
    cursor = conn.cursor()

    if post_id:
        cursor.execute('''
            SELECT c.commenter_name, c.comment_text, c.comment_timestamp, c.fb_profile_url,
                   c.fb_user_id, c.is_reply, c.comment_date, c.timestamp,
                   p.post_name, p.post_url
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            WHERE p.page_id = ? AND (p.id = ? OR p.post_url = ?)
            ORDER BY c.id ASC
        ''', (page_id, post_id, post_id))
    else:
        cursor.execute('''
            SELECT c.commenter_name, c.comment_text, c.comment_timestamp, c.fb_profile_url,
                   c.fb_user_id, c.is_reply, c.comment_date, c.timestamp,
                   p.post_name, p.post_url
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            WHERE p.page_id = ?
            ORDER BY c.id ASC
        ''', (page_id,))

    rows = cursor.fetchall()
    comments = []
    for r in rows:
        comments.append({
            "commenter_name": r["commenter_name"],
            "comment_text": r["comment_text"],
            "comment_timestamp": r["comment_timestamp"],
            "fb_profile_url": r["fb_profile_url"],
            "fb_user_id": r["fb_user_id"],
            "is_reply": bool(r["is_reply"]),
            "comment_date": r["comment_date"],
            "stored_at": r["timestamp"],
            "post_name": r["post_name"],
            "post_url": r["post_url"],
        })

    conn.close()
    logger.info(f"get_comments_by_post: Found {len(comments)} comments for page {page_id}, post {post_id}.")
    return {"success": True, "action": "get_comments_by_post", "count": len(comments), "comments": comments}


# --- Action: get_comment_users (DB-only) ---
# code:tool-fbcomments-001:get-users

def get_comment_users(page_id: str, time_range: str = "7d") -> dict:
    """List unique commenters sorted by last interaction, filtered by time range."""
    range_map = {"1d": "-1 day", "7d": "-7 days", "30d": "-30 days", "90d": "-90 days", "180d": "-180 days", "365d": "-365 days"}
    sql_range = range_map.get(time_range, "-7 days")

    conn = get_comment_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT cu.id, cu.post_id, cu.commenter_name, cu.fb_user_id, cu.fb_profile_url,
               cu.phone, cu.email,
               cu.city, cu.lead_stage, cu.first_seen, cu.last_interaction,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = cu.post_id AND c.commenter_name = cu.commenter_name) as comment_count
        FROM comment_users cu
        JOIN posts p ON cu.post_id = p.id
        WHERE p.page_id = ? AND cu.last_interaction >= datetime('now', ?)
        ORDER BY cu.last_interaction DESC
    ''', (page_id, sql_range))

    rows = cursor.fetchall()
    users = []
    for r in rows:
        users.append({
            "id": r["id"],
            "post_id": r["post_id"],
            "name": r["commenter_name"],
            "fb_user_id": r["fb_user_id"],
            "fb_profile_url": r["fb_profile_url"],
            "phone": r["phone"],
            "email": r["email"],
            "city": r["city"],
            "lead_stage": r["lead_stage"],
            "first_seen": r["first_seen"],
            "last_interaction": r["last_interaction"],
            "comment_count": r["comment_count"],
        })

    conn.close()
    logger.info(f"get_comment_users: Found {len(users)} commenters for page {page_id} within {time_range}.")
    return {"success": True, "action": "get_comment_users", "count": len(users), "users": users}


# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Fetch Facebook Page Post Comments")
    parser.add_argument("--pageId", required=True, help="Facebook Page ID or full Meta Business Suite URL containing asset_id")
    parser.add_argument("--credential", default="default", help="Credential ID to load/save state.")
    parser.add_argument("--time_range", default="7d", help="Time range to fetch (1d, 7d, 30d, 90d).")
    parser.add_argument("--headless", action="store_true", help="Launch the browser invisibly (headless).")
    parser.add_argument("--action", default="fetch_comments",
                        choices=["fetch_comments", "get_comments_by_post", "get_comment_users"],
                        help="Action to perform.")
    parser.add_argument("--refresh", action="store_true", help="Force a fresh fetch, bypassing 1-hour cache.")
    parser.add_argument("--postId", default=None, help="Post ID for get_comments_by_post (selected_item_id or internal post hash).")
    parser.add_argument("--maxPosts", type=int, default=50, help="Maximum number of posts to sync (default: 50).")
    parser.add_argument("--cdp", action="store_true", help="Scrape directly via CDP connection to Chrome on port 9222 (no cookie export/import).")

    args = parser.parse_args()
    page_id = parse_page_id(args.pageId)

    logger.debug("Starting fetch_comments.py execution...")
    logger.debug("# code:tool-fbcomments-001:main")

    if args.action == "fetch_comments":
        show_browser_flag = not args.headless
        result = fetch_comments(args.pageId, args.credential, args.time_range,
                                show_browser=show_browser_flag, force_refresh=args.refresh,
                                max_posts=args.maxPosts, use_cdp=args.cdp)
    elif args.action == "get_comments_by_post":
        result = get_comments_by_post(page_id, args.postId)
    elif args.action == "get_comment_users":
        result = get_comment_users(page_id, args.time_range)
    else:
        result = {"success": False, "error": f"Unknown action: {args.action}"}

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if not result.get("success", False):
        sys.exit(1)

if __name__ == "__main__":
    main()
