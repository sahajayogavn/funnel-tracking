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

KIND_MESSAGE = "message"
KIND_SYSTEM_BANNER = "system_banner"
KIND_REACTION = "reaction"
KIND_ATTACHMENT = "attachment"
KIND_AD_SOURCE = "ad_source"

ALL_KINDS = (KIND_MESSAGE, KIND_SYSTEM_BANNER, KIND_REACTION, KIND_ATTACHMENT, KIND_AD_SOURCE)

_REACTION_TAG_RE = re.compile(r':::REACTION_[A-Z]+:::')
_QUOTED_PREFIX_RE = re.compile(r'(\[Quoted Reply/Link\]:\s*)+')

# Exact-shape banners. Anchored so a genuine customer sentence that merely
# mentions "quảng cáo" is not swallowed.
_BANNER_PATTERNS = (
    re.compile(r'^\S.{0,80}? replied to an ad\.?$', re.IGNORECASE),
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


def strip_reaction_markers(content: str) -> str:
    """Remove trailing reaction annotations while keeping the human text.

    ``"Cảm ơn bạn\\n[Quoted Reply/Link]: :::REACTION_LOVE:::"`` is a real
    customer message that the Page later reacted to; only the marker goes.
    """
    text = (content or "").replace("\\n", "\n")
    text = _REACTION_TAG_RE.sub("", text)
    lines = []
    for line in text.splitlines():
        cleaned = _QUOTED_PREFIX_RE.sub("", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def classify_message_kind(content: str) -> str:
    """Return one of ALL_KINDS for a raw message content string."""
    raw = (content or "").replace("\\n", "\n").strip()
    if not raw:
        return KIND_MESSAGE
    if raw.startswith("--- [AD SOURCE]"):
        return KIND_AD_SOURCE
    without_reactions = strip_reaction_markers(raw)
    if not without_reactions and _REACTION_TAG_RE.search(raw):
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
    "classify_message_kind",
    "is_customer_turn",
    "strip_reaction_markers",
]
