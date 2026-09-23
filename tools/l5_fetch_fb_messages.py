import json
import argparse
import sys
import logging
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.contracts.l1_inbox import (
    EnrichedThreadRecord,
    InboxMessage,
    MasHandoff,
    SeekerInfo,
    ThreadRecord,
    detect_city as shared_detect_city,
    detect_city_smart as shared_detect_city_smart,
    extract_user_info as shared_extract_user_info,
    parse_page_id as shared_parse_page_id,
)
from fb_pipeline.browser.l3_inbox import (
    extract_ad_id_labels as shared_extract_ad_id_labels,
    scrape_inbox_ui as shared_scrape_inbox,
)
from fb_pipeline.browser.inbox.thread_worker import ThreadWorkerDeps
from fb_pipeline.inbox.l3_parallel_fetch import MAX_TOTAL_TABS, run_parallel_fetch
from fb_pipeline.inbox.l3_pipeline import (
    build_thread_record as shared_build_thread_record,
    enrich_thread_record as shared_enrich_thread_record,
    persist_thread_record as shared_persist_thread_record,
)
from fb_pipeline.persistence.l4_sqlite_store import (
    get_db_connection as shared_get_db_connection,
    record_fetch as shared_record_fetch,
    setup_database as shared_setup_database,
    should_fetch as shared_should_fetch,
)
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session, sanitize_storage_state_file
from fb_pipeline.session.l2_facebook_block_gate import FacebookBlockGate, FacebookTemporaryBlockError
from fb_pipeline.contracts.l1_city_llm import detect_city_llm, gather_signals_for_user

from tools.l5_fetch_fb_city_classify import _post_scrape_llm_city_classify
from tools.l5_fetch_fb_db_queries import get_list_unique_user, fetch_message_by_user, get_user_ad_ids
from tools.l5_fetch_fb_ad_resolver import resolve_ad_posts, propagate_city_from_ads

