import re
import time
from datetime import datetime
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
    is_ignored_inbox_name,
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
# A single no-change result is common while Meta fetches the next virtual
# page.  Two consecutive results after the helper's pagination grace period
# mean neither the scrollbar nor the cards progressed, so continuing would
# only re-read the same viewport.
MAX_CONSECUTIVE_STAGNANT_SIDEBAR_SCROLLS = 3


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

def _parse_day(parsed_at, utime_ms=None):
    """Calendar day of a sidebar card: exact epoch when present, else the
    ``parse_sidebar_time_token`` result (``None`` if unknown)."""
    if utime_ms:
        try:
            return datetime.fromtimestamp(float(utime_ms) / 1000).date()
        except (ValueError, OverflowError, OSError):
            pass
    if not parsed_at:
        return None
    try:
        return datetime.strptime(str(parsed_at)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def discover_threads(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                     record_fetch, *, skip_navigation: bool = False, force_refresh: bool = False, refresh_older_than_days: int | None = None,
                     allow_early_exit: bool = True, target_total_messages: int | None = None,
                     on_task: Callable[[ThreadTask], bool | None]) -> dict:
    """Stage 1: discover conversation threads top-down in the sidebar and
    dispatch a ``ThreadTask`` for every one that needs Stage 2 processing.

    ``on_task`` is invoked, in Inbox order, once for every discovered thread
    that is not a "skip" (already-synced) card, at the end of each
    visible-cards round, before the next sidebar scroll. Returning False
    stops discovery without marking it complete (quality gate/target stop).

    Returns a dict:
    - Early-exit cases (cache hit, or the message target already met):
      ``{"early_exit": True, "stats": <the same stats dict scrape_inbox used
      to return directly for that case>}``.
    - Normal completion: ``{"early_exit": False, "stats": <Stage 1 stats>,
      "existing_message_count": <int>}``.

    # code:inbox-parallel-fetch-001:discover
    """
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, canonical_thread_id, normalize_preview_text
    from fb_pipeline.inbox.l3_sync_decision import (
        FETCH, SKIP, decide_by_fetched_marker, is_out_of_order, previous_fetch_incomplete,
    )

    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

    # code:inbox-sync-skip-001:previous-run-complete
    # "Two clean cards => everything below is clean" only holds when the
    # previous run finished.  After an aborted run, cards below the abort
    # point were never re-marked, so keep scanning the whole range.
    if allow_early_exit and not force_refresh and previous_fetch_incomplete(page_id, conn):
        logger.warning("Previous fetch for this page did not complete; early exit disabled for this run.")
        allow_early_exit = False

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
    prev_sidebar_day = None
    stats = {
        "new_threads": 0, "new_messages": 0, "skipped_threads": 0, "threads_seen": 0,
        "threads_processed": 0, "threads_skipped_duplicate": 0, "threads_skipped_cutoff": 0,
        "cards_skipped_messenger_user": 0,
        "processed_thread_ids": [],
        "threads_skipped_click_verify": 0,
        "threads_psid_resolved": 0,
        "threads_time_out_of_order": 0,
        "skip_reasons": {},
        "fetch_reasons": {},
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
    interrupted = False
    stagnant_sidebar_scrolls = 0

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
            if is_ignored_inbox_name(name):
                stats["cards_skipped_messenger_user"] += 1
                logger.info("Stage1 skip reason=operator_excluded_messenger_user")
                continue
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

            # code:inbox-sync-skip-001:stage1-decision
            # Decide whether this already-known thread must be opened again.
            # Primary signal: the sidebar time token compared with the token
            # stored when the thread was last synced (``fetched_sidebar_*``).
            # Fallback (marker missing / token untrustworthy): the legacy
            # preview-vs-last-3-messages match.  ``--refresh`` bypasses both.
            skip_reason = None
            fetch_reason = "new_thread" if row is None else "force_refresh"
            parsed_day = _parse_day(parsed_time.get("parsed_at"), vt.get("sidebarTimestampMs"))
            out_of_order = is_out_of_order(prev_sidebar_day, parsed_day)
            if out_of_order:
                stats["threads_time_out_of_order"] += 1
                logger.warning(
                    f"sidebar_time_out_of_order thread='{name}' token='{vt.get('sidebarTimeText', '')}' "
                    f"prev_day={prev_sidebar_day} day={parsed_day}; not trusting token for skip"
                )
            else:
                prev_sidebar_day = parsed_day or prev_sidebar_day
            ui_norm = normalize_preview_text(thread_record.preview_text or "")

            if row and not force_refresh:
                cursor.execute(
                    "SELECT fetched_sidebar_token, fetched_preview_norm, fetched_at, fetched_sidebar_utime_ms, fetch_history_complete "
                    "FROM threads WHERE id = ?",
                    (thread_record.thread_id,),
                )
                marker = cursor.fetchone() or (None, None, None, None, 1)
                decision = decide_by_fetched_marker(
                    token_now=vt.get("sidebarTimeText", ""),
                    source_now=vt.get("sidebarTimeSource", ""),
                    preview_norm_now=ui_norm,
                    fetched_token=marker[0],
                    fetched_preview_norm=marker[1],
                    fetched_at=marker[2],
                    utime_now_ms=vt.get("sidebarTimestampMs"),
                    fetched_utime_ms=marker[3],
                    history_complete=bool(marker[4]),
                    out_of_order=out_of_order,
                    refresh_older_than_days=refresh_older_than_days,
                )
                if decision.action == SKIP:
                    skip_reason = f"token:{decision.reason}"
                elif decision.action == FETCH:
                    fetch_reason = decision.reason
                else:
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
                    msg_row = None
                    for candidate in tail_rows:
                        db_norm = normalize_preview_text(candidate[0])
                        min_len = min(len(db_norm), len(ui_norm))
                        if (min_len > 0 and db_norm[:min_len] == ui_norm[:min_len]) or (min_len == 0 and db_norm == ui_norm):
                            msg_row = candidate
                            break
                    if msg_row is None:
                        fetch_reason = f"preview_mismatch:{decision.reason}"
                    else:
                        skip_reason = f"preview:{decision.reason}"
                        # The sidebar shows our own reply but the matching stored
                        # row is positively a customer turn: the reply was never
                        # persisted.  ``sender='Unknown'`` (thousands of legacy
                        # rows) must not trigger this: it re-fetched ~90 % of
                        # already-synced threads on every run.
                        preview_lower = (thread_record.preview_text or "").strip().lower()
                        if (preview_lower.startswith("you:") or preview_lower.startswith("bạn:")) \
                                and msg_row[1] == "Customer":
                            skip_reason = None
                            fetch_reason = "page_reply_missing"

            token_log = f"token_now='{vt.get('sidebarTimeText', '')}' token_db='{marker[0] if row and not force_refresh else ''}'"
            if skip_reason:
                stats["skipped_threads"] += 1
                stats["skip_reasons"][skip_reason] = stats["skip_reasons"].get(skip_reason, 0) + 1
                logger.info(f"Stage1 '{name}' decision=skip reason={skip_reason} {token_log}")
                # Refresh the marker so a token that merely changed shape
                # ("Tue" -> "Sep 15") is stored in its current form.
                utime_ms = vt.get("sidebarTimestampMs")
                cursor.execute(
                    """UPDATE threads SET fetched_sidebar_token = ?, fetched_sidebar_kind = ?, fetched_sidebar_utime_ms = ?,
                       fetched_preview_norm = ?, fetched_at = datetime('now', 'localtime') WHERE id = ?""",
                    (vt.get("sidebarTimeText", ""), parsed_time.get("kind", "unknown"),
                     int(utime_ms) if utime_ms else None, ui_norm, thread_record.thread_id),
                )
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

            consecutive_clean_threads = 0
            if row is not None and force_refresh:
                # ``--refresh`` re-opens known threads; keep counting them as
                # cache hits so ``record_fetch`` still reports every thread
                # seen in range (new + already known).
                stats["skipped_threads"] += 1
            stats["fetch_reasons"][fetch_reason] = stats["fetch_reasons"].get(fetch_reason, 0) + 1
            logger.info(f"Stage1 '{name}' decision=fetch reason={fetch_reason} {token_log}")

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
        conn.commit()
        for task in round_tasks:
            keep_discovering = on_task(task)
            # Worker 0 may resolve a provisional card to its canonical ID.
            # Remember both keys so the overlapping next viewport cannot
            # dispatch the same conversation again under the newly found ID.
            if task.record.selected_item_id:
                processed_thread_keys.add(task.record.selected_item_id)
            if keep_discovering is False:
                interrupted = True
                reached_date_limit = True
                break

        # Stage 1 may have updated ``inbox_sort_index`` for cache-hit rows.
        # In parallel mode those same rows can be UPSERTed by Stage 2 as soon
        # as they are dispatched.  Holding the Stage-1 transaction until the
        # entire 180-day sidebar scan completes makes every worker wait on
        # that transaction ID.  Commit each dispatched batch so its order
        # updates remain durable without serializing all detail persistence.
        conn.commit()

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
        if scroll_result.get("progressed"):
            stagnant_sidebar_scrolls = 0
        else:
            stagnant_sidebar_scrolls += 1
            logger.warning(
                "sidebar_scroll_no_progress "
                f"round={scroll_round} consecutive={stagnant_sidebar_scrolls} "
                f"dom_moved={scroll_result.get('dom_moved')}"
            )
            if stagnant_sidebar_scrolls >= MAX_CONSECUTIVE_STAGNANT_SIDEBAR_SCROLLS:
                interrupted = True
                logger.error(
                    "Stopping Stage 1: sidebar did not advance after three guarded "
                    "pagination attempts; leaving this fetch incomplete for retry."
                )
                break

    # END STAGE 1
    conn.commit()
    logger.info(
        f"Stage 1 Complete. Listed {len(collected_threads)} threads in range. "
        f"skipped={stats['skipped_threads']} skip_reasons={stats['skip_reasons']} "
        f"fetch_reasons={stats['fetch_reasons']} time_out_of_order={stats['threads_time_out_of_order']}"
    )

    return {
        "early_exit": False,
        "stats": stats,
        "existing_message_count": existing_message_count,
        "interrupted": interrupted,
    }

def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels_arg, extract_user_info, detect_city,
                 skip_navigation: bool = False, force_refresh: bool = False, refresh_older_than_days: int | None = None,
                 allow_early_exit: bool = True,
                 target_total_messages: int | None = None) -> dict:
    # Shared engine preserves checkpoint/resume and worker-0 semantics even
    # for the non-CDP browser path. No background tab is created here.
    from fb_pipeline.inbox.l3_parallel_fetch import run_parallel_fetch
    return run_parallel_fetch(
        page, page_id, time_range, max_threads, conn, logger, record_fetch,
        ThreadWorkerDeps(extract_ad_id_labels_arg, extract_user_info, detect_city),
        workers=1, inbox_url=f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
        skip_navigation=skip_navigation, force_refresh=force_refresh,
        refresh_older_than_days=refresh_older_than_days, allow_early_exit=allow_early_exit,
        target_total_messages=target_total_messages,
    )

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
