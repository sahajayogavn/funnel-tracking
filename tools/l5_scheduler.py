#!/usr/bin/env python3
"""
Unified MAS Scheduler — Periodic trigger daemon for all MAS action routes.
code:tool-scheduler-001

Runs as a long-lived daemon using the `schedule` library. Fires 4 jobs:
  1. fetch_and_react()  — every 15 min: fetch inbox+comments, react
  2. inbox_reply_cycle() — every 15 min: existing MAS reply flow
  3. warmup_cycle()      — daily at 09:00: warm up dormant seekers
  4. event_cycle()       — daily at 10:00: advertise new events

Usage:
    # All routes, dry-run
    python tools/scheduler.py --page-id 119587786260266

    # Specific routes only
    python tools/scheduler.py --page-id 119587786260266 --routes react,warmup

    # Live mode (sends real messages)
    python tools/scheduler.py --page-id 119587786260266 --live

    # Custom intervals
    python tools/scheduler.py --page-id 119587786260266 --fetch-interval 10 --warmup-time 08:30
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

# Setup paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    import schedule
except ImportError:
    print("ERROR: 'schedule' library not found. Install with: pip install schedule")
    sys.exit(1)

from fb_pipeline.contracts.l1_inbox import parse_page_id

from tools.l5_scheduler_routes import run_fetch_cycle, run_reply_cycle, run_react_cycle, run_warmup_cycle, run_event_cycle
from tools.l5_scheduler_core import _update_user_decision_state
from tools.l5_scheduler_adk import run_adk_warmup_composer, run_adk_event_advertiser
# Setup logging
os.makedirs(os.path.join(PROJECT_ROOT, 'logs'), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, 'logs', 'scheduler.log')),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("scheduler")

# --- Constants ---
DEFAULT_FETCH_INTERVAL = 15   # minutes
DEFAULT_WARMUP_TIME = "09:00"
DEFAULT_EVENT_TIME = "10:00"
ALL_ROUTES = {"react", "reply", "warmup", "event"}

# --- Graceful shutdown ---
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    logger.info(f"Received signal {signum}. Requesting graceful shutdown...")
    _shutdown_requested = True


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# --- HITL Executor ---
# code:tool-scheduler-001:hitl-executor

def telegram_poller_job():
    from tools.l5_telegram_hitl import poll_telegram_updates
    poll_telegram_updates()


def hitl_execution_job(page_id: str, dry_run: bool = True):
    """Claim and execute only FIFO action-queue items humans approved.

    Telegram's status table is retained for discussion/rewrite history, but it
    is never a delivery queue on its own.
    """
    from tools.l5_action_queue import QUEUE_TYPES, claim_next_action, finish_action
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    from tools.l5_telegram_hitl import mark_hitl_executed, send_proposal_to_telegram
    import json

    for queue_type in QUEUE_TYPES:
        item = claim_next_action(queue_type)
        if not item:
            continue
        try:
            _execute_approved_action(item, page_id)
        except Exception as exc:
            logger.exception("Action queue item %s failed", item["id"])
            finish_action(item["id"], str(exc))
        else:
            finish_action(item["id"])

    conn = get_db_connection()
    try:
        rejected = conn.execute("SELECT * FROM telegram_hitl_queue WHERE status = 'rejected' AND route IN ('warmup', 'event', 'inbox') LIMIT 10").fetchall()
        for row in rejected:
            msg_id = row['telegram_message_id']
            route = row['route']
            feedback = row['feedback_text']
            payload = json.loads(row['payload_json'])
            proposals = payload.get("proposals", [])
            
            if not proposals:
                mark_hitl_executed(msg_id)
                continue
                
            logger.info(f"Regenerating rejected {route} batch based on HITL feedback: {feedback}")
            from tools.l5_inbox_mas_runner import load_knowledge_context
            knowledge_context = load_knowledge_context()
            
            new_proposals = []
            for prop in proposals:
                if route == 'warmup':
                    new_msg = run_adk_warmup_composer(prop['seeker'], prop['strategy'], knowledge_context, dry_run=True, feedback=feedback)
                    if new_msg:
                        prop['message_text'] = new_msg
                        new_proposals.append(prop)
                elif route == 'event':
                    new_msg = run_adk_event_advertiser(prop['event'], prop['seeker'], knowledge_context, dry_run=True, feedback=feedback)
                    if new_msg:
                        prop['message_text'] = new_msg
                        new_proposals.append(prop)
                elif route == 'inbox':
                    from tools.l5_inbox_mas_runner import run_adk_pipeline
                    adk_result = run_adk_pipeline(payload.get("msg_messages_json", []), payload.get("seeker_dict", prop.get("seeker", {})), feedback=feedback)
                    new_msg = adk_result.get("reply_text")
                    if new_msg:
                        prop['message_text'] = new_msg
                        new_proposals.append(prop)

            mark_hitl_executed(msg_id)
            if new_proposals:
                summary = f"Revised {route} proposal for {len(new_proposals)} seekers:\n"
                for i, p in enumerate(new_proposals[:5], 1):
                    summary += f"{i}. {p['seeker_name']}: {p['message_text'][:40]}...\n"
                send_proposal_to_telegram(route, "batch", summary, {"proposals": new_proposals, "page_id": page_id})

    finally:
        conn.close()


def _execute_approved_action(item: dict, fallback_page_id: str) -> None:
    """The sole CDP delivery boundary; approval is already persisted."""
    if item.get("reaction_type"):
        raise RuntimeError("Live Facebook reaction executor is not configured")
    if item["queue_type"] in {"reply_comment", "proactive_comment"}:
        raise RuntimeError("Live Facebook comment executor is not configured")

    from playwright.sync_api import sync_playwright
    from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session
    from fb_pipeline.browser.l2_actions import navigate_to_thread, send_reply_via_cdp, commit_reply_via_cdp
    page_id = item.get("page_id") or fallback_page_id
    with sync_playwright() as p:
        session = attach_to_authorized_session(
            p, page_id, f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
            tab_role=f"outbound:{item['id']}",
        )
        if not navigate_to_thread(session.page, page_id, item.get("target_name") or "", item.get("target_id")):
            raise RuntimeError("Facebook inbox thread could not be opened")
        if not send_reply_via_cdp(session.page, item.get("action_text") or "", dry_run=False):
            raise RuntimeError("Facebook composer could not be filled")
        if not commit_reply_via_cdp(session.page):
            raise RuntimeError("Facebook message could not be sent")
        if item["queue_type"] == "reply_message":
            from adk_agents.tools.l5_facebook_tools import log_auto_reply
            log_auto_reply(
                item.get("target_id") or "", item.get("action_text") or "",
                agent_name="human_approved_executor", dry_run=False,
                customer_message_timestamp=item.get("payload", {}).get("customer_message_timestamp"),
            )


# --- Scheduler Setup ---
# code:tool-scheduler-001:setup

def setup_schedule(page_id: str, dry_run: bool, routes: set,
                   fetch_interval: int, warmup_time: str, event_time: str):
    """Register scheduled jobs based on enabled routes."""
    registered = []

    if "react" in routes or "reply" in routes:
        schedule.every(fetch_interval).minutes.do(
            run_fetch_cycle, page_id=page_id, dry_run=dry_run
        )
        registered.append(f"fetch every {fetch_interval}min")

    if "react" in routes:
        schedule.every(fetch_interval).minutes.do(
            run_react_cycle, page_id=page_id, dry_run=dry_run
        )
        registered.append(f"react every {fetch_interval}min")

    if "reply" in routes:
        schedule.every(fetch_interval).minutes.do(
            run_reply_cycle, page_id=page_id, dry_run=dry_run
        )
        registered.append(f"reply every {fetch_interval}min")

    if "warmup" in routes:
        schedule.every().day.at(warmup_time).do(
            run_warmup_cycle, page_id=page_id, dry_run=dry_run
        )
        registered.append(f"warmup daily at {warmup_time}")

    if "event" in routes:
        schedule.every().day.at(event_time).do(
            run_event_cycle, page_id=page_id, dry_run=dry_run
        )
        registered.append(f"event daily at {event_time}")

    schedule.every(10).seconds.do(telegram_poller_job)
    registered.append("telegram_poller every 10s")
    
    schedule.every(30).seconds.do(hitl_execution_job, page_id=page_id, dry_run=dry_run)
    registered.append("hitl_execution every 30s")

    return registered


def run_scheduler_loop():
    """Main scheduler event loop with graceful shutdown."""
    logger.info("Scheduler loop started. Triggering immediate first run. Press Ctrl+C to stop.")
    schedule.run_all()
    while not _shutdown_requested:
        schedule.run_pending()
        time.sleep(10)
    logger.info("Scheduler loop stopped gracefully.")


# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(
        description="Unified MAS Scheduler — periodic trigger daemon for all routes"
    )
    parser.add_argument(
        "--page-id", required=True,
        help="Facebook Page ID (numeric) or Business Suite URL with asset_id"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Send replies/reactions for real (default is dry-run)"
    )
    parser.add_argument(
        "--routes", default="react,reply,warmup,event",
        help="Comma-separated routes to enable (default: all). "
             "Options: react, reply, warmup, event"
    )
    parser.add_argument(
        "--fetch-interval", type=int, default=DEFAULT_FETCH_INTERVAL,
        help=f"Fetch/react/reply interval in minutes (default: {DEFAULT_FETCH_INTERVAL})"
    )
    parser.add_argument(
        "--warmup-time", default=DEFAULT_WARMUP_TIME,
        help=f"Daily warmup time HH:MM (default: {DEFAULT_WARMUP_TIME})"
    )
    parser.add_argument(
        "--event-time", default=DEFAULT_EVENT_TIME,
        help=f"Daily event advertising time HH:MM (default: {DEFAULT_EVENT_TIME})"
    )
    parser.add_argument(
        "--num", type=int, default=None,
        help="Exact number of threads/seekers to process per cycle (alias for max-threads/max-seekers)"
    )
    parser.add_argument(
        "--run-once", action="store_true",
        help="Run all enabled routes once and exit (for testing)"
    )

    args = parser.parse_args()
    dry_run = not args.live
    page_id = parse_page_id(args.page_id)
    routes = set(r.strip() for r in args.routes.split(",")) & ALL_ROUTES

    if not routes:
        logger.error("No valid routes specified. Use: react, reply, warmup, event")
        sys.exit(1)

    # Setup LLM env if any agent route is enabled
    if routes & {"reply", "react", "warmup", "event"}:
        try:
            from tools.l5_inbox_mas_runner import setup_llm_env
            setup_llm_env()
        except Exception as e:
            logger.warning(f"LLM env setup skipped: {e}")

    mode_str = "[DRY-RUN]" if dry_run else "[LIVE]"
    logger.info(f"=== MAS Scheduler {mode_str} ===")
    logger.info(f"Page ID: {page_id}")
    logger.info(f"Enabled routes: {', '.join(sorted(routes))}")

    if args.run_once:
        logger.info("Running all enabled routes once...")
        results = {}
        max_limit = args.num if args.num is not None else 5

        if "react" in routes:
            run_fetch_cycle(page_id, dry_run=dry_run)
            results["react"] = run_react_cycle(page_id, dry_run=dry_run)
        if "reply" in routes:
            results["reply"] = run_reply_cycle(page_id, dry_run=dry_run, max_threads=max_limit)
        if "warmup" in routes:
            results["warmup"] = run_warmup_cycle(page_id, dry_run=dry_run, max_seekers=max_limit)
        if "event" in routes:
            results["event"] = run_event_cycle(page_id, dry_run=dry_run, max_seekers=max_limit)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    registered = setup_schedule(
        page_id=page_id, dry_run=dry_run, routes=routes,
        fetch_interval=args.fetch_interval,
        warmup_time=args.warmup_time, event_time=args.event_time
    )
    logger.info(f"Registered jobs: {', '.join(registered)}")

    run_scheduler_loop()


if __name__ == "__main__":
    main()
