"""
Message kind classification for inbox rows.
code:inbox-msg-kind-001

Facebook Business Inbox renders several non-message rows inside the message
list: "<name> replied to an ad.", "<name> replied to a post. View post",
"Bạn đang phản hồi bình luận…", pure reaction markers, attachments.  The DOM
parser reports them as ordinary messages, and older rows in the database carry
them under ``sender='Customer'``.  Everything downstream (unreplied detection,
MAS prompts, last_interaction) must treat them as system noise, not as a
customer turn.  This module is pure string logic with no DB or browser access.
"""
import re
from dataclasses import dataclass

KIND_MESSAGE = "message"
KIND_SYSTEM_BANNER = "system_banner"
KIND_REACTION = "reaction"
KIND_ATTACHMENT = "attachment"
KIND_AD_SOURCE = "ad_source"

ALL_KINDS = (KIND_MESSAGE, KIND_SYSTEM_BANNER, KIND_REACTION, KIND_ATTACHMENT, KIND_AD_SOURCE)

# A sender value is usable as an actor only when the parser preserved a
# Facebook-provided actor signal.  Old rows contain Page/Customer values that
# were inferred from a bubble's CSS; those are display hints from a retired
# parser, not evidence that a particular person sent the body.
_VERIFIED_SENDER_CONFIDENCES = frozenset({"explicit", "high", "confirmed", "exact"})

_REACTION_TAG_RE = re.compile(r':::REACTION_([A-Z]+):::')
# This tag is a legacy parser delimiter, not evidence that the following text
# belongs to the bubble sender.  Keep it as a separately represented quote.
_QUOTED_REPLY_SPLIT_RE = re.compile(r'(?:^|\n)\s*\[Quoted Reply/Link\]:\s*')
_QUOTED_PREFIX_RE = re.compile(r'^\s*(?:\[Quoted Reply/Link\]:\s*)+')

