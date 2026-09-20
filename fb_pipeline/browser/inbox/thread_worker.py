"""Per-thread Stage 2 worker body: locate, verify, extract, enrich, persist.

Moved out of `fb_pipeline/browser/l3_inbox.py` Stage 2 loop as part of the
inbox parallel-fetch refactor. Behaviour is unchanged; this is the pure
function of (page, ThreadRecord, conn) described in
docs/architect/inbox-fetch-pipeline.md §1.2.

# code:inbox-parallel-fetch-001:thread-worker
"""
import time
from dataclasses import dataclass
from typing import Callable

from fb_pipeline.contracts.l1_inbox_tasks import ThreadResult, ThreadTask
from fb_pipeline.session.l2_facebook_block_gate import FacebookBlockGate

from .integrity_validator import validate_thread_integrity
from .thread_detail_parser import (
    extract_ad_context,
    extract_ad_id_labels,
    extract_thread_messages,
    scroll_up_message_panel,
    verify_thread_switch,
)
from .thread_locator import locate_thread, locate_thread_in_sidebar


@dataclass
class ThreadWorkerDeps:
    extract_ad_id_labels: Callable
    extract_user_info: Callable
    detect_city: Callable
    block_gate: FacebookBlockGate | None = None


def process_thread_task(page, conn, task: ThreadTask, deps: ThreadWorkerDeps, logger,
                         is_first_thread: bool = False, page_id: str = "") -> ThreadResult:
    """``page_id`` defaults to "" so Phase 1 callers/tests (sequential
    ``scrape_inbox``, which has no worker tab of its own) keep locating via
    L1 (``locate_thread_in_sidebar``) only. Parallel-fetch workers pass their
    page id to run the full L0->L1 ladder (``locate_thread``) whenever the
    task carries a PSID (``record.selected_item_id`` or ``psid_hint``).
    """
    from fb_pipeline.inbox.l3_pipeline import canonical_thread_id, enrich_thread_record, persist_thread_record

    start = time.monotonic()
    thread_record = task.record
    name = thread_record.thread_name
    record_page_id = thread_record.page_id

    if deps.block_gate:
        deps.block_gate.trip_if_present(page)

    has_psid = bool(thread_record.selected_item_id or task.psid_hint)
    if has_psid and not thread_record.selected_item_id:
        # Let verify_thread_switch compare the post-click URL against the
        # PSID we are targeting (method=selected_item_id / _exact).
        thread_record.selected_item_id = task.psid_hint
    if page_id and has_psid:
        locate_result = locate_thread(page, page_id, task, logger, absolute_top=task.absolute_top)
    else:
        locate_result = locate_thread_in_sidebar(page, task, logger)

    if not locate_result.clicked:
        return ThreadResult(
            ordinal=task.ordinal,
            thread_id="",
            status="click_verify_failed",
            locate_method=locate_result.method,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        page.wait_for_selector('div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]', timeout=10000)
        page.wait_for_timeout(1000)
    except Exception:
        page.wait_for_timeout(4000)

    if deps.block_gate:
        deps.block_gate.trip_if_present(page)

    fb_url, verified = verify_thread_switch(
        page, logger, name, locate_result.prev_fb_url, locate_result.pre_click_fingerprint,
        is_first_thread, thread_record
    )
    if not verified:
        return ThreadResult(
            ordinal=task.ordinal,
            thread_id="",
            status="click_verify_failed",
            locate_method=locate_result.method,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    # Sidebar cards expose `href="#"` until they are selected, so the
    # selected-item ID confirmed above is the first stable Facebook
    # identity we can persist. Recompute the provisional card ID here;
    # otherwise a changed preview or timestamp creates a duplicate row.
    if fb_url:
        thread_record.selected_item_id = fb_url
        thread_record.thread_id = canonical_thread_id(record_page_id, fb_url)

    page.wait_for_timeout(1000)

    ad_context = extract_ad_context(page)
    scroll_up_message_panel(page, logger, name)

    messages_list = extract_thread_messages(page)
    is_valid = validate_thread_integrity(messages_list, logger)

    if len(messages_list) == 0:
        logger.warning(f"No message bubbles found for thread '{name}'.")
        return ThreadResult(
            ordinal=task.ordinal,
            thread_id=thread_record.thread_id,
            status="no_messages",
            locate_method=locate_result.method,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    ad_ids = deps.extract_ad_id_labels(page) if callable(deps.extract_ad_id_labels) else extract_ad_id_labels(page)

    enriched_record = enrich_thread_record(
        thread_record,
        messages_list,
        extract_user_info=deps.extract_user_info,
        ad_context=ad_context,
        fb_url=fb_url,
        ad_ids=ad_ids,
    )
    persist_result = persist_thread_record(conn, enriched_record)
    messages_added = persist_result.get("messages_added", 0) if isinstance(persist_result, dict) else 0

    return ThreadResult(
        ordinal=task.ordinal,
        thread_id=thread_record.thread_id,
        status="persisted",
        messages_added=messages_added,
        locate_method=locate_result.method,
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )


__all__ = ["ThreadWorkerDeps", "process_thread_task"]
