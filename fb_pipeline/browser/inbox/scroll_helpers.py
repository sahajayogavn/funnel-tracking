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
                              timeout_ms: int = 60000, poll_ms: int = 1000) -> dict:
    """Move mouse to left sidebar, scroll once, wait for loading indicators,
    then wait up to timeout_ms for new threads to appear and stabilize.

    Returns a sidebar loading snapshot dict.
    """
    pre_snapshot = sidebar_loading_snapshot(page)
    pre_count = pre_snapshot["count"]
    pre_fingerprint = pre_snapshot["fingerprint"]

    try:
        page.mouse.move(200, 400)
    except Exception:
        pass

    try:
        page.evaluate(r'''(config) => {
            const cards = Array.from(document.querySelectorAll(config.threadSelector));
            if (cards.length > 0) {
                cards[cards.length - 1].scrollIntoView({block: 'center', inline: 'nearest'});
            }
        }''', {"threadSelector": thread_card_selector()})
        logger.info(f"sidebar_scroll_performed round={scroll_round} pre_count={pre_count}")
    except Exception as e:
        logger.warning(f"sidebar_scroll_failed round={scroll_round}: {e}")
        pre_snapshot["elapsed_ms"] = 0
        return pre_snapshot

    page.wait_for_timeout(500)

    start = time.time()
    stable_polls = 0
    saw_loading = False

    while True:
        snapshot = sidebar_loading_snapshot(page)
        effective_loading = sidebar_loading_count(snapshot)
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

def scroll_sidebar_once(page, logger, scroll_round: int) -> bool:
    """Deprecated: use scroll_sidebar_and_wait instead."""
    result = scroll_sidebar_and_wait(page, logger, scroll_round, timeout_ms=60000)
    return result.get("count", 0) > 0
