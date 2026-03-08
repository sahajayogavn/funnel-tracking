import json
import argparse
import sys
import logging
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
import sqlite3

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
                         "Xô Viết Nghệ Tĩnh", "Bình Thạnh", "Quận 1", "Quận 3",
                         "Quận 7", "Thủ Đức", "Gò Vấp", "Tân Bình"],
    "Đà Nẵng": ["Đà Nẵng", "Da Nang", "Đà nẵng"],
    "Nghệ An": ["Nghệ An", "Nghe An", "Vinh"],
    "Hải Phòng": ["Hải Phòng", "Hai Phong"],
}

CACHE_TTL_SECONDS = 3600  # 1 hour

# --- Utility Functions ---

def parse_page_id(input_str: str) -> str:
    """Extract page ID from a URL or return the input if it's just an ID."""
    try:
        parsed = urlparse(input_str)
        if parsed.query:
            qs = parse_qs(parsed.query)
            if 'asset_id' in qs:
                return qs['asset_id'][0]
    except Exception:
        pass
    if re.match(r'^\d+$', input_str):
        return input_str
    logger.warning(f"Input '{input_str}' does not strictly look like a numeric ID or valid URL with asset_id. Proceeding anyway.")
    return input_str


# code:tool-fbmessages-002:cache
def should_fetch(page_id: str, conn: sqlite3.Connection) -> bool:
    """Return True if last fetch was > 1 hour ago or no fetch recorded."""
    row = conn.execute(
        "SELECT fetched_at FROM fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
        (page_id,)
    ).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() > CACHE_TTL_SECONDS
    except Exception:
        return True


def record_fetch(page_id: str, threads_found: int, messages_found: int, conn: sqlite3.Connection):
    """Record a fetch event in the log."""
    conn.execute(
        "INSERT INTO fetch_log (page_id, fetched_at, threads_found, messages_found) VALUES (?, ?, ?, ?)",
        (page_id, datetime.now().isoformat(), threads_found, messages_found)
    )
    conn.commit()


# code:tool-fbmessages-002:user-extract
def extract_user_info(messages: list, thread_name: str, ad_context: str = "") -> dict:
    """Extract phone, email, FB URL from message content."""
    all_text = " ".join([m.get("content", "") for m in messages]) + " " + ad_context
    
    phone_match = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', all_text)
    email_match = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', all_text)
    
    # Pick the first customer-sent phone/email (most likely the seeker's own info)
    customer_phone = None
    customer_email = None
    for m in messages:
        if m.get("sender") == "Customer":
            content = m.get("content", "")
            p = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', content)
            e = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', content)
            if p and not customer_phone:
                customer_phone = p[0]
            if e and not customer_email:
                customer_email = e[0]
    
    return {
        "phone": customer_phone or (phone_match[0] if phone_match else None),
        "email": customer_email or (email_match[0] if email_match else None),
    }


# code:tool-fbmessages-002:city-detect
def detect_city(ad_context: str, page_messages: list) -> str:
    """Detect city from ad context or page-sent messages using keyword matching."""
    # Priority: ad context first, then page messages
    search_text = ad_context
    for m in page_messages:
        if m.get("sender") == "Page":
            search_text += " " + m.get("content", "")
    
    for city, keywords in CITY_KEYWORDS.items():
        for kw in keywords:
            if kw in search_text:
                return city
    return "Unknown"


# --- DB Setup ---

def setup_database(conn: sqlite3.Connection):
    """Create all required tables (idempotent)."""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            page_id TEXT,
            thread_name TEXT,
            last_synced_time TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            sender TEXT,
            content TEXT,
            message_timestamp TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, sender, content, message_timestamp)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT NOT NULL,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            threads_found INTEGER,
            messages_found INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT UNIQUE,
            thread_name TEXT,
            phone TEXT,
            email TEXT,
            fb_url TEXT,
            city TEXT DEFAULT 'Unknown',
            lead_stage TEXT DEFAULT 'Intake',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()


