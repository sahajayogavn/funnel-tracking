#!/usr/bin/env python3
"""
Inbox MAS Runner — CLI tool for the Sahaja Yoga Facebook Inbox MAS.
code:tool-inbox-mas-001

Fetches new Facebook inbox messages via CDP, processes them through
the ADK multi-agent pipeline (Classify → Respond), and optionally
replies via CDP.

Usage:
    # Single cycle, dry-run (type reply but don't send)
    python tools/inbox_mas_runner.py --page-id 119587786260266 --once

    # Single cycle, live (send replies)
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


# code:tool-inbox-mas-001:knowledge-loader
KNOWLEDGE_FILES = [
    "memory/SOUL.md",
    "memory/agent_memory/faq.md",
    "memory/agent_memory/lop-hoc.md",
    "memory/agent_memory/su-kien.md",
    "memory/research.md",
    "memory/mas_strategy.md",
]


def load_knowledge_context() -> str:
    """Load markdown knowledge files into a single prompt context string."""
    sections = []
    for relative_path in KNOWLEDGE_FILES:
        absolute_path = os.path.join(PROJECT_ROOT, relative_path)
        try:
            with open(absolute_path, "r", encoding="utf-8") as f:
                sections.append(f"## Source: {relative_path}\n{f.read().strip()}")
        except FileNotFoundError:
            logger.warning(f"Knowledge file missing: {relative_path}")
        except Exception as exc:
            logger.warning(f"Knowledge file load failed ({relative_path}): {exc}")
    return "\n\n".join(section for section in sections if section)


def setup_llm_env():
    """Configure LLM environment variables for ADK/LiteLLM."""
    # Load credentials from env_manager
    from tools.env_manager import load_credentials
    creds = load_credentials()

    # Set OpenAI-compatible vars for LiteLLM
    api_base = creds.get("OPENAI_COMPATIBLE_URL", os.environ.get("OPENAI_COMPATIBLE_URL", ""))
    api_key = creds.get("OPENAI_COMPATIBLE_KEY", os.environ.get("OPENAI_COMPATIBLE_KEY", ""))

    if api_base:
        os.environ["OPENAI_API_BASE"] = api_base
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    logger.info(f"LLM configured: base={api_base[:30]}... model={os.environ.get('ADK_MODEL', 'openai/gpt-5.4')}")


def run_adk_pipeline(thread_messages: list, seeker_context: dict) -> dict:
    """Run the ADK classifier + responder pipeline on a thread.

    Args:
        thread_messages: List of messages [{sender, content, timestamp}].
        seeker_context: Seeker profile dict from CRM lookup.

    Returns:
        dict: {classification, reply_text} from the pipeline.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from adk_agents.agent import root_agent

    # Format conversation for the agent
    conversation_text = "\n".join([
        f"[{m['sender']}] {m['content']}"
        for m in thread_messages
        if m.get('content')
    ])
    seeker_text = json.dumps(seeker_context, ensure_ascii=False, indent=2)
    knowledge_context = load_knowledge_context()

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="sahajayoga_inbox",
        session_service=session_service,
    )
    session = asyncio.run(
        session_service.create_session(
            app_name="sahajayoga_inbox",
            user_id="inbox_runner",
            state={
                "thread_messages": conversation_text,
                "seeker_context": seeker_text,
                "knowledge_context": knowledge_context,
            },
        )
    )

    prompt = (
        "Process this Facebook inbox thread using the provided session state. "
        "Use thread_messages, seeker_context, and knowledge_context when relevant."
    )
    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=prompt)]
    )

    result = {
        "classification": "",
        "reply_text": "",
        "thread_messages": conversation_text,
        "seeker_context": seeker_text,
        "knowledge_context": knowledge_context,
    }

    for event in runner.run(
        user_id="inbox_runner",
        session_id=session.id,
        new_message=user_msg
    ):
        if hasattr(event, 'content') and event.content and event.content.parts:
            text = event.content.parts[0].text
            if hasattr(event, 'author') and event.author == "MessageClassifier":
                result["classification"] = text
            elif hasattr(event, 'author') and event.author == "Responder":
                result["reply_text"] = text
            else:
                result["reply_text"] = text

    return result


# code:tool-inbox-mas-001:telegram-notify
def _notify_telegram_if_needed(thread_name: str, classification: str, reply_text: str):
    trigger_terms = ("escalate", "register", "phone", "urgent")
    classification_text = (classification or "").lower()
    if not any(term in classification_text for term in trigger_terms):
        return

    try:
        from tools.env_manager import load_credentials

        creds = load_credentials()
        bot_token = creds.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = creds.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            logger.warning("Telegram notification skipped: missing bot token or chat id")
            return

        message_text = (
            f"Inbox MAS alert\n"
            f"Thread: {thread_name}\n"
            f"Classification: {classification}\n"
            f"Reply draft: {reply_text}"
        )
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message_text},
            timeout=10,
        ).raise_for_status()
        logger.info(f"Telegram notification sent for {thread_name}")
    except Exception as exc:
        logger.warning(f"Telegram notification failed for {thread_name}: {exc}")


