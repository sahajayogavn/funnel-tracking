import re
import time
from typing import Callable

from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask, ThreadResult

from .inbox.scroll_helpers import (
    wait_for_inbox_shell,
    wait_for_initial_threads,
    scroll_sidebar_and_wait,
    reset_sidebar_to_top,
    sidebar_loading_snapshot,
    sidebar_loading_count,
    wait_for_sidebar_threads,
)
from .inbox.thread_list_parser import (
    extract_visible_threads,
    is_conversation_name,
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
from .inbox.thread_locator import (
    LocateResult,
    locate_thread_in_sidebar,
    MAX_STAGNANT_STAGE2_CLICK_RETRIES,
    MAX_THREAD_LOADING_WAIT_MS,
    _sidebar_snapshot_progressed,
    _thread_panel_loading_count,
)
from .inbox.thread_worker import ThreadWorkerDeps, process_thread_task
from .inbox.constants import (
    LOADING_INDICATOR_SELECTORS,
    MESSAGE_REGION_SELECTOR,
    thread_card_selector,
)

MAX_IDLE_SIDEBAR_NO_PROGRESS_MS = 120_000


# code:inbox-thread-identity-001:stage1-psid
def _resolve_psid(conn, page_id: str, thread_name: str) -> str:
    """PSID cached from an earlier crawl (``users.fb_url``), "" when unknown/ambiguous."""
    try:
        from fb_pipeline.persistence.l4_sqlite_store import resolve_psid_hint
        return resolve_psid_hint(conn, page_id, thread_name) or ""
    except Exception:
        return ""

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

def discover_threads(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                     record_fetch, *, skip_navigation: bool = False, force_refresh: bool = False,
                     allow_early_exit: bool = True, target_total_messages: int | None = None,
                     on_task: Callable[[ThreadTask], None]) -> dict:
    """Stage 1: discover conversation threads top-down in the sidebar and
    dispatch a ``ThreadTask`` for every one that needs Stage 2 processing.

    ``on_task`` is invoked, in Inbox order, once for every discovered thread
    that is not a "skip" (already-synced) card, at the end of each
    visible-cards round, before the next sidebar scroll.

    Returns a dict:
    - Early-exit cases (cache hit, or the message target already met):
      ``{"early_exit": True, "stats": <the same stats dict scrape_inbox used
      to return directly for that case>}``.
    - Normal completion: ``{"early_exit": False, "stats": <Stage 1 stats>,
      "existing_message_count": <int>}``.

    # code:inbox-parallel-fetch-001:discover
    """
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, canonical_thread_id

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

    # Stage 1 establishes the exact ordering shown by Seekers.  Reset before
    # collecting, not only later for detail extraction, so max_threads means
    # the actual top N conversations in Meta's Inbox.
    try:
        top_reset = reset_sidebar_to_top(page, logger)
        page.wait_for_timeout(500)
        logger.info(f"Stage 1 sidebar reset to top: {top_reset}")
    except Exception as exc:
        logger.warning(f"Could not reset sidebar before Stage 1: {exc}")

    if not force_refresh and allow_early_exit:
        first_glance_threads = extract_visible_threads(page)
        is_cache_hit = validate_quick_fetch_cache(first_glance_threads, conn, logger, page_id)
        if is_cache_hit:
            return {
                "early_exit": True,
                "stats": {
                    "new_threads": 0, "new_messages": 0, "skipped_threads": len(first_glance_threads),
                    "threads_seen": len(first_glance_threads), "threads_processed": 0,
                    "processed_thread_ids": [],
                    "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0, "threads_skipped_click_verify": 0,
                    "sidebar_scrolls": 0, "sidebar_wait_ms": initial_snapshot.get("elapsed_ms", 0),
                    "method": "dynamic_cache_hit"
                },
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
    # Do not clear the saved ordering before discovery succeeds.  Targeted,
    # capped, or interrupted fetches are partial snapshots; clearing here
    # erased the prior top-ten order and made the Seekers list fall back to
    # timestamp sorting.  Each discovered sidebar card updates its own rank.
    processed_thread_keys = set()
    scroll_round = 0
    reached_date_limit = False
    last_new_thread_at = time.monotonic()
    thread_counter = 0
    consecutive_clean_threads = 0
    consecutive_old_threads = 0
    stats = {
        "new_threads": 0, "new_messages": 0, "skipped_threads": 0, "threads_seen": 0,
        "threads_processed": 0, "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0,
        "processed_thread_ids": [],
        "threads_skipped_click_verify": 0,
        "threads_psid_resolved": 0,
        "sidebar_scrolls": 0,
        "sidebar_wait_ms": initial_snapshot.get("elapsed_ms", 0),
    }

    existing_message_count = 0
    if target_total_messages is not None:
        existing_message_count = cursor.execute(
            "SELECT COUNT(*) FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.page_id = ?",
            (page_id,),
        ).fetchone()[0]
        stats["target_total_messages"] = target_total_messages
        stats["starting_total_messages"] = existing_message_count
        if existing_message_count >= target_total_messages:
            logger.info(
                f"Message target already met ({existing_message_count}/{target_total_messages}); skipping scrape."
            )
            record_fetch(page_id, 0, 0, conn)
            return {"early_exit": True, "stats": stats}

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
        round_tasks = []
        for vt in visible_threads:
            name = (vt.get("name") or "").strip()
            # Keep a Python-side guard as well as the DOM parser guard. This
            # prevents a navigation label from being persisted if a caller
            # supplies a visible-thread payload from a different parser.
            if not is_conversation_name(name):
                logger.warning("Skipping invalid inbox conversation label: %r", name)
                continue
            if thread_counter >= max_threads:
                break

            parsed_time = parse_sidebar_time_token(vt.get("sidebarTimeText", ""))
            vt["sidebarTimeKind"] = parsed_time.get("kind", "unknown")
            # code:inbox-thread-identity-001:stage1-psid
            # Sidebar cards expose href="#" until selected, so the card rarely
            # carries a PSID. Recover it from a previous run (users.fb_url,
            # unique per name) so this thread gets its canonical id *now*:
            # the DB lookup below can hit (skip already-synced threads) and
            # Stage 2 can open it by direct URL instead of scrolling the sidebar.
            psid = (vt.get("selectedItemId") or "").strip() or _resolve_psid(conn, page_id, name)
            thread_key = psid or vt.get("sidebarIdentityKey") or "|".join([
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
            # `domIndex` restarts for every virtualized viewport.  Persist the
            # global Stage-1 ordinal instead, which matches the top-down Inbox
            # sequence across all sidebar scroll rounds.
            thread_record.dom_index = thread_counter - 1
            if psid:
                thread_record.selected_item_id = psid
                thread_record.thread_id = canonical_thread_id(page_id, psid)
                stats["threads_psid_resolved"] += 1
            cursor.execute("SELECT id FROM threads WHERE id = ?", (thread_record.thread_id,))
            row = cursor.fetchone()

            # INBOX-ORDER INVARIANT: Stage 1 is the sole authority for the
            # visible Inbox order.  Cache-hit threads deliberately skip Stage
            # 2/message persistence, but their rank still has to be written
            # here.  Do not move this update below the skip path: doing so
            # leaves a successful fetch with stale (or NULL) top-ten ranks.
            if row:
                cursor.execute(
                    "UPDATE threads SET inbox_sort_index = ? WHERE id = ?",
                    (thread_record.dom_index, thread_record.thread_id),
                )

            is_match = False
            force_resync = False

            if row:
                # code:inbox-thread-identity-001:preview-match
                # Compare the sidebar preview with the last few persisted rows,
                # not only the newest one: system banners such as
                # "<name> replied to an ad." are sometimes extracted on a later
                # crawl and land at the end of the thread although they belong
                # to its start, which made the newest row never match.
                cursor.execute(
                    "SELECT content, sender FROM messages WHERE thread_id = ? ORDER BY seq DESC LIMIT 3",
                    (thread_record.thread_id,)
                )
                tail_rows = cursor.fetchall()

                def _normalize_msg(s):
                    if not s: return ""
                    s = re.sub(r'^---\s*\[AD SOURCE\]:.*?---\s*', '', s, flags=re.DOTALL)
                    s = re.sub(r'^(you|bạn):\s*', '', s, flags=re.IGNORECASE)
                    return ''.join(c.lower() for c in s if c.isalnum())

                ui_norm = _normalize_msg(thread_record.preview_text or "")
                msg_row = None
                for candidate in tail_rows:
                    db_norm = _normalize_msg(candidate[0])
                    min_len = min(len(db_norm), len(ui_norm))
                    if (min_len > 0 and db_norm[:min_len] == ui_norm[:min_len]) or (min_len == 0 and db_norm == ui_norm):
                        is_match = True
                        msg_row = candidate
                        break

                if is_match:
                    preview_lower = (thread_record.preview_text or "").strip().lower()
                    if preview_lower.startswith("you:") or preview_lower.startswith("bạn:"):
                        if msg_row and msg_row[1] not in ("Page", "Auto_Page"):
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
            round_tasks.append(ThreadTask(
                ordinal=thread_record.dom_index,
                record=thread_record,
                absolute_top=vt.get("absoluteTop", 0),
                psid_hint=psid,
                is_new=(row is None),
            ))

            if row is None:
                stats["new_threads"] += 1

        # Dispatch every non-skip thread found in this round before the next
        # sidebar scroll (whether the round ended naturally or via the
        # cutoff/early-exit breaks above).
        for task in round_tasks:
            on_task(task)

        if reached_date_limit:
            break
        if new_in_round == 0:
            idle_ms = int((time.monotonic() - last_new_thread_at) * 1000)
            if idle_ms >= MAX_IDLE_SIDEBAR_NO_PROGRESS_MS:
                logger.info(
                    "Stopping Stage 1 after "
                    f"{idle_ms}ms without a newly discovered sidebar thread."
                )
                break
        else:
            last_new_thread_at = time.monotonic()

        if thread_counter >= max_threads:
            break

        last_vt = visible_threads[-1] if visible_threads else {}
        current_date_reach = last_vt.get("sidebarTimeText", "Unknown")
        logger.info(f"Completed processing {thread_counter} threads (reached [{current_date_reach}]) before sidebar scroll round {scroll_round}.")
        scroll_result = scroll_sidebar_and_wait(page, logger, scroll_round=scroll_round, timeout_ms=60000)
        stats["sidebar_scrolls"] += 1
        stats["sidebar_wait_ms"] += scroll_result.get("elapsed_ms", 0)

    # END STAGE 1
    conn.commit()
    logger.info(f"Stage 1 Complete. Listed {len(collected_threads)} threads in range.")

    return {
        "early_exit": False,
        "stats": stats,
        "existing_message_count": existing_message_count,
    }

def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels_arg, extract_user_info, detect_city,
                 skip_navigation: bool = False, force_refresh: bool = False,
                 allow_early_exit: bool = True,
                 target_total_messages: int | None = None) -> dict:
    tasks: list[ThreadTask] = []
    discovery = discover_threads(
        page, page_id, time_range, max_threads, conn, logger, record_fetch,
        skip_navigation=skip_navigation, force_refresh=force_refresh,
        allow_early_exit=allow_early_exit, target_total_messages=target_total_messages,
        on_task=tasks.append,
    )

    if discovery["early_exit"]:
        return discovery["stats"]

    stats = discovery["stats"]
    existing_message_count = discovery["existing_message_count"]

    # Avoid writing tens of thousands of PII-bearing log lines during an
    # archive import. The total and per-thread Stage 2 logs remain auditable.
    logger.info(f"Stage 2 will extract details for {len(tasks)} threads.")

    # STAGE 2
    if len(tasks) > 0:
        logger.info("Resetting sidebar scroll to top for Stage 2...")
        try:
            # code:fb-inbox-scroll-001:stage2-reset
            # Use the same tab-aware virtual-list resolver as Stage 1.
            scroll_reset_info = reset_sidebar_to_top(page, logger)
            if scroll_reset_info.get("found") and scroll_reset_info.get("after") != 0:
                logger.warning("Stage 2 sidebar reset did not reach scrollTop=0; continuing with guarded click retries.")
        except Exception as e:
            logger.warning(f"Failed to run Stage 2 scroll reset: {e}")

        page.wait_for_timeout(1500)

        deps = ThreadWorkerDeps(
            extract_ad_id_labels=extract_ad_id_labels_arg,
            extract_user_info=extract_user_info,
            detect_city=detect_city,
        )

        for i, task in enumerate(tasks):
            if (target_total_messages is not None
                    and existing_message_count + stats["new_messages"] >= target_total_messages):
                logger.info(
                    f"Reached total message target ({existing_message_count + stats['new_messages']}/"
                    f"{target_total_messages}). Stopping Stage 2."
                )
                break
            name = task.record.thread_name
            logger.info(f"Syncing thread '{name}' (#{i+1}/{len(tasks)})...")

            result = process_thread_task(page, conn, task, deps, logger, is_first_thread=(i == 0))

            if result.status == "click_verify_failed":
                stats["threads_skipped_click_verify"] += 1
            elif result.status == "persisted":
                stats["new_messages"] += result.messages_added
                stats["threads_processed"] += 1
                stats["processed_thread_ids"].append(result.thread_id)
            # "no_messages" -> no stats change, matching the original `continue`
            # (a warning was already logged inside process_thread_task).

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
