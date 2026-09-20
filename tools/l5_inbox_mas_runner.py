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

from fb_pipeline.browser.l3_inbox import extract_ad_id_labels
from fb_pipeline.contracts.l1_inbox import extract_user_info, parse_page_id
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, record_fetch
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session

from tools.l5_inbox_mas_context import setup_llm_env, load_knowledge_context
from tools.l5_inbox_mas_pipeline import run_adk_pipeline, _sanitize_reply
from tools.l5_delivery_guard import conversation_snapshot
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
                    max_threads: int = 5, target_thread: str = None,
                    target_city: str = "Hà Nội") -> dict:
    from adk_agents.tools.seeker_tools import find_unreplied_threads
    import datetime

    results = []
    
    # Check freshness gate
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT qa_status, fetched_at FROM fetch_log WHERE page_id=? ORDER BY fetched_at DESC LIMIT 1",
            (page_id,)
        ).fetchone()
        if row:
            qa_status = row["qa_status"]
            fetched_at_str = row["fetched_at"]
            
            # Simple check for freshness
            # fetched_at is usually ISO format e.g., 2026-09-17 09:00:00
            try:
                fetched_at = datetime.datetime.fromisoformat(fetched_at_str)
                # assuming timezone-aware or naive but matching now()
                # A safer way in sqlite:
            except ValueError:
                fetched_at = datetime.datetime.strptime(fetched_at_str, "%Y-%m-%d %H:%M:%S")
                
            # If not using python, we could just query sqlite: 
            # we'll do the python way but safe fallback
        
        # Let's use SQLite directly for the freshness gate to avoid timezone headaches
        is_fresh = conn.execute('''
            SELECT 1 FROM fetch_log 
            WHERE page_id=? 
              AND (qa_status IS NULL OR qa_status IN ('passed', 'warn'))
              AND datetime(fetched_at) >= datetime('now', '-6 hours')
            ORDER BY fetched_at DESC LIMIT 1
        ''', (page_id,)).fetchone()
        
        if not is_fresh:
            # Maybe there's no fetch log at all, or it's old/failed
            conn.close()
            return {"status": "skipped", "reason": "fetch_untrusted"}
            
        conn.close()
    except Exception as e:
        logger.error(f"Freshness check failed: {e}")

    try:
        logger.info("Finding unreplied threads...")
        fetch_limit = 200
        unreplied = find_unreplied_threads(page_id, limit=fetch_limit)

        if target_thread:
            logger.info(f"Filtering to target thread: {target_thread}")
            unreplied["threads"] = [t for t in unreplied["threads"] if t.get("thread_name") == target_thread]
            unreplied["count"] = len(unreplied["threads"])

        if unreplied["status"] != "success" or unreplied["count"] == 0:
            logger.info("No unreplied threads found. Cycle complete.")
            return {"status": "no_unreplied"}

        logger.info(f"Found {unreplied['count']} unreplied thread(s).")

        candidates = []
        gate_skipped = []
        from adk_agents.tools.seeker_tools import (
            claim_scheduled_inbox_message, get_thread_messages, lookup_seeker,
        )
        from adk_agents.tools.l5_stage_tools import evaluate_stage_gate
        from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision
        from fb_pipeline.contracts.l1_conversation_state import (
            compute_conversation_state, format_conversation_lines, format_now_context,
            ACTION_REPLY, ACTION_REPLY_LATE,
        )
        from tools.l5_telegram_hitl import format_inbox_proposal, format_escalation_proposal, send_proposal_to_telegram
        from tools.l5_action_queue import enqueue_action, has_active_proposal

        now = datetime.datetime.now()
        now_context = format_now_context(now)

        def queue_missing_mas_outcome(payload: dict, note: str) -> None:
            """A post-admission MAS failure is work for a human, never a skip.

            Deterministic gates above may legitimately produce no reply. Once a
            candidate has passed those gates, however, an empty/unsafe agent
            result must leave an observable HITL item instead of disappearing
            from the operating queue.
            """
            thread_id = payload["thread_id"]
            thread_name = payload["thread_name"]
            log_mas_decision(
                page_id, "inbox_escalation", "thread", thread_id,
                "non_convergence", note, dry_run=dry_run,
                payload={"thread_name": thread_name, "loop_count": 0},
            )
            send_proposal_to_telegram(
                route="inbox", thread_id=thread_id,
                proposed_text=format_escalation_proposal(
                    thread_id, thread_name, payload["messages"], "non_convergence", note,
                ),
                payload={"status": "escalated", "escalation_reason": "non_convergence"},
                escalation_reason="non_convergence", escalation_note=note,
            )

        for thread in unreplied["threads"]:
            thread_id = thread["thread_id"]
            thread_name = thread["thread_name"]
            msg_result = get_thread_messages(thread_id)
            if msg_result["status"] != "success" or msg_result["count"] == 0:
                continue

            # code:inbox-conv-state-001 — time gate + conversation state BEFORE the LLM.
            state = compute_conversation_state(msg_result["messages"], now=now)
            log_mas_decision(
                page_id, "inbox_gate", "thread", thread_id, state.action, state.reason,
                dry_run=dry_run, payload={**state.to_dict(), "thread_name": thread_name},
            )
            if state.action not in (ACTION_REPLY, ACTION_REPLY_LATE):
                gate_skipped.append({"status": "gate_skip", "thread_name": thread_name,
                                     "state": state.state, "action": state.action,
                                     "reason": state.reason, "age_hours": state.age_hours})
                continue

            # code:stage-gate-decouple-001 — only a genuine customer turn with a
            # phone number is evidence; the gate no longer runs per generated draft.
            stage_result = {}
            if state.has_phone:
                stage_result = evaluate_stage_gate(thread_id)
                if stage_result.get("promoted"):
                    log_mas_decision(page_id, "stage_gate", "thread", thread_id, "promoted",
                                     stage_result.get("reason"), dry_run=dry_run, payload=stage_result)

            # Duplicate check before spending an LLM call.
            if has_active_proposal(thread_id, "reply_message"):
                gate_skipped.append({"status": "skipped_duplicate_proposal", "thread_name": thread_name})
                continue

            seeker = lookup_seeker(thread_id)
            seeker_city = (seeker or {}).get("city")
            # code:agent-mas-002:city-admit — a mismatch against a KNOWN, different
            # city still skips (keeps one cycle scoped to one city's operating
            # capacity). An unresolved/Unknown city is admitted instead of dropped:
            # the first-pass classifier can be wrong, and the InboxOrchestrator
            # (get_seeker_profile / propose_seeker_update) is what actually corrects
            # it from the conversation. Silently excluding Unknown seekers forever
            # was the original defect this replaces.
            if target_city and seeker_city and seeker_city != "Unknown" and seeker_city != target_city:
                gate_skipped.append({
                    "status": "gate_skip",
                    "thread_name": thread_name,
                    "reason": "city_not_eligible",
                    "city": seeker_city,
                    "target_city": target_city,
                })
                continue

            # Claim before spending LLM tokens.  A scheduled run must process a
            # customer turn at most once, regardless of whether the result is a
            # pending human review, approval/rejection, escalation, timeout, or
            # error.  Manual website MAS intentionally bypasses this claim.
            latest_customer_seq = next(
                (message.get("seq") for message in reversed(msg_result["messages"])
                 if message.get("sender") == "Customer"),
                None,
            )
            if latest_customer_seq is None or not claim_scheduled_inbox_message(
                thread_id, expected_message_seq=latest_customer_seq,
            ):
                gate_skipped.append({"status": "skipped_already_processed", "thread_name": thread_name})
                continue

            recent_messages = msg_result["messages"][-15:] if len(msg_result["messages"]) > 15 else msg_result["messages"]

            candidates.append({
                "thread_id": thread_id,
                "thread_name": thread_name,
                "seeker": seeker,
                "messages": recent_messages,
                "conversation_text": format_conversation_lines(
                    recent_messages, msg_result.get("reaction_events") or [],
                ),
                "full_messages_json": msg_result["messages"],
                "reaction_events": msg_result.get("reaction_events") or [],
                "latest_timestamp": msg_result["messages"][-1].get("timestamp"),
                "conversation_state": state.to_dict(),
                "late": state.late,
                "stage_result": stage_result,
            })

            if len(candidates) >= max_threads:
                break

        results.extend(gate_skipped)
        if not candidates:
            return {"status": "no_valid_threads", "processed": len(results), "results": results}

        logger.info("Running isolated MAS pipelines for %d eligible thread(s)", len(candidates))
        for payload in candidates:
            thread_id = payload["thread_id"]
            thread_name = payload["thread_name"]
            try:
                # One latest-customer-message candidate gets one session and one trace.
                # The deterministic gate above has already excluded stale/answered turns.
                pipeline_kwargs = {
                    "trigger": "scheduler", "page_id": page_id,
                    "subject_id": thread_id, "now_context": now_context,
                }
                if payload.get("reaction_events"):
                    pipeline_kwargs["reaction_events"] = payload["reaction_events"]
                delivery_snapshot = conversation_snapshot(payload["messages"])
                llm_output = run_adk_pipeline(
                    payload["messages"], payload["seeker"], **pipeline_kwargs,
                )
                if not llm_output:
                    queue_missing_mas_outcome(payload, "MAS không trả về kết quả cuối cùng.")
                    results.append({"status": "escalated", "thread_name": thread_name,
                                    "escalation_reason": "non_convergence"})
                    continue

                # code:agent-mas-002:escalation-taxonomy — the orchestrator decided this
                # needs a human, not another rewrite. Queue it as a distinct HITL card
                # with no reply attached; there is nothing here for an operator to
                # approve-and-send, only to read and answer themselves.
                escalation_reason = llm_output.get("escalation_reason", "")
                if escalation_reason:
                    log_mas_decision(page_id, "inbox_escalation", "thread", thread_id, escalation_reason,
                                     llm_output.get("escalation_note", ""), dry_run=dry_run,
                                     payload={"thread_name": thread_name, "loop_count": llm_output.get("loop_count")})
                    combined_text = format_escalation_proposal(
                        thread_id, thread_name, payload["messages"],
                        escalation_reason, llm_output.get("escalation_note", ""),
                    )
                    send_proposal_to_telegram(
                        route="inbox", thread_id=thread_id, proposed_text=combined_text,
                        payload={"status": "escalated", "escalation_reason": escalation_reason,
                                "classification": llm_output.get("classification", "")},
                        escalation_reason=escalation_reason, escalation_note=llm_output.get("escalation_note", ""),
                    )
                    results.append({"status": "escalated", "thread_name": thread_name,
                                    "escalation_reason": escalation_reason})
                    continue

                if not llm_output.get("reply_text"):
                    queue_missing_mas_outcome(payload, "MAS không tạo được reply hoặc verdict an toàn.")
                    results.append({"status": "escalated", "thread_name": thread_name,
                                    "escalation_reason": "non_convergence"})
                    continue

                reply_text = _sanitize_reply(llm_output.get("reply_text", ""))
                classification = llm_output.get("classification", "")

                if not reply_text:
                    queue_missing_mas_outcome(payload, "Reply bị deterministic safety gate từ chối.")
                    results.append({"status": "escalated", "thread_name": thread_name,
                                    "escalation_reason": "non_convergence"})
                    continue

                latest_customer_message_timestamp = None
                last_message_seq = None
                conn_seq = get_db_connection()
                seq_row = conn_seq.execute(
                    "SELECT MAX(seq) as max_seq FROM messages WHERE thread_id=?",
                    (thread_id,)
                ).fetchone()
                conn_seq.close()
                if seq_row and seq_row["max_seq"] is not None:
                    last_message_seq = seq_row["max_seq"]
                latest_customer_message_timestamp = payload["conversation_state"].get("last_customer_at")

                # code:agent-mas-001:no-reply-sentinel
                if reply_text.strip().upper().startswith("[NO_REPLY"):
                    log_mas_decision(page_id, "inbox_gate", "thread", thread_id, "llm_no_reply",
                                     reply_text.strip()[:120], dry_run=dry_run,
                                     payload={"thread_name": thread_name, "classification": classification})
                    results.append({"status": "no_reply", "thread_name": thread_name, "reason": reply_text.strip()[:120]})
                    continue

                is_out_of_scope = (reply_text.strip() == "[OUT_OF_SCOPE]")

                if is_out_of_scope:
                    combined_text = (
                        "🚨 [OUT OF SCOPE] Lời nhắn không thuộc phạm vi MAS (Sahaja Yoga)\n\n"
                        + format_inbox_proposal(thread_id, thread_name, payload["messages"], "Cần người phụ trách xem xét.")
                    )
                    msg_id = send_proposal_to_telegram(
                        route="inbox", thread_id=thread_id, proposed_text=combined_text,
                        payload={"classification": classification, "status": "out_of_scope", "last_message_seq": last_message_seq}
                    )
                    results.append({"status": "out_of_scope", "thread_name": thread_name, "classification": classification})
                    continue

                stage_result = payload.get("stage_result") or {}

                combined_text = format_inbox_proposal(
                    thread_id=thread_id,
                    seeker_name=thread_name,
                    messages=payload["messages"],
                    reply_text=reply_text,
                )

                action_queue_id = enqueue_action(
                    queue_type="reply_message", page_id=page_id, target_type="thread",
                    target_id=thread_id, target_name=thread_name, action_text=reply_text,
                    payload={
                        "source": "inbox_mas",
                        "classification": classification,
                        "customer_message_timestamp": latest_customer_message_timestamp,
                        "conversation_snapshot": delivery_snapshot,
                        "seeker": payload["seeker"],
                        "conversation_state": payload["conversation_state"],
                    },
                )
                msg_id = send_proposal_to_telegram(
                    route="inbox", thread_id=thread_id, proposed_text=combined_text,
                    payload={
                        "action_queue_id": action_queue_id,
                        "classification": classification,
                        "msg_messages_json": payload["full_messages_json"],
                        "seeker_dict": payload["seeker"],
                        "proposals": [{"thread_id": thread_id, "seeker_name": thread_name, "message_text": reply_text}],
                        "last_message_seq": last_message_seq
                    }
                )

                results.append({
                    "status": "queued_for_human_approval",
                    "action_queue_id": action_queue_id,
                    "thread_name": thread_name,
                    "classification": classification,
                    "reply_text": reply_text,
                    "stage_result": stage_result,
                })

            except Exception as e:
                logger.error(f"Error processing resulting drafted reply for {thread_name}: {e}")
                results.append({"status": "error", "error": str(e), "thread_name": thread_name})

    except Exception as e:
        logger.error(f"Cycle error: {e}")
        return {"status": "error", "error": str(e)}

    return {
        "status": "complete",
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
    parser.add_argument(
        "--city", type=str, default="Hà Nội",
        help="Only run MAS for seekers whose city exactly matches this value (default: Hà Nội)."
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
    logger.info(f"MAS city filter: {args.city or 'disabled'}")

    if args.once:
        result = run_inbox_cycle(page_id, dry_run=dry_run, max_threads=max_threads_to_use,
                                 target_thread=args.target_thread, target_city=args.city)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.poll:
        logger.info(f"Starting polling loop (interval: {args.interval}s)...")
        while True:
            try:
                result = run_inbox_cycle(page_id, dry_run=dry_run,
                                         max_threads=max_threads_to_use,
                                         target_thread=args.target_thread,
                                         target_city=args.city)
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
