import re
from urllib.parse import parse_qs, urlparse

from .inbox.scroll_helpers import (
    wait_for_inbox_shell,
    wait_for_initial_threads,
    scroll_sidebar_and_wait,
    sidebar_loading_snapshot,
    sidebar_loading_count,
    wait_for_sidebar_threads,
)
from .inbox.thread_list_parser import (
    extract_visible_threads,
    parse_sidebar_time_token,
    is_thread_older_than_range,
    validate_quick_fetch_cache,
)
from .inbox.thread_detail_parser import (
    verify_thread_switch,
    extract_ad_context,
    scroll_up_message_panel,
    extract_thread_messages,
    extract_ad_id_labels,
)
from .inbox.integrity_validator import validate_thread_integrity
from .inbox.constants import thread_card_selector

# Provide backward-compatible aliases for legacy tools calling l3_inbox directly
_wait_for_inbox_shell = wait_for_inbox_shell
_wait_for_initial_threads = wait_for_initial_threads
_sidebar_loading_snapshot = sidebar_loading_snapshot
_sidebar_loading_count = sidebar_loading_count
_wait_for_sidebar_threads = wait_for_sidebar_threads
_scroll_sidebar_and_wait = scroll_sidebar_and_wait
_scroll_sidebar_once = scroll_sidebar_and_wait
_extract_visible_threads = extract_visible_threads
_parse_sidebar_time_token = parse_sidebar_time_token
_validate_quick_fetch_cache = validate_quick_fetch_cache


