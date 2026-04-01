import json
import argparse
import sys
import logging
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.contracts.l1_inbox import (
    EnrichedThreadRecord,
    InboxMessage,
    MasHandoff,
    SeekerInfo,
    ThreadRecord,
    detect_city as shared_detect_city,
    detect_city_smart as shared_detect_city_smart,
    extract_user_info as shared_extract_user_info,
    parse_page_id as shared_parse_page_id,
)
from fb_pipeline.browser.l3_inbox import (
    extract_ad_id_labels as shared_extract_ad_id_labels,
    scrape_inbox_ui as shared_scrape_inbox,
)
from fb_pipeline.inbox.l3_pipeline import (
    build_thread_record as shared_build_thread_record,
    enrich_thread_record as shared_enrich_thread_record,
    persist_thread_record as shared_persist_thread_record,
)
from fb_pipeline.persistence.l4_sqlite_store import (
    get_db_connection as shared_get_db_connection,
    record_fetch as shared_record_fetch,
    setup_database as shared_setup_database,
    should_fetch as shared_should_fetch,
)
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session, sanitize_storage_state_file
from fb_pipeline.contracts.l1_city_llm import detect_city_llm, gather_signals_for_user

# Ensure logs directory exists
os.makedirs('./logs/', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('./logs/fetch_fb_messages.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("fetch_fb_messages")

# --- Constants ---
# code:tool-fbmessages-002:city-detect
CITY_KEYWORDS = {
    "Hà Nội": ["Hà Nội", "Ha Noi", "Vương Thừa Vũ", "Khương Đình", "Khương Trung",
                "Thanh Xuân", "Cầu Giấy", "Đống Đa", "Ba Đình", "Hoàn Kiếm",
                "Hai Bà Trưng", "Long Biên", "Hà nội"],
    "TP. Hồ Chí Minh": ["Hồ Chí Minh", "TP.HCM", "TPHCM", "Sài Gòn", "Saigon",
                         "Bình Thạnh", "Quận 1", "Quận 3",
                         "Quận 7", "Thủ Đức", "Gò Vấp", "Tân Bình", "HCM"],
    "Đà Nẵng": ["Đà Nẵng", "Da Nang", "Đà nẵng"],
    "Huế": ["Huế", "Hue"],
    "Hội An": ["Hội An", "Hoi An"],
    "Nghệ An": ["Nghệ An", "Nghe An", "Vinh"],
    "Hải Phòng": ["Hải Phòng", "Hai Phong"],
    "Online": ["online", "Online", "ONLINE", "zoom", "Zoom", "trực tuyến"],
}

CACHE_TTL_SECONDS = 3600  # 1 hour

# --- Utility Functions ---

def parse_page_id(input_str: str) -> str:
    """Extract page ID from a URL or return the input if it's just an ID."""
    page_id = shared_parse_page_id(input_str)
    if page_id == input_str and not re.match(r'^\d+$', input_str):
        logger.warning(f"Input '{input_str}' does not strictly look like a numeric ID or valid URL with asset_id. Proceeding anyway.")
    return page_id


# code:tool-fbmessages-002:cache
def should_fetch(page_id: str, conn: sqlite3.Connection) -> bool:
    """Return True if last fetch was > 1 hour ago or no fetch recorded."""
    return shared_should_fetch(page_id, conn)


def record_fetch(page_id: str, threads_found: int, messages_found: int, conn: sqlite3.Connection):
    """Record a fetch event in the log."""
    return shared_record_fetch(page_id, threads_found, messages_found, conn)


# code:tool-fbmessages-002:user-extract
def extract_user_info(messages: list, thread_name: str, ad_context: str = "") -> dict:
    """Extract phone, email, FB URL from message content."""
    return shared_extract_user_info(messages, thread_name, ad_context)


# code:tool-fbmessages-003:parse-ad-ids
def parse_ad_ids(text: str) -> list:
    """Extract unique ad_id numbers from text containing ad_id.XXXXX patterns."""
    raw = re.findall(r'ad_id\.?(\d{5,})', text)
    # Deduplicate while preserving order
    return list(dict.fromkeys(raw))


# code:tool-fbmessages-003:extract-labels
def extract_ad_id_labels(page) -> list:
    """Extract ad_id values from the Labels section in thread detail panel."""
    return shared_extract_ad_id_labels(page)


# code:tool-fbmessages-002:city-detect
def detect_city(ad_context: str, page_messages: list) -> str:
    """Detect city using LLM-first with rule-based fallback."""
    return shared_detect_city_smart(ad_context, page_messages)


# --- DB Setup ---

def setup_database(conn: sqlite3.Connection):
    """Create all required tables (idempotent)."""
    return shared_setup_database(conn, logger=logger)


def get_db_connection(memory_dir: str = None) -> sqlite3.Connection:
    """Get a connection to the FrankenSQLite DB."""
    return shared_get_db_connection(memory_dir, logger=logger)



def _to_legacy_dict(value):
    if is_dataclass(value):
        return asdict(value)
    return value


def build_thread_record(page_id: str, visible_thread: dict) -> dict:
    """Parse a visible inbox thread card into a normalized thread record."""
    return _to_legacy_dict(shared_build_thread_record(page_id, visible_thread))


def enrich_thread_record(thread_record: dict, js_messages: list, ad_context: str = "",
                         fb_url: str = "", ad_ids: list | None = None) -> dict:
    """Enrich a parsed thread with seeker fields, city prediction, and MAS payload."""
    shared_thread_record = ThreadRecord(**thread_record) if isinstance(thread_record, dict) else thread_record
    enriched = shared_enrich_thread_record(
        shared_thread_record,
        js_messages,
        extract_user_info=extract_user_info,
        detect_city=detect_city,
        ad_context=ad_context,
        fb_url=fb_url,
        ad_ids=ad_ids,
    )
    return _to_legacy_dict(enriched)


def _to_shared_enriched_thread_record(thread_record):
    if not isinstance(thread_record, dict):
        return thread_record
    mas_handoff = thread_record.get("mas_handoff")
    shared_mas_handoff = None
    if mas_handoff:
        seeker = mas_handoff.get("seeker") or {}
        shared_mas_handoff = MasHandoff(
            thread_id=mas_handoff["thread_id"],
            thread_name=mas_handoff["thread_name"],
            page_id=mas_handoff["page_id"],
            fb_url=mas_handoff["fb_url"],
            seeker=SeekerInfo(
                name=seeker.get("name", ""),
                phone=seeker.get("phone"),
                email=seeker.get("email"),
                city=seeker.get("city", "Unknown"),
                lead_stage=seeker.get("lead_stage", "Intake"),
            ),
            ad_context=mas_handoff.get("ad_context", ""),
            ad_ids=list(mas_handoff.get("ad_ids") or []),
            messages=[InboxMessage(**message) for message in mas_handoff.get("messages") or []],
            temperature=mas_handoff.get("temperature", "warm"),
            cool_step=mas_handoff.get("cool_step", 0),
        )
    return EnrichedThreadRecord(
        page_id=thread_record["page_id"],
        thread_id=thread_record["thread_id"],
        thread_name=thread_record["thread_name"],
        preview_text=thread_record["preview_text"],
        thread_lines=list(thread_record.get("thread_lines") or []),
        dom_index=thread_record.get("dom_index", 0),
        sidebar_time_text=thread_record.get("sidebar_time_text", ""),
        sidebar_time_kind=thread_record.get("sidebar_time_kind", ""),
        sidebar_identity_key=thread_record.get("sidebar_identity_key", ""),
        selected_item_id=thread_record.get("selected_item_id", ""),
        fb_url=thread_record.get("fb_url", ""),
        ad_context=thread_record.get("ad_context", ""),
        ad_ids=list(thread_record.get("ad_ids") or []),
        user_info=dict(thread_record.get("user_info") or {}),
        city=thread_record.get("city", "Unknown"),
        messages=[InboxMessage(**message) for message in thread_record.get("messages") or []],
        mas_handoff=shared_mas_handoff,
    )


def persist_thread_record(conn: sqlite3.Connection, thread_record: dict) -> dict:
    """Persist one parsed+enriched thread record to FrankenSQLite."""
    return shared_persist_thread_record(
        conn,
        _to_shared_enriched_thread_record(thread_record),
        detect_city=detect_city,
    )


# --- Shared scraping helper ---
# code:tool-fbmessages-001:scrape-inbox

def _scrape_inbox(page, page_id: str, time_range: str, max_threads: int,
                  conn: sqlite3.Connection, skip_navigation: bool = False) -> dict:
    """Core scraping loop: scroll sidebar, click threads, extract messages."""
    return shared_scrape_inbox(
        page,
        page_id,
        time_range,
        max_threads,
        conn,
        logger=logger,
        record_fetch=record_fetch,
        extract_ad_id_labels=extract_ad_id_labels,
        extract_user_info=extract_user_info,
        detect_city=detect_city,
        skip_navigation=skip_navigation,
    )


# --- Post-scrape LLM city classification ---
# code:tool-citydetect-001:post-scrape-integration

def _get_llm_config_safe() -> dict | None:
    """Try to load LLM config. Returns None if credentials unavailable."""
    try:
        from tools.env_manager import load_credentials
        creds = load_credentials()
        api_base = creds.get("OPENAI_COMPATIBLE_URL") or os.environ.get("OPENAI_API_BASE", "")
        api_key = creds.get("OPENAI_COMPATIBLE_KEY") or os.environ.get("OPENAI_API_KEY", "")
        model = creds.get("OPENAI_COMPATIBLE_MODELS") or os.environ.get("ADK_MODEL", "gpt-5.4")
        if not api_base or not api_key:
            return None
        return {"api_base": api_base, "api_key": api_key, "model": model}
    except Exception as e:
        logger.debug(f"LLM config not available: {e}")
        return None


def _post_scrape_llm_city_classify(conn, page_id: str) -> dict:
    """Run LLM city classification on all users for this page after scraping.

    Classifies users whose city is 'Unknown' or was set by keyword matching.
    Gracefully skips if LLM credentials are not available.
    """
    import time

    llm_config = _get_llm_config_safe()
    if not llm_config:
        logger.info("LLM city classification skipped: no LLM credentials available.")
        return {"llm_city_classify": "skipped", "reason": "no_credentials"}

    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.thread_id, u.thread_name, u.city FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE t.page_id = ?
        ORDER BY u.last_interaction DESC
    """, (page_id,))
    users = cursor.fetchall()

    if not users:
        return {"llm_city_classify": "done", "total": 0, "updated": 0}

    logger.info(f"LLM city classification: processing {len(users)} users for page {page_id}")
    updated = 0
    errors = 0

    for i, user_row in enumerate(users):
        thread_id = user_row["thread_id"]
        old_city = user_row["city"]
        try:
            signals = gather_signals_for_user(conn, thread_id)
            result = detect_city_llm(
                thread_name=signals["thread_name"],
                customer_messages=signals["customer_messages"],
                page_messages=signals["page_messages"],
                ad_content=signals["ad_content"],
                api_base=llm_config["api_base"],
                api_key=llm_config["api_key"],
                model=llm_config["model"],
            )
            new_city = result["city"]
            if new_city != "Unknown" and new_city != old_city:
                cursor.execute(
                    "UPDATE users SET city = ? WHERE thread_id = ?",
                    (new_city, thread_id)
                )
                updated += 1
                logger.info(
                    f"LLM city [{i+1}/{len(users)}] {user_row['thread_name']}: "
                    f"{old_city} → {new_city} [{result['confidence']}] {result['reasoning']}"
                )
            # Rate limiting
            if i < len(users) - 1:
                time.sleep(0.3)
        except Exception as e:
            logger.warning(f"LLM city error for {thread_id}: {e}")
            errors += 1

    conn.commit()
    logger.info(f"LLM city classification done: {updated} updated, {errors} errors out of {len(users)} users.")
    return {"llm_city_classify": "done", "total": len(users), "updated": updated, "errors": errors}


# --- Action: fetch_messages (Playwright browser fetch) ---
# code:tool-fbmessages-001:main

def fetch_messages(page_input: str, credential_id: str, time_range: str = "7d",
                   show_browser: bool = True, force_refresh: bool = False,
                   max_threads: int = 50, use_cdp: bool = False) -> dict:
    page_id = parse_page_id(page_input)
    logger.info(f"Using Page ID: {page_id}, Time Range: {time_range}")

    memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    credential_path = os.path.join(memory_dir, f"fb_credential_{credential_id}.json")

    # --- Mode 1: Direct CDP scraping (--cdp flag) ---
    # code:tool-fbmessages-001:cdp-direct
    if use_cdp:
        logger.info("CDP Direct Mode: Connecting to Chrome at http://127.0.0.1:9222")
        conn = get_db_connection(memory_dir)

        # Cache check (same as headless mode)
        if not force_refresh and not should_fetch(page_id, conn):
            last_row = conn.execute(
                "SELECT fetched_at, threads_found, messages_found FROM fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
                (page_id,)
            ).fetchone()
            logger.info(f"Cache hit: Last fetch at {last_row['fetched_at']}. Use --refresh to force.")
            conn.close()
            return {
                "success": True, "method": "cache_hit",
                "message": f"Using cached data from {last_row['fetched_at']}. Use --refresh to force a new fetch.",
                "data": {"last_fetch": last_row["fetched_at"], "threads": last_row["threads_found"], "messages": last_row["messages_found"]}
            }

        with sync_playwright() as p:
            session = None
            try:
                session = attach_to_authorized_session(p, page_id, f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}")
                logger.info(f"Connected to CDP session. Opened new tab (total tabs: {len(session.context.pages)}).")

                diag_dir = "./logs/diagnostic/cdp-direct"
                os.makedirs(diag_dir, exist_ok=True)
                run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                logger.info("Starting direct scrape...")
                stats = _scrape_inbox(session.page, page_id, time_range, max_threads, conn, skip_navigation=True)

                # Post-scrape LLM city classification
                llm_stats = _post_scrape_llm_city_classify(conn, page_id)
                stats["llm_city"] = llm_stats

                with open(os.path.join(diag_dir, f"run_{run_ts}.log"), "w") as f:
                    f.write(f"CDP Direct Scrape: {run_ts}\n")
                    f.write(f"Page ID: {page_id}, Time Range: {time_range}, Max Threads: {max_threads}\n")
                    f.write(f"Stats: {json.dumps(stats, indent=2)}\n")

                conn.close()
                session.close_page()
                logger.info(f"CDP Direct: Saved to FrankenSQLite. Stats: {stats}")
                return {"success": True, "method": "cdp_direct", "data": {"stats": stats}}
            except Exception as e:
                logger.error(f"CDP Direct scrape failed: {e}")
                try:
                    diag_dir = "./logs/diagnostic/cdp-direct"
                    os.makedirs(diag_dir, exist_ok=True)
                    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    with open(os.path.join(diag_dir, f"error_{run_ts}.log"), "w") as f:
                        f.write(f"CDP Direct Error: {e}\n")
                        f.write(f"Page ID: {page_id}, Time Range: {time_range}\n")
                except Exception:
                    pass
                conn.close()
                if session:
                    session.close_page()
                return {"success": False, "error": str(e)}

    # --- Mode 2: Legacy credential capture (no credential file, no --cdp) ---
    with sync_playwright() as p:
        if not os.path.exists(credential_path):
            logger.info(f"Credential '{credential_id}' not found at {credential_path}.")
            logger.info("Attempting to connect to existing Chrome instance via CDP at http://127.0.0.1:9222")

            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                session = None
                try:
                    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
                    session = attach_to_authorized_session(p, page_id, inbox_url, prefer_new_tab=False)

                    logger.info(f"Navigating to {inbox_url} to ensure authenticated state is captured properly...")

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
            # --- Mode 3: Headful/Headless fetch with saved credentials ---
            conn = get_db_connection(memory_dir)

            # code:tool-fbmessages-002:cache
            if not force_refresh and not should_fetch(page_id, conn):
                last_row = conn.execute(
                    "SELECT fetched_at, threads_found, messages_found FROM fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
                    (page_id,)
                ).fetchone()
                logger.info(f"Cache hit: Last fetch at {last_row['fetched_at']} ({last_row['threads_found']} threads, {last_row['messages_found']} messages). Use --refresh to force.")
                conn.close()
                return {
                    "success": True, "method": "cache_hit",
                    "message": f"Using cached data from {last_row['fetched_at']}. Use --refresh to force a new fetch.",
                    "data": {"last_fetch": last_row["fetched_at"], "threads": last_row["threads_found"], "messages": last_row["messages_found"]}
                }

            logger.info(f"Credential '{credential_id}' found. Launching {'headful' if show_browser else 'headless'} browser.")
            try:
                browser = p.chromium.launch(headless=not show_browser)
                context = browser.new_context(
                    storage_state=credential_path,
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()

                stats = _scrape_inbox(page, page_id, time_range, max_threads, conn)

                # Post-scrape LLM city classification
                llm_stats = _post_scrape_llm_city_classify(conn, page_id)
                stats["llm_city"] = llm_stats

                conn.close()
                logger.info(f"Storage: Saved output to FrankenSQLite DB. Stats: {stats}")

                context.close()
                browser.close()
                return {"success": True, "method": "headless_fetch", "data": {"stats": stats}}
            except Exception as e:
                logger.error(f"Error while waiting for inbox or extracting messages: {e}")
                return {"success": False, "error": str(e)}


# --- Action: get_list_unique_user (DB-only) ---
# code:tool-fbmessages-002:list-users

def get_list_unique_user(page_id: str, time_range: str = "7d") -> dict:
    """List unique users sorted by last interaction, filtered by time range."""
    range_map = {"1d": "-1 day", "7d": "-7 days", "30d": "-30 days", "90d": "-90 days", "180d": "-180 days", "365d": "-365 days"}
    sql_range = range_map.get(time_range, "-7 days")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(f'''
        SELECT u.id, u.thread_id, u.thread_name, u.phone, u.email, u.fb_url,
               u.city, u.lead_stage, u.first_seen, u.last_interaction,
               (SELECT COUNT(*) FROM messages m WHERE m.thread_id = u.thread_id) as msg_count
        FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE t.page_id = ? AND u.last_interaction >= datetime('now', ?)
        ORDER BY u.last_interaction DESC
    ''', (page_id, sql_range))
    
    rows = cursor.fetchall()
    users = []
    for r in rows:
        users.append({
            "id": r["id"],
            "thread_id": r["thread_id"],
            "name": r["thread_name"],
            "phone": r["phone"],
            "email": r["email"],
            "fb_url": r["fb_url"],
            "city": r["city"],
            "lead_stage": r["lead_stage"],
            "first_seen": r["first_seen"],
            "last_interaction": r["last_interaction"],
            "msg_count": r["msg_count"],
        })
    
    conn.close()
    logger.info(f"get_list_unique_user: Found {len(users)} users for page {page_id} within {time_range}.")
    return {"success": True, "action": "get_list_unique_user", "count": len(users), "users": users}


# --- Action: fetch_message_by_user (DB-only) ---
# code:tool-fbmessages-002:user-messages

def fetch_message_by_user(page_id: str, user_id: str) -> dict:
    """Fetch messages for a user. user_id can be thread_id, phone, or email."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Find the user row
    cursor.execute('''
        SELECT u.* FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE t.page_id = ? AND (u.thread_id = ? OR u.phone = ? OR u.email = ?)
    ''', (page_id, user_id, user_id, user_id))
    
    user_row = cursor.fetchone()
    if not user_row:
        conn.close()
        return {"success": False, "error": f"User '{user_id}' not found for page {page_id}."}
    
    thread_id = user_row["thread_id"]
    
    # Fetch all messages for this thread
    cursor.execute('''
        SELECT sender, content, message_timestamp, timestamp
        FROM messages
        WHERE thread_id = ?
        ORDER BY id ASC
    ''', (thread_id,))
    
    messages = []
    for m in cursor.fetchall():
        messages.append({
            "sender": m["sender"],
            "content": m["content"],
            "message_timestamp": m["message_timestamp"],
            "stored_at": m["timestamp"],
        })
    
    # Fetch ad_ids for this user
    cursor.execute('SELECT ad_id FROM user_ad_ids WHERE thread_id = ?', (thread_id,))
    ad_ids = [r["ad_id"] for r in cursor.fetchall()]
    
    user_info = {
        "id": user_row["id"],
        "name": user_row["thread_name"],
        "phone": user_row["phone"],
        "email": user_row["email"],
        "fb_url": user_row["fb_url"],
        "city": user_row["city"],
        "lead_stage": user_row["lead_stage"],
        "ad_ids": ad_ids,
    }
    
    conn.close()
    logger.info(f"fetch_message_by_user: Found {len(messages)} messages for user '{user_id}'.")
    return {"success": True, "action": "fetch_message_by_user", "user": user_info, "messages": messages}


# --- Action: get_user_ad_ids (DB-only) ---
# code:tool-fbmessages-003:get-user-ad-ids

def get_user_ad_ids(page_id: str) -> dict:
    """List all ad_id associations for users on this page."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.thread_name, u.city, u.lead_stage, ua.ad_id, ua.first_seen,
               ap.city AS ad_city, ap.ad_content
        FROM user_ad_ids ua
        JOIN users u ON ua.thread_id = u.thread_id
        JOIN threads t ON u.thread_id = t.id
        LEFT JOIN ad_posts ap ON ua.ad_id = ap.ad_id
        WHERE t.page_id = ?
        ORDER BY ua.first_seen DESC
    ''', (page_id,))
    
    rows = cursor.fetchall()
    results = []
    for r in rows:
        results.append({
            "name": r["thread_name"],
            "city": r["city"],
            "lead_stage": r["lead_stage"],
            "ad_id": r["ad_id"],
            "first_seen": r["first_seen"],
            "ad_city": r["ad_city"],
            "ad_content_preview": (r["ad_content"] or "")[:100],
        })
    
    conn.close()
    logger.info(f"get_user_ad_ids: Found {len(results)} ad associations for page {page_id}.")
    return {"success": True, "action": "get_user_ad_ids", "count": len(results), "associations": results}


# --- Action: resolve_ad_posts (CDP browser) ---
# code:tool-fbmessages-003:resolve-ad-posts

def resolve_ad_posts(page_id: str, use_cdp: bool = True) -> dict:
    """Resolve unmatched ad_ids to posts via FB Ad Library and insights page.
    
    Strategy:
    1. For each unresolved ad_id, check FB Ad Library (public)
    2. Try insights/content page to find post↔ad relationships
    3. Match ad_content against existing posts table
    4. Use detect_city() on matched content → update users
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Find unresolved ad_ids (no post content or no city)
    cursor.execute('''
        SELECT DISTINCT ua.ad_id FROM user_ad_ids ua
        LEFT JOIN ad_posts ap ON ua.ad_id = ap.ad_id
        WHERE ap.ad_id IS NULL OR ap.city = 'Unknown' OR ap.ad_content IS NULL
    ''')
    unresolved = [r["ad_id"] for r in cursor.fetchall()]
    
    if not unresolved:
        conn.close()
        logger.info("resolve_ad_posts: All ad_ids already resolved.")
        return {"success": True, "action": "resolve_ad_posts", "message": "All ad_ids already resolved.", "resolved": 0}
    
    logger.info(f"resolve_ad_posts: {len(unresolved)} ad_ids to resolve: {unresolved}")
    
    # Strategy 1: Try matching ad_content (already stored) against posts table
    resolved_count = 0
    for aid in list(unresolved):
        cursor.execute('SELECT ad_content FROM ad_posts WHERE ad_id = ?', (aid,))
        row = cursor.fetchone()
        if row and row["ad_content"]:
            # Try to match against posts table
            city = detect_city(row["ad_content"], [])
            if city != "Unknown":
                cursor.execute('''
                    UPDATE ad_posts SET city = ?, resolved_at = datetime('now')
                    WHERE ad_id = ? AND city = 'Unknown'
                ''', (city, aid))
                conn.commit()
                resolved_count += 1
                unresolved.remove(aid)
                logger.info(f"Resolved ad_id {aid} → city '{city}' from stored ad_content.")
                # Update users who have this ad_id and Unknown city
                _update_users_city_from_ad(cursor, conn, aid, city)
    
    if not unresolved:
        conn.close()
        return {"success": True, "action": "resolve_ad_posts", "resolved": resolved_count}
    
    # Strategy 2: Try FB Ad Library (public) with ralph loop
    if not use_cdp:
        conn.close()
        return {"success": True, "action": "resolve_ad_posts", "resolved": resolved_count,
                "unresolved": len(unresolved), "message": "Use --cdp to attempt FB Ad Library resolution."}
    
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            default_context = browser.contexts[0]
            cdp_page = default_context.new_page()
            
            for aid in list(unresolved):
                ad_content = _ralph_loop_ad_library(cdp_page, aid)
                if ad_content:
                    city = detect_city(ad_content, [])
                    # Try to match against posts table
                    post_id = _match_ad_to_post(cursor, ad_content)
                    cursor.execute('''
                        INSERT INTO ad_posts (ad_id, post_id, ad_content, city, resolved_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(ad_id) DO UPDATE SET
                            ad_content = excluded.ad_content,
                            post_id = COALESCE(excluded.post_id, ad_posts.post_id),
                            city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE ad_posts.city END,
                            resolved_at = datetime('now')
                    ''', (aid, post_id, ad_content, city))
                    conn.commit()
                    resolved_count += 1
                    unresolved.remove(aid)
                    logger.info(f"Resolved ad_id {aid} via Ad Library → city '{city}', post_id '{post_id}'.")
                    if city != "Unknown":
                        _update_users_city_from_ad(cursor, conn, aid, city)
            
            # Strategy 3: Try insights/content page
            if unresolved:
                insights_resolved = _ralph_loop_insights_page(cdp_page, page_id, unresolved, cursor, conn)
                resolved_count += insights_resolved
            
            try:
                cdp_page.close()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"CDP connection failed for resolve_ad_posts: {e}")
    
    conn.close()
    logger.info(f"resolve_ad_posts: Resolved {resolved_count} ad_ids total.")
    return {"success": True, "action": "resolve_ad_posts", "resolved": resolved_count,
            "unresolved": len(unresolved)}


def _update_users_city_from_ad(cursor, conn, ad_id: str, city: str):
    """Update city for users linked to this ad_id whose city is Unknown."""
    cursor.execute('''
        UPDATE users SET city = ?
        WHERE thread_id IN (SELECT thread_id FROM user_ad_ids WHERE ad_id = ?)
        AND city = 'Unknown'
    ''', (city, ad_id))
    updated = cursor.rowcount
    conn.commit()
    if updated:
        logger.info(f"Updated city to '{city}' for {updated} user(s) via ad_id {ad_id}.")


def _match_ad_to_post(cursor, ad_content: str) -> str:
    """Try to match ad content against existing posts table."""
    if not ad_content or len(ad_content) < 20:
        return None
    # Use first 80 chars of ad content for fuzzy match
    snippet = ad_content[:80]
    cursor.execute('SELECT id FROM posts WHERE post_name LIKE ?', (f"%{snippet[:40]}%",))
    row = cursor.fetchone()
    return row["id"] if row else None


# code:tool-fbmessages-003:ralph-loop-ad-library
def _ralph_loop_ad_library(page, ad_id: str, max_attempts: int = 3) -> str:
    """Ralph loop: try FB Ad Library to get ad content."""
    url = f"https://www.facebook.com/ads/library/?id={ad_id}"
    logger.info(f"Ralph loop: Trying FB Ad Library for ad_id {ad_id}...")
    
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            
            # Extract ad content from the ad library page
            content = page.evaluate('''() => {
                // Look for the ad creative text
                let results = [];
                // FB Ad Library uses specific containers for ad content
                let containers = document.querySelectorAll(
                    'div._7jyr, div._8jg_, div[class*="_4ik4"], ' +
                    'div[class*="creative"], div[data-testid*="ad_creative"]'
                );
                for (let c of containers) {
                    let t = (c.innerText || "").trim();
                    if (t.length > 20) results.push(t);
                }
                // Fallback: get main content area
                if (results.length === 0) {
                    let main = document.querySelector('div[role="main"], main, #content');
                    if (main) {
                        let t = main.innerText.trim();
                        if (t.length > 50) results.push(t);
                    }
                }
                return results.join("\\n");
            }''')
            
            if content and len(content) > 20:
                logger.info(f"Ralph loop attempt {attempt}: Got {len(content)} chars from Ad Library.")
                
                # Save diagnostic
                diag_dir = "./logs/diagnostic/ad-library"
                os.makedirs(diag_dir, exist_ok=True)
                with open(os.path.join(diag_dir, f"ad_{ad_id}.txt"), "w") as f:
                    f.write(f"Ad ID: {ad_id}\nURL: {url}\nAttempt: {attempt}\n\n{content}")
                
                return content
            else:
                logger.info(f"Ralph loop attempt {attempt}: No content found, retrying...")
                page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"Ralph loop attempt {attempt} for ad_id {ad_id}: {e}")
    
    logger.warning(f"Ralph loop: Failed to get content for ad_id {ad_id} after {max_attempts} attempts.")
    return ""


