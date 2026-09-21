"""
Absolute message time resolution.
code:inbox-msg-abs-time-001

``messages.message_timestamp`` stores whatever label Facebook rendered next to
the bubble ("Mon 11:10 AM", "Sep 6, 2026, 9:10 PM", "4:32 PM").  Relative
labels only make sense relative to the moment they were scraped, so this module
resolves them against an anchor (``messages.timestamp`` = recorded_at) and
returns an ISO local datetime, or None when the label cannot be trusted.
"""
import re
from datetime import datetime, timedelta

from fb_pipeline.browser.inbox.thread_list_parser import parse_sidebar_time_token

ISO_FMT = "%Y-%m-%d %H:%M:%S"

# Older bubbles render a US-locale short date: "3/8/17, 12:58 PM" (month/day/yy)
# with a narrow no-break space (U+202F) before AM/PM.
_US_SHORT_DATETIME_RE = re.compile(
    r'^(\d{1,2})/(\d{1,2})/(\d{2,4}),?\s+(\d{1,2}):(\d{2})\s*([ap]m)$', re.IGNORECASE
)

# Labels with a clock component resolve to a precise instant.  Date-only
# labels are still useful for ageing decisions but are flagged approximate.
_PRECISE_KINDS = {"relative_day_time", "absolute_time", "relative", "time_today", "day_time"}
_APPROX_KINDS = {"today", "yesterday", "slash_day", "month_day", "month_day_rev", "viet_month"}


def _coerce_anchor(anchor) -> datetime:
    if isinstance(anchor, datetime):
        return anchor
    if isinstance(anchor, str) and anchor.strip():
        text = anchor.strip().replace("T", " ")
        for fmt in (ISO_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt)
            except ValueError:
                continue
    return datetime.now()


def resolve_message_at(label: str | None, anchor=None) -> tuple[str | None, bool]:
    """Resolve a Facebook time label to ``(iso_local_datetime, approx)``.

    Args:
        label: The scraped label, e.g. "Mon 11:10 AM".
        anchor: When the label was scraped (datetime or ISO string). Relative
            labels are resolved against this moment, never against "now".

    Returns:
        ``(iso, approx)`` where ``iso`` is ``YYYY-MM-DD HH:MM:SS`` or None, and
        ``approx`` is True when only the date was recoverable.
    """
    label = (label or "").strip()
    if not label:
        return None, False
    label = label.replace("\u202f", " ").replace("\u00a0", " ")
    anchor_dt = _coerce_anchor(anchor)
    us_match = _US_SHORT_DATETIME_RE.match(label)
    if us_match:
        month, day, year, hour, minute, ampm = us_match.groups()
        if not 1 <= int(hour) <= 12 or not 0 <= int(minute) <= 59:
            return None, True
        year = int(year) if len(year) == 4 else 2000 + int(year)
        hour = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
        try:
            dt = datetime(year, int(month), int(day), hour, int(minute))
            if dt > anchor_dt + timedelta(minutes=5):
                return None, True
            return dt.strftime(ISO_FMT), False
        except ValueError:
            return None, False
    parsed = parse_sidebar_time_token(label, now=anchor_dt)
    kind = parsed.get("kind")
    value = parsed.get("parsed_at")
    if not value:
        return None, False
    value = value.replace("T", " ")
    if kind in _PRECISE_KINDS and " " in value:
        dt = datetime.strptime(value[:19], ISO_FMT)
        # Only relative labels may wrap. Never rewrite an explicit calendar
        # date to yesterday merely because it is in the future.
        if dt > anchor_dt + timedelta(minutes=5):
            if kind == "day_time":
                dt -= timedelta(days=7)
            elif kind == "time_today":
                dt -= timedelta(days=1)
            else:
                return None, True
        return dt.strftime(ISO_FMT), False
    if len(value) >= 10:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None, False
        # Date-only labels: place at noon so ordering stays sane, mark approx.
        dt = dt.replace(hour=12, minute=0, second=0)
        if dt.date() == anchor_dt.date():
            dt = anchor_dt.replace(second=0)
        return dt.strftime(ISO_FMT), True
    return None, False


def hours_between(later_iso: str | None, earlier_iso: str | None) -> float | None:
    """Difference in hours between two ISO strings; None if either is missing."""
    if not later_iso or not earlier_iso:
        return None
    try:
        later = datetime.strptime(later_iso[:19].replace("T", " "), ISO_FMT)
        earlier = datetime.strptime(earlier_iso[:19].replace("T", " "), ISO_FMT)
    except ValueError:
        return None
    return (later - earlier).total_seconds() / 3600.0


__all__ = ["ISO_FMT", "hours_between", "resolve_message_at"]
