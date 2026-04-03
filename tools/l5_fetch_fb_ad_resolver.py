import logging
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection as shared_get_db_connection
from fb_pipeline.contracts.l1_inbox import detect_city_smart as shared_detect_city_smart

logger = logging.getLogger("fetch_fb_ad_resolver")

def get_db_connection(memory_dir: str = None):
    return shared_get_db_connection(memory_dir, logger=logger)

def detect_city(ad_context: str, page_messages: list) -> str:
    """Detect city using LLM-first with rule-based fallback."""
    return shared_detect_city_smart(ad_context, page_messages)

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
    """Batch-update users.city from resolved ad_posts data."""
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
