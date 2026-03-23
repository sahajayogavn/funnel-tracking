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


# --- Decision Core ---
# code:tool-scheduler-001:decision-core


TEMPERATURE_THRESHOLDS = {
    "Follower": {"hot": 3, "warm": 7, "cool": 21},
    "Curious Seeker": {"hot": 3, "warm": 7, "cool": 14},
    "Registered": {"hot": 2, "warm": 5, "cool": 14},
    "Deep Learner": {"hot": 7, "warm": 14, "cool": 28},
    "Sahaja Yogi": {"hot": 14, "warm": 30, "cool": 90},
}

STAGE_TO_STRATEGY_STAGE = {
    "intake": "Follower",
    "user": "Follower",
    "seeker": "Curious Seeker",
    "registered": "Registered",
    "public program seeker": "Registered",
    "seeker public program": "Registered",
    "seeker_public_program": "Registered",
    "18-week seeker": "Deep Learner",
    "18 week seeker": "Deep Learner",
    "seeker_18_weeks": "Deep Learner",
    "seed": "Sahaja Yogi",
    "sahaja yogi": "Sahaja Yogi",
    "sahaja_yogi": "Sahaja Yogi",
    "sahaja yogi dedicated": "Sahaja Yogi",
    "sahaja_yogi_dedicated": "Sahaja Yogi",
    "sahaja mahayogi": "Sahaja Yogi",
    "sahaja_mahayogi": "Sahaja Yogi",
}


def _normalize_strategy_stage(lead_stage: str | None) -> str:
    normalized = " ".join((lead_stage or "").strip().replace("_", " ").replace("-", " ").lower().split())
    return STAGE_TO_STRATEGY_STAGE.get(normalized, "Follower")


