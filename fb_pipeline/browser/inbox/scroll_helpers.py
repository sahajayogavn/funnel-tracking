import hashlib
import time

from .constants import (
    THREAD_LIST_CONTAINER_SELECTORS,
    LOADING_INDICATOR_SELECTORS,
    thread_card_selector,
)

def wait_for_inbox_shell(page, logger, timeout_ms: int = 30000) -> str:
    """Wait for the thread list container pagelet to appear in the DOM."""
    selector = ", ".join(THREAD_LIST_CONTAINER_SELECTORS)
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
        logger.info("Thread list container detected.")
        return selector
    except Exception:
        logger.info(f"Thread list pagelet not found within {timeout_ms}ms, proceeding with fallback...")
        return ""

def wait_for_initial_threads(page, logger, timeout_ms: int = 30000, poll_ms: int = 1000) -> dict:
    """Poll until at least 1 thread card is visible in the DOM.

    This ensures Facebook's SPA hydration has completed rendering the thread
    list before any scrolling or processing begins.
    """
    start = time.time()
    logger.info(f"initial_threads_wait_start timeout_ms={timeout_ms}")
    while True:
        snapshot = sidebar_loading_snapshot(page)
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

def sidebar_loading_snapshot(page) -> dict:
    selector = thread_card_selector()
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
    fingerprint_val = snapshot.get("fingerprint", "")
    digest = hashlib.sha256(str(fingerprint_val).encode("utf-8")).hexdigest()[:12]
    snapshot["fingerprint"] = digest
    return snapshot

def sidebar_loading_count(snapshot: dict) -> int:
    if snapshot.get("hasContainer"):
        return int(snapshot.get("loadingCount") or 0)
    return int(snapshot.get("globalLoadingCount") or snapshot.get("loadingCount") or 0)

def wait_for_sidebar_threads(page, logger, timeout_ms: int = 60000, poll_ms: int = 1000) -> dict:
    start = time.time()
    stable_polls = 0
    saw_growth = False
    last_snapshot = None
    logger.info(f"sidebar_load_start timeout_ms={timeout_ms}")

    while True:
        snapshot = sidebar_loading_snapshot(page)
        effective_loading = sidebar_loading_count(snapshot)
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

