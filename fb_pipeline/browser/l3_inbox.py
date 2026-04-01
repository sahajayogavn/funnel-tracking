import hashlib
import re
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse


THREAD_LIST_CONTAINER_SELECTORS = [
    'div[data-pagelet="GenericBizInboxThreadListViewBody"]',
    'div[data-pagelet="BizP13NInboxUinifiedThreadListView"]',
    'div[aria-label="Inbox"]',
]
THREAD_CARD_SELECTORS = [
    'div._5_n1',
    'div[role="listitem"]',
    'a[role="link"][href*="selected_item_id"]',
]
MESSAGE_REGION_SELECTOR = 'div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]'
LOADING_INDICATOR_SELECTORS = [
    '[aria-busy="true"]',
    'div[role="progressbar"]',
    'svg[aria-label*="Loading"]',
]
DAY_NAMES = {
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
TIME_TODAY = {"today", "hôm nay"}
TIME_YESTERDAY = {"yesterday", "hôm qua"}
MONTH_DAY_RE = re.compile(r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}$', re.I)


def _thread_card_selector() -> str:
    return ", ".join(THREAD_CARD_SELECTORS)


def _wait_for_inbox_shell(page, logger, timeout_ms: int = 30000) -> str:
    """Wait for the thread list container pagelet to appear in the DOM."""
    selector = ", ".join(THREAD_LIST_CONTAINER_SELECTORS)
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
        logger.info("Thread list container detected.")
        return selector
    except Exception:
        logger.info(f"Thread list pagelet not found within {timeout_ms}ms, proceeding with fallback...")
        return ""


def _wait_for_initial_threads(page, logger, timeout_ms: int = 30000, poll_ms: int = 1000) -> dict:
    """Poll until at least 1 thread card is visible in the DOM.

    This ensures Facebook's SPA hydration has completed rendering the thread
    list before any scrolling or processing begins.
    """
    start = time.time()
    logger.info(f"initial_threads_wait_start timeout_ms={timeout_ms}")
    while True:
        snapshot = _sidebar_loading_snapshot(page)
        elapsed_ms = int((time.time() - start) * 1000)
        if snapshot["count"] > 0:
            logger.info(
                f"initial_threads_ready count={snapshot['count']} "
                f"fingerprint={snapshot['fingerprint']} elapsed_ms={elapsed_ms}"
            )
            snapshot["elapsed_ms"] = elapsed_ms
            return snapshot
        if elapsed_ms >= timeout_ms:
            logger.warning(f"initial_threads_timeout count=0 elapsed_ms={elapsed_ms}")
            snapshot["elapsed_ms"] = elapsed_ms
            return snapshot
        page.wait_for_timeout(poll_ms)


def _extract_visible_threads(page) -> list[dict]:
    selector = _thread_card_selector()
    return page.evaluate(
        r'''(config) => {
            const threadSelector = config.threadSelector;
            const candidates = Array.from(document.querySelectorAll(threadSelector));

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

            return candidates.map((el, idx) => {
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
                const sidebarIdentityKey = identityParts.join(' || ');
                return {
                    domIndex: idx,
                    name,
                    text,
                    lines,
                    previewText: previewLines.join(' ').trim(),
                    sidebarTimeText,
                    sidebarIdentityKey,
                    selectedItemId,
                    href,
                };
            }).filter(item => item.name || item.text);
        }''',
        {"threadSelector": selector},
    )


def _sidebar_loading_snapshot(page) -> dict:
    selector = _thread_card_selector()
    loading_selector = ", ".join(LOADING_INDICATOR_SELECTORS)
    container_selector = ", ".join(THREAD_LIST_CONTAINER_SELECTORS)
    snapshot = page.evaluate(
        r'''(config) => {
            const cards = Array.from(document.querySelectorAll(config.threadSelector));
            const visibleTexts = cards.map(el => (el.innerText || '').trim()).filter(Boolean);
            const allLoadingNodes = config.loadingSelector
                ? Array.from(document.querySelectorAll(config.loadingSelector))
                : [];
            const container = config.containerSelector
                ? document.querySelector(config.containerSelector)
                : null;
            const containerLoading = container
                ? allLoadingNodes.filter(node => container.contains(node)).length
                : 0;
            const digestSource = visibleTexts.slice(0, 25).join('\n---\n');
            let fingerprint = '';
            if (digestSource) {
                fingerprint = digestSource;
            }
            return {
                count: visibleTexts.length,
                loadingCount: containerLoading,
                globalLoadingCount: allLoadingNodes.length,
                hasContainer: Boolean(container),
                fingerprint,
            };
        }''',
        {
            "threadSelector": selector,
            "loadingSelector": loading_selector,
            "containerSelector": container_selector,
        },
    )
    digest = hashlib.sha256((snapshot.get("fingerprint") or "").encode("utf-8")).hexdigest()[:12]
    snapshot["fingerprint"] = digest
    return snapshot


def _sidebar_loading_count(snapshot: dict) -> int:
    if snapshot.get("hasContainer"):
        return int(snapshot.get("loadingCount") or 0)
    return int(snapshot.get("globalLoadingCount") or snapshot.get("loadingCount") or 0)


def _wait_for_sidebar_threads(page, logger, timeout_ms: int = 60000, poll_ms: int = 1000) -> dict:
    start = time.time()
    stable_polls = 0
    saw_growth = False
    last_snapshot = None
    logger.info(f"sidebar_load_start timeout_ms={timeout_ms}")

    while True:
        snapshot = _sidebar_loading_snapshot(page)
        effective_loading = _sidebar_loading_count(snapshot)
        changed = (
            last_snapshot is None
            or snapshot["count"] != last_snapshot["count"]
            or snapshot["fingerprint"] != last_snapshot["fingerprint"]
        )
        if last_snapshot and snapshot["count"] > last_snapshot["count"]:
            saw_growth = True
        if changed:
            stable_polls = 0
        else:
            stable_polls += 1

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "sidebar_load_poll "
            f"count={snapshot['count']} fingerprint={snapshot['fingerprint']} "
            f"loading={effective_loading} container_loading={snapshot.get('loadingCount', 0)} "
            f"global_loading={snapshot.get('globalLoadingCount', 0)} stable_polls={stable_polls} elapsed_ms={elapsed_ms}"
        )

        if snapshot["count"] > 0 and effective_loading == 0 and stable_polls >= 2:
            logger.info(
                "sidebar_load_complete "
                f"count={snapshot['count']} elapsed_ms={elapsed_ms}"
            )
            snapshot["elapsed_ms"] = elapsed_ms
            return snapshot

        if snapshot["count"] > 0 and saw_growth and stable_polls >= 2:
            logger.info(
                "sidebar_load_complete "
                f"count={snapshot['count']} elapsed_ms={elapsed_ms} reason=stable_after_growth"
            )
            snapshot["elapsed_ms"] = elapsed_ms
            return snapshot

        if elapsed_ms >= timeout_ms:
            logger.warning(
                "sidebar_load_timeout "
                f"count={snapshot['count']} loading={effective_loading} elapsed_ms={elapsed_ms}"
            )
            snapshot["elapsed_ms"] = elapsed_ms
            return snapshot

        last_snapshot = snapshot
        page.wait_for_timeout(poll_ms)


