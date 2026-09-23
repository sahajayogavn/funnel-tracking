"""Cross-check observed history before it can change canonical data.

Unknown evidence is a failed admission, not permission to infer an actor/date.
Repeated DOM reads test stability, not independent proof of Facebook truth.
"""
from datetime import datetime, timedelta
import json
import re

# Actor evidence the DOM itself establishes: an explicit platform label
# ("You sent", "<name> sent a message") or Meta's own message layout
# (right-aligned `row-reverse` wrapper for the Page, the seeker's avatar
# beside incoming clusters).  Bubble colour is never accepted.
VERIFIED_SENDER_CONFIDENCE = {"explicit", "structural"}

# Senders a verified turn may carry.  ``Auto_Page`` is the Page side speaking
# through an Inbox automation (Meta creatorType ``automated_response``); it is
# the same *side* as ``Page`` for evidence conflicts but not a human reply.
ADMITTED_SENDERS = {"Page", "Customer", "Auto_Page"}
_SENDER_SIDE = {"Page": "page", "Auto_Page": "page", "Customer": "customer"}


def _same_side(a, b) -> bool:
    return _SENDER_SIDE.get(a) is not None and _SENDER_SIDE.get(a) == _SENDER_SIDE.get(b)

# Reasons that mean "evidence is missing", as opposed to a contradiction.
INCOMPLETE_REASONS = {"missing_message_identity", "unverified_actor",
                      "unresolved_calendar_day", "unresolved_clock",
                      "missing_source_model", "missing_source_timestamp", "unsupported_source_payload"}


def _is_conversation_message(message: dict) -> bool:
    from fb_pipeline.contracts.l1_message_kind import classify_message_kind, KIND_MESSAGE
    return (message.get("kind") or classify_message_kind(message.get("text") or message.get("body") or "")) == KIND_MESSAGE


def check_snapshot(messages: list[dict]) -> list[dict]:
    from fb_pipeline.contracts.l1_message_time import resolve_message_at
    issues = []
    seen = set()
    previous = None
    for index, message in enumerate(messages):
        source = message.get("source_id")
        def issue(field, reason):
            issues.append({"index": index, "source_id": source, "field": field, "reason": reason})
        for reason in message.get("source_issues", []):
            issue("facebook_source", reason)
            if reason == "facebook_source_schema_drift" and message.get("source_diagnostics"):
                issues[-1]["detail"] = message["source_diagnostics"]
        if not _is_conversation_message(message):
            # System banners / attachment placeholders carry no actor or
            # message id by design; they are not admitted as turns and must
            # not block the conversation's real messages.
            if source and source in seen:
                issue("source_id", "duplicate_message_identity")
            if source:
                seen.add(source)
            continue
        if not source:
            issue("source_id", "missing_message_identity")
        elif source in seen:
            issue("source_id", "duplicate_message_identity")
        seen.add(source)
        if (message.get("sender") not in ADMITTED_SENDERS
                or message.get("sender_confidence") not in VERIFIED_SENDER_CONFIDENCE
                or not message.get("sender_evidence")):
            issue("sender", "unverified_actor")
        day = message.get("day_context") or ""
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except (ValueError, TypeError):
            issue("datetime", "unresolved_calendar_day")
            continue
        if message.get("time_precision") != "date_time":
            issue("datetime", "unresolved_clock")
            continue
        stamp, approximate = resolve_message_at(message.get("timestamp"), message.get("observed_at"))
        if not stamp or approximate or stamp[:10] != day:
            issue("datetime", "timestamp_day_disagreement")
        elif previous and stamp < previous:
            issue("datetime", "non_monotonic_message_order")
        else:
            previous = stamp
    return issues


def compare_snapshots(first: list[dict], second: list[dict]) -> list[dict]:
    # `observed_at`/`time_evidence` differ between two reads by design.
    fields = ("source_id", "sender", "sender_confidence", "sender_evidence",
              "text", "timestamp", "day_context", "time_precision",
              "quoted_text", "reply_to_message_id", "kind", "source_links", "source_issues")
    if len(first) != len(second):
        return [{"field": "count", "reason": "snapshot_changed", "before": len(first), "after": len(second)}]
    return [
        {"index": index, "source_id": a.get("source_id"), "field": field, "reason": "snapshot_changed"}
        for index, (a, b) in enumerate(zip(first, second))
        for field in fields if a.get(field) != b.get(field)
    ]


LEGACY_LABEL_MAX_DRIFT = timedelta(hours=24)


def _within_forward_window(stored_stamp: str, stamp: str) -> bool:
    """True when ``stamp`` is at/after the legacy label and within one day of it."""
    try:
        stored_dt = datetime.strptime(stored_stamp, "%Y-%m-%d %H:%M:%S")
        stamp_dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    return timedelta(0) <= stamp_dt - stored_dt <= LEGACY_LABEL_MAX_DRIFT


def _source_contract(evidence):
    try:
        return json.loads(evidence or '{}').get('source')
    except (ValueError, AttributeError, TypeError):
        return None


def compare_stored(messages: list[dict], rows: list[dict], thread_id: str) -> list[dict]:
    """Source IDs are compared within one Page, never via text or names."""
    from fb_pipeline.contracts.l1_message_time import resolve_message_at
    existing = {}
    for row in rows:
        existing.setdefault(row["source_id"], []).append(row)
    issues = []
    for message in messages:
        for row in existing.get(message.get("source_id"), []):
            field = None
            if row["thread_id"] != thread_id:
                field = "recipient"
            elif (row.get("sender_confidence") in VERIFIED_SENDER_CONFIDENCE
                  and row.get("sender") != message.get("sender")
                  # Page -> Auto_Page (or back) is a refinement of *who on the
                  # Page side* spoke, not a change of side: never a conflict.
                  and not _same_side(row.get("sender"), message.get("sender"))):
                field = "sender"
            elif row.get("time_precision") == "date_time" and row.get("message_at"):
                stamp, _ = resolve_message_at(message.get("timestamp"), message.get("observed_at"))
                stored_stamp = str(row["message_at"])[:19].replace("T", " ")
                # code:inbox-fetch-integrity-001:legacy-label-refinement
                # A DOM-derived row stored a Facebook minute label, and that
                # label belonged to the bubble *cluster* (divider / hover of
                # the first bubble), so it only bounds the message from below:
                # the real send time is at or after the label, never before.
                # An exact source epoch may therefore move such a row forward
                # (observed drift: 60s to ~2h), but an epoch that is earlier
                # than the label, or more than a day later, or one that
                # disagrees with a previously stored source epoch, is a
                # contradiction and must fail closed.
                incoming_source = _source_contract(message.get('sender_evidence'))
                stored_source = _source_contract(row.get('sender_evidence'))
                legacy_label_refinement = (
                    incoming_source == 'facebook_bound_message_v1'
                    and stored_source != 'facebook_bound_message_v1'
                    and bool(re.search(r'\b\d{1,2}:\d{2}\s*[AP]M\b', row.get('message_timestamp') or '', re.I))
                    and not re.search(r'\d{1,2}:\d{2}:\d{2}', row.get('message_timestamp') or '')
                    and bool(stamp) and stored_stamp.endswith(':00')
                    and _within_forward_window(stored_stamp, stamp)
                )
                if stored_stamp != stamp and not legacy_label_refinement:
                    field = "datetime"
            if field:
                issues.append({"source_id": message.get("source_id"), "field": field,
                               "reason": "stored_evidence_conflict", "stored_thread_id": row["thread_id"]})
    return issues