# Exact-shape banners. Anchored so a genuine customer sentence that merely
# mentions "quảng cáo" is not swallowed.
_BANNER_PATTERNS = (
    re.compile(r'^.*(?:resolved this conversation|đã giải quyết cuộc trò chuyện|đã giao cuộc trò chuyện|đã chỉ định cuộc trò chuyện).*$', re.IGNORECASE),
    re.compile(r'^(?:you can now call each other|giờ đây, các bạn có thể gọi|lead stage set to|trạng thái khách hàng được đặt).*$', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? replied to an ad\.?(?:\s*View (?:ad|post)\.?)?$', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? replied to a post\.?(?:\s*View post\.?)?$', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? đã trả lời (?:về )?(?:một )?(?:quảng cáo|bài viết)\b.*$', re.IGNORECASE),
    re.compile(r'^Bạn đang phản hồi bình luận', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? assigned this conversation to .{0,80}$', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? (?:marked|labeled|labelled) (?:this )?conversation\b.*$', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? đã (?:chỉ định|gán) cuộc trò chuyện\b.*$', re.IGNORECASE),
    re.compile(r'^You are replying to .*comment', re.IGNORECASE),
    re.compile(r'^(?:This chat|Cuộc trò chuyện này) (?:contains|chứa)', re.IGNORECASE),
)

_ATTACHMENT_PATTERNS = (
    re.compile(r'^\S.{0,80}? sent an? (?:attachment|photo|video|sticker|voice message|GIF)\.?$', re.IGNORECASE),
    re.compile(r'^\S.{0,80}? đã gửi (?:một )?(?:tệp đính kèm|ảnh|video|nhãn dán|tin nhắn thoại)\.?$', re.IGNORECASE),
)


@dataclass(frozen=True)
class LegacyMessageAnnotations:
    """Loss-aware view of a pre-structured inbox message.

    Older crawls embedded reply and reaction UI fragments in ``content``.  The
    delimiters carry no trustworthy actor or target information, so consumers
    must never concatenate their text into the sender's message body.
    """

    body: str
    quoted_contents: tuple[str, ...] = ()
    reaction_types: tuple[str, ...] = ()


def canonical_sender_for_actor(message: dict) -> str:
    """Return a sender safe for gates, prompts, and reader-facing history.

    A pre-source-aware Page/Customer value becomes unsafe when the old parser
    also merged a quote/reaction UI fragment into that same body.  In that
    shape one CSS-derived actor was applied to multiple events, so the gate
    must not use it.  Do not downgrade every legacy row: that would erase the
    readable history while providing no additional evidence.  Pure callers
    from before the evidence contract may omit confidence entirely.
    """
    sender = str(message.get("sender") or "Unknown").strip() or "Unknown"
    if sender not in {"Page", "Customer", "Auto_Page"}:
        return sender
    if "sender_confidence" not in message:
        return sender
    confidence = str(message.get("sender_confidence") or "unknown").strip().lower()
    annotations = parse_legacy_message_annotations(message.get("content") or "")
    if confidence not in _VERIFIED_SENDER_CONFIDENCES and (
        annotations.quoted_contents or annotations.reaction_types
    ):
        return "Unknown"
    return sender


def has_unverified_sender_claim(message: dict) -> bool:
    """Whether a row claims a known actor without admissible evidence."""
    claimed = str(message.get("sender") or "").strip()
    if claimed not in {"Page", "Customer", "Auto_Page"} or "sender_confidence" not in message:
        return False
    annotations = parse_legacy_message_annotations(message.get("content") or "")
    return bool(annotations.quoted_contents or annotations.reaction_types) and (
        canonical_sender_for_actor(message) == "Unknown"
    )


def parse_legacy_message_annotations(content: str) -> LegacyMessageAnnotations:
    """Separate a legacy body from un-attributed quotes and reaction markers.

    The returned quote/reaction fields intentionally contain no inferred
    sender, actor, or target.  They are observations only until a structured
    parser supplies evidence for those fields.
    """
    raw = (content or "").replace("\\n", "\n").strip()
    reaction_types = tuple(match.group(1) for match in _REACTION_TAG_RE.finditer(raw))
    parts = _QUOTED_REPLY_SPLIT_RE.split(raw)

    def clean(part: str) -> str:
        return _REACTION_TAG_RE.sub("", part).strip()

    body = clean(parts[0]) if parts else ""
    quoted_contents = tuple(part for part in (clean(item) for item in parts[1:]) if part)
    return LegacyMessageAnnotations(
        body=body, quoted_contents=quoted_contents, reaction_types=reaction_types,
    )


def strip_reaction_markers(content: str) -> str:
    """Remove reaction markers without erasing a textual quote boundary.

    This compatibility helper historically removed ``[Quoted Reply/Link]`` as
    well, which made quoted text appear to be written by the bubble sender.
    New prompt formatters should use :func:`parse_legacy_message_annotations`;
    callers needing only sender-authored text should use
    :func:`strip_non_sender_annotations`.
    """
    text = (content or "").replace("\\n", "\n")
    text = _REACTION_TAG_RE.sub("", text)
    lines = []
    for line in text.splitlines():
        # Drop an empty legacy delimiter left behind by a reaction, but retain
        # the delimiter when it introduces actual quoted content.
        quote_body = _QUOTED_PREFIX_RE.sub("", line).strip()
        if not quote_body:
            continue
        if _QUOTED_PREFIX_RE.match(line):
            lines.append(f"[Quoted Reply/Link]: {quote_body}")
        else:
            lines.append(line.strip())
    return "\n".join(lines).strip()


def strip_non_sender_annotations(content: str) -> str:
    """Return only the observed sender body, excluding quote/reaction UI data."""
    return parse_legacy_message_annotations(content).body


def classify_message_kind(content: str) -> str:
    """Return one of ALL_KINDS for a raw message content string."""
    raw = (content or "").replace("\\n", "\n").strip()
    if not raw:
        return KIND_MESSAGE
    if raw.startswith("--- [AD SOURCE]"):
        return KIND_AD_SOURCE
    annotations = parse_legacy_message_annotations(raw)
    without_reactions = annotations.body
    if not annotations.body and not annotations.quoted_contents and annotations.reaction_types:
        return KIND_REACTION
    for pattern in _BANNER_PATTERNS:
        if pattern.match(without_reactions):
            return KIND_SYSTEM_BANNER
    for pattern in _ATTACHMENT_PATTERNS:
        if pattern.match(without_reactions):
            return KIND_ATTACHMENT
    return KIND_MESSAGE


def is_customer_turn(sender: str, kind: str) -> bool:
    """True when a row counts as the customer actually saying something."""
    return sender == "Customer" and kind == KIND_MESSAGE


__all__ = [
    "ALL_KINDS",
    "KIND_AD_SOURCE",
    "KIND_ATTACHMENT",
    "KIND_MESSAGE",
    "KIND_REACTION",
    "KIND_SYSTEM_BANNER",
    "LegacyMessageAnnotations",
    "classify_message_kind",
    "is_customer_turn",
    "parse_legacy_message_annotations",
    "strip_reaction_markers",
    "strip_non_sender_annotations",
]