def process_single_thread(cdp_page, page_id: str, thread_id: str,
                          thread_name: str, dry_run: bool = True) -> dict:
    """Process a single thread: lookup CRM, run ADK, reply via CDP.

    Args:
        cdp_page: Playwright page connected via CDP.
        page_id: Facebook Page ID.
        thread_id: Thread identifier.
        thread_name: Display name of the thread.
        dry_run: If True, type reply but don't press Enter.

    Returns:
        dict: Processing result.
    """
    from adk_agents.tools.seeker_tools import lookup_seeker, get_thread_messages
    from adk_agents.tools.facebook_tools import (
        navigate_to_thread, send_reply_via_cdp, log_auto_reply
    )
    from adk_agents.tools.l5_stage_tools import evaluate_stage_gate
    from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision

    logger.info(f"{'[DRY-RUN]' if dry_run else '[LIVE]'} Processing thread: {thread_name}")

    # 1. Get messages from DB
    msg_result = get_thread_messages(thread_id)
    if msg_result["status"] != "success" or msg_result["count"] == 0:
        logger.warning(f"No messages found for thread {thread_name}")
        return {"status": "skipped", "reason": "no_messages"}

    # 2. Lookup seeker in CRM
    seeker = lookup_seeker(thread_id)

    # 3. Run ADK pipeline
    logger.info(f"Running ADK pipeline for {thread_name}...")
    adk_result = run_adk_pipeline(msg_result["messages"], seeker)

    classification = adk_result.get("classification", "")
    reply_text = adk_result.get("reply_text", "")

    logger.info(f"Classification: {classification[:100]}")
    logger.info(f"Generated reply: {reply_text[:100]}")

    if not reply_text:
        logger.warning(f"No reply generated for {thread_name}")
        return {"status": "no_reply", "classification": classification}

    # 4. Navigate to thread in FB inbox
    if not navigate_to_thread(cdp_page, page_id, thread_name):
        logger.warning(f"Could not navigate to thread {thread_name}")
        return {"status": "nav_failed", "reply_text": reply_text}

    # 5. Type reply (dry-run: type only, live: type + Enter)
    sent = send_reply_via_cdp(cdp_page, reply_text, dry_run=dry_run)

    # 6. Log the auto-reply
    log_auto_reply(
        thread_id,
        reply_text,
        agent_name="responder",
        escalated=False,
        dry_run=dry_run,
    )

    mode = "typed (not sent)" if dry_run else "sent"
    logger.info(f"Reply {mode} for {thread_name}: {reply_text[:60]}...")

    stage_result = evaluate_stage_gate(thread_id)
    if stage_result.get("promoted"):
        log_mas_decision(
            page_id,
            "stage_gate",
            "thread",
            thread_id,
            "promoted",
            stage_result.get("reason"),
            dry_run=dry_run,
            payload=stage_result,
        )
        logger.info(
            f"Stage promoted for {thread_name}: "
            f"{stage_result.get('from_stage')} -> {stage_result.get('to_stage')}"
        )

    _notify_telegram_if_needed(thread_name, classification, reply_text)

    return {
        "status": "success",
        "mode": "dry_run" if dry_run else "live",
        "thread_name": thread_name,
        "classification": classification,
        "reply_text": reply_text,
        "stage_result": stage_result,
    }


def run_inbox_cycle(page_id: str, dry_run: bool = True,
                    max_threads: int = 5) -> dict:
    """Run one complete inbox cycle: fetch → find unreplied → process → reply.

    Args:
        page_id: Facebook Page ID.
        dry_run: If True, type replies but don't send them.
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

            # Step 1: Fetch new inbox messages
            logger.info(f"Step 1: Fetching inbox for page {page_id}...")
            scrape_stats = scrape_inbox(
                cdp_page,
                page_id,
                "1d",
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
            logger.info("Step 2: Finding unreplied threads...")
            unreplied = find_unreplied_threads(page_id, limit=max_threads)

            if unreplied["status"] != "success" or unreplied["count"] == 0:
                logger.info("No unreplied threads found. Cycle complete.")
                return {"status": "no_unreplied", "scrape_stats": scrape_stats}

            logger.info(f"Found {unreplied['count']} unreplied thread(s).")

            # Step 3: Process each thread
            for thread in unreplied["threads"]:
                try:
                    result = process_single_thread(
                        cdp_page, page_id,
                        thread["thread_id"], thread["thread_name"],
                        dry_run=dry_run
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(f"Error processing {thread['thread_name']}: {e}")
                    results.append({"status": "error", "error": str(e),
                                    "thread_name": thread["thread_name"]})

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
        help="Send replies for real (default is dry-run: type but don't send)"
    )
    parser.add_argument(
        "--max-threads", type=int, default=5,
        help="Max threads to process per cycle (default: 5)"
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Polling interval in seconds (default: {POLL_INTERVAL})"
    )

    args = parser.parse_args()
    dry_run = not args.live

    # Parse page_id from URL if needed
    page_id = parse_page_id(args.page_id)

    # Setup LLM environment
    setup_llm_env()

    mode_str = "[DRY-RUN] Type replies but don't send" if dry_run else "[LIVE] Replies will be SENT"
    logger.info(f"=== Inbox MAS Runner ===")
    logger.info(f"Page ID: {page_id}")
    logger.info(f"Mode: {mode_str}")
    logger.info(f"Max threads/cycle: {args.max_threads}")

    if args.once:
        result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=args.max_threads)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.poll:
        logger.info(f"Starting polling loop (interval: {args.interval}s)...")
        while True:
            try:
                result = run_inbox_cycle(page_id, dry_run=dry_run,
                                         max_threads=args.max_threads)
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
