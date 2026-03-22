import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from fb_pipeline.contracts.inbox import (
    EnrichedThreadRecord,
    InboxMessage,
    MasHandoff,
    SeekerInfo,
    ThreadRecord,
    detect_city,
    extract_user_info,
    parse_ad_ids,
)


def build_thread_record(page_id: str, visible_thread: dict) -> ThreadRecord:
    name = (visible_thread.get("name") or "").strip()
    thread_text_full = visible_thread.get("text", "")
    thread_lines = [l.strip() for l in thread_text_full.split('\n') if l.strip()]
    preview_text = " ".join(thread_lines[1:]) if len(thread_lines) > 1 else ""
    return ThreadRecord(
        page_id=page_id,
        thread_id=f"{page_id}_{abs(hash(name))}",
        thread_name=name,
        preview_text=preview_text,
        thread_lines=thread_lines,
        dom_index=visible_thread.get("domIndex", 0),
    )


def enrich_thread_record(thread_record: ThreadRecord, js_messages: list, extract_user_info,
                         detect_city, ad_context: str = "", fb_url: str = "",
                         ad_ids: list | None = None) -> EnrichedThreadRecord:
    db_msgs = [{"sender": m.get("sender"), "content": m.get("text", "")} for m in js_messages]
    user_info = extract_user_info(db_msgs, thread_record.thread_name, ad_context)
    city = detect_city(ad_context, db_msgs)
    normalized_messages = []
    for idx, msg in enumerate(js_messages):
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        normalized_messages.append(
            InboxMessage(
                sender=msg.get("sender", "Unknown"),
                content=text,
                message_timestamp=msg.get("timestamp", ""),
                seq=idx,
            )
        )

    seeker = SeekerInfo(
        name=thread_record.thread_name,
        phone=user_info["phone"],
        email=user_info["email"],
        city=city,
        lead_stage="Intake",
    )
    mas_handoff = MasHandoff(
        thread_id=thread_record.thread_id,
        thread_name=thread_record.thread_name,
        page_id=thread_record.page_id,
        fb_url=fb_url,
        seeker=seeker,
        ad_context=ad_context,
        ad_ids=list(ad_ids or []),
        messages=normalized_messages,
    )
    return EnrichedThreadRecord(
        page_id=thread_record.page_id,
        thread_id=thread_record.thread_id,
        thread_name=thread_record.thread_name,
        preview_text=thread_record.preview_text,
        thread_lines=thread_record.thread_lines,
        dom_index=thread_record.dom_index,
        fb_url=fb_url,
        ad_context=ad_context,
        ad_ids=list(ad_ids or []),
        user_info=user_info,
        city=city,
        messages=normalized_messages,
        mas_handoff=mas_handoff,
    )


def persist_thread_record(conn, thread_record: EnrichedThreadRecord, detect_city) -> dict:
    cursor = conn.cursor()
    messages_added = 0
    ad_context = thread_record.ad_context

    for idx, msg in enumerate(thread_record.messages):
        msg_content_to_save = msg.content
        if messages_added == 0 and ad_context:
            msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{msg_content_to_save}"
        cursor.execute(
            "INSERT OR IGNORE INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
            (
                thread_record.thread_id,
                msg.sender,
                msg_content_to_save,
                msg.message_timestamp,
                msg.seq if msg.seq is not None else idx,
            )
        )
        if cursor.rowcount > 0:
            messages_added += 1

    cursor.execute('''
        INSERT INTO threads (id, page_id, thread_name, last_synced_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_synced_time=excluded.last_synced_time
    ''', (
        thread_record.thread_id,
        thread_record.page_id,
        thread_record.thread_name,
        thread_record.preview_text,
    ))

    for aid in thread_record.ad_ids:
        cursor.execute('''
            INSERT OR IGNORE INTO user_ad_ids (thread_id, ad_id)
            VALUES (?, ?)
        ''', (thread_record.thread_id, aid))
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

    user_info = thread_record.user_info
    cursor.execute('''
        INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, last_interaction)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(thread_id) DO UPDATE SET
            phone = COALESCE(excluded.phone, users.phone),
            email = COALESCE(excluded.email, users.email),
            fb_url = COALESCE(excluded.fb_url, users.fb_url),
            city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE users.city END,
            last_interaction = datetime('now')
    ''', (
        thread_record.thread_id,
        thread_record.thread_name,
        user_info.get("phone"),
        user_info.get("email"),
        thread_record.fb_url,
        thread_record.city,
    ))

    conn.commit()
    return {
        "thread_id": thread_record.thread_id,
        "messages_added": messages_added,
        "ad_ids_count": len(thread_record.ad_ids),
        "city": thread_record.city,
        "mas_handoff": _mas_handoff_to_dict(thread_record.mas_handoff),
    }


