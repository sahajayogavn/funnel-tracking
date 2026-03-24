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

COOL_SEQUENCE_TEMPLATES = {
    1: "Chào bạn, lâu rồi mình không thấy bạn. Hy vọng bạn khỏe 🙏",
    2: "Mình chia sẻ bạn mẹo thiền 5 phút mỗi sáng giúp tỉnh táo cả ngày 🌿",
    3: "Cuối tuần này có Thiền Âm nhạc tại {city}, hoàn toàn miễn phí. Bạn muốn tham gia không?",
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


# code:tool-stage-001
# code:tool-event-001
def _load_user_state(thread_id: str) -> dict | None:
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection

    if thread_id.startswith("comment_"):
        comment_key = thread_id[len("comment_"):]
        conn = get_comment_db_connection()
        try:
            row = conn.execute(
                "SELECT commenter_name AS thread_name, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step, city "
                "FROM comment_users WHERE fb_user_id = ?",
                (comment_key,),
            ).fetchone()
            if row:
                return {"thread_id": thread_id, **dict(row)}
            return None
        finally:
            conn.close()

    conn = get_db_connection(logger=logger)
    try:
        row = conn.execute(
            "SELECT thread_id, thread_name, lead_stage, last_interaction, temperature, last_warmup_at, warmup_count, cool_step, city "
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


# code:tool-scheduler-001:cool-sequence
def _get_next_cool_step(user_state: dict) -> int | None:
    current_step = int(user_state.get("cool_step") or 0)
    return current_step + 1 if current_step < 3 else None


# code:tool-stage-001
# code:tool-scheduler-001:cool-sequence
def _update_user_decision_state(
    thread_id: str,
    temperature: str,
    warmup_sent: bool = False,
    cool_step: int | None = None,
):
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, get_comment_db_connection

    is_comment_user = thread_id.startswith("comment_")
    if is_comment_user:
        conn = get_comment_db_connection()
        where_clause = "fb_user_id = ?"
        thread_key = thread_id[len("comment_"):]
        table_name = "comment_users"
    else:
        conn = get_db_connection(logger=logger)
        where_clause = "thread_id = ?"
        thread_key = thread_id
        table_name = "users"

    try:
        if warmup_sent:
            if cool_step is None:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ?, last_warmup_at = datetime('now'), warmup_count = COALESCE(warmup_count, 0) + 1 WHERE {where_clause}",
                    (temperature, thread_key),
                )
            else:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ?, last_warmup_at = datetime('now'), warmup_count = COALESCE(warmup_count, 0) + 1, cool_step = ? WHERE {where_clause}",
                    (temperature, cool_step, thread_key),
                )
        else:
            if cool_step is None:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ? WHERE {where_clause}",
                    (temperature, thread_key),
                )
            else:
                conn.execute(
                    f"UPDATE {table_name} SET temperature = ?, cool_step = ? WHERE {where_clause}",
                    (temperature, cool_step, thread_key),
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
            reaction_type = run_adk_reactor(item, dry_run=True) or _select_reaction_heuristic(item)
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


# code:tool-scheduler-001:reactor-adk
# code:tool-scheduler-001:warmup-composer-adk
# code:tool-scheduler-001:event-advertiser-adk
def _run_adk_route(agent, app_name: str, user_id: str, state: dict, prompt: str) -> list[dict]:
    """Run a single ADK route agent and collect text events."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service,
    )
    session = asyncio.run(
        session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
        )
    )

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

    events = []
    for event in runner.run(user_id=user_id, session_id=session.id, new_message=user_msg):
        if hasattr(event, "content") and event.content and event.content.parts:
            text = event.content.parts[0].text
            if text:
                events.append({
                    "author": getattr(event, "author", ""),
                    "text": text.strip(),
                })
    return events


def run_adk_reactor(item: dict, dry_run: bool = True) -> str:
    """Run the Reactor ADK agent for a reaction decision."""
    from adk_agents.agent import reactor

    _ = dry_run
    state = {
        "reaction_content": item.get("content") or "",
        "reaction_sender": json.dumps(
            {
                "sender": item.get("sender"),
                "item_type": item.get("item_type"),
                "thread_id": item.get("thread_id"),
                "post_id": item.get("post_id"),
                "thread_name": item.get("thread_name"),
                "timestamp": item.get("timestamp"),
            },
            ensure_ascii=False,
            indent=2,
        ),
    }
    events = _run_adk_route(
        agent=reactor,
        app_name="sahajayoga_reactor",
        user_id="scheduler_reactor",
        state=state,
        prompt="Choose the best Facebook reaction using the provided session state.",
    )
    valid_reactions = {"like", "love", "care", "haha", "wow", "sad"}
    for event in reversed(events):
        reaction = (event.get("text") or "").strip().lower()
        if reaction in valid_reactions:
            return reaction
    return ""


def run_adk_warmup_composer(seeker: dict, strategy: dict, knowledge_context: str, dry_run: bool = True) -> str:
    """Run the WarmUpComposer ADK agent for a warm-up message."""
    from adk_agents.agent import warmup_composer

    _ = dry_run
    seeker_context = json.dumps(seeker, ensure_ascii=False, indent=2)
    warmup_brief = json.dumps(
        {
            "seeker_context": seeker,
            "strategy_type": strategy.get("type"),
            "cool_step": strategy.get("cool_step"),
            "knowledge_context": knowledge_context,
        },
        ensure_ascii=False,
        indent=2,
    )
    events = _run_adk_route(
        agent=warmup_composer,
        app_name="sahajayoga_warmup",
        user_id="scheduler_warmup",
        state={
            "seeker_context": seeker_context,
            "strategy_type": strategy.get("type") or "",
            "cool_step": strategy.get("cool_step") or "",
            "knowledge_context": knowledge_context,
            "warmup_brief": warmup_brief,
        },
        prompt="Compose a warm-up message using the provided session state.",
    )
    for event in reversed(events):
        message_text = (event.get("text") or "").strip()
        if message_text:
            return message_text
    return ""


def run_adk_event_advertiser(event: dict, seeker: dict, knowledge_context: str, dry_run: bool = True) -> str:
    """Run the EventAdvertiser ADK agent for an event notification."""
    from adk_agents.agent import event_advertiser

    _ = dry_run
    event_details = json.dumps({**event, "knowledge_context": knowledge_context}, ensure_ascii=False, indent=2)
    seeker_context = json.dumps(seeker, ensure_ascii=False, indent=2)
    events = _run_adk_route(
        agent=event_advertiser,
        app_name="sahajayoga_event",
        user_id="scheduler_event",
        state={
            "event_details": event_details,
            "seeker_context": seeker_context,
        },
        prompt="Compose an event notification using the provided session state.",
    )
    for event_output in reversed(events):
        message_text = (event_output.get("text") or "").strip()
        if message_text:
            return message_text
    return ""


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


# code:tool-scheduler-001:cool-sequence
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
        from tools.l5_inbox_mas_runner import load_knowledge_context

        knowledge_context = load_knowledge_context()
        dormant = find_dormant_seekers(page_id, max_seekers=max_seekers)
        if dormant["status"] != "success" or dormant["count"] == 0:
            logger.info("[WARMUP] No dormant seekers found.")
            return {"status": "no_dormant", "count": 0}

        processed = 0
        skipped = 0
        decisioned = 0
        for seeker in dormant["seekers"]:
            thread_id = seeker["thread_id"]
            source = seeker.get("source")
            subject_type = "comment_user" if thread_id.startswith("comment_") else "thread"

            if thread_id.startswith("comment_"):
                logger.info(f"[WARMUP] Skipping comment user {seeker.get('name')} ({thread_id}) — no delivery channel.")
                log_mas_decision(
                    page_id,
                    "warmup",
                    subject_type,
                    thread_id,
                    "blocked",
                    "no_delivery_channel",
                    dry_run=dry_run,
                    payload={
                        "name": seeker.get("name"),
                        "days_dormant": seeker.get("days_dormant"),
                        "source": source,
                        "temperature": seeker.get("temperature"),
                        "last_warmup_at": seeker.get("last_warmup_at"),
                        "warmup_count": seeker.get("warmup_count"),
                        "cool_step": seeker.get("cool_step"),
                    },
                )
                skipped += 1
                continue

            user_state = _load_user_state(thread_id)
            eligible, reason, payload = _evaluate_proactive_eligibility(page_id, "warmup", thread_id)
            payload.update({"name": seeker.get("name"), "days_dormant": seeker.get("days_dormant"), "source": source})
            if user_state:
                payload.update({"cool_step": user_state.get("cool_step", 0), "last_warmup_at": user_state.get("last_warmup_at")})
            if not eligible:
                log_mas_decision(page_id, "warmup", subject_type, thread_id, "blocked", reason, dry_run=dry_run, payload=payload)
                skipped += 1
                continue

            strategy = None
            message_text = ""
            next_cool_step = None
            next_temperature = payload["temperature"]

            if payload["temperature"] == "cool":
                if not user_state:
                    log_mas_decision(page_id, "warmup", subject_type, thread_id, "blocked", "missing_user_state", dry_run=dry_run, payload=payload)
                    skipped += 1
                    continue

                next_cool_step = _get_next_cool_step(user_state)
                if next_cool_step is None:
                    next_temperature = "cold"
                    log_mas_decision(
                        page_id,
                        "warmup",
                        subject_type,
                        thread_id,
                        "blocked",
                        "cool_sequence_exhausted",
                        dry_run=dry_run,
                        payload={**payload, "temperature": next_temperature, "cool_step": 0},
                    )
                    _update_user_decision_state(thread_id, next_temperature, warmup_sent=False, cool_step=0)
                    skipped += 1
                    continue

                last_warmup_at = _parse_db_time(user_state.get("last_warmup_at"))
                days_since_last_warmup = None if last_warmup_at is None else (datetime.now() - last_warmup_at).days
                if next_cool_step == 2 and (days_since_last_warmup is None or days_since_last_warmup < 3):
                    log_mas_decision(page_id, "warmup", subject_type, thread_id, "blocked", "cool_step_interval_pending", dry_run=dry_run, payload={**payload, "required_gap_days": 3, "next_cool_step": 2})
                    skipped += 1
                    continue
                if next_cool_step == 3 and (days_since_last_warmup is None or days_since_last_warmup < 5):
                    log_mas_decision(page_id, "warmup", subject_type, thread_id, "blocked", "cool_step_interval_pending", dry_run=dry_run, payload={**payload, "required_gap_days": 5, "next_cool_step": 3})
                    skipped += 1
                    continue

                strategy = {"type": f"cool_step_{next_cool_step}", "cool_step": next_cool_step}
                template = COOL_SEQUENCE_TEMPLATES[next_cool_step]
                city = seeker.get("city") or user_state.get("city") or "thành phố của bạn"
                strategy["template"] = template.format(city=city)
                message_text = run_adk_warmup_composer(
                    seeker,
                    strategy,
                    knowledge_context,
                    dry_run=True,
                ) or strategy.get("template", "")
            else:
                if was_recently_warmed_up(thread_id, days=7):
                    log_mas_decision(page_id, "warmup", subject_type, thread_id, "blocked", "recent_live_warmup", dry_run=dry_run, payload=payload)
                    logger.info(f"[WARMUP] Skipping {seeker['name']} — recently warmed up.")
                    skipped += 1
                    continue

                strategy = select_warmup_strategy(
                    lead_stage=seeker.get("lead_stage", "Intake"),
                    days_dormant=seeker.get("days_dormant", 7)
                )
                if not strategy:
                    log_mas_decision(page_id, "warmup", subject_type, thread_id, "blocked", "no_strategy", dry_run=dry_run, payload=payload)
                    skipped += 1
                    continue
                strategy = {**strategy, "cool_step": next_cool_step}
                message_text = run_adk_warmup_composer(
                    seeker,
                    strategy,
                    knowledge_context,
                    dry_run=True,
                ) or strategy.get("template", "")

            if message_text:
                log_mas_decision(
                    page_id,
                    "warmup",
                    subject_type,
                    thread_id,
                    "allowed",
                    "eligible",
                    dry_run=dry_run,
                    payload={**payload, "strategy_type": strategy["type"], "next_cool_step": next_cool_step},
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
                    next_temperature,
                    warmup_sent=not dry_run,
                    cool_step=next_cool_step,
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
        from tools.l5_inbox_mas_runner import load_knowledge_context

        knowledge_context = load_knowledge_context()
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
                subject_type = "comment_user" if thread_id.startswith("comment_") else "thread"
                if thread_id.startswith("comment_"):
                    skipped += 1
                    logger.info(f"[EVENT] Skipping comment user {seeker.get('name')} ({thread_id}) — no delivery channel.")
                    log_mas_decision(
                        page_id,
                        "event",
                        "comment_user",
                        thread_id,
                        "blocked",
                        "no_delivery_channel",
                        dry_run=dry_run,
                        payload={
                            "event_id": event["id"],
                            "city": event["city"],
                            "source": seeker.get("source"),
                            "temperature": seeker.get("temperature"),
                            "last_warmup_at": seeker.get("last_warmup_at"),
                            "warmup_count": seeker.get("warmup_count"),
                            "cool_step": seeker.get("cool_step"),
                        },
                    )
                    continue

                eligible, reason, payload = _evaluate_proactive_eligibility(page_id, "event", thread_id)
                payload.update({"event_id": event["id"], "event_city": event["city"], "source": seeker.get("source")})
                if not eligible:
                    log_mas_decision(page_id, "event", subject_type, thread_id, "blocked", reason, dry_run=dry_run, payload=payload)
                    skipped += 1
                    continue

                fallback_message_text = (
                    f"Xin chào {seeker['name']}! "
                    f"Sahaja Yoga có lớp thiền mới: {event['name']} "
                    f"tại {event['city']} vào ngày {event['event_date']}. "
                    f"Lớp hoàn toàn MIỄN PHÍ. Bạn có muốn tham gia không?"
                )
                message_text = run_adk_event_advertiser(
                    event,
                    seeker,
                    knowledge_context,
                    dry_run=True,
                ) or fallback_message_text
                log_mas_decision(
                    page_id,
                    "event",
                    subject_type,
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