def _scroll_sidebar_and_wait(page, logger, scroll_round: int,
                              timeout_ms: int = 60000, poll_ms: int = 1000) -> dict:
    """Move mouse to left sidebar, scroll once, wait for loading indicators,
    then wait up to timeout_ms for new threads to appear and stabilize.

    Returns a sidebar loading snapshot dict.
    """
    # 1. Snapshot before scroll
    pre_snapshot = _sidebar_loading_snapshot(page)
    pre_count = pre_snapshot["count"]
    pre_fingerprint = pre_snapshot["fingerprint"]

    # 2. Move mouse to left sidebar center (x=200 is within the ~360px sidebar)
    try:
        page.mouse.move(200, 400)
    except Exception:
        pass

    # 3. Single scroll down
    try:
        page.mouse.wheel(0, 600)
        logger.info(f"sidebar_scroll_performed round={scroll_round} pre_count={pre_count}")
    except Exception as e:
        logger.warning(f"sidebar_scroll_failed round={scroll_round}: {e}")
        pre_snapshot["elapsed_ms"] = 0
        return pre_snapshot

    # 4. Brief pause to let Facebook's loading indicator appear
    page.wait_for_timeout(500)

    # 5. Wait: loading-indicator phase + new-threads phase (up to timeout_ms total)
    start = time.time()
    stable_polls = 0
    saw_loading = False

    while True:
        snapshot = _sidebar_loading_snapshot(page)
        effective_loading = _sidebar_loading_count(snapshot)
        elapsed_ms = int((time.time() - start) * 1000)

        if effective_loading > 0:
            saw_loading = True
            stable_polls = 0
            logger.info(
                f"sidebar_scroll_wait round={scroll_round} loading={effective_loading} "
                f"count={snapshot['count']} elapsed_ms={elapsed_ms}"
            )
        else:
            changed = (
                snapshot["count"] != pre_count
                or snapshot["fingerprint"] != pre_fingerprint
            )
            if changed:
                stable_polls = 0
                pre_count = snapshot["count"]
                pre_fingerprint = snapshot["fingerprint"]
            else:
                stable_polls += 1

            if stable_polls >= 2:
                reason = "stable_after_loading" if saw_loading else "no_change"
                logger.info(
                    f"sidebar_scroll_complete round={scroll_round} "
                    f"count={snapshot['count']} reason={reason} elapsed_ms={elapsed_ms}"
                )
                snapshot["elapsed_ms"] = elapsed_ms
                return snapshot

        if elapsed_ms >= timeout_ms:
            logger.warning(
                f"sidebar_scroll_timeout round={scroll_round} "
                f"count={snapshot['count']} loading={effective_loading} elapsed_ms={elapsed_ms}"
            )
            snapshot["elapsed_ms"] = elapsed_ms
            return snapshot

        page.wait_for_timeout(poll_ms)


