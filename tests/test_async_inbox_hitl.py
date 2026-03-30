#!/usr/bin/env python3
"""
Live Telegram HITL Batch Tester
code:test-live-telegram-hitl-001

A wrapper script to safely execute Case C: Proactive Batch Delivery (Warm-up Route)
exclusively against the "Hung Bui" thread, satisfying Rule 10 safety guardrails.
"""
import os
import sys
import time
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tools.l5_telegram_hitl import (
    send_proposal_to_telegram, 
    poll_telegram_updates, 
    check_hitl_status,
    get_db_connection
)
from tools.l5_scheduler import hitl_execution_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
logger = logging.getLogger("TestLiveHitlBatch")

def run_test():
    page_id = "1548373332058326"
    test_fb_user_id = "100001005716854"
    test_thread_name = "Hung Bui"
    
    logger.info(f"Starting E2E Batch Wrapper isolating: {test_thread_name} ({test_fb_user_id})")
    
    # 1. Fetch the thread ID for Hung Bui from the database
    conn = get_db_connection()
    row = conn.execute(
        "SELECT thread_id FROM users WHERE thread_name = ? LIMIT 1", 
        (test_thread_name,)
    ).fetchone()
    conn.close()
    
    if not row:
        logger.error(f"Cannot find user {test_thread_name} in FrankenSQLite database.")
        sys.exit(1)
        
    thread_id = row['thread_id']
    logger.info(f"Resolved thread_id: {thread_id}")
    
    # 2. Fabricate a batch configuration
    mock_message_text = "Chào bạn! Đây là test automation E2E script kiểm tra tính năng Telegram H-I-T-L. Bạn đã thu xếp thời gian tập luyện chưa ạ?"
    
    proposals = [{
        "thread_id": thread_id,
        "thread_name": test_thread_name,
        "fb_user_id": test_fb_user_id,
        "seeker_name": test_thread_name,
        "message_text": mock_message_text,
    }]
    
    summary = f"⚙️ E2E Test **Inbox** proposal for seeker:\n\n1. {test_thread_name}: {mock_message_text}\n"
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="Bypass Telegram API and inject approval directly into DB")
    args = parser.parse_args()

    logger.info("Transmitting Mock Batch Proposal to Telegram...")
    if args.simulate:
        logger.info("[SIMULATE] Bypassing Telegram network call. Injecting fake message ID.")
        msg_id = "test-msg-12345"
        # Manually create the pending record since send_proposal_to_telegram normally does this
        import json
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO telegram_hitl_queue (telegram_message_id, route, payload_json, status, proposed_text) VALUES (?, ?, ?, ?, ?)",
            (msg_id, "inbox", json.dumps({
                "classification": "E2E Test Inbox",
                "proposals": proposals,
                "msg_messages_json": [{"role": "user", "text": "test"}],
                "seeker_dict": {"name": test_thread_name}
            }), "pending", summary)
        )
        conn.commit()
        conn.close()
    else:
        msg_id = send_proposal_to_telegram("inbox", "inbox_test", summary, {
            "classification": "E2E Test Inbox",
            "proposals": proposals,
            "msg_messages_json": [{"role": "user", "text": "test"}],
            "seeker_dict": {"name": test_thread_name}
        })
    
    if not msg_id:
        logger.error("Failed to send proposal. Check TELEGRAM_BOT_TOKEN and GROUP ID.")
        sys.exit(1)
        
    logger.info(f"Sent Telegram Message ID: {msg_id}")
    
    if args.simulate:
        # Launch background threat to approve it
        import threading
        def _approve_soon():
            time.sleep(4)
            logger.info(">>> [SIMULATE] Mock human clicked 👍. Updating DB to 'approved'...")
            c = get_db_connection()
            c.execute("UPDATE telegram_hitl_queue SET status = 'approved' WHERE telegram_message_id = ?", (msg_id,))
            c.commit()
            c.close()
        threading.Thread(target=_approve_soon, daemon=True).start()
    
    logger.info("### WAITING FOR TELEGRAM APPROVAL (LIKE 👍) ###")
    
    # 3. Block and wait for human operator to LIKE
    while True:
        poll_telegram_updates()
        status, feedback = check_hitl_status(msg_id)
        
        if status == "approved":
            logger.info(">>> Approval Confirmed! Executing HITL Job against browser CDP...")
            break
        elif status == "rejected":
            logger.warning(f"Reaction rejected. Rewrite feedback: {feedback}")
            logger.info("Aborting E2E script. (Batch rewriting logic is handled by runner).")
            sys.exit(0)
            
        time.sleep(3)
        
    # 4. Fire the Execution Job (the real operation)
    logger.info("Connecting to Playwright CDP Session...")
    hitl_execution_job(page_id, dry_run=False)
    
    logger.info("E2E Batch Test Completed Successfully!")

if __name__ == "__main__":
    run_test()