# code:tool-fbmessages-003:ralph-loop-insights
def _ralph_loop_insights_page(page, page_id: str, unresolved_ads: list,
                               cursor, conn, max_attempts: int = 3) -> int:
    """Ralph loop: navigate insights/content page to find ad↔post relationships."""
    url = f"https://business.facebook.com/latest/insights/content?asset_id={page_id}"
    logger.info(f"Ralph loop: Trying insights/content page for {len(unresolved_ads)} unresolved ads...")
    resolved = 0
    
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)
            
            # Extract all post data from insights page
            posts_data = page.evaluate('''() => {
                let results = [];
                // Look for post rows/cards in the insights content table
                let rows = document.querySelectorAll(
                    'table tr, div[role="row"], div[class*="content-card"], ' +
                    'div[data-pagelet*="Content"] > div > div'
                );
                for (let row of rows) {
                    let text = (row.innerText || "").trim();
                    if (text.length > 30) {
                        // Look for any ad_id references in this row
                        let links = row.querySelectorAll('a');
                        let hrefs = Array.from(links).map(a => a.href).filter(h => h);
                        results.push({text: text.substring(0, 500), hrefs: hrefs});
                    }
                }
                return results;
            }''')
            
            if posts_data and len(posts_data) > 0:
                logger.info(f"Insights page attempt {attempt}: Found {len(posts_data)} content items.")
                
                # Save diagnostic
                diag_dir = "./logs/diagnostic/insights-content"
                os.makedirs(diag_dir, exist_ok=True)
                with open(os.path.join(diag_dir, f"insights_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"), "w") as f:
                    for i, pd in enumerate(posts_data):
                        f.write(f"--- Item {i} ---\n{pd.get('text', '')}\n")
                        f.write(f"HREFs: {pd.get('hrefs', [])}\n\n")
                
                # Try matching post content to unresolved ad_ids
                for pd in posts_data:
                    text = pd.get("text", "")
                    for aid in list(unresolved_ads):
                        # Check if this post's content matches any ad content we have
                        cursor.execute('SELECT ad_content FROM ad_posts WHERE ad_id = ?', (aid,))
                        row = cursor.fetchone()
                        if row and row["ad_content"]:
                            # Check if the insights post content overlaps with stored ad content
                            ad_snippet = row["ad_content"][:60]
                            if ad_snippet in text or text[:60] in row["ad_content"]:
                                city = detect_city(text, [])
                                if city != "Unknown":
                                    cursor.execute('''
                                        UPDATE ad_posts SET city = ?, ad_content = ?, resolved_at = datetime('now')
                                        WHERE ad_id = ?
                                    ''', (city, text, aid))
                                    conn.commit()
                                    _update_users_city_from_ad(cursor, conn, aid, city)
                                    resolved += 1
                                    unresolved_ads.remove(aid)
                                    logger.info(f"Resolved ad_id {aid} via insights page → city '{city}'.")
                break  # Only need one successful load
            else:
                logger.info(f"Insights page attempt {attempt}: No content found, retrying...")
                page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning(f"Insights page attempt {attempt}: {e}")
    
    return resolved