# Legacy shim — kept for backward compatibility with callers outside this module
def _scroll_sidebar_once(page, logger, scroll_round: int) -> bool:
    """Deprecated: use _scroll_sidebar_and_wait instead."""
    result = _scroll_sidebar_and_wait(page, logger, scroll_round, timeout_ms=60000)
    return result.get("count", 0) > 0


def _parse_sidebar_time_token(token: str, now: datetime | None = None) -> dict:
    token = (token or "").strip()
    if not token:
        return {"kind": "unknown", "token": "", "days_ago": None, "parsed_at": None}

    now = now or datetime.now()
    lower = token.lower()
    if lower in TIME_TODAY:
        return {"kind": "today", "token": token, "days_ago": 0, "parsed_at": now.date().isoformat()}
    if lower in TIME_YESTERDAY:
        return {"kind": "yesterday", "token": token, "days_ago": 1, "parsed_at": (now.date() - timedelta(days=1)).isoformat()}
    if lower in DAY_NAMES:
        return {"kind": "weekday", "token": token, "days_ago": 6, "parsed_at": None}
    if MONTH_DAY_RE.match(token):
        parsed = datetime.strptime(f"{token} {now.year}", "%b %d %Y")
        days_ago = (now - parsed).days
        if days_ago < 0:
            parsed = datetime.strptime(f"{token} {now.year - 1}", "%b %d %Y")
            days_ago = (now - parsed).days
        return {"kind": "month_day", "token": token, "days_ago": days_ago, "parsed_at": parsed.date().isoformat()}
    return {"kind": "unknown", "token": token, "days_ago": None, "parsed_at": None}


def _is_thread_older_than_range(parsed_time: dict, max_days: int) -> bool:
    days_ago = parsed_time.get("days_ago")
    if days_ago is None:
        return False
    return days_ago > max_days


def _verify_thread_switch(page, logger, name: str, prev_fb_url: str, pre_click_fingerprint: str,
                          is_first_thread: bool) -> tuple[str, bool]:
    fb_url = ""
    url_changed = False
    for _poll in range(20):
        try:
            current_qs = parse_qs(urlparse(page.url).query)
            candidate = current_qs.get('selected_item_id', [''])[0]
            if candidate and candidate != prev_fb_url:
                fb_url = candidate
                url_changed = True
                logger.info(f"thread_switch_verified method=selected_item_id thread='{name}'")
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
            if not url_changed:
                logger.info(f"thread_switch_verified method=panel_fingerprint thread='{name}'")
            break
        page.wait_for_timeout(500)

    if not panel_refreshed and is_first_thread:
        logger.info(f"thread_switch_verified method=first_thread_already_loaded thread='{name}'")
        return fb_url, True

    if not panel_refreshed and not url_changed:
        logger.warning(f"thread_switch_failed thread='{name}' reason=no_url_change_no_panel_refresh")
        return "", False

    return fb_url, True