# Ensure logs directory exists
os.makedirs('./logs/', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('./logs/fetch_fb_messages.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("fetch_fb_messages")


def _notify_facebook_block(page_id: str, message: str) -> None:
    """Alert the operator once the run has already been stopped."""
    from tools.l5_telegram_hitl import send_telegram_notification
    send_telegram_notification(
        f"🚨 Facebook fetch STOPPED for Page {page_id}. Meta displayed an unsafe/non-Inbox screen ({message}). "
        "No further fetch cycles will run until an operator restarts them after the restriction clears."
    )

# --- Constants ---
# code:tool-fbmessages-002:city-detect
CITY_KEYWORDS = {
    "Hà Nội": ["Hà Nội", "Ha Noi", "Vương Thừa Vũ", "Khương Đình", "Khương Trung",
                "Thanh Xuân", "Cầu Giấy", "Đống Đa", "Ba Đình", "Hoàn Kiếm",
                "Hai Bà Trưng", "Long Biên", "Hà nội"],
    "TP. Hồ Chí Minh": ["Hồ Chí Minh", "TP.HCM", "TPHCM", "Sài Gòn", "Saigon",
                         "Bình Thạnh", "Quận 1", "Quận 3",
                         "Quận 7", "Thủ Đức", "Gò Vấp", "Tân Bình", "HCM"],
    "Đà Nẵng": ["Đà Nẵng", "Da Nang", "Đà nẵng"],
    "Huế": ["Huế", "Hue"],
    "Hội An": ["Hội An", "Hoi An"],
    "Nghệ An": ["Nghệ An", "Nghe An", "Vinh"],
    "Hải Phòng": ["Hải Phòng", "Hai Phong"],
    "Online": ["online", "Online", "ONLINE", "zoom", "Zoom", "trực tuyến"],
}

CACHE_TTL_SECONDS = 3600  # 1 hour

# --- Utility Functions ---

def parse_page_id(input_str: str) -> str:
    """Extract page ID from a URL or return the input if it's just an ID."""
    page_id = shared_parse_page_id(input_str)
    if page_id == input_str and not re.match(r'^\d+$', input_str):
        logger.warning(f"Input '{input_str}' does not strictly look like a numeric ID or valid URL with asset_id. Proceeding anyway.")
    return page_id


# code:tool-fbmessages-002:cache
def should_fetch(page_id: str, conn: sqlite3.Connection) -> bool:
    """Return True if last fetch was > 1 hour ago or no fetch recorded."""
    return shared_should_fetch(page_id, conn)


def record_fetch(page_id: str, threads_found: int, messages_found: int, conn: sqlite3.Connection):
    """Record a fetch event in the log."""
    return shared_record_fetch(page_id, threads_found, messages_found, conn)


# code:inbox-fetch-qa-001:loop-stop-scope
def _fetch_stop_error(stats: dict) -> str | None:
    """Return a loop-stop error only for unsafe persisted conversation data.

    A worker verification failure or an incomplete fetch is diagnostic and is
    safe to retry on the next scheduled pass. Fetch QA's sidebar-order check
    is also diagnostic. The loop must stop only when QA directly finds that
    the persisted newest message does not match Facebook's message history,
    or that the message belongs to the other sender (Facebook actor/UID).
    """
    qa = stats.get("qa") or {}
    for row in qa.get("qa2") or []:
        if row.get("verdict") == "hard":
            return "fetch_qa_message_history_or_sender_mismatch"
    return None


# code:tool-fbmessages-002:user-extract
def extract_user_info(messages: list, thread_name: str, ad_context: str = "") -> dict:
    """Extract phone, email, FB URL from message content."""
    return shared_extract_user_info(messages, thread_name, ad_context)


# code:tool-fbmessages-003:parse-ad-ids
def parse_ad_ids(text: str) -> list:
    """Extract unique ad_id numbers from text containing ad_id.XXXXX patterns."""
    raw = re.findall(r'ad_id\.?(\d{5,})', text)
    # Deduplicate while preserving order
    return list(dict.fromkeys(raw))


# code:tool-fbmessages-003:extract-labels
def extract_ad_id_labels(page) -> list:
    """Extract ad_id values from the Labels section in thread detail panel."""
    return shared_extract_ad_id_labels(page)


# code:tool-fbmessages-002:city-detect
def detect_city(ad_context: str, page_messages: list) -> str:
    """Cheap keyword-only city guess used while scraping.

    Retrospective [2026-09-17]: this used to be the LLM-first variant, so every
    persisted thread made a synchronous LLM call inside the crawl. Scraping is
    scraping; the LLM city/program pass is a separate job
    (``--action classify_city_llm`` / scheduler ``[CLASSIFY]``) that runs
    without the browser and without the ``inbox_fetch_cli`` lock.
    """
    return shared_detect_city(ad_context, page_messages)


# --- DB Setup ---

def setup_database(conn: sqlite3.Connection):
    """Create all required tables (idempotent)."""
    return shared_setup_database(conn, logger=logger)


def get_db_connection(memory_dir: str = None) -> sqlite3.Connection:
    """Get a connection to the FrankenSQLite DB."""
    return shared_get_db_connection(memory_dir, logger=logger)



def _to_legacy_dict(value):
    if is_dataclass(value):
        return asdict(value)
    return value


def build_thread_record(page_id: str, visible_thread: dict) -> dict:
    """Parse a visible inbox thread card into a normalized thread record."""
    return _to_legacy_dict(shared_build_thread_record(page_id, visible_thread))


def enrich_thread_record(thread_record: dict, js_messages: list, ad_context: str = "",
                         fb_url: str = "", ad_ids: list | None = None) -> dict:
    """Enrich a parsed thread with seeker fields, city prediction, and MAS payload."""
    shared_thread_record = ThreadRecord(**thread_record) if isinstance(thread_record, dict) else thread_record
    enriched = shared_enrich_thread_record(
        shared_thread_record,
        js_messages,
        extract_user_info=extract_user_info,
        detect_city=None,
        ad_context=ad_context,
        fb_url=fb_url,
        ad_ids=ad_ids,
    )
    return _to_legacy_dict(enriched)


def _to_shared_enriched_thread_record(thread_record):
    if not isinstance(thread_record, dict):
        return thread_record
    mas_handoff = thread_record.get("mas_handoff")
    shared_mas_handoff = None
    if mas_handoff:
        seeker = mas_handoff.get("seeker") or {}
        shared_mas_handoff = MasHandoff(
            thread_id=mas_handoff["thread_id"],
            thread_name=mas_handoff["thread_name"],
            page_id=mas_handoff["page_id"],
            fb_url=mas_handoff["fb_url"],
            seeker=SeekerInfo(
                name=seeker.get("name", ""),
                phone=seeker.get("phone"),
                email=seeker.get("email"),
                city=seeker.get("city", "Unknown"),
                program_code=seeker.get("program_code"),
                lead_stage=seeker.get("lead_stage", "Intake"),
            ),
            ad_context=mas_handoff.get("ad_context", ""),
            ad_ids=list(mas_handoff.get("ad_ids") or []),
            messages=[InboxMessage(**message) for message in mas_handoff.get("messages") or []],
            temperature=mas_handoff.get("temperature", "warm"),
            cool_step=mas_handoff.get("cool_step", 0),
        )
    return EnrichedThreadRecord(
        page_id=thread_record["page_id"],
        thread_id=thread_record["thread_id"],
        thread_name=thread_record["thread_name"],
        preview_text=thread_record["preview_text"],
        thread_lines=list(thread_record.get("thread_lines") or []),
        dom_index=thread_record.get("dom_index", 0),
        sidebar_time_text=thread_record.get("sidebar_time_text", ""),
        sidebar_time_kind=thread_record.get("sidebar_time_kind", ""),
        sidebar_time_source=thread_record.get("sidebar_time_source", ""),
        sidebar_identity_key=thread_record.get("sidebar_identity_key", ""),
        selected_item_id=thread_record.get("selected_item_id", ""),
        fb_url=thread_record.get("fb_url", ""),
        ad_context=thread_record.get("ad_context", ""),
        ad_ids=list(thread_record.get("ad_ids") or []),
        user_info=dict(thread_record.get("user_info") or {}),
        city=thread_record.get("city", "Unknown"),
        program_code=thread_record.get("program_code"),
        messages=[InboxMessage(**message) for message in thread_record.get("messages") or []],
        mas_handoff=shared_mas_handoff,
    )


def persist_thread_record(conn: sqlite3.Connection, thread_record: dict) -> dict:
    """Persist one parsed+enriched thread record to FrankenSQLite."""
    return shared_persist_thread_record(
        conn,
        _to_shared_enriched_thread_record(thread_record),
        detect_city=None,
    )


# --- Shared scraping helper ---
# code:tool-fbmessages-001:scrape-inbox

def _scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn,
                  skip_navigation: bool = False, force_refresh: bool = False, refresh_older_than_days: int | None = None,
                  allow_early_exit: bool = True,
                  target_total_messages: int | None = None) -> dict:
    """Core scraping loop: scroll sidebar, click threads, extract messages."""
    return shared_scrape_inbox(
        page,
        page_id,
        time_range,
        max_threads,
        conn,
        logger=logger,
        record_fetch=record_fetch,
        extract_ad_id_labels_arg=extract_ad_id_labels,
        extract_user_info=extract_user_info,
        detect_city=None,
        skip_navigation=skip_navigation,
        force_refresh=force_refresh, refresh_older_than_days=refresh_older_than_days,
        allow_early_exit=allow_early_exit,
        target_total_messages=target_total_messages,
    )


# --- Post-scrape LLM city classification ---
# code:tool-citydetect-001:post-scrape-integration



# --- Action: fetch_messages (Playwright browser fetch) ---
# code:tool-fbmessages-001:main

# code:inbox-parallel-fetch-001:cli
def _clamp_workers(workers: int, log=None) -> int:
    """Clamp --workers to [0, 3]: worker 0 is the orchestrator tab itself."""
    log = log or logger
    if workers < 0:
        log.warning(f"--workers {workers} is below the minimum; clamping to 0.")
        return 0
    if workers > MAX_TOTAL_TABS:
        log.warning(f"--workers {workers} exceeds the maximum; clamping to {MAX_TOTAL_TABS}.")
        return MAX_TOTAL_TABS
    return workers


# code:inbox-activity-lock-001:fetch-cli
SCHEDULER_IDLE_WAIT_S = float(os.environ.get("FB_FETCH_WAIT_FOR_SCHEDULER_S", "900"))


def fetch_messages(page_input: str, credential_id: str, time_range: str = "7d",
                   show_browser: bool = True, force_refresh: bool = False, refresh_older_than_days: int | None = None,
                   max_threads: int = 50, use_cdp: bool = False, allow_early_exit: bool = True,
                   target_total_messages: int | None = None, workers: int = 1,
                   classify_city: bool = False, skip_qa: bool = False, resume_run: str | None = None) -> dict:
    """Public entry point. In ``--cdp`` mode the shared Chrome is coordinated
    with the scheduler through ``l2_activity_lock``: wait for any running
    scheduler browser cycle to finish, then publish ``inbox_fetch_cli`` (kept
    alive by this tool's own log output) so scheduler cycles skip until we exit.

    ``classify_city`` runs the LLM city/program pass for the threads this run
    persisted *after* the lock is released. Retrospective [2026-09-17]: the
    pass took 48 min for 547 users while still holding ``inbox_fetch_cli``,
    so the scheduler skipped [FETCH]/[REPLY]/[WARMUP] the whole time although
    Chrome had been idle since Stage 2 ended.
    """
    from fb_pipeline.session.l2_activity_lock import (
        ROLE_FETCH_CLI, ROLE_SCHEDULER_BROWSER, hold_activity, wait_until_idle,
    )
    workers = _clamp_workers(workers)
    if resume_run and not use_cdp:
        raise ValueError('--resume requires --cdp')
    page_id = parse_page_id(page_input)
    kwargs = dict(show_browser=show_browser, force_refresh=force_refresh, refresh_older_than_days=refresh_older_than_days, max_threads=max_threads,
                  use_cdp=use_cdp, allow_early_exit=allow_early_exit,
                  target_total_messages=target_total_messages, workers=workers, skip_qa=skip_qa, resume_run=resume_run)
    if not use_cdp:
        result = _fetch_messages_impl(page_input, credential_id, time_range, **kwargs)
    else:
        if not wait_until_idle(ROLE_SCHEDULER_BROWSER, page_id, SCHEDULER_IDLE_WAIT_S, logger):
            return {"success": False,
                    "error": f"Scheduler browser cycle still active after {SCHEDULER_IDLE_WAIT_S:.0f}s; refusing to share Chrome."}
        with hold_activity(ROLE_FETCH_CLI, page_id, logger,
                           label=f"fetch_messages {time_range} workers={workers}",
                           log_paths=["./logs/fetch_fb_messages.log"]):
            result = _fetch_messages_impl(page_input, credential_id, time_range, **kwargs)

    if classify_city and result.get("success"):
        result = _classify_after_fetch(result, page_id)
    return result


# code:tool-citydetect-001:post-fetch-optin
def _classify_after_fetch(result: dict, page_id: str) -> dict:
    """Opt-in LLM pass over the threads a fetch just persisted (lock already released)."""
    stats = (result.get("data") or {}).get("stats") or {}
    thread_ids = stats.get("processed_thread_ids") or []
    if not thread_ids:
        logger.info("Skipping city classification: this run persisted no threads.")
        stats["llm_city"] = {"skipped": "no_processed_threads"}
        return result
    conn = get_db_connection(os.path.join("memory", "agent_memory"))
    try:
        stats["llm_city"] = _post_scrape_llm_city_classify(conn, page_id, thread_ids)
    finally:
        conn.close()
    return result


def _fetch_messages_impl(page_input: str, credential_id: str, time_range: str = "7d",
                         show_browser: bool = True, force_refresh: bool = False, refresh_older_than_days: int | None = None,
                         max_threads: int = 50, use_cdp: bool = False, allow_early_exit: bool = True,
                         target_total_messages: int | None = None, workers: int = 1, skip_qa: bool = False, resume_run: str | None = None) -> dict:
    page_id = parse_page_id(page_input)
    logger.info(f"Using Page ID: {page_id}, Time Range: {time_range}")
    
    fetch_started_at = datetime.now()

    memory_dir = os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    credential_path = os.path.join(memory_dir, f"fb_credential_{credential_id}.json")

    # --- Mode 1: Direct CDP scraping (--cdp flag) ---
    # code:tool-fbmessages-001:cdp-direct
    if use_cdp:
        logger.info("CDP Direct Mode: Connecting to Chrome at http://127.0.0.1:9222")
        conn = get_db_connection(memory_dir)

        with sync_playwright() as p:
            session = None
            block_gate = FacebookBlockGate()
            try:
                session = attach_to_authorized_session(
                    p,
                    page_id,
                    f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
                    prefer_new_tab=False,
                )
                logger.info(
                    f"Connected to CDP session. {'Reused existing inbox tab' if session.selected_existing_tab else 'Opened new tab'} "
                    f"(total tabs: {len(session.context.pages)})."
                )

                diag_dir = "./logs/diagnostic/cdp-direct"
                os.makedirs(diag_dir, exist_ok=True)
                run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                logger.info("Starting direct scrape...")
                # A prior run may already have been blocked. Check before
                # opening any worker tabs or touching the sidebar again.
                block_gate.trip_if_present(session.page)
                # The CDP attachment waits only for DOM readiness.  Reload through the
                # scraper before collecting so Meta has initialized the complete
                # virtualized sidebar rather than a stale short viewport.
                logger.info(f"Fetch: {workers} tab(s), including orchestrator/worker 0.")
                deps = ThreadWorkerDeps(
                    extract_ad_id_labels=extract_ad_id_labels,
                    extract_user_info=extract_user_info,
                    detect_city=None,
                    block_gate=block_gate,
                )
                stats = run_parallel_fetch(
                    session.page, page_id, time_range, max_threads, conn, logger, record_fetch, deps,
                    workers=workers,
                    inbox_url=f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
                    skip_navigation=True,
                    force_refresh=force_refresh, refresh_older_than_days=refresh_older_than_days,
                    allow_early_exit=allow_early_exit,
                    target_total_messages=target_total_messages,
                    memory_dir=memory_dir,
                    resume_run=resume_run,
                )

                # City/program classification is not part of message
                # ingestion: see ``fetch_messages`` (opt-in ``--classify-city``,
                # runs after the browser lock is released).
                stats["llm_city"] = {"skipped": "decoupled"}
                
                # Fetch QA
                if not skip_qa:
                    logger.info("Running Fetch QA...")
                    try:
                        from fb_pipeline.inbox.l3_fetch_qa import run_fetch_qa
                        # code:inbox-fetch-qa-001:reattach
                        # The orchestrator tab can be closed from outside
                        # (Chrome tab discard, an operator closing tabs) while
                        # Stage 2 drains.  QA needs a live sidebar, so attach a
                        # fresh inbox tab like a worker would instead of
                        # reporting a crash as a QA failure.
                        qa_page = session.page
                        if qa_page.is_closed():
                            logger.warning("Orchestrator tab is closed; re-attaching an inbox tab for Fetch QA.")
                            session = attach_to_authorized_session(
                                p, page_id, f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
                                prefer_new_tab=False,
                            )
                            qa_page = session.page
                            qa_page.goto(f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
                                         wait_until="networkidle", timeout=60000)
                            from fb_pipeline.browser.inbox.scroll_helpers import wait_for_inbox_shell, wait_for_initial_threads
                            wait_for_inbox_shell(qa_page, logger, timeout_ms=30000)
                            wait_for_initial_threads(qa_page, logger, timeout_ms=30000)
                        qa_res = run_fetch_qa(page_id, fetch_started_at, qa_page, conn, logger)
                        stats["qa"] = qa_res
                    except Exception as qa_e:
                        logger.error(f"Fetch QA failed: {qa_e}")
                else:
                    logger.info("Skipping Fetch QA as requested.")
                    try:
                        conn.execute('''
                            UPDATE fetch_log 
                            SET qa_status='skipped'
                            WHERE id = (SELECT id FROM fetch_log WHERE page_id=? ORDER BY fetched_at DESC LIMIT 1)
                        ''', (page_id,))
                        conn.commit()
                    except Exception:
                        pass

                with open(os.path.join(diag_dir, f"run_{run_ts}.log"), "w") as f:
                    f.write(f"CDP Direct Scrape: {run_ts}\n")
                    f.write(f"Page ID: {page_id}, Time Range: {time_range}, Max Threads: {max_threads}\n")
                    f.write(f"Stats: {json.dumps(stats, indent=2)}\n")

                conn.close()
                session.close_page()
                logger.info(f"CDP Direct: Saved to FrankenSQLite. Stats: {stats}")
                # code:inbox-fetch-qa-001:exit-gate
                # Only a QA-2 hard reject means the stored conversation's
                # message history or sender disagrees with Facebook. Worker
                # verification/incomplete-run and QA-1 sidebar diagnostics
                # remain visible in stats but must not stop the retry loop.
                qa_status = (stats.get("qa") or {}).get("qa_status")
                fetch_error = _fetch_stop_error(stats)
                if fetch_error:
                    logger.critical(f"FETCH_STOP_GATE: {fetch_error} (qa_status={qa_status}, "
                                    f"failed_threads={stats.get('failed_threads')}, "
                                    f"abandoned={stats.get('tasks_abandoned')}, stranded={stats.get('tasks_stranded')})")
                elif stats.get("failed_threads") or stats.get("fetch_complete") is False or qa_status == "failed":
                    logger.warning("FETCH_RETRYABLE_DIAGNOSTIC: qa_status=%s failed_threads=%s "
                                   "fetch_complete=%s abandoned=%s stranded=%s",
                                   qa_status, stats.get("failed_threads"), stats.get("fetch_complete"),
                                   stats.get("tasks_abandoned"), stats.get("tasks_stranded"))
                return {"success": fetch_error is None, "method": "cdp_direct",
                        "error": fetch_error, "data": {"stats": stats}}
            except FacebookTemporaryBlockError as e:
                logger.critical("FACEBOOK_FETCH_SAFETY_GATE: %s", e)
                _notify_facebook_block(page_id, str(e))
                conn.close()
                if session:
                    session.close_page()
                return {"success": False, "error": "facebook_temporarily_blocked", "detail": str(e)}
            except Exception as e:
                logger.error(f"CDP Direct scrape failed: {e}")
                try:
                    diag_dir = "./logs/diagnostic/cdp-direct"
                    os.makedirs(diag_dir, exist_ok=True)
                    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    with open(os.path.join(diag_dir, f"error_{run_ts}.log"), "w") as f:
                        f.write(f"CDP Direct Error: {e}\n")
                        f.write(f"Page ID: {page_id}, Time Range: {time_range}\n")
                except Exception:
                    pass
                conn.close()
                if session:
                    session.close_page()
                return {"success": False, "error": str(e)}

    # --- Mode 2: Legacy credential capture (no credential file, no --cdp) ---
    with sync_playwright() as p:
        if not os.path.exists(credential_path):
            logger.info(f"Credential '{credential_id}' not found at {credential_path}.")
            logger.info("Attempting to connect to existing Chrome instance via CDP at http://127.0.0.1:9222")

            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                session = None
                try:
                    inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
                    session = attach_to_authorized_session(p, page_id, inbox_url, prefer_new_tab=False)

                    logger.info(f"Navigating to {inbox_url} to ensure authenticated state is captured properly...")

                    diagnostic_dir = f"./logs/diagnostic/iteration-0001"
                    os.makedirs(diagnostic_dir, exist_ok=True)
                    with open(os.path.join(diagnostic_dir, "consoleLog.txt"), "a") as f:
                        f.write(f"Attempt {attempt}: Navigated to {inbox_url}\n")
                    with open(os.path.join(diagnostic_dir, "DOM.txt"), "w") as f:
                        f.write(session.page.content())

                    logger.info(f"Saving browser state to {credential_path}")
                    session.context.storage_state(path=credential_path)

                    try:
                        sanitize_storage_state_file(credential_path)
                        logger.info("Successfully sanitized saved CDP state.")
                    except Exception as ex:
                        logger.warning(f"Failed to sanitize cookie state: {ex}")

                    logger.info("Successfully fetched and saved authenticated browser state via CDP session.")
                    session.close_page()
                    session.browser.close()
                    return {"success": True, "method": "cdp_capture", "message": "Authenticated state saved. Run again to fetch headless."}
                except Exception as e:
                    logger.warning(f"Attempt {attempt} failed: {e}. Retrying Ralph loop...")
                    if session:
                        session.close_page()
                    if attempt == max_attempts:
                        logger.error(f"Failed to connect via CDP after {max_attempts} attempts.")
                        logger.error("Make sure Chrome is running with --remote-debugging-port=9222")
                        return {"success": False, "error": str(e)}
        else:
            # --- Mode 3: Headful/Headless fetch with saved credentials ---
            conn = get_db_connection(memory_dir)

            logger.info(f"Credential '{credential_id}' found. Launching {'headful' if show_browser else 'headless'} browser.")
            if workers != 1:
                logger.info("--workers applies to --cdp mode only; running sequentially")
            try:
                browser = p.chromium.launch(headless=not show_browser)
                context = browser.new_context(
                    storage_state=credential_path,
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()

                stats = _scrape_inbox(page, page_id, time_range, max_threads, conn, force_refresh=force_refresh, refresh_older_than_days=refresh_older_than_days, allow_early_exit=allow_early_exit, target_total_messages=target_total_messages)

                stats["llm_city"] = {"skipped": "decoupled"}

                conn.close()
                logger.info(f"Storage: Saved output to FrankenSQLite DB. Stats: {stats}")

                context.close()
                browser.close()
                return {"success": True, "method": "headless_fetch",
                        "error": None,
                        "data": {"stats": stats}}
            except Exception as e:
                import traceback
                logger.error(f"Error while waiting for inbox or extracting messages: {e}\n{traceback.format_exc()}")
                return {"success": False, "error": str(e)}





# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Fetch Facebook Business Suite Inbox Messages")
    parser.add_argument("--pageId", required=True, help="Facebook Page ID or full Meta Business Suite URL containing asset_id")
    parser.add_argument("--credential", default="default", help="Credential ID to load/save state.")
    parser.add_argument("--time_range", default="7d", help="Time range to fetch (1d, 7d, 30d, 90d).")
    parser.add_argument("--headless", action="store_true", help="Launch the browser invisibly (headless).")
    parser.add_argument("--action", default="fetch_messages",
                        choices=["fetch_messages", "get_list_unique_user", "fetch_message_by_user",
                                 "get_user_ad_ids", "resolve_ad_posts", "propagate_city",
                                 "classify_city_llm"],
                        help="Action to perform.")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch every thread in range, ignoring the 'fetched' sidebar-token marker and the preview cache.")
    parser.add_argument("--refresh-older-than", type=int, default=None, metavar="DAYS",
                        help="Re-fetch threads whose 'fetched' marker is older than DAYS days even if their sidebar token is unchanged.")
    parser.add_argument("--userId", default=None, help="User ID (thread_id, phone, or email) for fetch_message_by_user.")
    parser.add_argument("--maxThreads", type=int, default=1000, help="Maximum number of threads to sync (default: 1000).")
    parser.add_argument("--targetMessages", type=int, default=None,
                        help="Stop after the page has this many messages in the local database.")
    parser.add_argument("--cdp", action="store_true", help="Scrape directly via CDP connection to Chrome on port 9222 (no cookie export/import).")
    parser.add_argument("--no-early-exit", action="store_true", help="Disable the targeted early-exit algorithm, allowing deep retroactive UI scrolls.")
    parser.add_argument("--workers", "--worker", type=int, default=None,
                        help="Fetch workers: 0 uses only the orchestrator tab; 1 uses it sequentially; max: 3. "
                             "For classify_city_llm, default: 10.")
    parser.add_argument("--resume", default=None, metavar="PATH", help="Resume a parallel-fetch checkpoint using the original range/options.")
    parser.add_argument("--skip-qa", action="store_true", help="Skip the QA check after fetching.")
    parser.add_argument("--classify-city", action="store_true",
                        help="After fetch_messages, run the LLM city/program pass on the threads this run "
                             "persisted (browser lock already released). Default: off — use "
                             "--action classify_city_llm or the scheduler [CLASSIFY] route instead.")
    parser.add_argument("--all-users", action="store_true",
                        help="classify_city_llm: re-classify every user of the page instead of only stale ones.")
    parser.add_argument("--missing-real-name", action="store_true",
                        help="classify_city_llm: explicitly retry City/Program/Name/Phone extraction only for DM seekers whose real_name is empty.")

    args = parser.parse_args()
    page_id = parse_page_id(args.pageId)
    
    logger.debug("Starting fetch_fb_messages.py execution...")
    logger.debug("# code:tool-fbmessages-003:main")
    
    if args.action == "fetch_messages":
        show_browser_flag = not args.headless
        workers = _clamp_workers(args.workers if args.workers is not None else 1, logger)
        result = fetch_messages(args.pageId, args.credential, args.time_range,
                                show_browser=show_browser_flag, force_refresh=args.refresh, refresh_older_than_days=args.refresh_older_than,
                                max_threads=args.maxThreads, use_cdp=args.cdp,
                                allow_early_exit=not args.no_early_exit,
                                target_total_messages=args.targetMessages,
                                workers=workers, classify_city=args.classify_city, skip_qa=args.skip_qa, resume_run=args.resume)
    elif args.action == "get_list_unique_user":
        result = get_list_unique_user(page_id, args.time_range)
    elif args.action == "fetch_message_by_user":
        if not args.userId:
            result = {"success": False, "error": "--userId is required for fetch_message_by_user action."}
        else:
            result = fetch_message_by_user(page_id, args.userId)
    elif args.action == "get_user_ad_ids":
        result = get_user_ad_ids(page_id)
    elif args.action == "resolve_ad_posts":
        result = resolve_ad_posts(page_id, use_cdp=args.cdp)
    elif args.action == "propagate_city":
        result = propagate_city_from_ads(page_id)
    elif args.action == "classify_city_llm":
        conn = get_db_connection()
        llm_result = _post_scrape_llm_city_classify(
            conn, page_id, only_stale=not args.all_users and not args.missing_real_name,
            only_missing_real_name=args.missing_real_name, workers=args.workers if args.workers is not None else 10,
        )
        conn.close()
        result = {"success": True, "action": "classify_city_llm", **llm_result}
    else:
        result = {"success": False, "error": f"Unknown action: {args.action}"}
    
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    if not result.get("success", False):
        if result.get("error") == "fetch_qa_message_history_or_sender_mismatch":
            sys.exit(76)
        if result.get("error") in ("fetch_verification_failed", "fetch_qa_failed", "fetch_incomplete"):
            # Backward compatibility for callers that still return the former
            # diagnostic errors: do not let them stop the scheduler loop.
            return
        sys.exit(75 if result.get("error") == "facebook_temporarily_blocked" else 1)

if __name__ == "__main__":
    main()
