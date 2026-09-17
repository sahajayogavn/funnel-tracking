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

        batch_payload = []
        from adk_agents.tools.seeker_tools import lookup_seeker, get_thread_messages
        from adk_agents.tools.l5_stage_tools import evaluate_stage_gate
        from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision
        from tools.l5_telegram_hitl import send_proposal_to_telegram

        for thread in unreplied["threads"]:
            thread_id = thread["thread_id"]
            msg_result = get_thread_messages(thread_id)
            if msg_result["status"] != "success" or msg_result["count"] == 0:
                continue

            last_msg = msg_result["messages"][-1] if msg_result["messages"] else None
            if last_msg and last_msg.get("sender") == "Page":
                continue

            seeker = lookup_seeker(thread_id)
            recent_messages = msg_result["messages"][-15:] if len(msg_result["messages"]) > 15 else msg_result["messages"]
            
            # Find last customer message seq equivalent. Since we get them from DB, the length or max seq is needed.
            # We don't have seq in `msg_result["messages"]`, so we will use len of full_messages_json or something.
            # Wait, `get_thread_messages` does not return `seq`. 
            # I can just count the total messages. 
            
            batch_payload.append({
                "thread_id": thread_id,
                "thread_name": thread["thread_name"],
                "seeker": seeker,
                "messages": recent_messages,
                "full_messages_json": msg_result["messages"],
                "latest_timestamp": msg_result["messages"][-1].get("timestamp")
            })

            if len(batch_payload) >= max_threads:
                break

        if not batch_payload:
            return {"status": "no_valid_threads"}

        logger.info(f"Running Batched ADK Pipeline for {len(batch_payload)} threads...")
        batch_results = run_adk_batch_pipeline(batch_payload)
        
        llm_replies = {item.get("thread_id"): item for item in batch_results if isinstance(item, dict) and item.get("thread_id")}

        for payload in batch_payload:
            thread_id = payload["thread_id"]
            thread_name = payload["thread_name"]
            try:
                llm_output = llm_replies.get(thread_id)
                if not llm_output or not llm_output.get("reply_text"):
                    results.append({"status": "no_reply", "thread_name": thread_name})
                    continue

                reply_text = _sanitize_reply(llm_output.get("reply_text", ""))
                classification = llm_output.get("classification", "")

                if not reply_text:
                    results.append({"status": "no_reply", "thread_name": thread_name})
                    continue

                latest_customer_message_timestamp = None
                last_message_seq = None
                
                # Fetch the exact seq from DB for the last message
                conn_seq = get_db_connection()
                seq_row = conn_seq.execute(
                    "SELECT MAX(seq) as max_seq FROM messages WHERE thread_id=?", 
                    (thread_id,)
                ).fetchone()
                conn_seq.close()
                if seq_row and seq_row["max_seq"] is not None:
                    last_message_seq = seq_row["max_seq"]

                for message in reversed(payload["full_messages_json"]):
                    if message.get("sender") == "Customer":
                        latest_customer_message_timestamp = message.get("timestamp")
                        break

                convo_lines = []
                for msg in payload["messages"]:
                    sender_label = msg.get("sender", "Unknown")
                    convo_lines.append(f"[{sender_label}]: {msg.get('content', '')}")
                convo_text = "\\n".join(convo_lines)
                
                if len(convo_text) > 2500:
                    convo_text = "...(truncated)...\\n" + convo_text[-2500:]

                is_out_of_scope = (reply_text.strip() == "[OUT_OF_SCOPE]")
                stage_result = {}

                if is_out_of_scope:
                    combined_text = f"🚨 [OUT OF SCOPE] Lời nhắn không thuộc phạm vi MAS (Sahaja Yoga):\\n\\n{convo_text}"
                    msg_id = send_proposal_to_telegram(
                        route="inbox", thread_id=thread_id, proposed_text=combined_text,
                        payload={"classification": classification, "status": "out_of_scope", "last_message_seq": last_message_seq}
                    )
                    results.append({"status": "out_of_scope", "thread_name": thread_name, "classification": classification})
                    continue

                stage_result = evaluate_stage_gate(thread_id)
                if stage_result.get("promoted"):
                    log_mas_decision(page_id, "stage_gate", "thread", thread_id, "promoted", stage_result.get("reason"), dry_run=dry_run, payload=stage_result)

                combined_text = f"📜 Cuộc hội thoại gần đây:\\n{convo_text}\\n\\n🤖 Đề xuất trả lời (MAS):\\n{reply_text}"
                if len(combined_text) > 3500:
                    combined_text = combined_text[:3500] + "... (truncated)"

                from tools.l5_action_queue import enqueue_action, has_active_proposal
                if has_active_proposal(thread_id, "reply_message"):
                    results.append({"status": "skipped_duplicate_proposal", "thread_name": thread_name})
                    continue
                action_queue_id = enqueue_action(
                    queue_type="reply_message", page_id=page_id, target_type="thread",
                    target_id=thread_id, target_name=thread_name, action_text=reply_text,
                    payload={
                        "classification": classification,
                        "customer_message_timestamp": latest_customer_message_timestamp,
                        "seeker": payload["seeker"],
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
