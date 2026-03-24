"""
Stage evaluation tools for MAS auto-promotion.
code:agent-mas-001:stage-tools
"""
import logging
import os
import re
import sys

logger = logging.getLogger("mas.stage_tools")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
from adk_agents.tools.l5_warmup_tools import normalize_lead_stage

PHONE_REGEX = re.compile(r"(?:\+?84|0)(?:\d[ .-]?){8,10}")
EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PROGRAM_KEYWORDS = {
    "lớp",
    "lop",
    "khóa",
    "khoa",
    "đăng ký",
    "dang ky",
    "zoom",
    "online",
    "offline",
    "sự kiện",
    "su kien",
    "workshop",
    "thiền âm nhạc",
    "thien am nhac",
    "thiền trị liệu",
    "thien tri lieu",
    "4 tuần",
    "4 tuan",
    "18 tuần",
    "18 tuan",
}


# code:tool-stage-001:auto-promotion

def _get_user_and_thread(thread_id: str) -> dict | None:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT u.thread_id, u.thread_name, u.phone, u.email, u.city, u.lead_stage, t.page_id "
            "FROM users u "
            "LEFT JOIN threads t ON t.id = u.thread_id "
            "WHERE u.thread_id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# code:tool-stage-001:auto-promotion

def _get_thread_messages(thread_id: str) -> list[dict]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT sender, content, message_timestamp FROM messages WHERE thread_id = ? ORDER BY seq ASC, id ASC",
            (thread_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# code:tool-stage-001:auto-promotion

def _has_touchpoint(thread_id: str) -> bool:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return bool(row and row["cnt"] >= 1)
    finally:
        conn.close()


# code:tool-stage-001:auto-promotion

def _extract_valid_contact(user_row: dict, messages: list[dict]) -> dict:
    phone = (user_row.get("phone") or "").strip()
    email = (user_row.get("email") or "").strip()

    if phone and PHONE_REGEX.search(phone):
        return {"has_contact": True, "contact_type": "phone", "phone": phone, "email": email}
    if email and EMAIL_REGEX.search(email):
        return {"has_contact": True, "contact_type": "email", "phone": phone, "email": email}

    for message in messages:
        content = (message.get("content") or "").strip()
        if not content:
            continue
        phone_match = PHONE_REGEX.search(content)
        if phone_match:
            return {
                "has_contact": True,
                "contact_type": "phone",
                "phone": phone_match.group(0),
                "email": email,
            }
        email_match = EMAIL_REGEX.search(content)
        if email_match:
            return {
                "has_contact": True,
                "contact_type": "email",
                "phone": phone,
                "email": email_match.group(0),
            }

    return {"has_contact": False, "contact_type": None, "phone": phone, "email": email}


# code:tool-stage-001:auto-promotion

def _has_specific_program(messages: list[dict]) -> bool:
    for message in messages:
        content = (message.get("content") or "").lower()
        if any(keyword in content for keyword in PROGRAM_KEYWORDS):
            return True
    return False


# code:tool-stage-001:auto-promotion

def _update_lead_stage(thread_id: str, new_stage: str) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE users SET lead_stage = ? WHERE thread_id = ?",
            (new_stage, thread_id),
        )
        conn.commit()
    finally:
        conn.close()


# code:tool-stage-001:auto-promotion

def evaluate_stage_gate(thread_id: str) -> dict:
    user_row = _get_user_and_thread(thread_id)
    if not user_row:
        return {"promoted": False, "reason": "missing_user", "thread_id": thread_id}

    current_stage_raw = user_row.get("lead_stage") or "Intake"
    current_stage = normalize_lead_stage(current_stage_raw)
    messages = _get_thread_messages(thread_id)

    base_result = {
        "promoted": False,
        "thread_id": thread_id,
        "thread_name": user_row.get("thread_name"),
        "page_id": user_row.get("page_id"),
        "from_stage": current_stage_raw,
        "normalized_stage": current_stage,
    }

    if current_stage in {"Intake"}:
        if not _has_touchpoint(thread_id):
            return {**base_result, "gate": "G1", "reason": "missing_touchpoint"}
        _update_lead_stage(thread_id, "Seeker")
        return {
            **base_result,
            "promoted": True,
            "gate": "G1",
            "to_stage": "Seeker",
            "reason": "touchpoint_recorded",
        }

    if current_stage in {"Seeker"}:
        contact = _extract_valid_contact(user_row, messages)
        if not contact["has_contact"]:
            return {**base_result, **contact, "gate": "G3", "reason": "missing_valid_contact"}
        if not _has_specific_program(messages):
            return {**base_result, **contact, "gate": "G3", "reason": "missing_specific_program"}
        _update_lead_stage(thread_id, "Seeker_Public_Program")
        return {
            **base_result,
            **contact,
            "promoted": True,
            "gate": "G3",
            "to_stage": "Seeker_Public_Program",
            "reason": "valid_contact_and_program_detected",
        }

    if current_stage in {"Registered", "Public Program Seeker"}:
        return {**base_result, "gate": "G4", "reason": "manual_only"}

    if current_stage in {"18-Week Seeker"}:
        return {**base_result, "gate": "G5", "reason": "manual_only"}

    return {**base_result, "reason": "no_applicable_gate"}
