import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from fb_pipeline.comments.l3_pipeline import build_post_record, enrich_post_record, persist_post_record


# Back-compat alias for callers/tests that still import scrape_comments_ui.
def scrape_comments(page, page_id: str, time_range: str, max_posts: int, conn, logger,
                    record_fetch, extract_user_info, detect_city) -> dict:
    """Core comment scraping loop over the Facebook comments inbox."""
    cursor = conn.cursor()

    logger.info("Post inbox loaded. Scanning for post threads...")
    try:
        page.wait_for_selector(
            'div[data-pagelet="GenericBizInboxThreadListViewBody"], '
            'div[data-pagelet="BizP13NInboxUinifiedThreadListView"], '
            'div[data-pagelet="BizP13NInboxCommentListView"], '
            'div[aria-label="Inbox"]',
            timeout=10000
        )
    except Exception:
        logger.info("Thread list pagelet not found within 10s, proceeding with fallback...")

    page.wait_for_timeout(2000)

    range_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
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

        visible_posts = page.evaluate('''() => {
            let items = document.querySelectorAll('._5_n1');
            return Array.from(items).map((el, idx) => {
                let text = el.innerText || '';
                let lines = text.split('\n').map(l => l.trim()).filter(l => l);
                return {
                    domIndex: idx,
                    name: lines[0] || '',
                    text: text,
                    lines: lines
                };
            });
        }''')

        if not visible_posts:
            logger.info(f"Round {scroll_round}: no _5_n1 items visible.")
            break

        new_in_round = 0
        for vp in visible_posts:
            name = vp.get("name", "").strip()
            if not name or name in processed_names:
                continue

            if post_counter >= max_posts:
                break

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
                    if len(line_lower) > 30:
                        continue
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

            post_record = build_post_record(page_id, vp)
            post_id = post_record.post_id
            preview_text = post_record.preview_text

            cursor.execute("SELECT last_synced_time FROM posts WHERE id = ?", (post_id,))
            row = cursor.fetchone()

            if row and row[0] == preview_text:
                logger.info(f"Skipping post '{name}'. No new comments (preview matches).")
                stats["skipped_posts"] += 1
                continue

            logger.info(f"Syncing post '{name}' (#{post_counter})...")

            prev_post_url = ""
            try:
                prev_qs = parse_qs(urlparse(page.url).query)
                prev_post_url = prev_qs.get('selected_item_id', [''])[0]
            except Exception:
                pass

            dom_index = vp.get("domIndex", 0)
            try:
                post_el = page.locator('div._5_n1').nth(dom_index)
                post_el.click(force=True, timeout=5000)
            except Exception as e:
                logger.warning(f"Could not click post '{name}': {e}. Skipping.")
                continue

            comment_region_selector = 'div[aria-label*="Comment"], div[role="complementary"], div[data-pagelet*="Comment"]'
            try:
                page.wait_for_selector(comment_region_selector, timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                logger.warning(f"Comment region not found within 10s for post '{name}'. Falling back to timeout.")
                page.wait_for_timeout(4000)

            post_url = ""
            url_changed = False
            for _poll in range(20):
                try:
                    current_qs = parse_qs(urlparse(page.url).query)
                    candidate = current_qs.get('selected_item_id', [''])[0]
                    if candidate and candidate != prev_post_url:
                        post_url = candidate
                        url_changed = True
                        break
                except Exception:
                    pass
                page.wait_for_timeout(500)

            if not url_changed:
                try:
                    current_qs = parse_qs(urlparse(page.url).query)
                    post_url = current_qs.get('selected_item_id', [''])[0]
                except Exception:
                    post_url = ""
                if post_url == prev_post_url:
                    logger.warning(f"URL selected_item_id did NOT change after clicking post '{name}' (still {post_url}). Setting post_url to empty.")
                    post_url = ""

            js_comments = page.evaluate('''() => {
                let results = [];
                let seen = new Set();

                function extractUserId(url) {
                    if (!url) return '';
                    let idMatch = url.match(/profile\\.php\\?id=(\\d+)/);
                    if (idMatch) return idMatch[1];
                    let pathMatch = url.match(/facebook\\.com\\/([\\w.]+)/);
                    if (pathMatch && !['pages','groups','events','hashtag'].includes(pathMatch[1])) return pathMatch[1];
                    return '';
                }

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

                if (results.length === 0) {
                    let region = document.querySelector(
                        'div[aria-label*="Message list container"], ' +
                        'div[role="region"][aria-label*="message"]'
                    );
                    if (region) {
                        let bubble = region.querySelector('.x1fqp7bg');
                        let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
                        let topDivs = messageArea.children;
                        let currentTimestamp = '';

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
                persist_result = persist_post_record(
                    conn,
                    enrich_post_record(post_record, [], extract_user_info, detect_city, post_url=post_url)
                )
                if persist_result["is_new_post"]:
                    stats["new_posts"] += 1
                continue

            logger.info(f"Found {comment_count} comments for post '{name}'.")
            enriched_post_record = enrich_post_record(
                post_record,
                js_comments,
                extract_user_info,
                detect_city,
                post_url=post_url,
            )
            persist_result = persist_post_record(conn, enriched_post_record, logger=logger)
            stats["new_comments"] += persist_result["comments_added"]
            if persist_result["is_new_post"]:
                stats["new_posts"] += 1

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

        try:
            page.mouse.move(200, 500)
            for _ in range(5):
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(300)
            logger.info(f"Scrolled sidebar via mouse.wheel (round {scroll_round}).")
        except Exception as e:
            logger.warning(f"Mouse wheel scroll failed: {e}. Stopping.")
            break

        page.wait_for_timeout(3000)

    logger.info(f"Scroll-and-process complete. Processed {post_counter} posts. Stats: {stats}")
    record_fetch(page_id, stats["new_posts"] + stats["skipped_posts"], stats["new_comments"], conn)
    return stats


scrape_comments_ui = scrape_comments

__all__ = ["scrape_comments", "scrape_comments_ui"]