def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels, extract_user_info, detect_city,
                 skip_navigation: bool = False) -> dict:
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record

    """Core inbox scraping loop over the Facebook Business Suite thread list."""
    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

    if not skip_navigation:
        logger.info(f"Navigating to {inbox_url}")
        page.goto(inbox_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
    else:
        logger.info(f"Page is already correctly positioned at {inbox_url}. Skipping navigation.")

    # Phase 1: Wait for the inbox shell container to appear in DOM (up to 30s)
    logger.info("Waiting for inbox shell...")
    _wait_for_inbox_shell(page, logger, timeout_ms=30000)

    # Phase 2: Wait for at least 1 thread card to render (SPA hydration, up to 30s)
    logger.info("Waiting for initial threads to appear...")
    initial_snapshot = _wait_for_initial_threads(page, logger, timeout_ms=30000)

    # Phase 3: One controlled sidebar scroll to load more threads + wait up to 60s
    initial_sidebar = _scroll_sidebar_and_wait(page, logger, scroll_round=0, timeout_ms=60000)

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
    processed_thread_keys = set()
    scroll_round = 0
    max_scroll_rounds = 50
    reached_date_limit = False
    last_new_round = 0
    thread_counter = 0
    sidebar_scrolls = 1 if initial_sidebar.get("count", 0) >= 0 else 0
    sidebar_wait_ms = initial_sidebar.get("elapsed_ms", 0)
    stats = {
        "new_threads": 0,
        "new_messages": 0,
        "skipped_threads": 0,
        "threads_seen": 0,
        "threads_processed": 0,
        "threads_skipped_duplicate": 0,
        "threads_skipped_cutoff": 0,
        "threads_skipped_click_verify": 0,
        "sidebar_scrolls": sidebar_scrolls,
        "sidebar_wait_ms": sidebar_wait_ms,
    }

    while scroll_round < max_scroll_rounds and not reached_date_limit:
        scroll_round += 1
        if thread_counter >= max_threads:
            logger.info(f"Reached max threads ({max_threads}). Stopping.")
            break

        visible_threads = _extract_visible_threads(page)
        stats["threads_seen"] += len(visible_threads)
        if not visible_threads:
            logger.info(f"Round {scroll_round}: no thread cards visible.")
            break

        new_in_round = 0
        for vt in visible_threads:
            name = (vt.get("name") or "").strip()
            if not name:
                logger.info("sidebar_candidate_skipped reason=missing_name")
                continue
            if thread_counter >= max_threads:
                break

            parsed_time = _parse_sidebar_time_token(vt.get("sidebarTimeText", ""))
            vt["sidebarTimeKind"] = parsed_time.get("kind", "unknown")
            thread_key = vt.get("selectedItemId") or vt.get("sidebarIdentityKey") or "|".join([
                name,
                vt.get("previewText", ""),
                vt.get("sidebarTimeText", ""),
                str(vt.get("domIndex", 0)),
            ])
            vt["sidebarIdentityKey"] = thread_key

            logger.info(
                "sidebar_candidate "
                f"key={thread_key[:80]} name={name!r} time_text={vt.get('sidebarTimeText', '')!r} "
                f"time_kind={vt.get('sidebarTimeKind', '')!r} preview={vt.get('previewText', '')[:80]!r} dom_index={vt.get('domIndex', 0)}"
            )

            if thread_key in processed_thread_keys:
                stats["threads_skipped_duplicate"] += 1
                logger.info(f"sidebar_candidate_skipped reason=duplicate_key key={thread_key[:80]}")
                continue

            if _is_thread_older_than_range(parsed_time, max_days):
                reached_date_limit = True
                stats["threads_skipped_cutoff"] += 1
                logger.info(
                    f"Date cutoff: '{vt.get('sidebarTimeText', '')}' is {parsed_time.get('days_ago')}d ago "
                    f"(limit: {max_days}d)."
                )
                break

            processed_thread_keys.add(thread_key)
            new_in_round += 1
            thread_counter += 1

            thread_record = build_thread_record(page_id, vt)
            cursor.execute("SELECT last_synced_time FROM threads WHERE id = ?", (thread_record.thread_id,))
            row = cursor.fetchone()

            if row and row[0] == thread_record.preview_text:
                force_resync = False
                preview_lower = (thread_record.preview_text or "").strip().lower()
                if preview_lower.startswith("you:") or preview_lower.startswith("bạn:"):
                    last_msg_row = cursor.execute(
                        "SELECT sender FROM messages WHERE thread_id = ? ORDER BY seq DESC LIMIT 1",
                        (thread_record.thread_id,)
                    ).fetchone()
                    if last_msg_row and last_msg_row[0] != "Page":
                        force_resync = True
                        logger.info(f"Force re-sync thread '{name}': sidebar shows admin reply but DB last msg is '{last_msg_row[0]}'.")

                if not force_resync:
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
                thread_el = page.locator(_thread_card_selector()).nth(thread_record.dom_index)
                thread_el.click(force=True, timeout=5000)
            except Exception as e:
                stats["threads_skipped_click_verify"] += 1
                logger.warning(f"Could not click thread '{name}': {e}. Skipping.")
                continue

            try:
                page.wait_for_selector(MESSAGE_REGION_SELECTOR, timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                logger.warning(f"Message region not found within 10s for thread '{name}'. Falling back to timeout.")
                page.wait_for_timeout(4000)

            is_first_thread = (thread_counter == 1 and scroll_round == 1)
            fb_url, verified = _verify_thread_switch(
                page,
                logger,
                name,
                prev_fb_url,
                pre_click_fingerprint,
                is_first_thread,
            )
            if not verified:
                stats["threads_skipped_click_verify"] += 1
                continue

            page.wait_for_timeout(1000)
            logger.info(f"Thread '{name}': URL fb_url={fb_url}, panel_refreshed={verified}")

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
                    # NOTE: mouse.wheel removed — JS scrollTop above handles message
                    # panel scrolling. mouse.wheel leaked into the sidebar causing
                    # erratic scrollbar jumps during thread history loading.
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
                let elements = region.querySelectorAll('.x14vqqas, .x1fqp7bg');
                let processedBubbles = new Set();

                for (let el of elements) {
                    if (el.classList.contains('x14vqqas')) {
                        let ts = el.innerText.trim();
                        if (ts && ts.length < 50) currentTimestamp = ts;
                        continue;
                    }
                    if (el.classList.contains('x1fqp7bg')) {
                        let isNested = false;
                        let p = el.parentElement;
                        while(p && p !== region) {
                            if (p.classList.contains('x1fqp7bg') || processedBubbles.has(p)) {
                                isNested = true;
                                break;
                            }
                            p = p.parentElement;
                        }
                        if (isNested) continue;
                        processedBubbles.add(el);

                        let sender = "Unknown";
                        let outerWrapper = el.parentElement || el;
                        let htmlStr = outerWrapper.outerHTML.substring(0, 500);

                        if (htmlStr.includes('x13a6bvl')) {
                            sender = "Page";
                        } else if (htmlStr.includes('x1nhvcw1')) {
                            sender = "Customer";
                        } else {
                            let avatar = el.querySelector('img.img[alt]');
                            sender = avatar ? "Customer" : "Page";
                        }

                        let textContainers = el.querySelectorAll('.x1y1aw1k');
                        let texts = [];
                        if (textContainers.length > 0) {
                            for (let tc of textContainers) {
                                let t = tc.innerText.trim();
                                if (t) texts.push(t);
                            }
                        } else {
                            let spans = el.querySelectorAll('span > span');
                            let found = false;
                            if (spans.length > 0) {
                                for (let sp of spans) {
                                    let t = sp.innerText.trim();
                                    if (t) { texts.push(t); found = true; }
                                }
                            }
                            if (!found) {
                                let text = el.innerText.trim();
                                if (text && text.length > 2 && text.length < 2000) texts.push(text);
                            }
                        }

                        for(let segment of texts) {
                            results.push({sender, text: segment, timestamp: currentTimestamp});
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
            stats["threads_processed"] += 1

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

        if thread_counter >= max_threads:
            break

        # Scroll sidebar for next batch of threads
        scroll_result = _scroll_sidebar_and_wait(page, logger, scroll_round=scroll_round, timeout_ms=60000)
        stats["sidebar_scrolls"] += 1
        stats["sidebar_wait_ms"] += scroll_result.get("elapsed_ms", 0)

    logger.info(f"Scroll-and-process complete. Processed {thread_counter} threads. Stats: {stats}")
    record_fetch(page_id, stats["new_threads"] + stats["skipped_threads"], stats["new_messages"], conn)
    return stats


# Keep ad-label extraction at the canonical inbox namespace.
def extract_ad_id_labels(page) -> list:
    labels_text = page.evaluate('''() => {
        let sidebar = null;

        let headings = document.querySelectorAll('span, h3, h4, div');
        for (let h of headings) {
            let t = (h.innerText || "").trim();
            if (t === "Labels" || t === "Nhãn" || t === "Label") {
                sidebar = h.closest('div[class*="x1n2onr6"]') || h.parentElement?.parentElement;
                break;
            }
        }

        if (sidebar) {
            let text = (sidebar.innerText || "").trim();
            if (text.includes("ad_id")) return text;
        }

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
    raw = re.findall(r'ad_id\.?(\d{5,})', labels_text)
    return list(dict.fromkeys(raw))


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
]