def _parse_db_time(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _compute_temperature(lead_stage: str | None, last_interaction: str | None, stored_temperature: str | None = None) -> str:
    if stored_temperature in {"dormant", "unsubscribed"}:
        return stored_temperature

    if lead_stage and lead_stage.lower() in {"spam", "unsubscribed"}:
        return "unsubscribed"

    interaction_at = _parse_db_time(last_interaction)
    if interaction_at is None:
        return stored_temperature or "warm"

    days_silent = max(0, (datetime.now() - interaction_at).days)
    thresholds = TEMPERATURE_THRESHOLDS[_normalize_strategy_stage(lead_stage)]
    if days_silent < thresholds["hot"]:
        return "hot"
    if days_silent < thresholds["warm"]:
        return "warm"
    if days_silent < thresholds["cool"]:
        return "cool"
    return "cold"


def _load_user_state(thread_id: str) -> dict | None:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

    conn = get_db_connection(logger=logger)
    try:
        row = conn.execute(
            "SELECT thread_id, thread_name, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step "
            "FROM users WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _thread_has_pending_reply(page_id: str, thread_id: str) -> bool:
    from adk_agents.tools.l5_seeker_tools import find_unreplied_threads

    result = find_unreplied_threads(page_id, limit=200)
    if result.get("status") != "success":
        return False
    return any(thread.get("thread_id") == thread_id for thread in result.get("threads", []))


def _recent_live_touch_exists(thread_id: str, since_hours: int = 24) -> bool:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

    cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
    conn = get_db_connection(logger=logger)
    try:
        checks = [
            ("SELECT 1 FROM warmup_campaigns WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND sent_at > ? LIMIT 1", (thread_id, cutoff)),
            ("SELECT 1 FROM event_campaigns WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND sent_at > ? LIMIT 1", (thread_id, cutoff)),
            ("SELECT 1 FROM auto_replies WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND created_at > ? LIMIT 1", (thread_id, cutoff)),
        ]
        for query, params in checks:
            if conn.execute(query, params).fetchone():
                return True
        return False
    finally:
        conn.close()


def _has_recent_live_event(thread_id: str, since_days: int = 90) -> bool:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

    cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
    conn = get_db_connection(logger=logger)
    try:
        row = conn.execute(
            "SELECT 1 FROM event_campaigns WHERE thread_id = ? AND COALESCE(dry_run, 1) = 0 AND sent_at > ? LIMIT 1",
            (thread_id, cutoff),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _update_user_decision_state(thread_id: str, temperature: str, warmup_sent: bool = False):
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

    conn = get_db_connection(logger=logger)
    try:
        if warmup_sent:
            conn.execute(
                "UPDATE users SET temperature = ?, last_warmup_at = datetime('now'), warmup_count = COALESCE(warmup_count, 0) + 1, "
                "cool_step = CASE WHEN ? = 'cool' THEN MIN(COALESCE(cool_step, 0) + 1, 3) ELSE COALESCE(cool_step, 0) END "
                "WHERE thread_id = ?",
                (temperature, temperature, thread_id),
            )
        else:
            conn.execute(
                "UPDATE users SET temperature = ? WHERE thread_id = ?",
                (temperature, thread_id),
            )
        conn.commit()
    finally:
        conn.close()


def _evaluate_proactive_eligibility(page_id: str, route: str, thread_id: str) -> tuple[bool, str, dict]:
    user_state = _load_user_state(thread_id)
    if not user_state:
        return False, "missing_user_state", {"thread_id": thread_id}

    temperature = _compute_temperature(
        user_state.get("lead_stage"),
        user_state.get("last_interaction"),
        user_state.get("temperature"),
    )
    payload = {
        "thread_id": thread_id,
        "lead_stage": user_state.get("lead_stage"),
        "temperature": temperature,
        "last_interaction": user_state.get("last_interaction"),
    }

    if (user_state.get("lead_stage") or "").lower() in {"spam", "unsubscribed"} or temperature == "unsubscribed":
        return False, "hard_stop_status", payload

    if route == "warmup" and temperature == "dormant":
        return False, "dormant_blocks_warmup", payload

    if _thread_has_pending_reply(page_id, thread_id):
        return False, "pending_inbox_reply", payload

    if _recent_live_touch_exists(thread_id, since_hours=24):
        return False, "recent_live_touch", payload

    if route == "event" and temperature == "dormant" and _has_recent_live_event(thread_id, since_days=90):
        return False, "dormant_quarterly_limit", payload

    return True, "eligible", payload


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
        from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision

        dormant = find_dormant_seekers(page_id, max_seekers=max_seekers)
        if dormant["status"] != "success" or dormant["count"] == 0:
            logger.info("[WARMUP] No dormant seekers found.")
            return {"status": "no_dormant", "count": 0}

        processed = 0
        skipped = 0
        decisioned = 0
        for seeker in dormant["seekers"]:
            thread_id = seeker["thread_id"]
            eligible, reason, payload = _evaluate_proactive_eligibility(page_id, "warmup", thread_id)
            payload.update({"name": seeker.get("name"), "days_dormant": seeker.get("days_dormant")})
            if not eligible:
                log_mas_decision(page_id, "warmup", "thread", thread_id, "blocked", reason, dry_run=dry_run, payload=payload)
                skipped += 1
                continue

            if was_recently_warmed_up(thread_id, days=7):
                log_mas_decision(page_id, "warmup", "thread", thread_id, "blocked", "recent_live_warmup", dry_run=dry_run, payload=payload)
                logger.info(f"[WARMUP] Skipping {seeker['name']} — recently warmed up.")
                skipped += 1
                continue

            strategy = select_warmup_strategy(
                lead_stage=seeker.get("lead_stage", "Intake"),
                days_dormant=seeker.get("days_dormant", 7)
            )
            if not strategy:
                log_mas_decision(page_id, "warmup", "thread", thread_id, "blocked", "no_strategy", dry_run=dry_run, payload=payload)
                skipped += 1
                continue

            message_text = strategy.get("template", "")
            if message_text:
                log_mas_decision(
                    page_id,
                    "warmup",
                    "thread",
                    thread_id,
                    "allowed",
                    "eligible",
                    dry_run=dry_run,
                    payload={**payload, "strategy_type": strategy["type"]},
                )
                decisioned += 1
                log_warmup_campaign(
                    thread_id=thread_id,
                    seeker_name=seeker.get("name"),
                    strategy_type=strategy["type"],
                    message_text=message_text,
                    dry_run=dry_run
                )
                _update_user_decision_state(
                    thread_id,
                    payload["temperature"],
                    warmup_sent=not dry_run,
                )
                processed += 1
                logger.info(
                    f"[WARMUP] {'[DRY-RUN]' if dry_run else '[SENT]'} "
                    f"Warmup for {seeker['name']}: {strategy['type']}"
                )

        return {"status": "complete", "processed": processed, "skipped": skipped, "decisioned": decisioned}
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
        from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision

        events = get_upcoming_events(days_ahead=14)
        if events["status"] != "success" or events["count"] == 0:
            logger.info("[EVENT] No upcoming events found.")
            return {"status": "no_events", "count": 0}

        total_sent = 0
        skipped = 0
        decisioned = 0
        for event in events["events"]:
            targets = find_target_seekers_for_event(
                event_id=event["id"],
                city=event["city"],
                max_seekers=max_seekers
            )
            if targets["status"] != "success" or targets["count"] == 0:
                continue

            for seeker in targets["seekers"]:
                thread_id = seeker["thread_id"]
                if thread_id.startswith("comment_"):
                    skipped += 1
                    log_mas_decision(
                        page_id,
                        "event",
                        "comment_user",
                        thread_id,
                        "blocked",
                        "no_delivery_channel",
                        dry_run=dry_run,
                        payload={"event_id": event["id"], "city": event["city"], "source": seeker.get("source")},
                    )
                    continue

                eligible, reason, payload = _evaluate_proactive_eligibility(page_id, "event", thread_id)
                payload.update({"event_id": event["id"], "event_city": event["city"], "source": seeker.get("source")})
                if not eligible:
                    log_mas_decision(page_id, "event", "thread", thread_id, "blocked", reason, dry_run=dry_run, payload=payload)
                    skipped += 1
                    continue

                message_text = (
                    f"Xin chào {seeker['name']}! "
                    f"Sahaja Yoga có lớp thiền mới: {event['name']} "
                    f"tại {event['city']} vào ngày {event['event_date']}. "
                    f"Lớp hoàn toàn MIỄN PHÍ. Bạn có muốn tham gia không?"
                )
                log_mas_decision(
                    page_id,
                    "event",
                    "thread",
                    thread_id,
                    "allowed",
                    "eligible",
                    dry_run=dry_run,
                    payload=payload,
                )
                decisioned += 1
                log_event_campaign(
                    event_id=event["id"],
                    thread_id=thread_id,
                    seeker_name=seeker.get("name"),
                    message_text=message_text,
                    dry_run=dry_run
                )
                total_sent += 1

        logger.info(f"[EVENT] Cycle complete. Sent: {total_sent}")
        return {"status": "complete", "sent": total_sent, "skipped": skipped, "decisioned": decisioned}
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
