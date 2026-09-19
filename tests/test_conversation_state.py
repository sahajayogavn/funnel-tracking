# code:test-validation-001:conv-state
"""Unit tests for the pre-LLM inbox gate: message kind, absolute time and
conversation state (prd:mas-time-aware-001 P0/P1)."""
from datetime import datetime

import pytest

from fb_pipeline.contracts.l1_conversation_state import (
    ACTION_NEEDS_REVIEW, ACTION_REACT, ACTION_REPLY, ACTION_REPLY_LATE, ACTION_SKIP, ACTION_WARMUP,
    STATE_ALREADY_ANSWERED, STATE_CLOSED_BY_HUMAN, STATE_CLOSER_ONLY, STATE_NO_CUSTOMER_MESSAGE, STATE_UNCERTAIN_SENDER,
    STATE_OPEN_QUESTION, STATE_REGISTERED_AWAITING_CONFIRM, STATE_STALE,
    compute_conversation_state, format_conversation_lines, format_now_context, is_closer,
)
from fb_pipeline.contracts.l1_message_kind import (
    KIND_AD_SOURCE, KIND_MESSAGE, KIND_REACTION, KIND_SYSTEM_BANNER, classify_message_kind,
    parse_legacy_message_annotations, strip_reaction_markers, strip_non_sender_annotations,
)
from fb_pipeline.contracts.l1_message_time import resolve_message_at

NOW = datetime(2026, 9, 17, 14, 0, 0)


def _m(sender, content, at, seq=0):
    return {"sender": sender, "content": content, "message_at": at, "seq": seq}


# --- message kind -----------------------------------------------------------

@pytest.mark.parametrize("content,kind", [
    ("Quang Chien Nguyen replied to an ad.", KIND_SYSTEM_BANNER),
    ("Thu Pham Thi Anh replied to a post. View post", KIND_SYSTEM_BANNER),
    ("Bạn đang phản hồi bình luận của người dùng về bài viết X", KIND_SYSTEM_BANNER),
    ("Tống Hải Yến đã trả lời về một bài viết. Xem bài viết", KIND_SYSTEM_BANNER),
    (":::REACTION_LOVE:::", KIND_REACTION),
    ("[Quoted Reply/Link]: :::REACTION_LIKE:::", KIND_REACTION),
    ("Cảm ơn bạn\n[Quoted Reply/Link]: :::REACTION_LOVE:::", KIND_MESSAGE),
    ("--- [AD SOURCE]: ad ---\n\nChào bạn", KIND_AD_SOURCE),
    ("Tôi xem quảng cáo và muốn đăng ký học", KIND_MESSAGE),
    ("Học phí ?", KIND_MESSAGE),
])
def test_classify_message_kind(content, kind):
    assert classify_message_kind(content) == kind


def test_strip_reaction_markers_keeps_text():
    assert strip_reaction_markers("Cảm ơn bạn\n[Quoted Reply/Link]: :::REACTION_LOVE:::") == "Cảm ơn bạn"


def test_legacy_quote_is_not_treated_as_sender_body():
    content = "Dạ\n[Quoted Reply/Link]: Bạn ở Hà Nội phải không?"
    annotations = parse_legacy_message_annotations(content)
    assert annotations.body == "Dạ"
    assert annotations.quoted_contents == ("Bạn ở Hà Nội phải không?",)
    assert strip_reaction_markers(content).endswith("[Quoted Reply/Link]: Bạn ở Hà Nội phải không?")
    assert strip_non_sender_annotations(content) == "Dạ"


# --- absolute time ----------------------------------------------------------

@pytest.mark.parametrize("label,anchor,expected", [
    ("Mon 11:10 AM", "2026-09-16 17:01:05", "2026-09-14 11:10:00"),
    ("Sun 12:04 AM", "2026-09-16 17:01:05", "2026-09-13 00:04:00"),
    ("Sep 6, 2026, 9:10 PM", "2026-09-16 17:01:05", "2026-09-06 21:10:00"),
    ("9:35 AM", "2026-09-16 17:01:05", "2026-09-16 09:35:00"),
    ("3/8/17, 12:58 PM", "2026-09-16 17:01:05", "2017-03-08 12:58:00"),
])
def test_resolve_message_at(label, anchor, expected):
    iso, approx = resolve_message_at(label, anchor)
    assert iso == expected
    assert approx is False


