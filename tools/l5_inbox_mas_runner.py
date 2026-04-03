#!/usr/bin/env python3
"""
Inbox MAS Runner — CLI tool for the Sahaja Yoga Facebook Inbox MAS.
code:tool-inbox-mas-001

Fetches new Facebook inbox messages via CDP, processes them through
the ADK multi-agent pipeline (Classify → Respond), and drafts replies
into the composer for human review.

Usage:
    # Single cycle, draft replies for review
    python tools/inbox_mas_runner.py --page-id 119587786260266 --once

    # Backward-compatible flag; still drafts only and never sends
    python tools/inbox_mas_runner.py --page-id 119587786260266 --once --live

    # Continuous polling (5-min intervals)
    python tools/inbox_mas_runner.py --page-id 119587786260266 --poll
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time

try:
    import nest_asyncio
    nest_asyncio.apply()  # Allow asyncio.run() inside Playwright's sync event loop
except ImportError:
    pass  # nest_asyncio optional; install with: pip install nest-asyncio

import requests

# Setup paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from playwright.sync_api import sync_playwright

from fb_pipeline.browser.l3_inbox import extract_ad_id_labels
from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info, parse_page_id
from fb_pipeline.inbox.l3_pipeline import scrape_inbox
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, record_fetch
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session

from tools.l5_inbox_mas_context import setup_llm_env, load_knowledge_context
from tools.l5_inbox_mas_pipeline import run_adk_pipeline, run_adk_batch_pipeline, _sanitize_reply
from tools.l5_inbox_mas_thread import process_single_thread
# Setup logging
os.makedirs(os.path.join(PROJECT_ROOT, 'logs'), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, 'logs', 'inbox_mas_runner.log')),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("inbox_mas_runner")

# --- Constants ---
POLL_INTERVAL = 300  # 5 minutes
CDP_URL = "http://127.0.0.1:9222"









def run_inbox_cycle(page_id: str, dry_run: bool = True,
                    max_threads: int = 5, target_thread: str = None) -> dict:
    """Run one complete inbox cycle: fetch → find unreplied → process → draft.

    Args:
        page_id: Facebook Page ID.
        dry_run: Legacy compatibility flag. Inbox replies remain draft-only.
        max_threads: Max number of threads to process per cycle.

    Returns:
        dict: Summary of the cycle.
    """
    from adk_agents.tools.seeker_tools import find_unreplied_threads

    results = []

    with sync_playwright() as p:
        session = None
        try:
            inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
            session = attach_to_authorized_session(p, page_id, inbox_url)
            cdp_page = session.page
            logger.info("Connected to CDP. Opened new tab.")
        except Exception as e:
            logger.error(f"CDP connection failed: {e}")
            return {"status": "error", "error": f"CDP connection failed: {e}"}

        try:
            conn = get_db_connection()

            # Step 1: Fetch new inbox messages (JIT Pre-Flight Cache check)
            logger.info(f"Step 1: Executing JIT Pre-Flight Cache validation for page {page_id}...")
            scrape_stats = scrape_inbox(
                cdp_page,
                page_id,
                "7d",
                50,
                conn,
                logger,
                record_fetch,
                extract_ad_id_labels,
                extract_user_info,
                detect_city,
            )
            logger.info(f"Scrape stats: {scrape_stats}")

            conn.close()

            # Step 2: Find unreplied threads
            # Step 2: Find unreplied threads
            logger.info("Step 2: Finding unreplied threads...")
            fetch_limit = 200  # Default to 200 internally to ensure we can skip invalid ones
            unreplied = find_unreplied_threads(page_id, limit=fetch_limit)

            if target_thread:
                logger.info(f"Filtering to target thread: {target_thread}")
                unreplied["threads"] = [t for t in unreplied["threads"] if t.get("thread_name") == target_thread]
                unreplied["count"] = len(unreplied["threads"])

            if unreplied["status"] != "success" or unreplied["count"] == 0:
                logger.info("No unreplied threads found. Cycle complete.")
                return {"status": "no_unreplied", "scrape_stats": scrape_stats}

            logger.info(f"Found {unreplied['count']} unreplied thread(s).")

            # Step 2b: Re-navigate to inbox to reset sidebar scroll position.
            # The scrape phase scrolls the sidebar to the date cutoff so thread
            # _5_n1 divs are no longer in the DOM viewport. Re-loading the inbox
            # brings the sidebar back to the top before the navigate-and-type loop.
            logger.info("Step 2b: Re-navigating to inbox to reset sidebar scroll...")
            try:
                cdp_page.goto(inbox_url, wait_until="domcontentloaded", timeout=60000)
                cdp_page.wait_for_selector("div._5_n1", timeout=15000)
                cdp_page.wait_for_timeout(2000)
                logger.info("Inbox sidebar reset successfully.")
            except Exception as e:
                logger.warning(f"Sidebar re-navigation failed (will try anyway): {e}")

            # Step 3: Fetch DB context and assemble Batch Payload
            batch_payload = []
            from adk_agents.tools.seeker_tools import lookup_seeker, get_thread_messages
            from adk_agents.tools.l5_facebook_tools import (
                navigate_to_thread, send_reply_via_cdp, log_auto_reply,
            )
            from adk_agents.tools.l5_stage_tools import evaluate_stage_gate
            from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision
            from tools.l5_telegram_hitl import send_proposal_to_telegram

            for thread in unreplied["threads"]:
                thread_id = thread["thread_id"]
                msg_result = get_thread_messages(thread_id)
                if msg_result["status"] != "success" or msg_result["count"] == 0:
                    logger.warning(f"No messages found for thread {thread['thread_name']}")
                    continue

                # code:tool-inbox-001:last-sender-guard
                # Skip threads where the last message is from Page (admin already replied)
                last_msg = msg_result["messages"][-1] if msg_result["messages"] else None
                if last_msg and last_msg.get("sender") == "Page":
                    logger.info(f"Skipping thread '{thread['thread_name']}': last message is from Page (admin already replied).")
                    continue

                # Also check the sidebar preview for "You:" which indicates an admin
                # reply that hasn't been crawled into the DB yet
                try:
                    sidebar_conn = get_db_connection()
                    preview_row = sidebar_conn.execute(
                        "SELECT last_synced_time FROM threads WHERE id = ?", (thread_id,)
                    ).fetchone()
                    sidebar_conn.close()
                    if preview_row:
                        preview = (preview_row[0] or "").strip().lower()
                        if preview.startswith("you:") or preview.startswith("bạn:"):
                            logger.info(f"Skipping thread '{thread['thread_name']}': sidebar preview shows admin reply ('{preview[:50]}...').")
                            continue
                except Exception as e:
                    logger.warning(f"Could not check sidebar preview for {thread['thread_name']}: {e}")

                seeker = lookup_seeker(thread_id)
                
                # Truncate messages to the last 15 to save context window tokens
                recent_messages = msg_result["messages"][-15:] if len(msg_result["messages"]) > 15 else msg_result["messages"]
                
                batch_payload.append({
                    "thread_id": thread_id,
                    "thread_name": thread["thread_name"],
                    "seeker": seeker,
                    "messages": recent_messages,
                    "full_messages_json": msg_result["messages"],
                    "latest_timestamp": msg_result["messages"][-1].get("timestamp")
                })

                if len(batch_payload) >= max_threads:
                    logger.info(f"Reached max_threads ({max_threads}). Stopping batch assembly.")
                    break

            if not batch_payload:
                logger.info("No valid threads found to batch.")
                return {"status": "no_valid_threads", "scrape_stats": scrape_stats}

            # Step 4: Run Batched ADK Pipeline (1 LLM request)
            logger.info(f"Running Batched ADK Pipeline for {len(batch_payload)} threads...")
            batch_results = run_adk_batch_pipeline(batch_payload)
            
            # Map results by thread_id for O(1) lookup
            llm_replies = {item.get("thread_id"): item for item in batch_results if isinstance(item, dict) and item.get("thread_id")}

            # Step 5: Process generated replies via CDP and Telegram HITL
            for payload in batch_payload:
                thread_id = payload["thread_id"]
                thread_name = payload["thread_name"]
                try:
                    llm_output = llm_replies.get(thread_id)
                    if not llm_output or not llm_output.get("reply_text"):
                        logger.warning(f"No LLM reply generated for {thread_name}")
                        results.append({"status": "no_reply", "thread_name": thread_name})
                        continue

                    reply_text = _sanitize_reply(llm_output.get("reply_text", ""))
                    classification = llm_output.get("classification", "")

                    if not reply_text:
                        logger.warning(f"Sanitized reply is empty for {thread_name}")
                        results.append({"status": "no_reply", "thread_name": thread_name})
                        continue

                    latest_customer_message_timestamp = None
                    for message in reversed(payload["full_messages_json"]):
                        if message.get("sender") == "Customer":
                            latest_customer_message_timestamp = message.get("timestamp")
                            break

                    # Build Conversation Context for Telegram
                    convo_lines = []
                    for msg in payload["messages"]:
                        sender_label = msg.get("sender", "Unknown")
                        convo_lines.append(f"[{sender_label}]: {msg.get('content', '')}")
                    convo_text = "\n".join(convo_lines)
                    
                    # Ensure convo_text fits within Telegram limits (leave ~1000 chars for the proposal)
                    if len(convo_text) > 2500:
                        convo_text = "...(truncated)...\n" + convo_text[-2500:]

                    is_out_of_scope = (reply_text.strip() == "[OUT_OF_SCOPE]")
                    stage_result = {}

                    if is_out_of_scope:
                        logger.info(f"Thread '{thread_name}' classified as OUT_OF_SCOPE. Skipping CDP drafting.")
                        combined_text = f"🚨 [OUT OF SCOPE] Lời nhắn không thuộc phạm vi MAS (Sahaja Yoga):\n\n{convo_text}"
                        
                        msg_id = send_proposal_to_telegram(
                            route="inbox", thread_id=thread_id, proposed_text=combined_text,
                            payload={"classification": classification, "status": "out_of_scope"}
                        )
                        if msg_id:
                            logger.info(f"### ASYNC INBOX: Out-of-scope alert {msg_id} sent to Telegram ###")
                        
                        results.append({"status": "out_of_scope", "thread_name": thread_name, "classification": classification})
                        continue


                    log_auto_reply(thread_id, reply_text, agent_name="responder", escalated=False, dry_run=True, customer_message_timestamp=latest_customer_message_timestamp)
                    
                    stage_result = evaluate_stage_gate(thread_id)
                    if stage_result.get("promoted"):
                        log_mas_decision(page_id, "stage_gate", "thread", thread_id, "promoted", stage_result.get("reason"), dry_run=dry_run, payload=stage_result)

                    combined_text = f"📜 Cuộc hội thoại gần đây:\n{convo_text}\n\n🤖 Đề xuất trả lời (MAS):\n{reply_text}"
                    if len(combined_text) > 3500:
                        combined_text = combined_text[:3500] + "... (truncated)"

                    msg_id = send_proposal_to_telegram(
                        route="inbox", thread_id=thread_id, proposed_text=combined_text,
                        payload={
                            "classification": classification,
                            "msg_messages_json": payload["full_messages_json"],
                            "seeker_dict": payload["seeker"],
                            "proposals": [{"thread_id": thread_id, "seeker_name": thread_name, "message_text": reply_text}]
                        }
                    )

                    if msg_id:
                        logger.info(f"### ASYNC INBOX: Proposal {msg_id} queued to HITL DB ###")

                    results.append({
                        "status": "drafted",
                        "mode": "draft_only",
                        "thread_name": thread_name,
                        "classification": classification,
                        "reply_text": reply_text,
                        "stage_result": stage_result,
                    })

                except Exception as e:
                    logger.error(f"Error processing resulting drafted reply for {thread_name}: {e}")
                    results.append({"status": "error", "error": str(e), "thread_name": thread_name})

        finally:
            try:
                if session:
                    session.close_page()
                    logger.info("Closed CDP tab.")
            except Exception:
                pass

    return {
        "status": "complete",
        "scrape_stats": scrape_stats if 'scrape_stats' in dir() else {},
        "processed": len(results),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Sahaja Yoga Inbox MAS Runner — ADK-powered Facebook inbox handler"
    )
    parser.add_argument(
        "--page-id", required=True,
        help="Facebook Page ID (numeric) or Business Suite URL with asset_id"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single cycle and exit"
    )
    parser.add_argument(
        "--poll", action="store_true",
        help="Run continuously with 5-minute polling interval"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Deprecated compatibility flag. Inbox runner still drafts only and never sends."
    )
    parser.add_argument(
        "--max-threads", type=int, default=5,
        help="Max threads to process per cycle (default: 5)"
    )
    parser.add_argument(
        "--num", type=int, default=None,
        help="Exact number of threads to process per cycle (alias for --max-threads)"
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Polling interval in seconds (default: {POLL_INTERVAL})"
    )
    parser.add_argument(
        "--target-thread", type=str, default=None,
        help="Target a specific thread by name for E2E testing."
    )

    args = parser.parse_args()
    dry_run = True
    if args.live:
        logger.warning("--live is accepted for compatibility but ignored; inbox replies are always drafted for human review.")

    # Parse page_id from URL if needed
    page_id = parse_page_id(args.page_id)

    # Setup LLM environment
    setup_llm_env()

    max_threads_to_use = args.num if args.num is not None else args.max_threads

    mode_str = "[DRAFT-ONLY] Type replies for human review; automation never sends"
    logger.info(f"=== Inbox MAS Runner ===")
    logger.info(f"Page ID: {page_id}")
    logger.info(f"Mode: {mode_str}")
    logger.info(f"Max threads/cycle: {max_threads_to_use}")

    if args.once:
        result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=max_threads_to_use, target_thread=args.target_thread)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.poll:
        logger.info(f"Starting polling loop (interval: {args.interval}s)...")
        while True:
            try:
                result = run_inbox_cycle(page_id, dry_run=dry_run,
                                         max_threads=max_threads_to_use,
                                         target_thread=args.target_thread)
                logger.info(f"Cycle result: {result.get('status', 'unknown')}, "
                           f"processed: {result.get('processed', 0)}")
            except Exception as e:
                logger.error(f"Cycle error: {e}")
            logger.info(f"Sleeping {args.interval}s until next cycle...")
            time.sleep(args.interval)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
