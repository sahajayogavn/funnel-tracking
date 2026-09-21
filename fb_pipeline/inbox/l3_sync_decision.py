"""Stage 1 "already fetched?" decision based on the sidebar time token.

code:inbox-sync-skip-001:decision

Meta's inbox sidebar renders one timestamp per conversation card: a clock
("8:56 PM") for today, a bare weekday ("Tue") within the last week, and a
calendar date ("Aug 6", "10/3/25", "May 29, 2025") further back.  Any new
message in a thread moves that token forward, so comparing the live token with
the token stored when the thread was last synced (``threads.fetched_sidebar_*``)
tells whether the thread can be skipped without opening it.

Cards carry ``<abbr data-utime>`` with the exact epoch; when both the live card
and the stored marker have it the comparison is exact.  The visible token is
the fallback for cards (or old markers) without it.

The rules are deliberately conservative: whenever the comparison is not
trustworthy the decision is ``None`` and Stage 1 falls back to the legacy
preview-text match.  Fetching a thread that turns out to be unchanged only
wastes time; skipping one that has a new message loses a seeker's reply.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fb_pipeline.browser.inbox.thread_list_parser import parse_sidebar_time_token

SKIP = "skip"
FETCH = "fetch"

@dataclass(frozen=True)
class SyncDecision:
    action: str | None  # SKIP, FETCH or None (undecided -> legacy preview match)
    reason: str


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    # PostgreSQL returns "YYYY-MM-DD HH:MM:SS.ffffff", SQLite "YYYY-MM-DD HH:MM:SS"
    # and parse_sidebar_time_token a bare date for day-level tokens.
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _moment(parsed: dict) -> tuple[date, tuple[int, int] | None] | None:
    """Return (day, (hour, minute) | None) for a parsed sidebar token."""
    parsed_at = parsed.get("parsed_at")
    dt = _parse_dt(parsed_at)
    if dt is None:
        return None
    has_clock = " " in str(parsed_at)
    return dt.date(), ((dt.hour, dt.minute) if has_clock else None)


def same_moment(a: tuple[date, tuple[int, int] | None], b: tuple[date, tuple[int, int] | None]) -> bool:
    """Two tokens denote the same message time at the coarser of their resolutions."""
    if a[0] != b[0]:
        return False
    if a[1] is None or b[1] is None:
        return True
    return a[1] == b[1]


def decide_by_fetched_marker(
    *,
    token_now: str,
    source_now: str,
    preview_norm_now: str,
    fetched_token: str | None,
    fetched_preview_norm: str | None,
    fetched_at: str | datetime | None,
    utime_now_ms: float | int | None = None,
    fetched_utime_ms: float | int | None = None,
    now: datetime | None = None,
    out_of_order: bool = False,
    refresh_older_than_days: int | None = None,
) -> SyncDecision:
    """Decide whether a previously synced thread must be opened again.

    ``fetched_token`` is interpreted relative to ``fetched_at`` (a bare "Tue"
    stored last Thursday means *that* week's Tuesday) and ``token_now``
    relative to ``now``.
    """
    now = now or datetime.now()
    fetched_at_dt = _parse_dt(fetched_at)
    if not fetched_token or fetched_at_dt is None:
        return SyncDecision(None, "no_fetched_marker")
    if refresh_older_than_days is not None and now - fetched_at_dt > timedelta(days=refresh_older_than_days):
        return SyncDecision(FETCH, "marker_stale")
    # Exact epoch from <abbr data-utime> on both sides: the strongest signal.
    # Any new message (in either direction) moves it, so no preview check.
    if utime_now_ms and fetched_utime_ms:
        if int(utime_now_ms) == int(fetched_utime_ms):
            return SyncDecision(SKIP, "utime_match")
        return SyncDecision(FETCH, "utime_changed")
    if source_now != "utime":
        return SyncDecision(None, "time_source_ambiguous")
    if out_of_order:
        return SyncDecision(None, "time_out_of_order")

    parsed_now = parse_sidebar_time_token(token_now, now=now)
    parsed_then = parse_sidebar_time_token(fetched_token, now=fetched_at_dt)
    moment_now = _moment(parsed_now)
    moment_then = _moment(parsed_then)
    if parsed_now.get("kind") == "unknown" or moment_now is None:
        return SyncDecision(None, "time_unknown")
    if parsed_then.get("kind") == "unknown" or moment_then is None:
        return SyncDecision(None, "fetched_token_unknown")

    if not same_moment(moment_now, moment_then):
        return SyncDecision(FETCH, "token_changed")

    # Same moment.  A token that resolves before today needs no preview
    # check: a newer message would have moved the token to a later day or to
    # a clock.  For today (minute resolution) also require the preview to be
    # unchanged, since two messages can share a minute.
    if moment_now[0] < now.date():
        return SyncDecision(SKIP, "token_match")
    if preview_norm_now == (fetched_preview_norm or ""):
        return SyncDecision(SKIP, "token_match")
    return SyncDecision(FETCH, "preview_changed")


def is_out_of_order(prev_day: date | None, current_day: date | None, tolerance_days: int = 1) -> bool:
    """Sidebar order is newest-first; a card newer than its predecessor by
    more than ``tolerance_days`` means the timestamp was mis-extracted."""
    if prev_day is None or current_day is None:
        return False
    return (current_day - prev_day) > timedelta(days=tolerance_days)
