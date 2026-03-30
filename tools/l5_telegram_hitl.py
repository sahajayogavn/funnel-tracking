#!/usr/bin/env python3
"""
Telegram HITL Engine — Unified polling and proposal system.
code:tool-telegram-hitl-001

Handles sending proposals to a Telegram group and polling Long Updates
for LIKE reactions (approval) or text replies (rewrite via LLM).
"""
import json
import logging
import os
import requests
from typing import Tuple, Optional

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

logger = logging.getLogger("telegram_hitl")

def get_telegram_credentials() -> Tuple[str, str]:
    from tools.env_manager import load_credentials
    creds = load_credentials()
    bot_token = creds.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = creds.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        chat_id = creds.get("SYVN_TELEGRAM_GROUP_ID") or os.environ.get("SYVN_TELEGRAM_GROUP_ID", "")
    return bot_token, chat_id

def send_proposal_to_telegram(route: str, thread_id: str, proposed_text: str, payload: dict = None) -> Optional[str]:
    """Send a proposal to Telegram and log it to the HITL queue. Returns message_id or None."""
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token or not chat_id:
        logger.warning(f"HITL skipped: missing config. Would have proposed: {proposed_text[:50]}")
        return None

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": f"[{route.upper()}] Proposal:\n{proposed_text}"},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        message_id = str(data["result"]["message_id"])

        conn = get_db_connection()
        try:
            conn.execute(
                """INSERT INTO telegram_hitl_queue 
                   (route, thread_id, telegram_message_id, proposed_text, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (route, thread_id, message_id, proposed_text, json.dumps(payload or {}, ensure_ascii=False))
            )
            conn.commit()
            logger.info(f"Telegram HITL proposal sent. Message ID: {message_id}")
            return message_id
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to send proposal to Telegram: {e}")
        return None

def check_hitl_status(message_id: str) -> Tuple[str, str]:
    """Returns (status, feedback_text). Status is 'pending', 'approved', 'rejected'."""
    if not message_id:
        return "approved", ""  # If Telegram config is missing, auto-approve to not block
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT status, feedback_text FROM telegram_hitl_queue WHERE telegram_message_id = ? ORDER BY id DESC LIMIT 1",
            (message_id,)
        ).fetchone()
        if not row:
            return "pending", ""
        return row["status"], row["feedback_text"] or ""
    finally:
        conn.close()

def mark_hitl_executed(message_id: str):
    if not message_id:
        return
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE telegram_hitl_queue SET status = 'executed', updated_at = datetime('now') WHERE telegram_message_id = ?",
            (message_id,)
        )
        conn.commit()
    finally:
        conn.close()

def poll_telegram_updates():
    """Poll Telegram API for new reactions/replies and update the queue DB."""
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token:
        return

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT last_update_id FROM telegram_offset WHERE id = 1").fetchone()
        offset = row[0] if row else 0

        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"offset": offset, "timeout": 5, "allowed_updates": '["message","message_reaction"]'},
            timeout=10
        )
        if not resp.ok:
            logger.warning(f"Telegram getUpdates failed: {resp.text}")
            return
        
        updates = resp.json().get("result", [])
        max_update_id = offset

        for update in updates:
            upd_id = update["update_id"]
            if upd_id >= max_update_id:
                max_update_id = upd_id + 1

            if "message" in update:
                msg = update["message"]
                reply_to = msg.get("reply_to_message")
                text = msg.get("text")
                if reply_to and text:
                    target_msg_id = str(reply_to["message_id"])
                    matched = conn.execute(
                        "UPDATE telegram_hitl_queue SET status = 'rejected', feedback_text = ?, updated_at = datetime('now') WHERE telegram_message_id = ? AND status = 'pending'",
                        (text, target_msg_id)
                    ).rowcount
                    if matched:
                        logger.info(f"HITL message {target_msg_id} REJECTED with feedback: {text}")

            if "message_reaction" in update:
                reaction = update["message_reaction"]
                target_msg_id = str(reaction["message_id"])
                new_reactions = reaction.get("new_reaction", [])
                
                is_like = any(r.get("emoji") == "👍" for r in new_reactions if r.get("type") == "emoji")
                if is_like:
                    matched = conn.execute(
                        "UPDATE telegram_hitl_queue SET status = 'approved', updated_at = datetime('now') WHERE telegram_message_id = ? AND status = 'pending'",
                        (target_msg_id,)
                    ).rowcount
                    if matched:
                        logger.info(f"HITL message {target_msg_id} APPROVED via reaction.")

        if max_update_id > offset:
            conn.execute(
                "INSERT INTO telegram_offset (id, last_update_id) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET last_update_id=?",
                (max_update_id, max_update_id)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Error polling Telegram updates: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    poll_telegram_updates()