def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels_arg, extract_user_info, detect_city,
                 skip_navigation: bool = False, force_refresh: bool = False,
                 allow_early_exit: bool = True) -> dict:
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record

    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

    if not skip_navigation:
        logger.info(f"Navigating to {inbox_url}")
        page.goto(inbox_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
    else:
        logger.info(f"Page is already correctly positioned at {inbox_url}. Skipping navigation.")

    logger.info("Waiting for inbox shell...")
    wait_for_inbox_shell(page, logger, timeout_ms=30000)

    logger.info("Waiting for initial threads to appear...")
    initial_snapshot = wait_for_initial_threads(page, logger, timeout_ms=30000)

    if not force_refresh and allow_early_exit:
        first_glance_threads = extract_visible_threads(page)
        is_cache_hit = validate_quick_fetch_cache(first_glance_threads, conn, logger, page_id)
        if is_cache_hit:
            return {
                "new_threads": 0, "new_messages": 0, "skipped_threads": len(first_glance_threads),
                "threads_seen": len(first_glance_threads), "threads_processed": 0,
                "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0, "threads_skipped_click_verify": 0,
                "sidebar_scrolls": 0, "sidebar_wait_ms": initial_snapshot.get("elapsed_ms", 0),
                "method": "dynamic_cache_hit"
            }

    logger.info(f"Starting sidebar scroll-and-process within {time_range}...")

    range_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
    max_days = range_map.get(time_range, None)
    if max_days is None:
        try:
            max_days = int(str(time_range).rstrip('d'))
        except ValueError:
            logger.warning(f"Unrecognized time_range '{time_range}', defaulting to 7 days.")
            max_days = 7

    cursor = conn.cursor()
    processed_thread_keys = set()
    scroll_round = 0
    reached_date_limit = False
    last_new_round = 0
    thread_counter = 0
    consecutive_clean_threads = 0
    consecutive_old_threads = 0
    stats = {
        "new_threads": 0, "new_messages": 0, "skipped_threads": 0, "threads_seen": 0,
        "threads_processed": 0, "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0,
        "threads_skipped_click_verify": 0,
        "sidebar_scrolls": 0,
        "sidebar_wait_ms": initial_snapshot.get("elapsed_ms", 0),
    }

    collected_threads = []

    while not reached_date_limit:
        scroll_round += 1
        if thread_counter >= max_threads:
            logger.info(f"Reached max threads ({max_threads}). Stopping Stage 1.")
            break

        visible_threads = extract_visible_threads(page)
        stats["threads_seen"] += len(visible_threads)
        if not visible_threads:
            break

        new_in_round = 0
        for vt in visible_threads:
            name = (vt.get("name") or "").strip()
            if not name:
                continue
            if thread_counter >= max_threads:
                break

            parsed_time = parse_sidebar_time_token(vt.get("sidebarTimeText", ""))
            vt["sidebarTimeKind"] = parsed_time.get("kind", "unknown")
            thread_key = vt.get("selectedItemId") or vt.get("sidebarIdentityKey") or "|".join([
                name, vt.get("previewText", ""), vt.get("sidebarTimeText", ""), str(vt.get("domIndex", 0)),
            ])

            if thread_key in processed_thread_keys:
                stats["threads_skipped_duplicate"] += 1
                continue

            if is_thread_older_than_range(parsed_time, max_days):
                logger.info(f"Thread '{name}' is older than cutoff ({max_days} days). Parsed time: {parsed_time}")
                consecutive_old_threads += 1
                stats["threads_skipped_cutoff"] += 1
                if consecutive_old_threads >= 4:
                    reached_date_limit = True
                    break
                continue
            else:
                consecutive_old_threads = 0

            processed_thread_keys.add(thread_key)
            new_in_round += 1
            thread_counter += 1

            thread_record = build_thread_record(page_id, vt)
            cursor.execute("SELECT last_synced_time FROM threads WHERE id = ?", (thread_record.thread_id,))
            row = cursor.fetchone()

            is_match = False
            force_resync = False

            if row:
                db_norm = ''.join(c.lower() for c in (row[0] or "") if c.isalnum())
                ui_norm = ''.join(c.lower() for c in (thread_record.preview_text or "") if c.isalnum())
                min_len = min(len(db_norm), len(ui_norm))
                
                if min_len > 0:
                    is_match = db_norm[:min_len] == ui_norm[:min_len]
                else:
                    is_match = db_norm == ui_norm

                if is_match:
                    preview_lower = (thread_record.preview_text or "").strip().lower()
                    if preview_lower.startswith("you:") or preview_lower.startswith("bạn:"):
                        last_msg_row = cursor.execute(
                            "SELECT sender FROM messages WHERE thread_id = ? ORDER BY seq DESC LIMIT 1",
                            (thread_record.thread_id,)
                        ).fetchone()
                        if last_msg_row and last_msg_row[0] != "Page":
                            force_resync = True

                    if not force_resync:
                        stats["skipped_threads"] += 1
                        if not force_refresh:
                            collected_threads.append({
                                "record": thread_record,
                                "is_new": False,
                                "skip_process": True,
                                "vt": vt,
                                "name": name
                            })
                            if allow_early_exit:
                                consecutive_clean_threads += 1
                                if consecutive_clean_threads >= 2:
                                    reached_date_limit = True
                                    break
                            continue
                else:
                    consecutive_clean_threads = 0

            collected_threads.append({
                "record": thread_record,
                "is_new": (row is None),
                "skip_process": False,
                "vt": vt,
                "name": name
            })

            if row is None:
                stats["new_threads"] += 1

        if reached_date_limit:
            break
        if new_in_round == 0:
            if scroll_round - last_new_round >= 7:
                break
        else:
            last_new_round = scroll_round

        if thread_counter >= max_threads:
            break

        last_vt = visible_threads[-1] if visible_threads else {}
        current_date_reach = last_vt.get("sidebarTimeText", "Unknown")
        logger.info(f"Completed processing {thread_counter} threads (reached [{current_date_reach}]) before sidebar scroll round {scroll_round}.")
        scroll_result = scroll_sidebar_and_wait(page, logger, scroll_round=scroll_round, timeout_ms=60000)
        stats["sidebar_scrolls"] += 1
        stats["sidebar_wait_ms"] += scroll_result.get("elapsed_ms", 0)

    # END STAGE 1
    logger.info(f"Stage 1 Complete. Listed {len(collected_threads)} threads in range:")
    for ct in collected_threads:
        time_text = ct.get('vt', {}).get('sidebarTimeText', '')
        if time_text:
            logger.info(f"  - {ct.get('name', 'Unknown')} [{time_text}]")
        else:
            logger.info(f"  - {ct.get('name', 'Unknown')}")
    threads_to_process = [c for c in collected_threads if not c["skip_process"]]
    logger.info(f"Stage 2 will extract details for {len(threads_to_process)} threads.")

    # STAGE 2
    if len(threads_to_process) > 0:
        logger.info("Resetting sidebar scroll to top for Stage 2...")
        try:
            page.evaluate('''() => {
                let container = document.querySelector('div[aria-label*="Danh sách tin nhắn"]') || 
                                document.querySelector('div[role="region"][aria-label*="message"]');
                if(container) container.scrollTop = 0;
            }''')
            page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"Failed to reset sidebar scroll: {e}")

        for i, c in enumerate(threads_to_process):
            thread_record = c["record"]
            name = c["name"]
            logger.info(f"Syncing thread '{name}' (#{i+1}/{len(threads_to_process)})...")
            
            prev_fb_url = ""
            try:
                from urllib.parse import parse_qs, urlparse
                prev_fb_url = parse_qs(urlparse(page.url).query).get('selected_item_id', [''])[0]
            except Exception:
                pass

            pre_click_fingerprint = page.evaluate('''() => {
                let r = document.querySelector(
                    'div[aria-label*="Message list container"], ' +
                    'div[role="region"][aria-label*="message"]'
                );
                return (!r) ? "" : (r.innerText || "").substring(0, 200);
            }''')

            # Use thread_card_selector imported at the top of the file
            clicked = False
            click_attempts = 0
            while not clicked and click_attempts < 15:
                click_attempts += 1
                try:
                    clicked = page.evaluate(r'''({sidebarIdentityKey, threadSelector, targetName, targetSelectedItemId, targetPreviewText}) => {
                        let candidates = Array.from(document.querySelectorAll(threadSelector));
                        function pickTimeToken(lines) {
                            for (let i = lines.length - 1; i >= 1; i--) {
                                const token = (lines[i] || '').trim();
                                if (!token) continue;
                                if (/^\d+[smhdw]$/i.test(token)) return token;
                                if (/^(today|yesterday|hôm nay|hôm qua)$/i.test(token)) return token;
                                if (/^(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)$/i.test(token)) return token;
                                if (/^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}$/i.test(token)) return token;
                            }
                            return '';
                        }
                        function getIdentity(el) {
                            const text = (el.innerText || '').trim();
                            const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                            const name = lines[0] || '';
                            const sidebarTimeText = pickTimeToken(lines);
                            const previewLines = lines.slice(1).filter(line => line !== sidebarTimeText);
                            const hrefEl = el.closest('a[href]') || el.querySelector('a[href]');
                            const href = hrefEl ? (hrefEl.getAttribute('href') || '') : '';
                            let selectedItemId = '';
                            try {
                                if (href) {
                                    const absolute = new URL(href, window.location.origin);
                                    selectedItemId = absolute.searchParams.get('selected_item_id') || '';
                                }
                            } catch (_) {}
                            const attrs = [];
                            for (const attr of Array.from(el.attributes || [])) {
                                if (!attr || !attr.name) continue;
                                if (attr.name.startsWith('data-') || attr.name.startsWith('aria-') || attr.name === 'href') {
                                    attrs.push(`${attr.name}=${attr.value || ''}`);
                                }
                            }
                            const identityParts = [name, previewLines.join(' | '), sidebarTimeText, selectedItemId, href, attrs.join('|')].filter(Boolean);
                            return identityParts.join(' || ');
                        }
                        for (let c of candidates) {
                            if (sidebarIdentityKey && getIdentity(c) === sidebarIdentityKey) {
                                c.scrollIntoView({block: "center"});
                                c.click();
                                return true;
                            }
                            
                            // Relaxed Match
                            const text = (c.innerText || '').trim();
                            const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                            const elName = lines[0] || '';
                            
                            const hrefEl = c.closest('a[href]') || c.querySelector('a[href]');
                            let elSelectedItemId = '';
                            if (hrefEl) {
                                try {
                                    const absolute = new URL(hrefEl.getAttribute('href'), window.location.origin);
                                    elSelectedItemId = absolute.searchParams.get('selected_item_id') || '';
                                } catch (_) {}
                            }
                            
                            if (targetSelectedItemId && elSelectedItemId && targetSelectedItemId === elSelectedItemId) {
                                c.scrollIntoView({block: "center"});
                                c.click();
                                return true;
                            }
                            
                            let preLines = lines.slice(1).join(' ');
                            if (elName && elName === targetName && targetPreviewText && preLines.includes(targetPreviewText)) {
                                c.scrollIntoView({block: "center"});
                                c.click();
                                return true;
                            }
                        }
                        return false;
                    }''', {
                        "sidebarIdentityKey": thread_record.sidebar_identity_key, 
                        "threadSelector": thread_card_selector(),
                        "targetName": thread_record.thread_name,
                        "targetSelectedItemId": thread_record.selected_item_id,
                        "targetPreviewText": thread_record.preview_text
                    })
                except Exception:
                    pass
                    
                if not clicked:
                    try:
                        page.mouse.wheel(0, 300)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

            if not clicked:
                logger.warning(f"Failed to verify click for thread '{name}' in Stage 2 after {click_attempts} scroll attempts.")
                stats["threads_skipped_click_verify"] += 1
                continue

            try:
                page.wait_for_selector('div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]', timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                page.wait_for_timeout(4000)

            is_first_thread = (i == 0)
            fb_url, verified = verify_thread_switch(
                page, logger, name, prev_fb_url, pre_click_fingerprint, is_first_thread, thread_record
            )
            if not verified:
                stats["threads_skipped_click_verify"] += 1
                continue

            page.wait_for_timeout(1000)
            
            ad_context = extract_ad_context(page)
            scroll_up_message_panel(page, logger, name)

            messages_list = extract_thread_messages(page)
            is_valid = validate_thread_integrity(messages_list, logger)

            if len(messages_list) == 0:
                logger.warning(f"No message bubbles found for thread '{name}'.")
                continue

            ad_ids = extract_ad_id_labels_arg(page) if callable(extract_ad_id_labels_arg) else extract_ad_id_labels(page)

            enriched_record = enrich_thread_record(
                thread_record,
                messages_list,
                extract_user_info=extract_user_info,
                detect_city=detect_city,
                ad_context=ad_context,
                fb_url=fb_url,
                ad_ids=ad_ids,
            )
            persist_result = persist_thread_record(conn, enriched_record, detect_city=detect_city)
            stats["new_messages"] += persist_result.get("messages_added", 0) if isinstance(persist_result, dict) else 0
            stats["threads_processed"] += 1

    record_fetch(page_id, stats["new_threads"] + stats["skipped_threads"], stats["new_messages"], conn)
    return stats

scrape_inbox_ui = scrape_inbox

__all__ = [
    "extract_ad_id_labels",
    "scrape_inbox",
    "scrape_inbox_ui",
    "_extract_visible_threads",
    "_wait_for_sidebar_threads",
    "_wait_for_initial_threads",
    "_scroll_sidebar_and_wait",
    "_parse_sidebar_time_token",
    "_validate_quick_fetch_cache",
]
