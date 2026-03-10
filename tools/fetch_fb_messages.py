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


# code:tool-fbmessages-003:parse-ad-ids
def parse_ad_ids(text: str) -> list:
    """Extract unique ad_id numbers from text containing ad_id.XXXXX patterns."""
    raw = re.findall(r'ad_id\.?(\d{5,})', text)
    # Deduplicate while preserving order
    return list(dict.fromkeys(raw))


# code:tool-fbmessages-003:extract-labels
def extract_ad_id_labels(page) -> list:
    """Extract ad_id values from the Labels section in thread detail panel.
    
    Targets only the right sidebar (thread detail) panel to avoid scanning
    the entire page DOM which causes massive duplicate matches.
    """
    labels_text = page.evaluate('''() => {
        // Target the right sidebar panel where Labels are shown
        // FB Business Suite uses the rightmost panel for thread details
        let sidebar = null;
        
        // Strategy 1: Find the thread detail panel by looking for "Labels" heading
        let headings = document.querySelectorAll('span, h3, h4, div');
        for (let h of headings) {
            let t = (h.innerText || "").trim();
            if (t === "Labels" || t === "Nhãn" || t === "Label") {
                // Found the Labels heading, get its parent container
                sidebar = h.closest('div[class*="x1n2onr6"]') || h.parentElement?.parentElement;
                break;
            }
        }
        
        // Strategy 2: If we found the Labels container, extract text from it
        if (sidebar) {
            let text = (sidebar.innerText || "").trim();
            if (text.includes("ad_id")) return text;
        }
        
        // Strategy 3: Fallback - look only in the rightmost panel area
        // FB Business Suite has a right sidebar area for contact details
        let detailPanels = document.querySelectorAll(
            'div[aria-label*="detail"], div[aria-label*="contact"], ' +
            'div[role="complementary"], aside'
        );
        let allText = "";
        for (let panel of detailPanels) {
            let t = (panel.innerText || "").trim();
            if (t.includes("ad_id")) {
                allText += " " + t;
            }
        }
        if (allText) return allText.trim();
        
        // Strategy 4: Last resort - scan all listitem elements (FB label pills)
        let labels = document.querySelectorAll('[role="listitem"]');
        let labelText = "";
        for (let label of labels) {
            let t = (label.innerText || label.textContent || "").trim();
            if (t.includes("ad_id")) {
                labelText += " " + t;
            }
        }
        return labelText.trim();
    }''')
    return parse_ad_ids(labels_text)


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
            seq INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, sender, content, message_timestamp, seq)
        )
    ''')
    # Migration: ensure UNIQUE constraint includes seq column
    # SQLite cannot alter constraints, so we must recreate the table
    try:
        # Check if seq column exists
        cursor.execute("PRAGMA table_info(messages)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'seq' not in cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN seq INTEGER DEFAULT 0")
        # Check if the UNIQUE constraint includes seq
        # ALTER TABLE adds columns AFTER constraints, so 'seq' may exist
        # in the SQL but NOT inside the UNIQUE() clause
        cursor.execute("SELECT sql FROM sqlite_master WHERE name='messages'")
        table_sql = cursor.fetchone()
        if table_sql:
            sql_text = table_sql[0]
            # Extract the UNIQUE clause content
            import re as _re
            unique_match = _re.search(r'UNIQUE\s*\(([^)]+)\)', sql_text, _re.IGNORECASE)
            if unique_match:
                unique_cols = unique_match.group(1)
                if 'seq' not in unique_cols:
                    # Old constraint without seq — need full table recreation
                    cursor.execute("ALTER TABLE messages RENAME TO messages_old")
                    cursor.execute('''
                        CREATE TABLE messages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            thread_id TEXT,
                            sender TEXT,
                            content TEXT,
                            message_timestamp TEXT,
                            seq INTEGER DEFAULT 0,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(thread_id, sender, content, message_timestamp, seq)
                        )
                    ''')
                    cursor.execute('''
                        INSERT INTO messages (id, thread_id, sender, content, message_timestamp, seq, timestamp)
                        SELECT id, thread_id, sender, content, message_timestamp, COALESCE(seq, 0), timestamp
                        FROM messages_old
                    ''')
                    cursor.execute("DROP TABLE messages_old")
                    logger.info("Migrated messages table: UNIQUE constraint now includes seq column.")
    except Exception as e:
        logger.debug(f"Messages table migration check: {e}")
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
    # code:tool-fbmessages-003:ad-id-tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_ad_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            ad_id TEXT,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, ad_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ad_posts (
            ad_id TEXT PRIMARY KEY,
            post_id TEXT,
            ad_content TEXT,
            city TEXT DEFAULT 'Unknown',
            resolved_at DATETIME
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


# --- Shared scraping helper ---
# code:tool-fbmessages-001:scrape-inbox

def _scrape_inbox(page, page_id: str, time_range: str, max_threads: int,
                  conn: sqlite3.Connection) -> dict:
    """Core scraping loop: scroll sidebar, click threads, extract messages.
    
    Works identically whether `page` comes from a CDP session or a fresh
    Playwright browser with saved cookies.
    """
    cursor = conn.cursor()
    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

    logger.info(f"Navigating to {inbox_url}")
    page.goto(inbox_url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

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

    # Wait for thread items to actually render (FB SPA hydration may be slow in new tabs)
    _ikh_found = False
    for wait_attempt in range(1, 6):
        try:
            page.wait_for_selector('div._ikh', timeout=5000)
            _ikh_found = True
            logger.info(f"Thread items (_ikh) appeared after wait attempt {wait_attempt}.")
            break
        except Exception:
            logger.info(f"Wait attempt {wait_attempt}/5: _ikh not visible yet, retrying in 3s...")
            page.wait_for_timeout(3000)
    
    if not _ikh_found:
        logger.warning("Thread items (_ikh) never appeared after 5 attempts. Will proceed but may find 0 threads.")
    
    page.wait_for_timeout(1000)

    # Single-pass scroll-click loop: scroll sidebar, click visible
    # threads immediately (while they're in the DOM), extract messages,
    # then scroll for more. This avoids the virtualized list re-render problem.
    # code:tool-fbmessages-002:scroll-threads
    logger.info(f"Starting sidebar scroll-and-process within {time_range}...")

    # Parse time_range into days — accept both "365d" and "365" formats
    range_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
    max_days = range_map.get(time_range, None)
    if max_days is None:
        # Try bare numeric or with/without 'd' suffix
        clean = str(time_range).rstrip('d')
        try:
            max_days = int(clean)
        except ValueError:
            logger.warning(f"Unrecognized time_range '{time_range}', defaulting to 7 days.")
            max_days = 7
    logger.info(f"Time range resolved to {max_days} day(s).")

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

            # ============================================================
            # REQUIREMENT: Scroll up in message panel to load ALL messages
            # ============================================================
            # Facebook lazy-loads older messages in the chat panel. When a
            # thread is clicked, only the most recent messages are rendered
            # at the bottom. Older messages are loaded on-demand as the user
            # scrolls up. If we extract messages without scrolling up first,
            # we will MISS older messages and only capture the latest ones.
            #
            # SOLUTION: Before extracting messages, we must:
            # 1. Locate the scrollable message container
            # 2. Scroll UP repeatedly (mouse.wheel with negative deltaY)
            # 3. Wait for FB to load older messages after each scroll
            # 4. Continue until the message count stabilizes (no new
            #    messages loaded after scrolling) or we hit a max rounds limit
            # 5. Only THEN extract all message bubbles from the fully-loaded DOM
            # ============================================================
            # code:tool-fbmessages-003:scroll-up-messages
            logger.info(f"Scrolling up in message panel to load all messages for '{name}'...")

            # First, move the mouse to the center of the message panel area
            # (right side of the screen, approximately center)
            try:
                page.mouse.move(900, 400)
            except Exception:
                pass

            prev_msg_count = 0
            prev_scroll_height = 0
            stable_rounds = 0
            max_scroll_up_rounds = 50
            for scroll_up_round in range(1, max_scroll_up_rounds + 1):
                # Count current messages AND check scrollHeight for changes
                scroll_info = page.evaluate('''() => {
                    let region = document.querySelector(
                        'div[aria-label*="Message list container"], ' +
                        'div[role="region"][aria-label*="message"]'
                    );
                    if (!region) return {count: 0, scrollHeight: 0, scrollTop: 0};
                    let messageArea = region.querySelector('div.x1yrsyyn') || region;
                    let count = 0;
                    for (let div of messageArea.children) {
                        // Count ALL child divs to detect any new content loaded
                        count++;
                    }
                    // Find the actual scrollable parent
                    let scrollable = region;
                    let el = region;
                    while (el) {
                        if (el.scrollHeight > el.clientHeight && el.clientHeight > 100) {
                            scrollable = el;
                            break;
                        }
                        el = el.parentElement;
                    }
                    return {
                        count: count,
                        scrollHeight: scrollable.scrollHeight,
                        scrollTop: scrollable.scrollTop,
                        scrollableTag: scrollable.tagName
                    };
                }''')

                current_count = scroll_info.get("count", 0) if isinstance(scroll_info, dict) else 0
                current_sh = scroll_info.get("scrollHeight", 0) if isinstance(scroll_info, dict) else 0
                current_st = scroll_info.get("scrollTop", 0) if isinstance(scroll_info, dict) else 0

                # Check stability: both message count AND scrollHeight must be stable
                if current_count == prev_msg_count and current_sh == prev_scroll_height:
                    stable_rounds += 1
                    if stable_rounds >= 3:
                        logger.info(f"Message count stable at {current_count} (scrollHeight={current_sh}) after {scroll_up_round} scroll rounds. All messages loaded.")
                        break
                else:
                    if current_count != prev_msg_count or current_sh != prev_scroll_height:
                        logger.info(f"Scroll-up round {scroll_up_round}: count {prev_msg_count}→{current_count}, scrollHeight {prev_scroll_height}→{current_sh}, scrollTop={current_st}.")
                    stable_rounds = 0
                    prev_msg_count = current_count
                    prev_scroll_height = current_sh

                # Scroll UP incrementally — small steps to trigger FB's lazy-load
                # FB uses a React virtualizer that loads content based on scroll events
                try:
                    page.evaluate('''() => {
                        let region = document.querySelector(
                            'div[aria-label*="Message list container"], ' +
                            'div[role="region"][aria-label*="message"]'
                        );
                        if (!region) return;
                        
                        // Find the actual scrollable element
                        let scrollable = region;
                        let el = region;
                        while (el) {
                            if (el.scrollHeight > el.clientHeight && el.clientHeight > 100) {
                                scrollable = el;
                                break;
                            }
                            el = el.parentElement;
                        }
                        
                        // Scroll up incrementally (not jumping to 0)
                        let newTop = Math.max(0, scrollable.scrollTop - 800);
                        scrollable.scrollTop = newTop;
                        
                        // Dispatch scroll event to trigger FB's React virtualizer
                        scrollable.dispatchEvent(new Event('scroll', {bubbles: true}));
                    }''')
                    # Also use mouse wheel for additional coverage
                    page.mouse.wheel(0, -2000)
                except Exception as e:
                    logger.debug(f"Scroll-up attempt failed: {e}")

                # Wait for FB to fetch and render older messages
                page.wait_for_timeout(1500)

            logger.info(f"Scroll-up complete for '{name}'. Final element count: {prev_msg_count}.")

            # 2. Extract Message Bubbles (after scroll-up ensures all are loaded)
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
                    
                    // CRITICAL: FB groups consecutive same-sender messages into ONE
                    // container div. Each individual message has its own .x1y1aw1k
                    // text container inside the group. We must use querySelectorAll
                    // to extract ALL messages from grouped containers.
                    let textContainers = div.querySelectorAll('.x1y1aw1k');
                    if (textContainers.length > 0) {
                        for (let tc of textContainers) {
                            let text = tc.innerText.trim();
                            if (text && text.length > 0) {
                                results.push({sender, text, timestamp: currentTimestamp});
                            }
                        }
                    } else {
                        // Fallback: try span > span for messages without .x1y1aw1k
                        let spans = div.querySelectorAll('span > span');
                        let found = false;
                        if (spans.length > 0) {
                            for (let sp of spans) {
                                let t = sp.innerText.trim();
                                if (t && t.length > 0) {
                                    results.push({sender, text: t, timestamp: currentTimestamp});
                                    found = true;
                                }
                            }
                        }
                        // Last resort: get any visible text from the container
                        if (!found) {
                            let text = div.innerText.trim();
                            // Filter out very short text or system-like messages
                            if (text && text.length > 2 && text.length < 2000) {
                                results.push({sender, text, timestamp: currentTimestamp});
                            }
                        }
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

            for msg_idx, msg in enumerate(js_messages):
                sender = msg.get("sender", "Unknown")
                text = msg.get("text", "").strip()
                msg_ts = msg.get("timestamp", "")

                if not text:
                    continue

                try:
                    msg_content_to_save = text
                    if messages_added_this_thread == 0 and ad_context:
                        msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{text}"

                    # Use seq (sequence position) to differentiate duplicate-content
                    # messages that appear at different positions in the conversation
                    # (e.g., same auto-reply sent twice when user re-interacts with ad)
                    cursor.execute(
                        "INSERT OR IGNORE INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
                        (thread_id, sender, msg_content_to_save, msg_ts, msg_idx)
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

            # 3. Extract ad_id labels from thread detail panel
            # code:tool-fbmessages-003:extract-labels
            try:
                ad_ids = extract_ad_id_labels(page)
                if ad_ids:
                    logger.info(f"Found ad_ids {ad_ids} for thread '{name}'.")
                    for aid in ad_ids:
                        cursor.execute('''
                            INSERT OR IGNORE INTO user_ad_ids (thread_id, ad_id)
                            VALUES (?, ?)
                        ''', (thread_id, aid))
                        # Upsert ad_posts with ad_context if available
                        if ad_context:
                            ad_city = detect_city(ad_context, [])
                            cursor.execute('''
                                INSERT INTO ad_posts (ad_id, ad_content, city, resolved_at)
                                VALUES (?, ?, ?, datetime('now'))
                                ON CONFLICT(ad_id) DO UPDATE SET
                                    ad_content = CASE WHEN excluded.ad_content != '' THEN excluded.ad_content ELSE ad_posts.ad_content END,
                                    city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE ad_posts.city END,
                                    resolved_at = datetime('now')
                            ''', (aid, ad_context, ad_city))
                    conn.commit()
            except Exception as e:
                logger.warning(f"Could not extract ad_id labels for thread '{name}': {e}")

            # 4. Upsert user record with extracted info
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
    return stats


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
            try:
                browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                default_context = browser.contexts[0]
                # Always open a NEW tab — never hijack the user's existing tabs
                cdp_page = default_context.new_page()
                logger.info(f"Connected to CDP session. Opened new tab (total tabs: {len(default_context.pages)}).")

                # Diagnostic log per workflow step 6
                diag_dir = "./logs/diagnostic/cdp-direct"
                os.makedirs(diag_dir, exist_ok=True)
                run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                logger.info("Starting direct scrape...")
                stats = _scrape_inbox(cdp_page, page_id, time_range, max_threads, conn)

                # Write diagnostic summary
                with open(os.path.join(diag_dir, f"run_{run_ts}.log"), "w") as f:
                    f.write(f"CDP Direct Scrape: {run_ts}\n")
                    f.write(f"Page ID: {page_id}, Time Range: {time_range}, Max Threads: {max_threads}\n")
                    f.write(f"Stats: {json.dumps(stats, indent=2)}\n")

                # Close only our new tab, NOT the browser
                try:
                    cdp_page.close()
                except Exception:
                    pass

                conn.close()
                logger.info(f"CDP Direct: Saved to FrankenSQLite. Stats: {stats}")
                return {"success": True, "method": "cdp_direct", "data": {"stats": stats}}
            except Exception as e:
                logger.error(f"CDP Direct scrape failed: {e}")
                # Save error diagnostic
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
                return {"success": False, "error": str(e)}

    # --- Mode 2: Legacy credential capture (no credential file, no --cdp) ---
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
                    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

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


# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Fetch Facebook Business Suite Inbox Messages")
    parser.add_argument("--pageId", required=True, help="Facebook Page ID or full Meta Business Suite URL containing asset_id")
    parser.add_argument("--credential", default="default", help="Credential ID to load/save state.")
    parser.add_argument("--time_range", default="7d", help="Time range to fetch (1d, 7d, 30d, 90d).")
    parser.add_argument("--headless", action="store_true", help="Launch the browser invisibly (headless).")
    parser.add_argument("--action", default="fetch_messages",
                        choices=["fetch_messages", "get_list_unique_user", "fetch_message_by_user",
                                 "get_user_ad_ids", "resolve_ad_posts"],
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
    else:
        result = {"success": False, "error": f"Unknown action: {args.action}"}
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    if not result.get("success", False):
        sys.exit(1)

if __name__ == "__main__":
    main()
