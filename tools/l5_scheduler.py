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

from fb_pipeline.session.l2_activity_lock import scheduler_browser_cycle
from tools.l5_scheduler_routes import (
    run_fetch_cycle, run_reply_cycle, run_react_cycle, run_warmup_cycle, run_event_cycle,
    run_classify_cycle,
)
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
DEFAULT_CLASSIFY_INTERVAL = 30  # minutes; LLM city/program pass, no browser
ALL_ROUTES = {"react", "reply", "event", "classify"}

# --- Graceful shutdown ---
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    logger.info(f"Received signal {signum}. Requesting graceful shutdown...")
    _shutdown_requested = True


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# --- Scheduler Setup ---
# code:tool-scheduler-001:setup

def setup_schedule(page_id: str, dry_run: bool, routes: set,
                   fetch_interval: int, warmup_time: str, event_time: str,
                   classify_interval: int = DEFAULT_CLASSIFY_INTERVAL):
    """Register scheduled jobs based on enabled routes."""
    registered = []

    if "classify" in routes:
        # code:tool-citydetect-001:scheduler-route
        schedule.every(classify_interval).minutes.do(
            run_classify_cycle, page_id=page_id, dry_run=dry_run
        )
        registered.append(f"classify every {classify_interval}min")

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
    logger.info("Scheduler loop started. Triggering immediate first run for minute/second jobs. Press Ctrl+C to stop.")
    for job in schedule.jobs:
        if job.unit in ('minutes', 'seconds'):
            job.run()
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
        "--routes", default="react,reply,event,classify",
        help="Comma-separated routes to enable (default: all). "
             "Options: react, reply, event, classify"
    )
    parser.add_argument(
        "--classify-interval", type=int, default=DEFAULT_CLASSIFY_INTERVAL,
        help=f"LLM city/program classification interval in minutes (default: {DEFAULT_CLASSIFY_INTERVAL})"
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
    
    raw_routes = set(r.strip() for r in args.routes.split(","))
    if "warmup" in raw_routes:
        logger.warning("warmup is manual-only (use /queues)")
    
    routes = raw_routes & ALL_ROUTES

    if not routes:
        logger.error("No valid routes specified. Use: react, reply, event, classify")
        sys.exit(1)

    # Setup LLM env if any agent route is enabled
    if routes & {"reply", "react", "warmup", "event", "classify"}:
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
        if "classify" in routes:
            results["classify"] = run_classify_cycle(page_id, dry_run=dry_run, max_users=max_limit,
                                                     background=False)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    registered = setup_schedule(
        page_id=page_id, dry_run=dry_run, routes=routes,
        fetch_interval=args.fetch_interval,
        warmup_time=args.warmup_time, event_time=args.event_time,
        classify_interval=args.classify_interval,
    )
    logger.info(f"Registered jobs: {', '.join(registered)}")

    run_scheduler_loop()


if __name__ == "__main__":
    main()
