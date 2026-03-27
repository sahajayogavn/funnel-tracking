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
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    session = loop.run_until_complete(
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

    # code:tool-inbox-mas-001:reply-sanitizer
    # Strip any LLM chain-of-thought / reasoning lines from the reply before
    # it is typed into a Facebook message box. Lines that start with "**" or
    # match known reasoning patterns are removed. Only clean reply lines remain.
    result["reply_text"] = _sanitize_reply(result.get("reply_text", ""))

    return result


def _sanitize_reply(text: str) -> str:
    """Remove LLM reasoning-leak lines from a generated reply.

    Lines starting with '**' (e.g. '**Crafting a warm reply**') and lines
    that are pure reasoning narration are stripped. Empty results raise a
    warning so callers know no usable reply was produced.

    Args:
        text: Raw reply text from the LLM.

    Returns:
        Cleaned reply string (may be empty if the entire output was reasoning).
    """
    import re

    if not text:
        return text

    reasoning_patterns = re.compile(
        r'^(\*\*.*\*\*'                    # **Any heading**
        r'|I need to\b'                    # "I need to ..."
        r'|I\'m (?:going to|working|thinking|attempting)\b'
        r'|Let me\b'                       # "Let me ..."
        r'|I should\b'                     # "I should ..."
        r'|I want to\b'                    # "I want to ..."
        r'|I\'ll\b'                        # "I'll ..."
        r'|Here is the reply'
        r'|Here\'s (?:my|the) reply'
        r')',
        re.IGNORECASE,
    )

    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if reasoning_patterns.match(stripped):
            logger.debug(f"[sanitize_reply] Stripped reasoning line: {stripped[:80]}")
            continue
        clean_lines.append(line)

    # Remove leading/trailing blank lines from the result
    cleaned = "\n".join(clean_lines).strip()
    if not cleaned and text.strip():
        logger.warning(
            "[sanitize_reply] Entire reply was reasoning leak — no clean text. "
            "Original (truncated): " + text[:120]
        )
    return cleaned



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
    """Process a single thread and draft a reply for human review.

    Args:
        cdp_page: Playwright page connected via CDP.
        page_id: Facebook Page ID.
        thread_id: Thread identifier.
        thread_name: Display name of the thread.
        dry_run: Legacy compatibility flag. Inbox reply handling remains draft-only.

    Returns:
        dict: Processing result.
    """
    from adk_agents.tools.seeker_tools import lookup_seeker, get_thread_messages
    from adk_agents.tools.facebook_tools import (
        navigate_to_thread, send_reply_via_cdp, log_auto_reply
    )
    from adk_agents.tools.l5_stage_tools import evaluate_stage_gate
    from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision

    if not dry_run:
        logger.warning("--live is ignored for inbox replies; drafting only for human review.")
    logger.info(f"[DRAFT] Processing thread: {thread_name}")

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

    latest_customer_message_timestamp = None
    for message in reversed(msg_result["messages"]):
        if message.get("sender") == "Customer":
            latest_customer_message_timestamp = message.get("timestamp")
            break

    # 4. Navigate to thread in FB inbox
    if not navigate_to_thread(cdp_page, page_id, thread_name, thread_id):
        logger.warning(f"Could not navigate to thread {thread_name}")
        return {"status": "nav_failed", "reply_text": reply_text}

    # 4.5. Real-time DOM Verification
    # Ensure the SQLite DB isn't stale compared to the real thread.
    # If the human already replied while the LLM was running, abort!
    latest_dom_sender = cdp_page.evaluate('''() => {
        let region = document.querySelector(
            'div[aria-label*="Message list container"], ' +
            'div[role="region"][aria-label*="message"]'
        );
        if (!region) return "Unknown";
        let bubble = region.querySelector('.x1fqp7bg');
        let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
        let topDivs = Array.from(messageArea.children);
        for (let i = topDivs.length - 1; i >= 0; i--) {
            let div = topDivs[i];
            if (div.classList.contains('x14vqqas') || div.querySelector('.x14vqqas')) continue;
            if (div.classList.contains('xcxhlts') || div.querySelector('.xcxhlts')) continue;
            if (!div.classList.contains('x1fqp7bg') && !div.querySelector('.x1fqp7bg')) continue;
            let htmlStr = (div.outerHTML || "").substring(0, 500);
            if (htmlStr.includes('x13a6bvl')) {
                let text = div.innerText.trim();
                let is_auto = text.includes("Chúng tôi có thể giúp gì cho bạn?") || 
                              text.includes("Bạn để lại Họ tên và Số điện thoại") ||
                              text.includes("Khóa học thiền ở Hà Nội") ||
                              text.includes("Thời gian: 20h-21h30");
                return is_auto ? "Auto_Page" : "Page";
            }
            if (htmlStr.includes('x1nhvcw1')) return "Customer";
            let avatar = div.querySelector('img.img[alt]');
            return avatar ? "Customer" : "Page";
        }
        return "Unknown";
    }''')
    
    if latest_dom_sender == "Page":
        logger.warning(f"DOM Verification Failed: Thread {thread_name} already has a Page reply in the DOM! Aborting.")
        return {"status": "abort_already_replied", "reply_text": reply_text}

    # 5. Draft reply into the composer
    drafted = send_reply_via_cdp(cdp_page, reply_text, dry_run=True)
    if not drafted:
        logger.warning(f"Could not draft reply for {thread_name}")
        return {"status": "draft_failed", "reply_text": reply_text}

    # 6. Log the drafted reply acknowledgement
    log_auto_reply(
        thread_id,
        reply_text,
        agent_name="responder",
        escalated=False,
        dry_run=True,
        customer_message_timestamp=latest_customer_message_timestamp,
    )

    logger.info(f"Reply drafted for {thread_name}: {reply_text[:60]}...")

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
    
    logger.info("### USER REQUEST: HANGING CDP TAB OPEN FOR HUMAN INSPECTION. PRESS CTRL+C TO QUIT. ###")
    import time
    time.sleep(3600)

    return {
        "status": "drafted",
        "mode": "draft_only",
        "thread_name": thread_name,
        "classification": classification,
        "reply_text": reply_text,
        "stage_result": stage_result,
        "customer_message_timestamp": latest_customer_message_timestamp,
    }


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
            fetch_limit = 200 if target_thread else max_threads
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
        help="Deprecated compatibility flag. Inbox runner still drafts only and never sends."
    )
    parser.add_argument(
        "--max-threads", type=int, default=5,
        help="Max threads to process per cycle (default: 5)"
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

    mode_str = "[DRAFT-ONLY] Type replies for human review; automation never sends"
    logger.info(f"=== Inbox MAS Runner ===")
    logger.info(f"Page ID: {page_id}")
    logger.info(f"Mode: {mode_str}")
    logger.info(f"Max threads/cycle: {args.max_threads}")

    if args.once:
        result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=args.max_threads, target_thread=args.target_thread)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.poll:
        logger.info(f"Starting polling loop (interval: {args.interval}s)...")
        while True:
            try:
                result = run_inbox_cycle(page_id, dry_run=dry_run,
                                         max_threads=args.max_threads,
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
