"""
Conversation state + time gate for the inbox MAS.
code:inbox-conv-state-001

Runs *before* any LLM call.  Given a thread's genuine messages
(``kind='message'``) and the current time, it answers two questions the MAS
previously could not: "has the human already closed this?" and "how old is the
customer's last turn?".  Pure functions — no DB, no browser, no LLM.
"""
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime

# Reader-facing label for an Inbox-automation turn (stored sender ``Auto_Page``).
AUTOMATED_PAGE_LABEL = "Page (automated message)"

from fb_pipeline.contracts.l1_message_kind import (
    canonical_sender_for_actor,
    has_unverified_sender_claim,
    parse_legacy_message_annotations,
    strip_non_sender_annotations,
)
from fb_pipeline.contracts.l1_message_time import ISO_FMT, hours_between

# Decision constants (hours). Kept here so tests and docs cite one source.
REPLY_FRESH_HOURS = 24
REPLY_LATE_HOURS = 24 * 7
REGISTRATION_SLA_HOURS = 2

STATE_NO_CUSTOMER_MESSAGE = "no_customer_message"
STATE_UNCERTAIN_SENDER = "uncertain_sender"
STATE_CLOSED_BY_HUMAN = "closed_by_human"
STATE_CLOSER_ONLY = "closer_only"
STATE_REGISTERED_AWAITING_CONFIRM = "registered_awaiting_confirm"
STATE_OPEN_QUESTION = "open_question"
STATE_STALE = "stale"
STATE_ALREADY_ANSWERED = "already_answered"

ACTION_SKIP = "skip"
ACTION_REPLY = "reply"
ACTION_REPLY_LATE = "reply_late"
ACTION_WARMUP = "warmup"
ACTION_REACT = "react"
ACTION_NEEDS_REVIEW = "needs_review"

PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:\d[ .\-]?){8,9}\d(?!\d)")

# Short acknowledgements that end a conversation. Matched on the whole
# normalised text, so "ok vậy học ở đâu?" is NOT a closer (contains a question).
_CLOSER_WORDS = (
    "cảm ơn", "cám ơn", "cam on", "thank", "thanks", "tks", "thk",
    "vâng", "vang", "dạ", "da", "ok", "oke", "okê", "okie", "okay",
    "được", "duoc", "rồi", "roi", "ạ", "nhé", "nha", "nhá",
    "mình biết rồi", "hiểu rồi", "hẹn gặp", "chúc", "báo lại",
    "🙏", "👍", "❤", "❤️", "🥰", "😊", "🌿",
)
_QUESTION_MARKERS = ("?", "không", "ko ", "bao nhiêu", "ở đâu", "o dau", "khi nào", "mấy giờ",
                     "thế nào", "the nao", "làm sao", "lam sao", "cho mình", "cho em", "cho tôi",
                     "đăng ký", "dang ky", "đăng kí", "học phí", "hoc phi", "địa chỉ", "dia chi", "link", "zoom")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def is_closer(text: str) -> bool:
    """True for short acknowledgement/thanks messages with no request in them."""
    cleaned = strip_non_sender_annotations(text or "")
    norm = _norm(cleaned)
    if not norm:
        return False
    if any(marker in norm for marker in _QUESTION_MARKERS):
        return False
    if PHONE_RE.search(norm):
        return False
    words = norm.split()
    if len(words) > 12:
        return False
    return any(word in norm for word in _CLOSER_WORDS)


def find_phone(text: str) -> str | None:
    match = PHONE_RE.search(text or "")
    if not match:
        return None
    return re.sub(r"[ .\-]", "", match.group(0))


@dataclass
class ConversationState:
    state: str
    action: str
    reason: str
    age_hours: float | None = None
    last_customer_at: str | None = None
    last_customer_text: str = ""
    last_customer_seq: int | None = None
    last_human_page_at: str | None = None
    has_phone: bool = False
    phone: str | None = None
    is_closer: bool = False
    late: bool = False
    sla_breached: bool = False
    now: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _message_time(message: dict) -> str | None:
    value = message.get("message_at")
    if value:
        return str(value)[:19].replace("T", " ")
    return None


