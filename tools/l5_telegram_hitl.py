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
import uuid
from typing import Tuple, Optional
from urllib.parse import quote

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

logger = logging.getLogger("telegram_hitl")


def get_seeker_detail_url(thread_id: str) -> Optional[str]:
    """Return the operator-facing seeker URL for an inbox thread.

    The dashboard's canonical local origin is ``http://localhost:9995``.  A
    deployment may override it with ``SEEKER_WEB_BASE_URL`` or ``WEB_APP_URL``.
    """
    from tools.env_manager import load_credentials

    creds = load_credentials()
    base_url = (
        creds.get("SEEKER_WEB_BASE_URL")
        or creds.get("WEB_APP_URL")
        or os.environ.get("SEEKER_WEB_BASE_URL")
        or os.environ.get("WEB_APP_URL")
        or "http://localhost:9995"
    ).strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        logger.warning("Ignoring invalid SEEKER_WEB_BASE_URL (must begin with http:// or https://).")
        return None
    return f"{base_url}/seekers/{quote(str(thread_id), safe='')}"


def _indent(text: object, spaces: int = 4) -> str:
    prefix = " " * spaces
    lines = str(text or "").strip().splitlines() or ["—"]
    return "\n".join(f"{prefix}{line.strip()}" for line in lines if line.strip()) or f"{prefix}—"


def _compact_message_content(message: dict) -> str:
    """Keep post/ad placement text recognisable without flooding Telegram."""
    content = " ".join(str(message.get("content") or "").split())
    sender = str(message.get("sender") or "").lower()
    kind = str(message.get("kind") or "").lower()
    is_placement = sender in {"auto_page", "post", "ad", "advertisement"} or "post" in kind or "ad" in kind
    limit = 180 if is_placement else 600
    if len(content) <= limit:
        return content or "—"
    # Preserve enough of a placement's opening and CTA/end to identify it.
    head = 90 if is_placement else 420
    tail = 70 if is_placement else 120
    return f"{content[:head].rstrip()}…{content[-tail:].lstrip()}"


def format_inbox_proposal(thread_id: str, seeker_name: str, messages: list[dict], reply_text: str) -> str:
    """Format a compact, scan-friendly inbox proposal for Telegram HITL."""
    sections = [f"👤 Seeker\n  {seeker_name or 'Chưa rõ tên'}"]
    detail_url = get_seeker_detail_url(thread_id)
    if detail_url:
        sections.append(f"🔗 Hồ sơ seeker\n  {detail_url}")

    conversation = []
    for message in (messages or [])[-8:]:
        timestamp = message.get("timestamp") or message.get("message_at") or "Không rõ thời gian"
        sender = message.get("sender") or "Unknown"
        if sender == "Auto_Page":
            sender = "Page (automated message)"
        conversation.append(
            f"  [{timestamp} | {sender}]\n{_indent(_compact_message_content(message), 4)}"
        )
    sections.append("💬 Hội thoại gần đây\n" + ("\n".join(conversation) if conversation else "  —"))
    sections.append(f"🤖 Đề xuất trả lời (MAS)\n{_indent(reply_text, 2)}")
    return "\n\n".join(sections)


# code:agent-mas-002:escalation-taxonomy
# Kept in sync with adk_agents.agent.ORCHESTRATOR_MAX_LOOPS; duplicated here
# (rather than imported) so this lightweight HITL module never has to import
# the ADK agent graph just to format a Telegram card.
ORCHESTRATOR_MAX_LOOPS = 30

ESCALATION_REASON_LABELS = {
    "knowledge_gap": "Thiếu thông tin để trả lời",
    "contradiction": "Hội thoại/hồ sơ mâu thuẫn",
    "sensitive": "Chủ đề nhạy cảm (sức khỏe/tâm lý/khiếu nại)",
    "adversarial": "Nghi vấn câu hỏi bẫy / thử prompt",
    "policy_uncertain": "Ngoài phạm vi MAS Strategy hiện có",
    "identity_change_low_conf": "Đổi city/program nhưng bằng chứng yếu",
    "non_convergence": f"Hết ngân sách vòng lặp ({ORCHESTRATOR_MAX_LOOPS} lượt) mà chưa hội tụ",
}


def format_escalation_proposal(thread_id: str, seeker_name: str, messages: list[dict],
                               reason_code: str, note: str) -> str:
    """Format an MAS escalation (leave-to-human) card for Telegram HITL.

    Distinct from format_inbox_proposal: there is no reply to approve here —
    an operator must read the conversation and answer manually.
    """
    label = ESCALATION_REASON_LABELS.get(reason_code, reason_code or "Không rõ lý do")
    sections = [f"🚨 Cần người phụ trách trả lời — {label}"]
    sections.append(f"👤 Seeker\n  {seeker_name or 'Chưa rõ tên'}")
    detail_url = get_seeker_detail_url(thread_id)
    if detail_url:
        sections.append(f"🔗 Hồ sơ seeker\n  {detail_url}")
    conversation = []
    for message in (messages or [])[-8:]:
        timestamp = message.get("timestamp") or message.get("message_at") or "Không rõ thời gian"
        sender = message.get("sender") or "Unknown"
        if sender == "Auto_Page":
            sender = "Page (automated message)"
        conversation.append(
            f"  [{timestamp} | {sender}]\n{_indent(_compact_message_content(message), 4)}"
        )
    sections.append("💬 Hội thoại gần đây\n" + ("\n".join(conversation) if conversation else "  —"))
    if note:
        sections.append(f"📝 Ghi chú MAS\n  {note}")
    return "\n\n".join(sections)