def scroll_sidebar_and_wait(page, logger, scroll_round: int,
                              timeout_ms: int = 60000, poll_ms: int = 250) -> dict:
    """Move mouse to left sidebar, scroll once, wait for loading indicators,
    then wait up to timeout_ms for new threads to appear and stabilize.

    Returns a sidebar loading snapshot dict.
    """
    pre_snapshot = sidebar_loading_snapshot(page)
    pre_count = pre_snapshot["count"]
    pre_fingerprint = pre_snapshot["fingerprint"]
    initial_count = pre_count
    initial_fingerprint = pre_fingerprint

    try:
        scroll_info = page.evaluate(r'''(config) => {
            let before = -1;
            let after = -1;
            const cards = Array.from(document.querySelectorAll(config.threadSelector));
            // `a[role=link]` also matches inbox filter tabs (for example,
            // “All messages”). Tabs are not conversation cards and lead to a
            // non-scrollable ancestor, so exclude them before locating the
            // virtualized list.
            const conversationCards = cards.filter(card => !card.closest('[role="tablist"]'));
            const roots = [];
            for (const selector of config.containerSelectors) {
                for (const root of Array.from(document.querySelectorAll(selector))) {
                    const count = conversationCards.filter(card => root.contains(card)).length;
                    if (count) roots.push({root, count});
                }
            }
            roots.sort((a, b) => b.count - a.count);
            const scrollRoot = roots.length ? roots[0].root : (conversationCards[0] || cards[0] || null);
            const rect = scrollRoot ? scrollRoot.getBoundingClientRect() : null;
            let scroller = null;
            let node = conversationCards[0] || cards[0] || null;
            while (node && node.tagName !== 'BODY') {
                const style = window.getComputedStyle(node);
                const isScrollable = node.scrollHeight > node.clientHeight + 1
                    && ['auto', 'scroll', 'overlay'].includes(style.overflowY);
                if (isScrollable) {
                    scroller = node;
                    break;
                }
                node = node.parentElement;
            }
            if (scroller) {
                before = scroller.scrollTop;
                const delta = Math.max(160, Math.floor(scroller.clientHeight * 0.8));
                scroller.scrollTop = Math.min(scroller.scrollTop + delta, scroller.scrollHeight);
                // Setting scrollTop is normally sufficient, but this Inbox
                // variant only asks React to fetch the next virtual page when
                // its scroll listener receives an event.  In particular, the
                // final small move to the current bottom otherwise leaves the
                // same 8--10 recycled cards on screen forever.
                scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                after = scroller.scrollTop;
            } else if (conversationCards.length > 0) {
                // Keep this DOM-only fallback for list variants that do expose
                // a scroll target after React updates.
                conversationCards[conversationCards.length - 1].scrollIntoView({block: 'center', inline: 'nearest'});
            }
            return {
                before,
                after,
                domMoved: before >= 0 && after > before,
                reachedDomEnd: before >= 0 && after >= (scroller.scrollHeight - scroller.clientHeight - 1),
                targetX: rect ? Math.max(24, Math.min(window.innerWidth - 24, rect.left + rect.width / 2)) : null,
                targetY: rect ? Math.max(80, Math.min(window.innerHeight - 24, rect.top + Math.min(rect.height / 2, 240))) : null,
                targetHeight: rect ? rect.height : 0,
                conversationCardCount: conversationCards.length,
            };
        }''', {
            "threadSelector": thread_card_selector(),
            "containerSelectors": THREAD_LIST_CONTAINER_SELECTORS,
        })
        
        # Verify scroll action and log
        before_scroll = scroll_info.get("before", -1)
        after_scroll = scroll_info.get("after", -1)
        diff = (after_scroll - before_scroll) if before_scroll != -1 else 0
        logger.info(f"sidebar_scroll_performed round={scroll_round} pre_count={pre_count} "
                    f"scrollTop={before_scroll}->{after_scroll} (diff: {diff}) "
                    f"conversation_cards={scroll_info.get('conversationCardCount', 0)}")

        # code:fb-inbox-scroll-001:wheel-fallback
        # Facebook's current virtualized sidebar can load its next page only
        # from a wheel event over the list pagelet. In that variant there is no
        # DOM scrollTop to advance, so target the actual list geometry and
        # verify the outcome through the post-scroll fingerprint below.
        if not scroll_info.get("domMoved") and scroll_info.get("targetX") is not None:
            wheel_delta = max(480, min(1000, int(scroll_info.get("targetHeight") or 0)))
            page.mouse.move(scroll_info["targetX"], scroll_info["targetY"])
            page.mouse.wheel(0, wheel_delta)
            logger.info(
                f"sidebar_scroll_wheel_fallback round={scroll_round} "
                f"x={int(scroll_info['targetX'])} y={int(scroll_info['targetY'])} delta={wheel_delta}"
            )
    except Exception as e:
        logger.warning(f"sidebar_scroll_failed round={scroll_round}: {e}")
        pre_snapshot["elapsed_ms"] = 0
        return pre_snapshot

    page.wait_for_timeout(50)

    start = time.time()
    stable_polls = 0
    saw_loading = False
    stagnant_loading_polls = 0
    observed_count = pre_count
    observed_fingerprint = pre_fingerprint
    recovery_wheels_sent = 0
    # The virtual list may take longer than the usual three 250ms stable
    # polls to append its next page after we reach the current DOM bottom.
    # Do not mistake that brief pagination gap for a completed scroll.
    bottom_page_grace_ms = 2_500 if scroll_info.get("reachedDomEnd") else 0

    while True:
        snapshot = sidebar_loading_snapshot(page)
        effective_loading = sidebar_loading_count(snapshot)
        elapsed_ms = int((time.time() - start) * 1000)

        if effective_loading > 0:
            saw_loading = True
            stable_polls = 0
            changed_while_loading = (
                snapshot["count"] != observed_count
                or snapshot["fingerprint"] != observed_fingerprint
            )
            if changed_while_loading:
                stagnant_loading_polls = 0
                observed_count = snapshot["count"]
                observed_fingerprint = snapshot["fingerprint"]
            else:
                stagnant_loading_polls += 1
            logger.info(
                f"sidebar_scroll_wait round={scroll_round} loading={effective_loading} "
                f"count={snapshot['count']} elapsed_ms={elapsed_ms}"
            )
            # A programmatic scroll can move the virtual container and show
            # Meta's loading marker, yet its pagination reducer may only run
            # after a trusted wheel input.  This is precisely the state seen
            # in production: scrollTop advances, then the same cards and a
            # spinner remain forever. Nudge the actual list after it has been
            # static for two seconds, and once again later if necessary.
            if (stagnant_loading_polls in (8, 40)
                    and scroll_info.get("targetX") is not None):
                try:
                    page.mouse.move(scroll_info["targetX"], scroll_info["targetY"])
                    page.mouse.wheel(0, max(720, min(1400, int(scroll_info.get("targetHeight") or 0) * 2)))
                    recovery_wheels_sent += 1
                    logger.warning(
                        f"sidebar_scroll_loading_recovery round={scroll_round} "
                        f"attempt={recovery_wheels_sent} elapsed_ms={elapsed_ms}"
                    )
                except Exception as exc:
                    logger.warning(f"sidebar_scroll_loading_recovery_failed round={scroll_round}: {exc}")
            # Facebook can leave one loading marker rendered after card
            # pagination has stopped. Bound that static state independently
            # of the broader scroll timeout.
            if stagnant_loading_polls >= 120:
                logger.warning(
                    f"sidebar_scroll_stalled round={scroll_round} "
                    f"count={snapshot['count']} loading={effective_loading} "
                    f"elapsed_ms={elapsed_ms}"
                )
                snapshot["elapsed_ms"] = elapsed_ms
                snapshot["stalled"] = True
                snapshot["recovery_wheels_sent"] = recovery_wheels_sent
                return snapshot
        else:
            stagnant_loading_polls = 0
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

            if stable_polls >= 3 and elapsed_ms >= bottom_page_grace_ms:
                reason = "stable_after_loading" if saw_loading else "no_change"
                logger.info(
                    f"sidebar_scroll_complete round={scroll_round} "
                    f"count={snapshot['count']} reason={reason} elapsed_ms={elapsed_ms}"
                )
                snapshot["elapsed_ms"] = elapsed_ms
                snapshot["progressed"] = (
                    snapshot["count"] != initial_count
                    or snapshot["fingerprint"] != initial_fingerprint
                )
                snapshot["dom_moved"] = bool(scroll_info.get("domMoved"))
                return snapshot

        if elapsed_ms >= timeout_ms:
            logger.warning(
                f"sidebar_scroll_timeout round={scroll_round} "
                f"count={snapshot['count']} loading={effective_loading} elapsed_ms={elapsed_ms}"
            )
            snapshot["elapsed_ms"] = elapsed_ms
            snapshot["progressed"] = (
                snapshot["count"] != initial_count
                or snapshot["fingerprint"] != initial_fingerprint
            )
            snapshot["dom_moved"] = bool(scroll_info.get("domMoved"))
            return snapshot

        page.wait_for_timeout(poll_ms)