def compute_conversation_state(messages: list[dict], now: datetime | None = None) -> ConversationState:
    """Derive the state of a thread from its genuine messages.

    Args:
        messages: chronological list of dicts with ``sender``, ``content``,
            ``message_at`` (ISO local) and optionally ``seq``. Rows must already
            be filtered to ``kind='message'``.
        now: reference time (defaults to local now).
    """
    now = now or datetime.now()
    now_iso = now.strftime(ISO_FMT)

    # Never let a CSS-era Page/Customer claim decide who currently holds the
    # turn.  Keep every other field so an operator can inspect the evidence,
    # but make an unverified actor indistinguishable from Unknown to the gate.
    actor_messages = [
        {**message, "sender": canonical_sender_for_actor(message)}
        for message in messages
    ]

    last_customer_idx = None
    for idx in range(len(actor_messages) - 1, -1, -1):
        if actor_messages[idx].get("sender") == "Customer":
            last_customer_idx = idx
            break

    if last_customer_idx is None:
        # A non-empty message with an actor the parser could not establish is
        # not evidence of a customer turn, but neither is it safe to discard.
        # Keep it out of automatic drafting and make the uncertainty explicit
        # for review/re-crawl instead of silently reporting no conversation.
        unknown_messages = [
            (idx, message) for idx, message in enumerate(actor_messages)
            if (message.get("sender") in (None, "", "Unknown")
                and strip_non_sender_annotations(message.get("content") or ""))
        ]
        if unknown_messages:
            latest_unknown_idx, latest_unknown = unknown_messages[-1]
            return ConversationState(
                state=STATE_UNCERTAIN_SENDER,
                action=ACTION_NEEDS_REVIEW,
                reason="unresolved_sender_with_message_body",
                last_customer_at=_message_time(latest_unknown),
                last_customer_text=strip_non_sender_annotations(latest_unknown.get("content") or ""),
                last_customer_seq=latest_unknown.get("seq"),
                now=now_iso,
                extra={
                    "sender_confidence": latest_unknown.get("sender_confidence") or "unknown",
                    "stored_sender_claim": messages[latest_unknown_idx].get("sender"),
                },
            )
        return ConversationState(
            state=STATE_NO_CUSTOMER_MESSAGE, action=ACTION_SKIP,
            reason="no_customer_message", now=now_iso,
        )

    last_customer = actor_messages[last_customer_idx]
    last_customer_at = _message_time(last_customer)
    # Reply snippets and reaction UI markers are not statements by this
    # sender.  They must not create a question, city, phone, or closer signal.
    last_customer_text = strip_non_sender_annotations(last_customer.get("content") or "")
    age_hours = hours_between(now_iso, last_customer_at)

    # Human (not Auto_Page) reply after the last customer turn?
    unresolved_after_customer = [
        m for m in actor_messages[last_customer_idx + 1:]
        if m.get("sender") == "Unknown" and strip_non_sender_annotations(m.get("content") or "")
    ]
    if unresolved_after_customer:
        unresolved = unresolved_after_customer[-1]
        return ConversationState(
            state=STATE_UNCERTAIN_SENDER,
            action=ACTION_NEEDS_REVIEW,
            reason="unresolved_sender_after_last_customer_turn",
            age_hours=age_hours,
            last_customer_at=last_customer_at,
            last_customer_text=last_customer_text,
            last_customer_seq=last_customer.get("seq"),
            now=now_iso,
            extra={
                "unresolved_seq": unresolved.get("seq"),
                "sender_confidence": unresolved.get("sender_confidence") or "unknown",
                "unverified_sender_claim": any(
                    has_unverified_sender_claim(m)
                    for m in messages[last_customer_idx + 1:]
                ),
            },
        )

    human_after = [m for m in actor_messages[last_customer_idx + 1:] if m.get("sender") == "Page"]
    # Human reply immediately before the last customer turn (closer context).
    human_before_at = None
    for m in reversed(actor_messages[:last_customer_idx]):
        if m.get("sender") == "Page":
            human_before_at = _message_time(m)
            break
        if m.get("sender") == "Customer":
            break

    closer = is_closer(last_customer_text)
    phone = find_phone(last_customer_text)
    # A registration is "awaiting confirm" when the phone appeared in the
    # customer's most recent turns and no human has replied since.
    recent_customer_texts = []
    for m in reversed(actor_messages[: last_customer_idx + 1]):
        if m.get("sender") == "Customer":
            recent_customer_texts.append(strip_non_sender_annotations(m.get("content") or ""))
            if len(recent_customer_texts) >= 3:
                break
        else:
            break
    phone_recent = next((find_phone(t) for t in recent_customer_texts if find_phone(t)), None)

    base = dict(
        age_hours=age_hours, last_customer_at=last_customer_at,
        last_customer_text=last_customer_text, last_customer_seq=last_customer.get("seq"),
        last_human_page_at=_message_time(human_after[-1]) if human_after else None,
        has_phone=bool(phone_recent), phone=phone_recent, is_closer=closer, now=now_iso,
        # A phone left without any human reply is an SLA breach whatever the
        # reply decision is — the alert route reads this flag directly.
        sla_breached=bool(phone_recent) and not human_after
                     and age_hours is not None and age_hours > REGISTRATION_SLA_HOURS,
    )

    if human_after:
        return ConversationState(state=STATE_ALREADY_ANSWERED, action=ACTION_SKIP,
                                 reason="human_replied_after_last_customer_turn", **base)

    if closer and human_before_at is not None:
        # The human already handled it; at most a reaction, and only while fresh.
        fresh = age_hours is not None and age_hours <= REPLY_LATE_HOURS
        return ConversationState(state=STATE_CLOSED_BY_HUMAN,
                                 action=ACTION_REACT if fresh else ACTION_SKIP,
                                 reason="closer_after_human_reply", **base)

    if age_hours is not None and age_hours > REPLY_LATE_HOURS:
        return ConversationState(state=STATE_STALE, action=ACTION_WARMUP,
                                 reason=f"customer_turn_older_than_{REPLY_LATE_HOURS // 24}d", **base)

    if closer:
        # No human reply before it and still fresh: a short warm reply is fine,
        # otherwise let it rest.
        if age_hours is not None and age_hours <= REPLY_FRESH_HOURS:
            return ConversationState(state=STATE_CLOSER_ONLY, action=ACTION_REPLY,
                                     reason="fresh_closer_without_human_reply", **base)
        return ConversationState(state=STATE_CLOSER_ONLY, action=ACTION_SKIP,
                                 reason="old_closer_without_request", **base)

    late = age_hours is not None and age_hours > REPLY_FRESH_HOURS
    if phone_recent:
        return ConversationState(state=STATE_REGISTERED_AWAITING_CONFIRM,
                                 action=ACTION_REPLY_LATE if late else ACTION_REPLY,
                                 reason="phone_without_human_confirmation", late=late, **base)

    return ConversationState(state=STATE_OPEN_QUESTION,
                             action=ACTION_REPLY_LATE if late else ACTION_REPLY,
                             reason="open_customer_turn", late=late, **base)


