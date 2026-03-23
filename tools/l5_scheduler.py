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
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime

# Setup paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    import schedule
except ImportError:
    print("ERROR: 'schedule' library not found. Install with: pip install schedule")
    sys.exit(1)

from fb_pipeline.contracts.l1_inbox import parse_page_id

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


# --- Route Implementations ---
# code:tool-scheduler-001:fetch-react

def run_fetch_cycle(page_id: str, dry_run: bool = True):
    """Fetch new inbox messages and comments from Facebook via CDP."""
    logger.info(f"[FETCH] Starting inbox+comment fetch for page {page_id}...")
    try:
        from tools.l5_inbox_mas_runner import run_inbox_cycle
        result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=0)
        logger.info(f"[FETCH] Scrape result: {result.get('status', 'unknown')}")
        return result
    except Exception as e:
        logger.error(f"[FETCH] Failed: {e}")
        return {"status": "error", "error": str(e)}


def run_react_cycle(page_id: str, dry_run: bool = True):
    """Route 1: React to new messages/comments."""
    logger.info(f"[REACT] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting reaction cycle...")
    try:
        from adk_agents.tools.l5_reaction_tools import (
            find_unreacted_items, log_reaction
        )
        unreacted = find_unreacted_items(page_id)
        if unreacted["status"] != "success" or unreacted["count"] == 0:
            logger.info("[REACT] No unreacted items found.")
            return {"status": "no_unreacted", "count": 0}

        processed = 0
        for item in unreacted["items"]:
            # Use a simple heuristic for reaction selection
            # In production, this would go through the Reactor ADK agent
            reaction_type = _select_reaction_heuristic(item)
            log_reaction(
                item_type=item["item_type"],
                item_id=item["item_id"],
                reaction_type=reaction_type,
                dry_run=dry_run
            )
            processed += 1
            logger.info(
                f"[REACT] {'[DRY-RUN]' if dry_run else '[SENT]'} "
                f"{reaction_type} on {item['item_type']} {item['item_id'][:20]}..."
            )

        return {"status": "complete", "processed": processed}
    except Exception as e:
        logger.error(f"[REACT] Failed: {e}")
        return {"status": "error", "error": str(e)}


def _select_reaction_heuristic(item: dict) -> str:
    """Simple heuristic for reaction selection. Will be replaced by Reactor agent."""
    content = (item.get("content") or "").lower()
    if any(w in content for w in ["cảm ơn", "thank", "tuyệt", "great", "love"]):
        return "love"
    if any(w in content for w in ["buồn", "sad", "khó", "difficult"]):
        return "care"
    return "like"


# code:tool-scheduler-001:reply
def run_reply_cycle(page_id: str, dry_run: bool = True, max_threads: int = 5):
    """Inbox Reply: existing MAS reply flow (Classifier → Responder)."""
    logger.info(f"[REPLY] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting reply cycle...")
    try:
        from tools.l5_inbox_mas_runner import run_inbox_cycle
        result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=max_threads)
        logger.info(f"[REPLY] Result: {result.get('status', 'unknown')}, "
                     f"processed: {result.get('processed', 0)}")
        return result
    except Exception as e:
        logger.error(f"[REPLY] Failed: {e}")
        return {"status": "error", "error": str(e)}


# code:tool-scheduler-001:warmup
def run_warmup_cycle(page_id: str, dry_run: bool = True, max_seekers: int = 5):
    """Route 2: Warm up dormant seekers."""
    logger.info(f"[WARMUP] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting warmup cycle...")
    try:
        from adk_agents.tools.l5_warmup_tools import (
            find_dormant_seekers, was_recently_warmed_up,
            select_warmup_strategy, log_warmup_campaign
        )
        dormant = find_dormant_seekers(page_id, max_seekers=max_seekers)
        if dormant["status"] != "success" or dormant["count"] == 0:
            logger.info("[WARMUP] No dormant seekers found.")
            return {"status": "no_dormant", "count": 0}

        processed = 0
        skipped = 0
        for seeker in dormant["seekers"]:
            thread_id = seeker["thread_id"]
            if was_recently_warmed_up(thread_id, days=7):
                logger.info(f"[WARMUP] Skipping {seeker['name']} — recently warmed up.")
                skipped += 1
                continue

            strategy = select_warmup_strategy(
                lead_stage=seeker.get("lead_stage", "Intake"),
                days_dormant=seeker.get("days_dormant", 7)
            )
            if not strategy:
                continue

            # In production, this would go through the WarmUpComposer ADK agent
            # For now, use the strategy's template message
            message_text = strategy.get("template", "")
            if message_text:
                log_warmup_campaign(
                    thread_id=thread_id,
                    seeker_name=seeker.get("name"),
                    strategy_type=strategy["type"],
                    message_text=message_text,
                    dry_run=dry_run
                )
                processed += 1
                logger.info(
                    f"[WARMUP] {'[DRY-RUN]' if dry_run else '[SENT]'} "
                    f"Warmup for {seeker['name']}: {strategy['type']}"
                )

        return {"status": "complete", "processed": processed, "skipped": skipped}
    except Exception as e:
        logger.error(f"[WARMUP] Failed: {e}")
        return {"status": "error", "error": str(e)}


# code:tool-scheduler-001:event
def run_event_cycle(page_id: str, dry_run: bool = True, max_seekers: int = 10):
    """Route 3: Advertise new events to matched seekers."""
    logger.info(f"[EVENT] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting event cycle...")
    try:
        from adk_agents.tools.l5_event_tools import (
            get_upcoming_events, find_target_seekers_for_event,
            log_event_campaign
        )
        events = get_upcoming_events(days_ahead=14)
        if events["status"] != "success" or events["count"] == 0:
            logger.info("[EVENT] No upcoming events found.")
            return {"status": "no_events", "count": 0}

        total_sent = 0
        for event in events["events"]:
            targets = find_target_seekers_for_event(
                event_id=event["id"],
                city=event["city"],
                max_seekers=max_seekers
            )
            if targets["status"] != "success" or targets["count"] == 0:
                continue

            for seeker in targets["seekers"]:
                # In production, this would go through the EventAdvertiser ADK agent
                message_text = (
                    f"Xin chào {seeker['name']}! "
                    f"Sahaja Yoga có lớp thiền mới: {event['name']} "
                    f"tại {event['city']} vào ngày {event['event_date']}. "
                    f"Lớp hoàn toàn MIỄN PHÍ. Bạn có muốn tham gia không?"
                )
                log_event_campaign(
                    event_id=event["id"],
                    thread_id=seeker["thread_id"],
                    seeker_name=seeker.get("name"),
                    message_text=message_text,
                    dry_run=dry_run
                )
                total_sent += 1

        logger.info(f"[EVENT] Cycle complete. Sent: {total_sent}")
        return {"status": "complete", "sent": total_sent}
    except Exception as e:
        logger.error(f"[EVENT] Failed: {e}")
        return {"status": "error", "error": str(e)}


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

    return registered


def run_scheduler_loop():
    """Main scheduler event loop with graceful shutdown."""
    logger.info("Scheduler loop started. Press Ctrl+C to stop.")
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
        if "react" in routes:
            run_fetch_cycle(page_id, dry_run=dry_run)
            results["react"] = run_react_cycle(page_id, dry_run=dry_run)
        if "reply" in routes:
            results["reply"] = run_reply_cycle(page_id, dry_run=dry_run)
        if "warmup" in routes:
            results["warmup"] = run_warmup_cycle(page_id, dry_run=dry_run)
        if "event" in routes:
            results["event"] = run_event_cycle(page_id, dry_run=dry_run)
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