def reset_sidebar_to_top(page, logger) -> dict:
    """Reset the actual virtualized conversation-list scroller.

    Filter tabs also match the broad thread-card selector. Resetting from the
    first raw match can leave Stage 2 at the last scanned viewport instead of
    returning it to the earliest candidate.
    """
    result = page.evaluate(r'''(config) => {
        const cards = Array.from(document.querySelectorAll(config.threadSelector));
        const conversationCards = cards.filter(card => !card.closest('[role="tablist"]'));
        let node = conversationCards[0] || cards[0] || null;
        while (node && node.tagName !== 'BODY') {
            const style = window.getComputedStyle(node);
            const isScrollable = node.scrollHeight > node.clientHeight + 1
                && ['auto', 'scroll', 'overlay'].includes(style.overflowY);
            if (isScrollable) {
                const before = node.scrollTop;
                node.scrollTop = 0;
                return {before, after: node.scrollTop, found: true,
                    conversationCardCount: conversationCards.length};
            }
            node = node.parentElement;
        }
        return {before: -1, after: -1, found: false,
            conversationCardCount: conversationCards.length};
    }''', {"threadSelector": thread_card_selector()})
    if not isinstance(result, dict):
        result = {"before": -1, "after": -1, "found": False, "conversationCardCount": 0}
    logger.info(
        "sidebar_reset_to_top "
        f"found={result.get('found', False)} scrollTop={result.get('before', -1)}->{result.get('after', -1)} "
        f"conversation_cards={result.get('conversationCardCount', 0)}"
    )
    return result

def scroll_sidebar_once(page, logger, scroll_round: int) -> bool:
    """Deprecated: use scroll_sidebar_and_wait instead."""
    result = scroll_sidebar_and_wait(page, logger, scroll_round, timeout_ms=60000)
    return result.get("count", 0) > 0