def get_db_connection(memory_dir: str = None) -> sqlite3.Connection:
    """Get a connection to the FrankenSQLite DB."""
    if memory_dir is None:
        memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    db_path = os.path.join(memory_dir, "frankensqlite.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    return conn


# --- Action: fetch_messages (Playwright browser fetch) ---
# code:tool-fbmessages-001:main

def fetch_messages(page_input: str, credential_id: str, time_range: str = "7d",
                   show_browser: bool = True, force_refresh: bool = False,
                   max_threads: int = 50) -> dict:
    page_id = parse_page_id(page_input)
    logger.info(f"Using Page ID: {page_id}, Time Range: {time_range}")
    
    memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    credential_path = os.path.join(memory_dir, f"fb_credential_{credential_id}.json")
    
    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

    with sync_playwright() as p:
        if not os.path.exists(credential_path):
            logger.info(f"Credential '{credential_id}' not found at {credential_path}.")
            logger.info("Attempting to connect to existing Chrome instance via CDP at http://127.0.0.1:9222")
            
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                    default_context = browser.contexts[0]
                    page = default_context.pages[0] if default_context.pages else default_context.new_page()
                    
                    logger.info(f"Navigating to {inbox_url} to ensure authenticated state is captured properly...")
                    page.goto(inbox_url)
                    page.wait_for_timeout(5000)
                    
                    diagnostic_dir = f"./logs/diagnostic/iteration-0001"
                    os.makedirs(diagnostic_dir, exist_ok=True)
                    with open(os.path.join(diagnostic_dir, "consoleLog.txt"), "a") as f:
                        f.write(f"Attempt {attempt}: Navigated to {inbox_url}\n")
                    with open(os.path.join(diagnostic_dir, "DOM.txt"), "w") as f:
                        f.write(page.content())
                    
                    logger.info(f"Saving browser state to {credential_path}")
                    default_context.storage_state(path=credential_path)
                    
                    # Sanitize cookies
                    try:
                        with open(credential_path, 'r') as f:
                            state_data = json.load(f)
                        if "cookies" in state_data:
                            fb_cookies = []
                            for cookie in state_data["cookies"]:
                                domain = cookie.get("domain", "")
                                if "facebook.com" in domain or "messenger.com" in domain:
                                    if "expires" in cookie and cookie["expires"] < 0:
                                        del cookie["expires"]
                                    fb_cookies.append(cookie)
                            state_data["cookies"] = fb_cookies
                        with open(credential_path, 'w') as f:
                            json.dump(state_data, f, indent=2)
                        logger.info("Successfully sanitized saved CDP state.")
                    except Exception as ex:
                        logger.warning(f"Failed to sanitize cookie state: {ex}")
                    
                    logger.info("Successfully fetched and saved authenticated browser state via CDP session.")
                    browser.close()
                    return {"success": True, "method": "cdp_capture", "message": "Authenticated state saved. Run again to fetch headless."}
                except Exception as e:
                    logger.warning(f"Attempt {attempt} failed: {e}. Retrying Ralph loop...")
                    if attempt == max_attempts:
                        logger.error(f"Failed to connect via CDP after {max_attempts} attempts.")
                        logger.error("Make sure Chrome is running with --remote-debugging-port=9222")
                        return {"success": False, "error": str(e)}
        else:
            # --- Headful/Headless fetch with cache check ---
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
                
                logger.info(f"Navigating to {inbox_url}")
                page.goto(inbox_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                
                cursor = conn.cursor()

                # Find Threads — FB uses a virtualized scroll list
                # code:tool-fbmessages-002:scroll-threads
                logger.info("Inbox loaded successfully. Scanning for threads...")
                try:
                    page.wait_for_selector(
                        'div[data-pagelet="GenericBizInboxThreadListViewBody"], '
                        'div[data-pagelet="BizP13NInboxUinifiedThreadListView"], '
                        'div[aria-label="Inbox"]',
                        timeout=10000
                    )
                except Exception:
                    logger.info("Thread list pagelet not found within 10s, proceeding with fallback...")
                
                page.wait_for_timeout(2000)
                
                # Single-pass scroll-click loop: scroll sidebar, click visible 
                # threads immediately (while they're in the DOM), extract messages,
                # then scroll for more. This avoids the virtualized list re-render problem.
                # code:tool-fbmessages-002:scroll-threads
                logger.info(f"Starting sidebar scroll-and-process within {time_range}...")
                
                # Parse time_range into days
                range_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
                max_days = range_map.get(time_range, 7)
                
                processed_names = set()
                scroll_round = 0
                max_scroll_rounds = 50
                reached_date_limit = False
                last_new_round = 0
                thread_counter = 0
                stats = {"new_threads": 0, "new_messages": 0, "skipped_threads": 0}
                
                while scroll_round < max_scroll_rounds and not reached_date_limit:
                    scroll_round += 1
                    
                    if thread_counter >= max_threads:
                        logger.info(f"Reached max threads ({max_threads}). Stopping.")
                        break
                    
                    # Get currently visible _ikh threads
                    visible_threads = page.evaluate('''() => {
                        let items = document.querySelectorAll('._ikh');
                        return Array.from(items).map((el, idx) => {
                            let text = el.innerText || '';
                            let lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                            return {
                                domIndex: idx,
                                name: lines[0] || '',
                                text: text,
                                lines: lines
                            };
                        });
                    }''')
                    
                    if not visible_threads:
                        logger.info(f"Round {scroll_round}: no _ikh items visible.")
                        break
                    
                    new_in_round = 0
                    for vt in visible_threads:
                        name = vt.get("name", "").strip()
                        if not name or name in processed_names:
                            continue
                        
                        if thread_counter >= max_threads:
                            break
                        
                        # Check date labels
                        for line in vt.get("lines", []):
                            line_lower = line.lower().strip()
                            if line_lower in ("today",):
                                pass
                            elif line_lower in ("yesterday",):
                                if max_days < 1:
                                    reached_date_limit = True
                            elif line_lower in ("mon", "tue", "wed", "thu", "fri", "sat", "sun",
                                                  "monday", "tuesday", "wednesday", "thursday",
                                                  "friday", "saturday", "sunday"):
                                if max_days < 7:
                                    reached_date_limit = True
                            elif re.match(r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}$', line_lower):
                                try:
                                    parsed = datetime.strptime(f"{line} {datetime.now().year}", "%b %d %Y")
                                    days_ago = (datetime.now() - parsed).days
                                    if days_ago > max_days:
                                        reached_date_limit = True
                                        logger.info(f"Date cutoff: '{line}' is {days_ago}d ago (limit: {max_days}d).")
                                except Exception:
                                    pass
                        
                        if reached_date_limit:
                            break
                        
                        processed_names.add(name)
                        new_in_round += 1
                        thread_counter += 1
                        
                        # Thread is currently VISIBLE in the DOM — process it now
                        thread_text_full = vt.get("text", "")
                        thread_lines = [l.strip() for l in thread_text_full.split('\n') if l.strip()]
                        preview_text = " ".join(thread_lines[1:]) if len(thread_lines) > 1 else ""
                        thread_id = f"{page_id}_{abs(hash(name))}"
                        
                        cursor.execute("SELECT last_synced_time FROM threads WHERE id = ?", (thread_id,))
                        row = cursor.fetchone()
                        
                        if row and row[0] == preview_text:
                            logger.info(f"Skipping thread '{name}'. No new messages (preview matches).")
                            stats["skipped_threads"] += 1
                            continue
                        
                        logger.info(f"Syncing thread '{name}' (#{thread_counter})...")
                        
                        # Click this thread — it's in the visible DOM right now  
                        dom_index = vt.get("domIndex", 0)
                        try:
                            thread_el = page.locator('div._ikh').nth(dom_index)
                            thread_el.click(force=True, timeout=5000)
                        except Exception as e:
                            logger.warning(f"Could not click thread '{name}': {e}. Skipping.")
                    
                        # Wait for message region
                        msg_region_selector = 'div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]'
                        try:
                            page.wait_for_selector(msg_region_selector, timeout=10000)
                            page.wait_for_timeout(2000)
                        except Exception:
                            logger.warning(f"Message region not found within 10s for thread '{name}'. Falling back to timeout.")
                            page.wait_for_timeout(4000)
                        
                        logger.info(f"Page has {len(page.frames)} frames. Current URL after click: {page.url}")
                        
                        # Extract FB URL from URL param
                        fb_url = ""
                        try:
                            current_qs = parse_qs(urlparse(page.url).query)
                            if 'selected_item_id' in current_qs:
                                fb_url = current_qs['selected_item_id'][0]
                        except Exception:
                            pass
                        
                        # 1. Ad Context
                        # code:tool-fbmessages-001:ad-context
                        ad_context = page.evaluate('''() => {
                            let links = Array.from(document.querySelectorAll('a, div[role="button"]'));
                            let target = links.find(a => 
                                a.innerText && (
                                a.innerText.includes("Xem bài viết") || 
                                a.innerText.includes("View ad") ||
                                a.innerText.includes("replied to an ad") ||
                                a.innerText.includes("reply to your ad")
                                )
                            );
                            if (!target) return "";
                            let container = target;
                            for(let i=0; i<4; i++) {
                                 if(container.parentElement) container = container.parentElement;
                            }
                            return container.innerText.trim();
                        }''')
                        
                        if ad_context:
                            logger.info(f"Discovered Ad Context for thread '{name}'.")
                        
                        # 2. Extract Message Bubbles
                        # code:tool-fbmessages-001:extract-bubbles
                        js_messages = page.evaluate('''() => {
                            let region = document.querySelector(
                                'div[aria-label*="Message list container"], ' +
                                'div[role="region"][aria-label*="message"]'
                            );
                            if (!region) return [];
                            
                            let results = [];
                            let currentTimestamp = "";
                            
                            let messageArea = region.querySelector('div.x1yrsyyn') || region;
                            let topDivs = messageArea.children;
                            
                            for (let div of topDivs) {
                                if (div.classList.contains('x14vqqas') || 
                                    div.querySelector('.x14vqqas')) {
                                    let tsEl = div.classList.contains('x14vqqas') ? div : div.querySelector('.x14vqqas');
                                    if (tsEl) {
                                        let ts = tsEl.innerText.trim();
                                        if (ts && ts.length < 50) currentTimestamp = ts;
                                    }
                                    continue;
                                }
                                
                                if (div.classList.contains('xcxhlts') || 
                                    div.querySelector('.xcxhlts')) {
                                    continue;
                                }
                                
                                if (!div.classList.contains('x1fqp7bg') && 
                                    !div.querySelector('.x1fqp7bg')) continue;
                                
                                let sender = "Unknown";
                                let outerWrapper = div.querySelector('.xuk3077') || div;
                                let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                                
                                if (htmlStr.includes('x13a6bvl')) {
                                    sender = "Page";
                                } else if (htmlStr.includes('x1nhvcw1')) {
                                    sender = "Customer";
                                } else {
                                    let avatar = div.querySelector('img.img[alt]');
                                    if (avatar) {
                                        sender = "Customer";
                                    } else {
                                        sender = "Page";
                                    }
                                }
                                
                                let textContainer = div.querySelector('.x1y1aw1k');
                                if (!textContainer) {
                                    let spans = div.querySelectorAll('span > span');
                                    if (spans.length > 0) {
                                        for (let sp of spans) {
                                            let t = sp.innerText.trim();
                                            if (t && t.length > 0) {
                                                results.push({sender, text: t, timestamp: currentTimestamp});
                                            }
                                        }
                                    }
                                    continue;
                                }
                                
                                let text = textContainer.innerText.trim();
                                if (text && text.length > 0) {
                                    results.push({sender, text, timestamp: currentTimestamp});
                                }
                            }
                            
                            return results;
                        }''')
                        
                        bubble_count = len(js_messages)
                        if bubble_count == 0:
                            logger.warning(f"No message bubbles found for thread '{name}'.")
                            continue
                            
                        messages_added_this_thread = 0
                        logger.info(f"Found {bubble_count} message bubbles for thread '{name}'.")
                        
                        for msg in js_messages:
                            sender = msg.get("sender", "Unknown")
                            text = msg.get("text", "").strip()
                            msg_ts = msg.get("timestamp", "")
                            
                            if not text:
                                continue
                            
                            try:
                                msg_content_to_save = text
                                if messages_added_this_thread == 0 and ad_context:
                                    msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{text}"

                                cursor.execute(
                                    "INSERT OR IGNORE INTO messages (thread_id, sender, content, message_timestamp) VALUES (?, ?, ?, ?)",
                                    (thread_id, sender, msg_content_to_save, msg_ts)
                                )
                                if cursor.rowcount > 0:
                                    messages_added_this_thread += 1
                                    stats["new_messages"] += 1
                            except Exception as e:
                                logger.debug(f"Duplicate or error inserting message: {e}")
                            
                        # Upsert Thread record
                        cursor.execute('''
                            INSERT INTO threads (id, page_id, thread_name, last_synced_time) 
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET 
                                last_synced_time=excluded.last_synced_time
                        ''', (thread_id, page_id, name, preview_text))
                        
                        conn.commit()
                        if row is None:
                            stats["new_threads"] += 1
                        
                        # 3. Upsert user record with extracted info
                        # code:tool-fbmessages-002:user-extract
                        db_msgs = [{"sender": m.get("sender"), "content": m.get("text", "")} for m in js_messages]
                        user_info = extract_user_info(db_msgs, name, ad_context)
                        city = detect_city(ad_context, db_msgs)
                        
                        cursor.execute('''
                            INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, last_interaction)
                            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                            ON CONFLICT(thread_id) DO UPDATE SET
                                phone = COALESCE(excluded.phone, users.phone),
                                email = COALESCE(excluded.email, users.email),
                                fb_url = COALESCE(excluded.fb_url, users.fb_url),
                                city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE users.city END,
                                last_interaction = datetime('now')
                        ''', (thread_id, name, user_info["phone"], user_info["email"], fb_url, city))
                        conn.commit()
                    
                    # End of inner for loop — log round results
                    logger.info(f"Round {scroll_round}: {new_in_round} new threads processed (total: {thread_counter}).")
                    
                    if reached_date_limit:
                        logger.info(f"Reached date limit ({max_days}d). Stopping scroll.")
                        break
                    
                    if new_in_round == 0:
                        if scroll_round - last_new_round >= 3:
                            logger.info("No new threads after 3 consecutive scroll rounds. Stopping.")
                            break
                        logger.info(f"No new threads in round {scroll_round}. Retrying scroll...")
                    else:
                        last_new_round = scroll_round
                    
                    # Scroll sidebar using mouse.wheel() to trigger FB server load
                    try:
                        page.mouse.move(200, 500)
                        for _ in range(5):
                            page.mouse.wheel(0, 600)
                            page.wait_for_timeout(300)
                        logger.info(f"Scrolled sidebar via mouse.wheel (round {scroll_round}).")
                    except Exception as e:
                        logger.warning(f"Mouse wheel scroll failed: {e}. Stopping.")
                        break
                    
                    # Wait for FB to load new threads from server
                    page.wait_for_timeout(3000)
                
                logger.info(f"Scroll-and-process complete. Processed {thread_counter} threads. Stats: {stats}")
                record_fetch(page_id, stats["new_threads"] + stats["skipped_threads"], stats["new_messages"], conn)
                
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
    range_map = {"1d": "-1 day", "7d": "-7 days", "30d": "-30 days", "90d": "-90 days"}
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
    
    user_info = {
        "id": user_row["id"],
        "name": user_row["thread_name"],
        "phone": user_row["phone"],
        "email": user_row["email"],
        "fb_url": user_row["fb_url"],
        "city": user_row["city"],
        "lead_stage": user_row["lead_stage"],
    }
    
    conn.close()
    logger.info(f"fetch_message_by_user: Found {len(messages)} messages for user '{user_id}'.")
    return {"success": True, "action": "fetch_message_by_user", "user": user_info, "messages": messages}


# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Fetch Facebook Business Suite Inbox Messages")
    parser.add_argument("--pageId", required=True, help="Facebook Page ID or full Meta Business Suite URL containing asset_id")
    parser.add_argument("--credential", default="default", help="Credential ID to load/save state.")
    parser.add_argument("--time_range", default="7d", help="Time range to fetch (1d, 7d, 30d, 90d).")
    parser.add_argument("--headless", action="store_true", help="Launch the browser invisibly (headless).")
    parser.add_argument("--action", default="fetch_messages",
                        choices=["fetch_messages", "get_list_unique_user", "fetch_message_by_user"],
                        help="Action to perform.")
    parser.add_argument("--refresh", action="store_true", help="Force a fresh fetch, bypassing 1-hour cache.")
    parser.add_argument("--userId", default=None, help="User ID (thread_id, phone, or email) for fetch_message_by_user.")
    parser.add_argument("--maxThreads", type=int, default=200, help="Maximum number of threads to sync (default: 200).")
    
    args = parser.parse_args()
    page_id = parse_page_id(args.pageId)
    
    logger.debug("Starting fetch_fb_messages.py execution...")
    logger.debug("# code:tool-fbmessages-002:main")
    
    if args.action == "fetch_messages":
        show_browser_flag = not args.headless
        result = fetch_messages(args.pageId, args.credential, args.time_range,
                                show_browser=show_browser_flag, force_refresh=args.refresh,
                                max_threads=args.maxThreads)
    elif args.action == "get_list_unique_user":
        result = get_list_unique_user(page_id, args.time_range)
    elif args.action == "fetch_message_by_user":
        if not args.userId:
            result = {"success": False, "error": "--userId is required for fetch_message_by_user action."}
        else:
            result = fetch_message_by_user(page_id, args.userId)
    else:
        result = {"success": False, "error": f"Unknown action: {args.action}"}
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    if not result.get("success", False):
        sys.exit(1)

if __name__ == "__main__":
    main()