def get_telegram_credentials() -> Tuple[str, str]:
    from tools.env_manager import load_credentials
    creds = load_credentials()
    bot_token = creds.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = creds.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        chat_id = creds.get("SYVN_TELEGRAM_GROUP_ID") or os.environ.get("SYVN_TELEGRAM_GROUP_ID", "")
    return bot_token, chat_id

def _persist_hitl_proposal(route: str, thread_id: str, message_id: str, proposed_text: str,
                           payload: dict, escalation_reason: str | None,
                           escalation_note: str | None) -> None:
    """Persist every proposal even when Telegram delivery is unavailable.

    A local ID is intentionally stored in ``telegram_message_id`` for legacy
    schemas where that column is NOT NULL. It is never eligible for Telegram
    reaction processing, but remains visible to the web queue and recoverable
    for an operator instead of becoming an invisible no-op.
    """
    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT INTO telegram_hitl_queue
               (route, thread_id, telegram_message_id, proposed_text, payload_json,
                escalation_reason, escalation_note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (route, thread_id, message_id, proposed_text, json.dumps(payload or {}, ensure_ascii=False),
             escalation_reason, escalation_note),
        )
        conn.commit()
    finally:
        conn.close()


def send_proposal_to_telegram(route: str, thread_id: str, proposed_text: str, payload: dict = None,
                              escalation_reason: str = None, escalation_note: str = None) -> Optional[str]:
    """Send a proposal to Telegram and log it to the HITL queue. Returns message_id or None.

    escalation_reason/escalation_note (code:agent-mas-002:escalation-taxonomy) are set
    only when the InboxOrchestrator could not converge on a safe reply on its own —
    an ordinary reply proposal leaves both NULL.
    """
    payload = dict(payload or {})
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token or not chat_id:
        local_id = f"local:{uuid.uuid4()}"
        payload["delivery_state"] = "telegram_unconfigured"
        _persist_hitl_proposal(route, thread_id, local_id, proposed_text, payload,
                               escalation_reason, escalation_note)
        logger.warning("Telegram is unconfigured; retained HITL proposal locally as %s", local_id)
        return local_id

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": f"[{route.upper()}] Proposal:\n{proposed_text}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        message_id = str(data["result"]["message_id"])

        _persist_hitl_proposal(route, thread_id, message_id, proposed_text, payload,
                               escalation_reason, escalation_note)
        logger.info(f"Telegram HITL proposal sent. Message ID: {message_id}")
        return message_id
    except Exception as e:
        local_id = f"local:{uuid.uuid4()}"
        payload["delivery_state"] = "telegram_failed"
        payload["delivery_error"] = str(e)[:500]
        _persist_hitl_proposal(route, thread_id, local_id, proposed_text, payload,
                               escalation_reason, escalation_note)
        logger.error("Telegram delivery failed; retained HITL proposal locally as %s: %s", local_id, e)
        return local_id

def send_telegram_notification(text: str) -> Optional[str]:
    """Send a plain informational message (no HITL row, nothing to approve).

    Used by the SLA alert and morning brief routes
    (code:route-registration-sla-001, code:route-morning-brief-001).
    """
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token or not chat_id:
        logger.warning(f"Telegram notification skipped: missing config. Text: {text[:80]}")
        return None
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        resp.raise_for_status()
        return str(resp.json()["result"]["message_id"])
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return None


def send_telegram_reaction(message_id: str, emoji: str = "💯") -> bool:
    """Drop an emoji reaction on a proposal message instead of typing a full reply."""
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token or not chat_id or not message_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/setMessageReaction",
            json={
                "chat_id": chat_id, 
                "message_id": int(message_id),
                "reaction": [{"type": "emoji", "emoji": emoji}]
            },
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to set telegram emoji reaction on {message_id}: {e}")
        return False

def check_hitl_status(message_id: str) -> Tuple[str, str]:
    """Returns (status, feedback_text). Status is 'pending', 'approved', 'rejected'."""
    if not message_id:
        # Missing Telegram configuration/message identity is a delivery error,
        # never an implicit approval. The web queue remains available for
        # human review of the exact draft version.
        return "pending", "missing_telegram_message_id"
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
            timeout=30
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
                        queue_row = conn.execute(
                            "SELECT payload_json FROM telegram_hitl_queue WHERE telegram_message_id = ? ORDER BY id DESC LIMIT 1",
                            (target_msg_id,),
                        ).fetchone()
                        try:
                            queue_id = json.loads(queue_row["payload_json"] or "{}").get("action_queue_id")
                            if queue_id:
                                from tools.l5_action_queue import reject_action
                                reject_action(int(queue_id), "telegram:reply", text)
                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                            logger.warning("Could not reject linked action queue item: %s", exc)

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
                        queue_row = conn.execute(
                            "SELECT payload_json FROM telegram_hitl_queue WHERE telegram_message_id = ? ORDER BY id DESC LIMIT 1",
                            (target_msg_id,),
                        ).fetchone()
                        try:
                            queue_id = json.loads(queue_row["payload_json"] or "{}").get("action_queue_id")
                            if queue_id:
                                from tools.l5_action_queue import approve_action
                                approve_action(int(queue_id), "telegram:👍")
                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                            logger.warning("Could not approve linked action queue item: %s", exc)
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