def test_resolve_message_at_empty():
    assert resolve_message_at("", "2026-09-16 17:01:05") == (None, False)


# --- closer detection -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Vâng ạ", True), ("Dạ mình cảm ơn", True), ("Xin trân trọng cám ơn  !", True), ("ok", True),
    ("ok vậy học ở đâu?", False), ("cho mình xin học với sđt 0393140362", False),
    ("Quan tâm!", False), ("ib", False), ("Đăng Ký Học Thiền", False),
])
def test_is_closer(text, expected):
    assert is_closer(text) is expected


# --- conversation state -----------------------------------------------------

def test_quang_chien_case_is_already_answered():
    msgs = [
        _m("Customer", "Nguyễn Quang Chiến , 0878620783 , đăng ký học", "2026-09-13 16:12:00", 7),
        _m("Page", "Hẹn chú Chiến vào 14h30 Chủ Nhật", "2026-09-14 06:54:00", 9),
        _m("Customer", "Xin trân trọng cám ơn  !", "2026-09-14 11:10:00", 10),
        _m("Page", "Dạ !", "2026-09-14 11:10:00", 11),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_ALREADY_ANSWERED
    assert state.action == ACTION_SKIP


def test_closer_after_human_reply_fresh_reacts_only():
    msgs = [
        _m("Customer", "học ở đâu ạ?", "2026-09-16 09:00:00", 1),
        _m("Page", "Dạ ở 40 Vương Thừa Vũ ạ", "2026-09-16 09:30:00", 2),
        _m("Customer", "Dạ mình cảm ơn", "2026-09-16 10:00:00", 3),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_CLOSED_BY_HUMAN
    assert state.action == ACTION_REACT


def test_closer_after_human_reply_old_is_skipped():
    msgs = [
        _m("Page", "Dạ ở 40 Vương Thừa Vũ ạ", "2026-06-24 09:30:00", 2),
        _m("Customer", "M cám ơn", "2026-06-24 11:53:00", 3),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_CLOSED_BY_HUMAN
    assert state.action == ACTION_SKIP


def test_stale_open_question_goes_to_warmup():
    msgs = [_m("Customer", "Dạ trung tâm ơi, em tới 158 đào duy anh rồi", "2026-08-23 16:09:00", 1)]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_STALE
    assert state.action == ACTION_WARMUP


def test_fresh_open_question_replies():
    msgs = [
        _m("Auto_Page", "Học phí: Hoàn toàn MIỄN PHÍ", "2026-09-17 12:00:00", 1),
        _m("Customer", "Lớp Chủ Nhật học ở đâu ạ?", "2026-09-17 12:05:00", 2),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_OPEN_QUESTION
    assert state.action == ACTION_REPLY
    assert state.late is False
    assert round(state.age_hours, 2) == pytest.approx(1.92, abs=0.01)


def test_two_day_old_question_is_late_reply():
    msgs = [_m("Customer", "Lớp Chủ Nhật học ở đâu ạ?", "2026-09-15 12:00:00", 2)]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.action == ACTION_REPLY_LATE
    assert state.late is True


def test_registration_with_phone_awaiting_confirmation_and_sla():
    msgs = [_m("Customer", "cho mình xin học với sđt 0393140362", "2026-09-17 09:00:00", 1)]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_REGISTERED_AWAITING_CONFIRM
    assert state.action == ACTION_REPLY
    assert state.phone == "0393140362"
    assert state.sla_breached is True


def test_registration_confirmed_by_human_has_no_sla():
    msgs = [
        _m("Customer", "Nguyễn Văn A 0393140362", "2026-09-17 09:00:00", 1),
        _m("Page", "CLB đã nhận đăng ký của anh ạ", "2026-09-17 09:30:00", 2),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_ALREADY_ANSWERED
    assert state.sla_breached is False


def test_auto_page_does_not_count_as_human_reply():
    msgs = [
        _m("Customer", "Học phí ?", "2026-09-17 12:00:00", 1),
        _m("Auto_Page", "Học phí: Hoàn toàn MIỄN PHÍ", "2026-09-17 12:00:00", 2),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_OPEN_QUESTION


def test_no_customer_message():
    state = compute_conversation_state([_m("Page", "Chào bạn", "2026-09-17 12:00:00", 1)], now=NOW)
    assert state.state == STATE_NO_CUSTOMER_MESSAGE
    assert state.action == ACTION_SKIP


def test_unknown_sender_body_requires_review_instead_of_silent_no_customer_skip():
    state = compute_conversation_state([
        _m("Unknown", "Mình muốn đăng ký lớp", "2026-09-17 12:00:00", 1),
    ], now=NOW)

    assert state.state == STATE_UNCERTAIN_SENDER
    assert state.action == ACTION_NEEDS_REVIEW
    assert state.reason == "unresolved_sender_with_message_body"


def test_fresh_closer_without_human_reply_allows_short_reply():
    msgs = [
        _m("Auto_Page", "KHÓA HỌC THIỀN MIỄN PHÍ", "2026-09-17 12:00:00", 1),
        _m("Customer", "Vâng ạ", "2026-09-17 12:30:00", 2),
    ]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_CLOSER_ONLY
    assert state.action == ACTION_REPLY


# --- prompt rendering -------------------------------------------------------

def test_format_conversation_lines_separates_legacy_reaction_metadata():
    text = format_conversation_lines([
        _m("Customer", "Cảm ơn bạn\n[Quoted Reply/Link]: :::REACTION_LOVE:::", "2026-09-14 11:10:00", 1),
    ])
    assert "[2026-09-14 11:10 | Customer] Cảm ơn bạn" in text
    assert ":::REACTION_LOVE:::" not in text
    assert "[2026-09-14 11:10 | Reaction metadata] emoji: LOVE; actor: Unknown" in text


def test_format_conversation_lines_preserves_quote_attribution_and_target():
    text = format_conversation_lines([{
        **_m("Customer", "Dạ", "2026-09-19 09:00:00", 1),
        "quoted_text": "Bạn ở Hà Nội phải không?",
        "quoted_sender": "Page",
        "reply_to_message_id": "fb-message-42",
    }])
    assert "[2026-09-19 09:00 | Customer] Dạ" in text
    assert "[2026-09-19 09:00 | Reply/quote metadata] Bạn ở Hà Nội phải không?" in text
    assert "quoted sender: Page; reply target: fb-message-42" in text
    assert "NOT a statement from Customer" in text


def test_format_conversation_lines_preserves_structured_reaction_separately():
    text = format_conversation_lines([{
        **_m("Page", "Mời bạn tham gia lớp", "2026-09-19 09:00:00", 1),
        "reactions": [{
            "emoji": "LOVE", "actor": "Customer", "target_type": "message", "target_id": "fb-message-9",
        }],
    }])
    assert "[2026-09-19 09:00 | Page] Mời bạn tham gia lớp" in text
    assert "[2026-09-19 09:00 | Reaction metadata] emoji: LOVE; actor: Customer; target: message/fb-message-9" in text


def test_format_conversation_lines_keeps_thread_reaction_event_out_of_message_body():
    text = format_conversation_lines(
        [_m("Customer", "Dạ", "2026-09-19 09:00:00", 1)],
        [{
            "emoji": "👍", "actor": "Lan", "target_type": "thread",
            "target_message_id": None, "observed_at": "2026-09-19T09:01:00+07:00",
        }],
    )
    assert "[2026-09-19 09:00 | Customer] Dạ" in text
    assert "[Reaction event metadata] emoji: 👍; actor: Lan; target: thread/unknown" in text
    assert "not a message body" in text


def test_format_conversation_lines_keeps_sender_and_time_uncertainty_visible():
    text = format_conversation_lines([{
        **_m("Customer", "Dạ", "2026-09-19 09:00:00", 1),
        "sender_confidence": "unknown",
        "raw_timestamp": "9:00 AM",
        "day_context": "Sep 10, 2026",
        "time_precision": "unknown",
    }])
    assert "Customer (sender confidence: unknown)" in text
    assert "precision: unknown; raw: 9:00 AM; day context: Sep 10, 2026" in text


def test_quote_cannot_create_a_customer_question_or_phone_signal():
    msgs = [_m(
        "Customer",
        "Dạ\n[Quoted Reply/Link]: Bạn ở đâu? Gọi mình 0393140362 nhé",
        "2026-09-17 12:00:00",
        1,
    )]
    state = compute_conversation_state(msgs, now=NOW)
    assert state.state == STATE_CLOSER_ONLY
    assert state.has_phone is False


def test_format_now_context_vietnamese_weekday():
    assert format_now_context(NOW).startswith("Bây giờ là Thứ Năm 17/09/2026 14:00")
