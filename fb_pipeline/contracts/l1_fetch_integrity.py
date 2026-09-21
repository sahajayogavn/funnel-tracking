"""Cross-check observed history before it can change canonical data.

Unknown evidence is a failed admission, not permission to infer an actor/date.
Repeated DOM reads test stability, not independent proof of Facebook truth.
"""
from datetime import datetime

def check_snapshot(messages: list[dict]) -> list[dict]:
    from fb_pipeline.contracts.l1_message_time import resolve_message_at
    issues = []
    seen = set()
    previous = None
    for index, message in enumerate(messages):
        source = message.get("source_id")
        def issue(field, reason):
            issues.append({"index": index, "source_id": source, "field": field, "reason": reason})
        if not source or source in seen:
            issue("source_id", "missing_or_duplicate_message_identity")
        seen.add(source)
        if (message.get("sender") not in {"Page", "Customer"}
                or message.get("sender_confidence") != "explicit"
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
        stamp, approximate = resolve_message_at(message.get("timestamp"))
        if not stamp or approximate or stamp[:10] != day:
            issue("datetime", "timestamp_day_disagreement")
        elif previous and stamp < previous:
            issue("datetime", "non_monotonic_message_order")
        else:
            previous = stamp
    return issues


def compare_snapshots(first: list[dict], second: list[dict]) -> list[dict]:
    fields = ("source_id", "sender", "sender_confidence", "sender_evidence",
              "text", "timestamp", "day_context", "time_precision",
              "quoted_text", "reply_to_message_id")
    if len(first) != len(second):
        return [{"field": "count", "reason": "snapshot_changed", "before": len(first), "after": len(second)}]
    return [
        {"index": index, "source_id": a.get("source_id"), "field": field, "reason": "snapshot_changed"}
        for index, (a, b) in enumerate(zip(first, second))
        for field in fields if a.get(field) != b.get(field)
    ]


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
            elif (row.get("sender_confidence") == "explicit"
                  and row.get("sender") != message.get("sender")):
                field = "sender"
            elif row.get("time_precision") == "date_time" and row.get("message_at"):
                stamp, _ = resolve_message_at(message.get("timestamp"))
                if str(row["message_at"])[:19].replace("T", " ") != stamp:
                    field = "datetime"
            if field:
                issues.append({"source_id": message.get("source_id"), "field": field,
                               "reason": "stored_evidence_conflict", "stored_thread_id": row["thread_id"]})
    return issues
