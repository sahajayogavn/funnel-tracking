"""Fail-closed detection for unsafe/non-Inbox Meta screens during fetching."""
import threading


_BLOCK_MARKERS = (
    "you're temporarily blocked",
    "you’re temporarily blocked",
    "you've been temporarily blocked",
    "you’ve been temporarily blocked",
    "misusing this feature by going too fast",
)

_ABNORMAL_SCREEN_MARKERS = {
    "login_required": (
        "log in to facebook", "log in to continue", "please log in", "password",
    ),
    "security_checkpoint": (
        "confirm your identity", "security check", "suspicious activity", "checkpoint",
    ),
    "access_lost": (
        "you don't have permission", "you do not have permission", "page isn't available",
        "content isn't available", "content is not available", "access denied",
    ),
    "meta_error_page": (
        "something went wrong", "we're having trouble", "we’re having trouble",
        "an unexpected error occurred", "try again later",
    ),
}

_INBOX_SHELL_MARKERS = ("inbox", "hộp thư")
_NON_INBOX_VISIBLE_TEXT_MIN_CHARS = 80


class FacebookTemporaryBlockError(RuntimeError):
    """Raised when Meta explicitly says the account is temporarily blocked."""


def _rendered_page_text(page) -> str:
    """Read body text without making a failed DOM query a safety decision."""
    try:
        return page.locator("body").inner_text(timeout=1500) or ""
    except Exception:
        try:
            return page.content() or ""
        except Exception:
            return ""


def detect_facebook_fetch_safety_issue(page) -> tuple[str, str]:
    """Return ``(reason, evidence)`` for an explicit unsafe Meta screen.

    A short/empty body is deliberately *not* an error: it can be a normal
    loading shell. A populated screen without an Inbox/Hộp thư shell is unsafe
    only after all known explicit block/login/access/error screens are checked.
    """
    text = _rendered_page_text(page)
    if not isinstance(text, str):
        return "", ""
    normalized = " ".join((text or "").lower().split())
    if any(marker in normalized for marker in _BLOCK_MARKERS):
        return "temporary_feature_block", text[:1000]
    for reason, markers in _ABNORMAL_SCREEN_MARKERS.items():
        if any(marker in normalized for marker in markers):
            return reason, text[:1000]
    if (len(normalized) >= _NON_INBOX_VISIBLE_TEXT_MIN_CHARS
            and not any(marker in normalized for marker in _INBOX_SHELL_MARKERS)):
        return "visible_non_inbox_screen", text[:1000]
    return "", ""


def detect_facebook_temporary_block(page) -> str:
    """Backward-compatible evidence-only API for the former rate-limit check."""
    reason, evidence = detect_facebook_fetch_safety_issue(page)
    return evidence if reason == "temporary_feature_block" else ""


class FacebookBlockGate:
    """Thread-safe, run-scoped latch shared by all fetch tabs."""

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""
        self._evidence = ""

    def trip_if_present(self, page) -> None:
        reason, evidence = detect_facebook_fetch_safety_issue(page)
        if reason:
            self.trip(reason, evidence)

    def trip(self, reason: str, evidence: str = "") -> None:
        with self._lock:
            if not self._event.is_set():
                self._reason = reason
                self._evidence = evidence
                self._event.set()
        raise FacebookTemporaryBlockError(self.message)

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    @property
    def message(self) -> str:
        return f"Facebook fetch safety gate stopped all fetching: {self._reason or 'unsafe Meta screen'}."