def format_now_context(now: datetime | None = None) -> str:
    """Human-readable 'now' line for prompts, Vietnamese weekday included."""
    now = now or datetime.now()
    weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    return f"Bây giờ là {weekdays[now.weekday()]} {now.strftime('%d/%m/%Y %H:%M')} (giờ Việt Nam, Asia/Ho_Chi_Minh)"


def format_conversation_lines(messages: list[dict]) -> str:
    """Render source-aware message, reply, and reaction records for prompts.

    Legacy parser delimiters are rendered as *unattributed metadata* rather
    than appended to the current sender's body. Reactions are annotations on
    the target message; a line such as ``Seeker: thả Like ×2`` is not a spoken
    turn and must never affect who holds the conversation turn.
    """
    lines = []
    for m in messages:
        annotations = parse_legacy_message_annotations(m.get("content") or "")
        stamp = _message_time(m)
        stamp = stamp[:16] if stamp else "??"
        raw_timestamp = str(m.get("raw_timestamp") or "").strip()
        day_context = str(m.get("day_context") or "").strip()
        time_precision = str(m.get("time_precision") or "").strip()
        # ``message_at`` remains the displayed ordering key, but an uncertain
        # parser result must carry its raw/day evidence into the prompt.
        if time_precision and time_precision not in {"exact", "precise"}:
            time_evidence = "; ".join(
                item for item in (
                    f"precision: {time_precision}",
                    f"raw: {raw_timestamp}" if raw_timestamp else "",
                    f"day context: {day_context}" if day_context else "",
                ) if item
            )
            stamp = f"{stamp}; {time_evidence}"
        sender = canonical_sender_for_actor(m)
        stored_sender = str(m.get("sender") or "Unknown")
        sender_confidence = str(m.get("sender_confidence") or "").strip()
        # code:inbox-fetch-source-001:automated-page-turn
        # Tell the model *who* answered and *why the canned text is there*:
        # an automation the Page configured for a post/ad or keyword, not a
        # person.  ``Auto_Page`` stays the stored value; only the label changes.
        sender_label = AUTOMATED_PAGE_LABEL if sender == "Auto_Page" else sender
        if sender == "Unknown" and has_unverified_sender_claim(m):
            sender_label = (
                f"Unknown (unverified stored sender claim: {stored_sender}; "
                f"confidence: {sender_confidence or 'unknown'})"
            )
        elif sender_confidence and sender_confidence not in {"explicit", "high", "confirmed", "exact"}:
            sender_label = f"{sender} (sender confidence: {sender_confidence})"
        if annotations.body:
            lines.append(f"[{stamp} | {sender_label}] {annotations.body}")

        quoted_text = m.get("quoted_text")
        structured_quote = str(quoted_text).strip() if quoted_text else ""
        quote_items: list[tuple[str, str, str]] = []
        if structured_quote:
            quote_items.append((
                structured_quote,
                str(m.get("quoted_sender") or "Unknown"),
                str(m.get("reply_to_message_id") or "unknown"),
            ))
        for quote in annotations.quoted_contents:
            if quote != structured_quote:
                quote_items.append((quote, "Unknown", "unknown"))
        for quote, quoted_sender, target_id in quote_items:
            lines.append(
                f"[{stamp} | Reply/quote metadata] {quote} "
                f"(quoted sender: {quoted_sender}; reply target: {target_id}; "
                f"NOT a statement from {sender_label}; do not use as sender evidence)"
            )

        reactions = m.get("reactions") or []
        for reaction in reactions:
            if not isinstance(reaction, dict):
                continue
            emoji = reaction.get("emoji") or reaction.get("type") or "unknown"
            actor = reaction.get("actor") or "Unknown"
            count = reaction.get("count") or 1
            lines.append(
                f"[{stamp} | Reaction on preceding message] {actor}: thả {emoji} ×{count} "
                "(annotation, not a message body)"
            )
        # Legacy markers record only an emoji.  Keep the observation visible,
        # but explicitly refuse to assign actor/target/scope from bubble order.
        structured_emojis = {
            str(reaction.get("emoji") or reaction.get("type") or "").upper()
            for reaction in reactions if isinstance(reaction, dict)
        }
        for reaction_type in annotations.reaction_types:
            if reaction_type in structured_emojis:
                continue
            lines.append(
                f"[{stamp} | Reaction metadata] emoji: {reaction_type}; actor: Unknown; "
                "target: unknown/unknown (legacy marker; not a message body)"
            )
    return "\n".join(lines)


__all__ = [
    "ACTION_NEEDS_REVIEW", "ACTION_REACT", "ACTION_REPLY", "ACTION_REPLY_LATE", "ACTION_SKIP", "ACTION_WARMUP",
    "ConversationState", "PHONE_RE", "REGISTRATION_SLA_HOURS", "REPLY_FRESH_HOURS", "REPLY_LATE_HOURS",
    "STATE_ALREADY_ANSWERED", "STATE_CLOSED_BY_HUMAN", "STATE_CLOSER_ONLY", "STATE_NO_CUSTOMER_MESSAGE", "STATE_UNCERTAIN_SENDER",
    "STATE_OPEN_QUESTION", "STATE_REGISTERED_AWAITING_CONFIRM", "STATE_STALE",
    "compute_conversation_state", "find_phone", "format_conversation_lines", "format_now_context", "is_closer",
]
