"""Regression contract for structured Inbox history emitted by the DOM parser."""

from fb_pipeline.browser.inbox.thread_detail_parser import extract_thread_messages


class _RawPage:
    def __init__(self, rows):
        self.rows = rows
        self.script = ""

    def evaluate(self, script, *_args, **_kwargs):
        self.script = script
        return self.rows


def test_parser_keeps_body_separate_from_quote_and_normalizes_complete_day_context():
    page = _RawPage([{
        "sender": "Customer",
        "sender_confidence": "explicit",
        "body": "Dạ",
        "text": "Dạ",
        "source_id": "mid.customer.reply",
        "timestamp": "Sep 10, 2026 9:00 AM",
        "raw_timestamp": "Sep 10, 2026 | 9:00 AM",
        "day_context": "Sep 10, 2026",
        "time_precision": "date_time",
        "reply_to_message_id": "mid.page.question",
        "quoted_sender": "Page",
        "quoted_sender_confidence": "explicit",
        "quoted_text": "Bạn ở Hà Nội phải không?",
        "reactions": [],
    }])

    messages = extract_thread_messages(page)

    assert messages == [{
        "sender": "Customer",
        "text": "Dạ",
        "body": "Dạ",
        "sender_confidence": "explicit",
        "sender_candidate": "Customer",
        "sender_evidence": None,
        "source_id": "mid.customer.reply",
        "timestamp": "Sep 10, 2026 9:00 AM",
        "raw_timestamp": "Sep 10, 2026 | 9:00 AM",
        "day_context": "2026-09-10",
        "time_precision": "date_time",
        "reply_to_message_id": "mid.page.question",
        "quoted_sender": "Page",
        "quoted_sender_confidence": "explicit",
        "quoted_text": "Bạn ở Hà Nội phải không?",
        "quote_evidence": None,
        "reactions": [],
    }]
    # The browser-side extractor has distinct paths; it must never restore the
    # historical join marker that made quote text appear as sender body.
    assert "[Quoted Reply/Link]" not in page.script
    assert "currentDayContext" in page.script


def test_parser_refuses_to_turn_colour_heuristic_into_sender_fact():
    page = _RawPage([{
        "body": "Tin nhắn không có nhãn actor",
        "htmlStr": "<div>...</div>",
        "bg": "rgb(0, 132, 255)",
        "timestamp": "9:00 AM",
        "reactions": [],
    }])

    message = extract_thread_messages(page)[0]

    assert message["sender"] == "Unknown"
    assert message["sender_confidence"] == "unknown"
    assert message["sender_candidate"] == "Page"


def test_parser_does_not_infer_a_day_from_a_clock_only_label():
    page = _RawPage([{
        "sender": "Customer",
        "body": "Em đến lúc 9 giờ",
        "timestamp": "9:00 AM",
        "raw_timestamp": "9:00 AM",
        "day_context": None,
        "time_precision": "time_only",
        "reactions": [],
    }])

    message = extract_thread_messages(page)[0]

    assert message["timestamp"] == "9:00 AM"
    assert message["raw_timestamp"] == "9:00 AM"
    assert message["day_context"] is None
    assert message["time_precision"] == "time_only"


def test_parser_marks_yearless_day_unresolved_instead_of_guessing_crawl_year():
    page = _RawPage([{
        "sender": "Customer",
        "body": "Dạ",
        "timestamp": "Sep 10 9:00 AM",
        "raw_timestamp": "Sep 10 | 9:00 AM",
        "day_context": "Sep 10",
        "time_precision": "date_time",
        "reactions": [],
    }])

    message = extract_thread_messages(page)[0]

    assert message["day_context"] is None
    assert message["time_precision"] == "unresolved_day_time"


def test_parser_preserves_reaction_as_structured_evidence_without_emoji_text():
    reaction = {
        "source_id": None,
        "actor": "You",
        "actor_role": "Page",
        "emoji": "Love",
        "target_type": "message",
        "target_message_id": "mid.page.42",
        "target_id": "mid.page.42",
        "target_scope": "message",
        "observed_at": "Sep 10, 2026 9:00 AM",
        "occurred_at": None,
        "raw_label": "You reacted Love to Bạn ở Hà Nội phải không?",
        "parse_confidence": "explicit",
        "evidence": "You reacted Love to Bạn ở Hà Nội phải không?",
    }
    page = _RawPage([{
        "sender": "Page",
        "sender_confidence": "heuristic",
        "body": "",
        "text": "",
        "timestamp": "Sep 10, 2026 9:00 AM",
        "raw_timestamp": "Sep 10, 2026 | 9:00 AM",
        "day_context": "Sep 10, 2026",
        "time_precision": "date_time",
        "reactions": [reaction],
    }])

    message = extract_thread_messages(page)[0]

    assert message["text"] == ""
    assert message["body"] == ""
    assert message["reactions"] == [reaction]
    assert "REACTION_" not in message["text"]
    assert message["reactions"][0]["actor"] == "You"
    assert message["reactions"][0]["target_id"] == "mid.page.42"
    assert message["reactions"][0]["target_type"] == "message"
    assert message["reactions"][0]["target_message_id"] == "mid.page.42"