def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels, extract_user_info, detect_city) -> dict:
    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

    logger.info(f"Navigating to {inbox_url}")
    page.goto(inbox_url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

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

    _thread_items_found = False
    for wait_attempt in range(1, 6):
        try:
            page.wait_for_selector('div._5_n1', timeout=5000)
            _thread_items_found = True
            logger.info(f"Thread items (_5_n1) appeared after wait attempt {wait_attempt}.")
            break
        except Exception:
            logger.info(f"Wait attempt {wait_attempt}/5: _5_n1 not visible yet, retrying in 3s...")
            page.wait_for_timeout(3000)

    if not _thread_items_found:
        logger.warning("Thread items (_5_n1) never appeared after 5 attempts. Will proceed but may find 0 threads.")

    page.wait_for_timeout(1000)
    logger.info(f"Starting sidebar scroll-and-process within {time_range}...")

    range_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
    max_days = range_map.get(time_range, None)
    if max_days is None:
        clean = str(time_range).rstrip('d')
        try:
            max_days = int(clean)
        except ValueError:
            logger.warning(f"Unrecognized time_range '{time_range}', defaulting to 7 days.")
            max_days = 7
    logger.info(f"Time range resolved to {max_days} day(s).")

    cursor = conn.cursor()
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

        visible_threads = page.evaluate('''() => {
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

        if not visible_threads:
            logger.info(f"Round {scroll_round}: no _5_n1 items visible.")
            break

        new_in_round = 0
        for vt in visible_threads:
            name = vt.get("name", "").strip()
            if not name or name in processed_names:
                continue
            if thread_counter >= max_threads:
                break

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

            thread_record = build_thread_record(page_id, vt)
            cursor.execute("SELECT last_synced_time FROM threads WHERE id = ?", (thread_record.thread_id,))
            row = cursor.fetchone()

            if row and row[0] == thread_record.preview_text:
                logger.info(f"Skipping thread '{name}'. No new messages (preview matches).")
                stats["skipped_threads"] += 1
                continue

            logger.info(f"Syncing thread '{name}' (#{thread_counter})...")
            prev_fb_url = ""
            try:
                prev_qs = parse_qs(urlparse(page.url).query)
                prev_fb_url = prev_qs.get('selected_item_id', [''])[0]
            except Exception:
                pass

            pre_click_fingerprint = page.evaluate('''() => {
                let r = document.querySelector(
                    'div[aria-label*="Message list container"], ' +
                    'div[role="region"][aria-label*="message"]'
                );
                if (!r) return "";
                return (r.innerText || "").substring(0, 200);
            }''')

            try:
                thread_el = page.locator('div._5_n1').nth(thread_record.dom_index)
                thread_el.click(force=True, timeout=5000)
            except Exception as e:
                logger.warning(f"Could not click thread '{name}': {e}. Skipping.")
                continue

            msg_region_selector = 'div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]'
            try:
                page.wait_for_selector(msg_region_selector, timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                logger.warning(f"Message region not found within 10s for thread '{name}'. Falling back to timeout.")
                page.wait_for_timeout(4000)

            fb_url = ""
            url_changed = False
            for _poll in range(20):
                try:
                    current_qs = parse_qs(urlparse(page.url).query)
                    candidate = current_qs.get('selected_item_id', [''])[0]
                    if candidate and candidate != prev_fb_url:
                        fb_url = candidate
                        url_changed = True
                        break
                except Exception:
                    pass
                page.wait_for_timeout(500)

            if not url_changed:
                try:
                    current_qs = parse_qs(urlparse(page.url).query)
                    fb_url = current_qs.get('selected_item_id', [''])[0]
                except Exception:
                    fb_url = ""
                if fb_url == prev_fb_url:
                    logger.warning(f"URL selected_item_id did NOT change after clicking '{name}' (still {fb_url}). Setting fb_url to empty to avoid contamination.")
                    fb_url = ""

            panel_refreshed = False
            for _poll in range(20):
                post_click_fingerprint = page.evaluate('''() => {
                    let r = document.querySelector(
                        'div[aria-label*="Message list container"], ' +
                        'div[role="region"][aria-label*="message"]'
                    );
                    if (!r) return "";
                    return (r.innerText || "").substring(0, 200);
                }''')
                if post_click_fingerprint != pre_click_fingerprint:
                    panel_refreshed = True
                    break
                page.wait_for_timeout(500)

            if not panel_refreshed:
                logger.warning(f"Message panel text did NOT change after clicking '{name}'. Skipping thread to avoid contamination.")
                continue
            else:
                page.wait_for_timeout(1000)

            logger.info(f"Thread '{name}': URL fb_url={fb_url}, panel_refreshed={panel_refreshed}")

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

            logger.info(f"Scrolling up in message panel to load all messages for '{name}'...")
            try:
                page.mouse.move(900, 400)
            except Exception:
                pass

            prev_msg_count = 0
            prev_scroll_height = 0
            stable_rounds = 0
            max_scroll_up_rounds = 50
            for scroll_up_round in range(1, max_scroll_up_rounds + 1):
                scroll_info = page.evaluate('''() => {
                    let region = document.querySelector(
                        'div[aria-label*="Message list container"], ' +
                        'div[role="region"][aria-label*="message"]'
                    );
                    if (!region) return {count: 0, scrollHeight: 0, scrollTop: 0};
                    let bubble = region.querySelector('.x1fqp7bg');
                    let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
                    let count = 0;
                    for (let div of messageArea.children) {
                        count++;
                    }
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

                try:
                    page.evaluate('''() => {
                        let region = document.querySelector(
                            'div[aria-label*="Message list container"], ' +
                            'div[role="region"][aria-label*="message"]'
                        );
                        if (!region) return;
                        let scrollable = region;
                        let el = region;
                        while (el) {
                            if (el.scrollHeight > el.clientHeight && el.clientHeight > 100) {
                                scrollable = el;
                                break;
                            }
                            el = el.parentElement;
                        }
                        let newTop = Math.max(0, scrollable.scrollTop - 800);
                        scrollable.scrollTop = newTop;
                        scrollable.dispatchEvent(new Event('scroll', {bubbles: true}));
                    }''')
                    page.mouse.wheel(0, -2000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)

            logger.info(f"Scroll-up complete for '{name}'. Final element count: {prev_msg_count}.")
            js_messages = page.evaluate('''() => {
                let region = document.querySelector(
                    'div[aria-label*="Message list container"], ' +
                    'div[role="region"][aria-label*="message"]'
                );
                if (!region) return [];
                let results = [];
                let currentTimestamp = "";
                let bubble = region.querySelector('.x1fqp7bg');
                let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
                let topDivs = messageArea.children;
                for (let div of topDivs) {
                    if (div.classList.contains('x14vqqas') || div.querySelector('.x14vqqas')) {
                        let tsEl = div.classList.contains('x14vqqas') ? div : div.querySelector('.x14vqqas');
                        if (tsEl) {
                            let ts = tsEl.innerText.trim();
                            if (ts && ts.length < 50) currentTimestamp = ts;
                        }
                        continue;
                    }
                    if (div.classList.contains('xcxhlts') || div.querySelector('.xcxhlts')) {
                        continue;
                    }
                    if (!div.classList.contains('x1fqp7bg') && !div.querySelector('.x1fqp7bg')) continue;
                    let sender = "Unknown";
                    let outerWrapper = div.querySelector('.xuk3077') || div;
                    let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                    if (htmlStr.includes('x13a6bvl')) {
                        sender = "Page";
                    } else if (htmlStr.includes('x1nhvcw1')) {
                        sender = "Customer";
                    } else {
                        let avatar = div.querySelector('img.img[alt]');
                        sender = avatar ? "Customer" : "Page";
                    }
                    let textContainers = div.querySelectorAll('.x1y1aw1k');
                    if (textContainers.length > 0) {
                        for (let tc of textContainers) {
                            let text = tc.innerText.trim();
                            if (text && text.length > 0) {
                                results.push({sender, text, timestamp: currentTimestamp});
                            }
                        }
                    } else {
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
                        if (!found) {
                            let text = div.innerText.trim();
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
            logger.info(f"Found {bubble_count} message bubbles for thread '{name}'.")

            ad_ids = []
            try:
                ad_ids = extract_ad_id_labels(page)
                if ad_ids:
                    logger.info(f"Found ad_ids {ad_ids} for thread '{name}'.")
            except Exception as e:
                logger.warning(f"Could not extract ad_id labels for thread '{name}': {e}")

            enriched_record = enrich_thread_record(
                thread_record,
                js_messages,
                extract_user_info=extract_user_info,
                detect_city=detect_city,
                ad_context=ad_context,
                fb_url=fb_url,
                ad_ids=ad_ids,
            )
            persist_result = persist_thread_record(conn, enriched_record, detect_city=detect_city)
            stats["new_messages"] += persist_result["messages_added"]

            if row is None:
                stats["new_threads"] += 1

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

    logger.info(f"Scroll-and-process complete. Processed {thread_counter} threads. Stats: {stats}")
    record_fetch(page_id, stats["new_threads"] + stats["skipped_threads"], stats["new_messages"], conn)
    return stats


def _mas_handoff_to_dict(mas_handoff: MasHandoff | None) -> dict:
    if mas_handoff is None:
        return {}
    return {
        "thread_id": mas_handoff.thread_id,
        "thread_name": mas_handoff.thread_name,
        "page_id": mas_handoff.page_id,
        "fb_url": mas_handoff.fb_url,
        "seeker": {
            "name": mas_handoff.seeker.name,
            "phone": mas_handoff.seeker.phone,
            "email": mas_handoff.seeker.email,
            "city": mas_handoff.seeker.city,
            "lead_stage": mas_handoff.seeker.lead_stage,
        },
        "ad_context": mas_handoff.ad_context,
        "ad_ids": list(mas_handoff.ad_ids),
        "messages": [
            {
                "sender": message.sender,
                "content": message.content,
                "message_timestamp": message.message_timestamp,
                "seq": message.seq,
            }
            for message in mas_handoff.messages
        ],
    }
