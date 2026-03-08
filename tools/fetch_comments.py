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
        logging.FileHandler('./logs/fetch_comments.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("fetch_comments")

# --- Constants ---
CACHE_TTL_SECONDS = 3600  # 1 hour

# Reuse city detection keywords from fetch_fb_messages
# code:tool-fbcomments-001:city-detect
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


# --- Utility Functions ---

# code:tool-fbcomments-001:parse-input
def parse_page_id(input_str: str) -> str:
    """Extract page ID (asset_id) from a URL or return the input if it's just an ID."""
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
    logger.warning(f"Input '{input_str}' does not look like a numeric ID or valid URL with asset_id. Proceeding anyway.")
    return input_str


def parse_post_id(input_str: str) -> str:
    """Extract selected_item_id from a URL or return input as-is."""
    try:
        parsed = urlparse(input_str)
        if parsed.query:
            qs = parse_qs(parsed.query)
            if 'selected_item_id' in qs:
                return qs['selected_item_id'][0]
    except Exception:
        pass
    return input_str


# code:tool-fbcomments-001:cache
def should_fetch(page_id: str, conn: sqlite3.Connection) -> bool:
    """Return True if last comment fetch was > 1 hour ago or no fetch recorded."""
    row = conn.execute(
        "SELECT fetched_at FROM comment_fetch_log WHERE page_id=? ORDER BY id DESC LIMIT 1",
        (page_id,)
    ).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() > CACHE_TTL_SECONDS
    except Exception:
        return True


def record_fetch(page_id: str, posts_found: int, comments_found: int, conn: sqlite3.Connection):
    """Record a comment fetch event in the log."""
    conn.execute(
        "INSERT INTO comment_fetch_log (page_id, fetched_at, posts_found, comments_found) VALUES (?, ?, ?, ?)",
        (page_id, datetime.now().isoformat(), posts_found, comments_found)
    )
    conn.commit()


# code:tool-fbcomments-001:user-extract
def extract_user_info(comments: list) -> dict:
    """Extract phone and email from comment text."""
    all_text = " ".join([c.get("comment_text", "") for c in comments])

    phone_match = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', all_text)
    email_match = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', all_text)

    return {
        "phone": phone_match[0] if phone_match else None,
        "email": email_match[0] if email_match else None,
    }


# code:tool-fbcomments-001:city-detect
def detect_city(text: str) -> str:
    """Detect city from comment text using keyword matching."""
    for city, keywords in CITY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return city
    return "Unknown"


# --- DB Setup ---
# code:tool-fbcomments-001:db-setup
def setup_comment_database(conn: sqlite3.Connection):
    """Create all required tables for comment fetching (idempotent)."""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            page_id TEXT,
            post_name TEXT,
            post_url TEXT,
            last_synced_time TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            commenter_name TEXT,
            comment_text TEXT,
            comment_timestamp TEXT,
            fb_profile_url TEXT,
            fb_user_id TEXT,
            is_reply INTEGER DEFAULT 0,
            comment_date TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, commenter_name, comment_text)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT NOT NULL,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            posts_found INTEGER,
            comments_found INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            commenter_name TEXT,
            fb_user_id TEXT,
            fb_profile_url TEXT,
            phone TEXT,
            email TEXT,
            city TEXT DEFAULT 'Unknown',
            lead_stage TEXT DEFAULT 'Intake',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, commenter_name)
        )
    ''')
    conn.commit()


def get_db_connection(memory_dir: str = None) -> sqlite3.Connection:
    """Get a connection to the shared FrankenSQLite DB."""
    if memory_dir is None:
        memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    db_path = os.path.join(memory_dir, "frankensqlite.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_comment_database(conn)
    return conn


# --- Action: fetch_comments (Playwright browser fetch) ---
# code:tool-fbcomments-001:main

def fetch_comments(page_input: str, credential_id: str, time_range: str = "7d",
                   show_browser: bool = True, force_refresh: bool = False,
                   max_posts: int = 50) -> dict:
    """Fetch comments from Facebook Page posts using Playwright browser automation."""
    page_id = parse_page_id(page_input)
    logger.info(f"Using Page ID: {page_id}, Time Range: {time_range}")

    memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    credential_path = os.path.join(memory_dir, f"fb_credential_{credential_id}.json")

    # Navigate to the FB Page Post inbox
    inbox_url = f"https://business.facebook.com/latest/inbox/facebook?asset_id={page_id}&thread_type=FB_PAGE_POST"

    with sync_playwright() as p:
        if not os.path.exists(credential_path):
            logger.info(f"Credential '{credential_id}' not found at {credential_path}.")
            logger.info("Attempting to connect to existing Chrome instance via CDP at http://127.0.0.1:9222")

            # Ralph loop: retry CDP capture
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

            # code:tool-fbcomments-001:cache
            if not force_refresh and not should_fetch(page_id, conn):
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

                cursor = conn.cursor()

                # --- Scroll and process post threads in the sidebar ---
                # code:tool-fbcomments-001:scroll-posts
                logger.info("Post inbox loaded. Scanning for post threads...")
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

                # Parse time_range into days
                range_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
                max_days = range_map.get(time_range, 7)

                processed_names = set()
                scroll_round = 0
                max_scroll_rounds = 50
                reached_date_limit = False
                last_new_round = 0
                post_counter = 0
                stats = {"new_posts": 0, "new_comments": 0, "skipped_posts": 0}

                while scroll_round < max_scroll_rounds and not reached_date_limit:
                    scroll_round += 1

                    if post_counter >= max_posts:
                        logger.info(f"Reached max posts ({max_posts}). Stopping.")
                        break

                    # Get currently visible _ikh thread items (same DOM structure as messages)
                    visible_posts = page.evaluate('''() => {
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

                    if not visible_posts:
                        logger.info(f"Round {scroll_round}: no _ikh items visible.")
                        break

                    new_in_round = 0
                    for vp in visible_posts:
                        name = vp.get("name", "").strip()
                        if not name or name in processed_names:
                            continue

                        if post_counter >= max_posts:
                            break

                        # Check date labels for cutoff
                        for line in vp.get("lines", []):
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
                            else:
                                # Only check SHORT lines for date patterns — FB date labels
                                # are short (e.g. "Feb 4", "Nov 20", "Jan 15, 2025")
                                # Long lines are post content text, skip to avoid false positives
                                if len(line_lower) > 30:
                                    continue
                                # Try multiple date patterns:
                                date_patterns = [
                                    (r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2},?\s*\d{4}', "%b %d %Y"),
                                    (r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}', "%b %d"),
                                    (r'\d{1,2}/\d{1,2}/\d{4}', "%m/%d/%Y"),
                                    (r'\d{1,2}/\d{1,2}', "%m/%d"),
                                ]
                                for pattern, fmt in date_patterns:
                                    m = re.search(pattern, line_lower)
                                    if m:
                                        date_str = m.group(0).replace(",", "")
                                        try:
                                            now = datetime.now()
                                            if "%Y" in fmt:
                                                parsed_date = datetime.strptime(date_str, fmt)
                                            else:
                                                parsed_date = datetime.strptime(f"{date_str} {now.year}", fmt + " %Y")
                                                # If parsed date is in the future, it's from last year
                                                if parsed_date > now:
                                                    parsed_date = parsed_date.replace(year=now.year - 1)
                                            days_ago = (now - parsed_date).days
                                            if days_ago > max_days:
                                                reached_date_limit = True
                                                logger.info(f"Date cutoff: '{line}' is {days_ago}d ago (limit: {max_days}d).")
                                        except Exception:
                                            pass
                                        break

                        if reached_date_limit:
                            break

                        processed_names.add(name)
                        new_in_round += 1
                        post_counter += 1

                        # Post thread is visible in DOM — click and process it now
                        post_text_full = vp.get("text", "")
                        post_lines = [l.strip() for l in post_text_full.split('\n') if l.strip()]
                        preview_text = " ".join(post_lines[1:]) if len(post_lines) > 1 else ""
                        post_id = f"{page_id}_{abs(hash(name))}"

                        cursor.execute("SELECT last_synced_time FROM posts WHERE id = ?", (post_id,))
                        row = cursor.fetchone()

                        if row and row[0] == preview_text:
                            logger.info(f"Skipping post '{name}'. No new comments (preview matches).")
                            stats["skipped_posts"] += 1
                            continue

                        logger.info(f"Syncing post '{name}' (#{post_counter})...")

                        # Click this post thread
                        dom_index = vp.get("domIndex", 0)
                        try:
                            post_el = page.locator('div._ikh').nth(dom_index)
                            post_el.click(force=True, timeout=5000)
                        except Exception as e:
                            logger.warning(f"Could not click post '{name}': {e}. Skipping.")
                            continue

                        # Wait for comment section to load
                        comment_region_selector = 'div[aria-label*="Comment"], div[role="complementary"], div[data-pagelet*="Comment"]'
                        try:
                            page.wait_for_selector(comment_region_selector, timeout=10000)
                            page.wait_for_timeout(2000)
                        except Exception:
                            logger.warning(f"Comment region not found within 10s for post '{name}'. Falling back to timeout.")
                            page.wait_for_timeout(4000)

                        # Extract post URL from current page URL
                        post_url = ""
                        try:
                            current_qs = parse_qs(urlparse(page.url).query)
                            if 'selected_item_id' in current_qs:
                                post_url = current_qs['selected_item_id'][0]
                        except Exception:
                            pass

                        # --- Extract Comments ---
                        # code:tool-fbcomments-001:extract-comments
                        js_comments = page.evaluate('''() => {
                            let results = [];
                            let seen = new Set();
                            
                            // Helper: extract FB user ID from profile URL
                            function extractUserId(url) {
                                if (!url) return '';
                                // Pattern: /profile.php?id=XXXXX or /username
                                let idMatch = url.match(/profile\.php\?id=(\d+)/);
                                if (idMatch) return idMatch[1];
                                let pathMatch = url.match(/facebook\.com\/([\w.]+)/);
                                if (pathMatch && !['pages','groups','events','hashtag'].includes(pathMatch[1])) return pathMatch[1];
                                return '';
                            }
                            
                            // Strategy 1: Look for comment-like structures in the inbox detail panel
                            // FB Business inbox shows comments with commenter name + text pairs
                            let commentBlocks = document.querySelectorAll(
                                'div[role="article"], ' +
                                'div[aria-label*="Comment"], ' +
                                'div[aria-label*="comment"]'
                            );
                            
                            for (let block of commentBlocks) {
                                let nameEl = block.querySelector('a[role="link"] span, strong, span[dir="auto"]');
                                let profileLink = block.querySelector('a[role="link"][href*="facebook.com"]');
                                let textEl = block.querySelector('div[dir="auto"], span[dir="auto"]');
                                let timeEl = block.querySelector('abbr, time, a[role="link"] span');
                                
                                let commenterName = nameEl ? nameEl.innerText.trim() : '';
                                let commentText = textEl ? textEl.innerText.trim() : '';
                                let timestamp = timeEl ? timeEl.innerText.trim() : '';
                                let profileUrl = profileLink ? profileLink.href : '';
                                let userId = extractUserId(profileUrl);
                                
                                // Detect if this is a reply (nested under another comment)
                                let isReply = false;
                                let parent = block.parentElement;
                                for (let i = 0; i < 5 && parent; i++) {
                                    if (parent.getAttribute && parent.getAttribute('role') === 'article') {
                                        isReply = true;
                                        break;
                                    }
                                    parent = parent.parentElement;
                                }
                                
                                if (commentText && commentText.length > 0 && commenterName) {
                                    if (commentText === commenterName) continue;
                                    let key = commenterName + '|' + commentText;
                                    if (seen.has(key)) continue;
                                    seen.add(key);
                                    results.push({
                                        commenter_name: commenterName,
                                        comment_text: commentText,
                                        timestamp: timestamp,
                                        profile_url: profileUrl,
                                        fb_user_id: userId,
                                        is_reply: isReply
                                    });
                                }
                            }
                            
                            // Strategy 2: Fallback — look for the message-style layout in inbox
                            // The inbox renders post comments similar to messages with sender labels  
                            if (results.length === 0) {
                                let region = document.querySelector(
                                    'div[aria-label*="Message list container"], ' +
                                    'div[role="region"][aria-label*="message"]'
                                );
                                if (region) {
                                    let messageArea = region.querySelector('div.x1yrsyyn') || region;
                                    let topDivs = messageArea.children;
                                    let currentTimestamp = '';
                                    
                                    for (let div of topDivs) {
                                        // Timestamp rows
                                        if (div.classList.contains('x14vqqas') || 
                                            div.querySelector('.x14vqqas')) {
                                            let tsEl = div.classList.contains('x14vqqas') ? div : div.querySelector('.x14vqqas');
                                            if (tsEl) {
                                                let ts = tsEl.innerText.trim();
                                                if (ts && ts.length < 50) currentTimestamp = ts;
                                            }
                                            continue;
                                        }
                                        
                                        // System messages — skip
                                        if (div.classList.contains('xcxhlts') || 
                                            div.querySelector('.xcxhlts')) {
                                            continue;
                                        }
                                        
                                        if (!div.classList.contains('x1fqp7bg') && 
                                            !div.querySelector('.x1fqp7bg')) continue;
                                        
                                        let sender = 'Unknown';
                                        let outerWrapper = div.querySelector('.xuk3077') || div;
                                        let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                                        
                                        if (htmlStr.includes('x13a6bvl')) {
                                            sender = 'Page';
                                        } else if (htmlStr.includes('x1nhvcw1')) {
                                            sender = 'Customer';
                                        } else {
                                            let avatar = div.querySelector('img.img[alt]');
                                            sender = avatar ? 'Customer' : 'Page';
                                        }
                                        
                                        // Try to get profile link
                                        let profileLink = div.querySelector('a[href*="facebook.com/"]');
                                        let profileUrl = profileLink ? profileLink.href : '';
                                        let userId = extractUserId(profileUrl);
                                        
                                        let textContainer = div.querySelector('.x1y1aw1k');
                                        let text = '';
                                        if (textContainer) {
                                            text = textContainer.innerText.trim();
                                        } else {
                                            let spans = div.querySelectorAll('span > span');
                                            for (let sp of spans) {
                                                let t = sp.innerText.trim();
                                                if (t && t.length > 0) {
                                                    text = t;
                                                    break;
                                                }
                                            }
                                        }
                                        
                                        if (text && text.length > 0) {
                                            let key = sender + '|' + text;
                                            if (!seen.has(key)) {
                                                seen.add(key);
                                                results.push({
                                                    commenter_name: sender,
                                                    comment_text: text,
                                                    timestamp: currentTimestamp,
                                                    profile_url: profileUrl,
                                                    fb_user_id: userId,
                                                    is_reply: false
                                                });
                                            }
                                        }
                                    }
                                }
                            }
                            
                            return results;
                        }''')

                        comment_count = len(js_comments)
                        if comment_count == 0:
                            logger.warning(f"No comments found for post '{name}'.")
                            # Still record the post
                            cursor.execute('''
                                INSERT INTO posts (id, page_id, post_name, post_url, last_synced_time) 
                                VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET 
                                    last_synced_time=excluded.last_synced_time
                            ''', (post_id, page_id, name, post_url, preview_text))
                            conn.commit()
                            if row is None:
                                stats["new_posts"] += 1
                            continue

                        comments_added_this_post = 0
                        logger.info(f"Found {comment_count} comments for post '{name}'.")

                        for cmt in js_comments:
                            commenter = cmt.get("commenter_name", "Unknown").strip()
                            text = cmt.get("comment_text", "").strip()
                            cmt_ts = cmt.get("timestamp", "")
                            profile_url = cmt.get("profile_url", "")
                            fb_user_id = cmt.get("fb_user_id", "")
                            is_reply = 1 if cmt.get("is_reply", False) else 0
                            comment_date = cmt_ts  # Use timestamp string as comment_date

                            if not text:
                                continue

                            try:
                                cursor.execute(
                                    "INSERT OR IGNORE INTO comments (post_id, commenter_name, comment_text, comment_timestamp, fb_profile_url, fb_user_id, is_reply, comment_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (post_id, commenter, text, cmt_ts, profile_url, fb_user_id, is_reply, comment_date)
                                )
                                if cursor.rowcount > 0:
                                    comments_added_this_post += 1
                                    stats["new_comments"] += 1
                            except Exception as e:
                                logger.debug(f"Duplicate or error inserting comment: {e}")

                        # Upsert Post record
                        cursor.execute('''
                            INSERT INTO posts (id, page_id, post_name, post_url, last_synced_time) 
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET 
                                last_synced_time=excluded.last_synced_time
                        ''', (post_id, page_id, name, post_url, preview_text))

                        conn.commit()
                        if row is None:
                            stats["new_posts"] += 1

                        # Upsert comment_users record
                        # code:tool-fbcomments-001:user-extract
                        db_cmts = [{"comment_text": c.get("comment_text", "")} for c in js_comments]
                        user_info = extract_user_info(db_cmts)
                        all_comment_text = " ".join([c.get("comment_text", "") for c in js_comments])
                        city = detect_city(all_comment_text)

                        for cmt in js_comments:
                            commenter = cmt.get("commenter_name", "Unknown").strip()
                            if commenter in ("Page", "Unknown", ""):
                                continue
                            cmt_profile_url = cmt.get("profile_url", "")
                            cmt_user_id = cmt.get("fb_user_id", "")
                            try:
                                cursor.execute('''
                                    INSERT INTO comment_users (post_id, commenter_name, fb_user_id, fb_profile_url, phone, email, city, last_interaction)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                                    ON CONFLICT(post_id, commenter_name) DO UPDATE SET
                                        fb_user_id = COALESCE(excluded.fb_user_id, comment_users.fb_user_id),
                                        fb_profile_url = COALESCE(excluded.fb_profile_url, comment_users.fb_profile_url),
                                        phone = COALESCE(excluded.phone, comment_users.phone),
                                        email = COALESCE(excluded.email, comment_users.email),
                                        city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE comment_users.city END,
                                        last_interaction = datetime('now')
                                ''', (post_id, commenter, cmt_user_id, cmt_profile_url, user_info["phone"], user_info["email"], city))
                            except Exception as e:
                                logger.debug(f"Error upserting comment_user: {e}")
                        conn.commit()

                    # End of inner for loop — log round results
                    logger.info(f"Round {scroll_round}: {new_in_round} new posts processed (total: {post_counter}).")

                    if reached_date_limit:
                        logger.info(f"Reached date limit ({max_days}d). Stopping scroll.")
                        break

                    if new_in_round == 0:
                        if scroll_round - last_new_round >= 3:
                            logger.info("No new posts after 3 consecutive scroll rounds. Stopping.")
                            break
                        logger.info(f"No new posts in round {scroll_round}. Retrying scroll...")
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

                    # Wait for FB to load new post threads from server
                    page.wait_for_timeout(3000)

                logger.info(f"Scroll-and-process complete. Processed {post_counter} posts. Stats: {stats}")
                record_fetch(page_id, stats["new_posts"] + stats["skipped_posts"], stats["new_comments"], conn)

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
    conn = get_db_connection()
    cursor = conn.cursor()

    if post_id:
        # Look up by post_id directly or by post_url containing the post_id
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
    range_map = {"1d": "-1 day", "7d": "-7 days", "30d": "-30 days", "90d": "-90 days"}
    sql_range = range_map.get(time_range, "-7 days")

    conn = get_db_connection()
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

    args = parser.parse_args()
    page_id = parse_page_id(args.pageId)

    logger.debug("Starting fetch_comments.py execution...")
    logger.debug("# code:tool-fbcomments-001:main")

    if args.action == "fetch_comments":
        show_browser_flag = not args.headless
        result = fetch_comments(args.pageId, args.credential, args.time_range,
                                show_browser=show_browser_flag, force_refresh=args.refresh,
                                max_posts=args.maxPosts)
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
