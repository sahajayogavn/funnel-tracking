import json
import logging
from datetime import datetime
from tools.l5_scheduler_core import (
    _load_user_state, _evaluate_proactive_eligibility, 
    _get_next_cool_step, _update_user_decision_state, _parse_db_time, COOL_SEQUENCE_TEMPLATES
)
from tools.l5_scheduler_adk import run_adk_reactor, run_adk_warmup_composer, run_adk_event_advertiser
from fb_pipeline.session.l2_activity_lock import scheduler_browser_cycle
import functools


# code:inbox-activity-lock-001:scheduler-guard-decorator
def _browser_job(tag: str):
    """Skip the job while an interactive CLI fetch owns the shared Chrome; otherwise
    hold ``scheduler_browser`` for the job's duration so the CLI waits for us."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(page_id: str, *args, **kwargs):
            with scheduler_browser_cycle(tag, page_id, logger) as may_run:
                if not may_run:
                    return {"status": "skipped", "reason": "cli_fetch_active"}
                return fn(page_id, *args, **kwargs)
        return wrapper
    return deco

logger = logging.getLogger("scheduler_routes")

# --- Route Implementations ---
# code:tool-scheduler-001:fetch-react

def run_fetch_cycle(page_id: str, dry_run: bool = True):
    """Fetch new inbox messages and comments from Facebook via CDP."""
    if dry_run:
        logger.info("[FETCH] [DRY-RUN] Skipping CDP fetch; dry-run must not contact Facebook.")
        return {"status": "skipped", "reason": "dry_run_no_fetch"}
    # code:inbox-activity-lock-001:scheduler-fetch
    with scheduler_browser_cycle("[FETCH]", page_id, logger) as may_run:
        if not may_run:
            return {"status": "skipped", "reason": "cli_fetch_active"}
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
    """Route 1: propose reactions; a human approval is required to execute."""
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
            reaction_type = run_adk_reactor(item, dry_run=True, page_id=page_id) or _select_reaction_heuristic(item)
            if not dry_run:
                from tools.l5_action_queue import enqueue_action
                # Reactions share the queue for their source so the UI retains
                # only the four requested decision groups.
                queue_type = "reply_comment" if item["item_type"] == "comment" else "reply_message"
                enqueue_action(
                    queue_type=queue_type, page_id=page_id, target_type=item["item_type"],
                    target_id=item["item_id"], target_name=item.get("thread_name"),
                    reaction_type=reaction_type, payload={"source": "reactor"},
                )
            processed += 1
            logger.info(
                f"[REACT] {'[DRY-RUN]' if dry_run else '[SENT]'} "
                f"proposal {reaction_type} on {item['item_type']} {item['item_id'][:20]}..."
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


_propose_lock = __import__("threading").Lock()

# code:tool-scheduler-001:reply
def run_propose_cycle(page_id: str, dry_run: bool = True, max_threads: int = 5, background: bool = True):
    """Inbox Reply Propose: decoupled MAS reply flow."""
    if not _propose_lock.acquire(blocking=False):
        logger.info("[PROPOSE] Previous propose pass still running; skipping this tick.")
        return {"status": "skipped", "reason": "propose_in_progress"}

    def _run() -> dict:
        logger.info(f"[PROPOSE] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting propose cycle...")
        try:
            from tools.l5_inbox_mas_runner import run_inbox_cycle
            from fb_pipeline.persistence.l4_llm_trace import span
            
            with span(trigger="scheduler", route="propose", page_id=page_id, dry_run=dry_run):
                result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=max_threads)
            logger.info(f"[PROPOSE] Result: {result.get('status', 'unknown')}, processed: {result.get('processed', 0)}")
            return result
        except Exception as e:
            logger.error(f"[PROPOSE] Failed: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            _propose_lock.release()

    if not background:
        return _run()
    import threading
    threading.Thread(target=_run, name="mas-propose", daemon=True).start()
    return {"status": "started"}

run_reply_cycle = run_propose_cycle


# code:tool-scheduler-001:cool-sequence
# code:tool-scheduler-001:warmup
@_browser_job("[WARMUP]")
def run_warmup_cycle(page_id: str, dry_run: bool = True, max_seekers: int = 5):
    """Route 2: Warm up dormant seekers."""
    logger.info(f"[WARMUP] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting warmup cycle...")
    try:
        from tools.l5_fetch_fb_messages import fetch_messages
        if not dry_run:
            logger.info(f"[WARMUP] Executing JIT Pre-Flight Cache validation for page {page_id}...")
            try:
                fetch_result = fetch_messages(page_id, credential_id="env", time_range="3d", force_refresh=False, use_cdp=True)
                if fetch_result.get("method") == "dynamic_cache_hit":
                    logger.info("[WARMUP] JIT Cache perfect. Proceeding to MAS.")
                else:
                    new_msgs = fetch_result.get("data", {}).get("stats", {}).get("new_messages", 0)
                    logger.info(f"[WARMUP] JIT Synchronized {new_msgs} new messages.")
            except Exception as e:
                logger.warning(f"[WARMUP] JIT Validation failed (continuing with DB state): {e}")

        from adk_agents.tools.l5_warmup_tools import (
            find_dormant_seekers, was_recently_warmed_up,
            select_warmup_strategy, log_warmup_campaign
        )
        from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision
        from tools.l5_inbox_mas_context import load_knowledge_context

        knowledge_context = load_knowledge_context()
        dormant = find_dormant_seekers(page_id, max_seekers=max_seekers)
        if dormant["status"] != "success" or dormant["count"] == 0:
            logger.info("[WARMUP] No dormant seekers found.")
            return {"status": "no_dormant", "count": 0}

        processed = 0
        skipped = 0
        decisioned = 0
        proposals = []
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
                    if not dry_run:
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
                    page_id=page_id,
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
                    page_id=page_id,
                ) or strategy.get("template", "")

            if message_text:
                log_mas_decision(
                    page_id,
                    "warmup",
                    subject_type,
                    thread_id,
                    "allowed",
                    "proposed_to_hitl",
                    dry_run=dry_run,
                    payload={**payload, "strategy_type": strategy["type"], "next_cool_step": next_cool_step},
                )
                decisioned += 1
                proposals.append({
                    "seeker": seeker,
                    "thread_id": thread_id,
                    "seeker_name": seeker.get("name"),
                    "strategy": strategy,
                    "message_text": message_text,
                    "next_temperature": next_temperature,
                    "next_cool_step": next_cool_step,
                    "dry_run": dry_run,
                    "subject_type": subject_type
                })
                processed += 1

        if proposals and not dry_run:
            from tools.l5_telegram_hitl import send_proposal_to_telegram
            logger.info(f"[WARMUP] Proposing {len(proposals)} individual messages to Telegram HITL.")
            for p in proposals:
                from tools.l5_action_queue import enqueue_action
                action_queue_id = enqueue_action(
                    queue_type="proactive_message", page_id=page_id, target_type="thread",
                    target_id=p["thread_id"], target_name=p["seeker_name"], action_text=p["message_text"], payload=p,
                )
                send_proposal_to_telegram(
                    "warmup",
                    p["thread_id"],
                    f"Warmup proposal for {p['seeker_name']}:\\n\\n{p['message_text']}",
                    {"action_queue_id": action_queue_id, "proposals": [p], "page_id": page_id}
                )

        return {"status": "complete", "processed": processed, "skipped": skipped, "decisioned": decisioned, "proposed": len(proposals)}
    except Exception as e:
        logger.error(f"[WARMUP] Failed: {e}")
        return {"status": "error", "error": str(e)}


# code:tool-scheduler-001:event
@_browser_job("[EVENT]")
def run_event_cycle(page_id: str, dry_run: bool = True, max_seekers: int = 10):
    """Route 3: Advertise new events to matched seekers."""
    logger.info(f"[EVENT] {'[DRY-RUN]' if dry_run else '[LIVE]'} Starting event cycle...")
    try:
        from tools.l5_fetch_fb_messages import fetch_messages
        if not dry_run:
            logger.info(f"[EVENT] Executing JIT Pre-Flight Cache validation for page {page_id}...")
            try:
                fetch_result = fetch_messages(page_id, credential_id="env", time_range="3d", force_refresh=False, use_cdp=True)
                if fetch_result.get("method") == "dynamic_cache_hit":
                    logger.info("[EVENT] JIT Cache perfect. Proceeding to MAS.")
                else:
                    new_msgs = fetch_result.get("data", {}).get("stats", {}).get("new_messages", 0)
                    logger.info(f"[EVENT] JIT Synchronized {new_msgs} new messages.")
            except Exception as e:
                logger.warning(f"[EVENT] JIT Validation failed (continuing with DB state): {e}")

        from adk_agents.tools.l5_event_tools import (
            get_upcoming_events, find_target_seekers_for_event,
            log_event_campaign
        )
        from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision
        from tools.l5_inbox_mas_context import load_knowledge_context

        knowledge_context = load_knowledge_context()
        events = get_upcoming_events(days_ahead=14)
        if events["status"] != "success" or events["count"] == 0:
            logger.info("[EVENT] No upcoming events found.")
            return {"status": "no_events", "count": 0}

        total_sent = 0
        skipped = 0
        decisioned = 0
        proposals = []
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
                    page_id=page_id,
                ) or fallback_message_text
                if message_text:
                    log_mas_decision(
                        page_id,
                        "event",
                        subject_type,
                        thread_id,
                        "allowed",
                        "proposed_to_hitl",
                        dry_run=dry_run,
                        payload=payload,
                    )
                    decisioned += 1
                    proposals.append({
                        "seeker": seeker,
                        "thread_id": thread_id,
                        "seeker_name": seeker.get("name"),
                        "event": event,
                        "message_text": message_text,
                        "dry_run": dry_run,
                        "subject_type": subject_type
                    })
                    total_sent += 1

        if proposals and not dry_run:
            from tools.l5_telegram_hitl import send_proposal_to_telegram
            summary = f"Event proposal for {len(proposals)} seekers:\\n"
            for i, p in enumerate(proposals[:5], 1):
                summary += f"{i}. {p['seeker_name']}: {p['message_text'][:40]}...\\n"
            if len(proposals) > 5:
                summary += f"...and {len(proposals) - 5} more."
            from tools.l5_action_queue import enqueue_action
            for p in proposals:
                action_queue_id = enqueue_action(
                    queue_type="proactive_message", page_id=page_id, target_type="thread",
                    target_id=p["thread_id"], target_name=p["seeker_name"], action_text=p["message_text"], payload=p,
                )
                send_proposal_to_telegram("event", p["thread_id"], f"Event proposal for {p['seeker_name']}:\\n\\n{p['message_text']}", {"action_queue_id": action_queue_id, "proposals": [p], "page_id": page_id})
            logger.info(f"[EVENT] Proposed {len(proposals)} messages to Telegram HITL.")

        return {"status": "complete", "sent": total_sent, "skipped": skipped, "decisioned": decisioned, "proposed": len(proposals)}
    except Exception as e:
        logger.error(f"[EVENT] Failed: {e}")
        return {"status": "error", "error": str(e)}


# --- Route: [CLASSIFY] LLM city/program enrichment (no browser) ---
# code:tool-citydetect-001:scheduler-route
_classify_lock = __import__("threading").Lock()


def run_classify_cycle(page_id: str, dry_run: bool = True, max_users: int | None = None,
                       background: bool = True):
    """Incremental LLM city/program classification for users whose
    classification is missing or older than their last customer message.

    Decoupled from [FETCH] on purpose (retrospective 2026-09-17: the LLM pass
    took longer than the crawl and blocked every browser route while Chrome
    sat idle). It touches only SQLite + the LLM endpoint, so it neither takes
    nor waits for ``scheduler_browser``/``inbox_fetch_cli``. It runs on a
    daemon thread so the single-threaded ``schedule`` loop keeps serving
    [REPLY]/[HITL] while batches are in flight; overlapping ticks are skipped.
    ``dry_run`` is accepted for route-signature parity and ignored — writing
    city/program to ``users`` is an enrichment, not an outbound action.
    """
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    from tools.l5_fetch_fb_city_classify import _post_scrape_llm_city_classify, count_stale_users

    if not _classify_lock.acquire(blocking=False):
        logger.info("[CLASSIFY] Previous classification pass still running; skipping this tick.")
        return {"status": "skipped", "reason": "classify_in_progress"}

    def _run() -> dict:
        conn = None
        try:
            conn = get_db_connection()
            stale = count_stale_users(conn, page_id)
            if stale == 0:
                logger.info("[CLASSIFY] Nothing stale; skipping LLM pass.")
                return {"status": "noop", "stale": 0}
            logger.info(f"[CLASSIFY] {stale} users stale; running LLM city/program pass"
                        f"{f' (max {max_users})' if max_users else ''}...")
            result = _post_scrape_llm_city_classify(conn, page_id, only_stale=True, max_users=max_users)
            logger.info(f"[CLASSIFY] Done: {result}")
            return {"status": "complete", "stale_before": stale, **result}
        except Exception as e:
            logger.error(f"[CLASSIFY] Failed: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            _classify_lock.release()

    if not background:
        return _run()
    import threading
    threading.Thread(target=_run, name="mas-classify", daemon=True).start()
    return {"status": "started"}