# --- Action: propagate_city (DB-only batch update) ---
# code:tool-fbmessages-004:propagate-city

def propagate_city_from_ads(page_id: str) -> dict:
    """Batch-update users.city from resolved ad_posts data.
    
    Strategy 1: For each user with city='Unknown' who has ad_id in user_ad_ids,
                check ad_posts for that ad_id's resolved city.
    Strategy 2: Scan Page-sent messages for city keywords as a fallback.
    
    This enables city-based grouping in the network graph for users
    who interacted via Facebook ads.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Strategy 1: Propagate city from ad_posts → users via user_ad_ids
    # Find users with Unknown city who have resolved ad associations
    cursor.execute('''
        SELECT DISTINCT u.thread_id, u.thread_name, ap.city, ap.ad_id
        FROM users u
        JOIN user_ad_ids uai ON uai.thread_id = u.thread_id
        JOIN ad_posts ap ON ap.ad_id = uai.ad_id
        JOIN threads t ON t.id = u.thread_id
        WHERE u.city = 'Unknown'
        AND ap.city IS NOT NULL AND ap.city != 'Unknown'
        AND t.page_id = ?
    ''', (page_id,))
    
    ad_updates = cursor.fetchall()
    ad_updated_count = 0
    ad_updated_details = []
    
    for row in ad_updates:
        cursor.execute('''
            UPDATE users SET city = ? WHERE thread_id = ? AND city = 'Unknown'
        ''', (row["city"], row["thread_id"]))
        if cursor.rowcount > 0:
            ad_updated_count += 1
            ad_updated_details.append({
                "user": row["thread_name"],
                "city": row["city"],
                "ad_id": row["ad_id"]
            })
            logger.info(f"Propagated city '{row['city']}' to user '{row['thread_name']}' via ad_id {row['ad_id']}.")
    
    conn.commit()
    
    # Strategy 2: Scan Page messages for city keywords (fallback for users without ad data)
    cursor.execute('''
        SELECT u.thread_id, u.thread_name
        FROM users u
        JOIN threads t ON t.id = u.thread_id
        WHERE u.city = 'Unknown' AND t.page_id = ?
    ''', (page_id,))
    
    still_unknown = cursor.fetchall()
    msg_updated_count = 0
    
    for user_row in still_unknown:
        # Get Page-sent messages for this thread
        cursor.execute('''
            SELECT content FROM messages
            WHERE thread_id = ? AND sender = 'Page'
        ''', (user_row["thread_id"],))
        page_messages = [{"sender": "Page", "content": r["content"]} for r in cursor.fetchall()]
        
        if page_messages:
            detected = detect_city("", page_messages)
            if detected != "Unknown":
                cursor.execute('''
                    UPDATE users SET city = ? WHERE thread_id = ? AND city = 'Unknown'
                ''', (detected, user_row["thread_id"]))
                if cursor.rowcount > 0:
                    msg_updated_count += 1
                    logger.info(f"Detected city '{detected}' from Page messages for user '{user_row['thread_name']}'.")
    
    conn.commit()
    conn.close()
    
    total = ad_updated_count + msg_updated_count
    logger.info(f"propagate_city: Updated {total} user(s) total ({ad_updated_count} from ads, {msg_updated_count} from messages).")
    
    return {
        "success": True,
        "action": "propagate_city",
        "total_updated": total,
        "from_ads": ad_updated_count,
        "from_messages": msg_updated_count,
        "ad_details": ad_updated_details[:20],  # Limit output size
    }


# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Fetch Facebook Business Suite Inbox Messages")
    parser.add_argument("--pageId", required=True, help="Facebook Page ID or full Meta Business Suite URL containing asset_id")
    parser.add_argument("--credential", default="default", help="Credential ID to load/save state.")
    parser.add_argument("--time_range", default="7d", help="Time range to fetch (1d, 7d, 30d, 90d).")
    parser.add_argument("--headless", action="store_true", help="Launch the browser invisibly (headless).")
    parser.add_argument("--action", default="fetch_messages",
                        choices=["fetch_messages", "get_list_unique_user", "fetch_message_by_user",
                                 "get_user_ad_ids", "resolve_ad_posts", "propagate_city",
                                 "classify_city_llm"],
                        help="Action to perform.")
    parser.add_argument("--refresh", action="store_true", help="Force a fresh fetch, bypassing 1-hour cache.")
    parser.add_argument("--userId", default=None, help="User ID (thread_id, phone, or email) for fetch_message_by_user.")
    parser.add_argument("--maxThreads", type=int, default=200, help="Maximum number of threads to sync (default: 200).")
    parser.add_argument("--cdp", action="store_true", help="Scrape directly via CDP connection to Chrome on port 9222 (no cookie export/import).")
    
    args = parser.parse_args()
    page_id = parse_page_id(args.pageId)
    
    logger.debug("Starting fetch_fb_messages.py execution...")
    logger.debug("# code:tool-fbmessages-003:main")
    
    if args.action == "fetch_messages":
        show_browser_flag = not args.headless
        result = fetch_messages(args.pageId, args.credential, args.time_range,
                                show_browser=show_browser_flag, force_refresh=args.refresh,
                                max_threads=args.maxThreads, use_cdp=args.cdp)
    elif args.action == "get_list_unique_user":
        result = get_list_unique_user(page_id, args.time_range)
    elif args.action == "fetch_message_by_user":
        if not args.userId:
            result = {"success": False, "error": "--userId is required for fetch_message_by_user action."}
        else:
            result = fetch_message_by_user(page_id, args.userId)
    elif args.action == "get_user_ad_ids":
        result = get_user_ad_ids(page_id)
    elif args.action == "resolve_ad_posts":
        result = resolve_ad_posts(page_id, use_cdp=args.cdp)
    elif args.action == "propagate_city":
        result = propagate_city_from_ads(page_id)
    elif args.action == "classify_city_llm":
        conn = get_db_connection()
        llm_result = _post_scrape_llm_city_classify(conn, page_id)
        conn.close()
        result = {"success": True, "action": "classify_city_llm", **llm_result}
    else:
        result = {"success": False, "error": f"Unknown action: {args.action}"}
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    if not result.get("success", False):
        sys.exit(1)

if __name__ == "__main__":
    main()
