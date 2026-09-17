"""Thread locator ladder: L0 direct-URL navigation, then L1 sidebar identity
locate (jump to the Stage 1 offset hint, find and click the thread card by
identity, retrying against a virtualized list with a progressive scroll
walk).

L1 was moved verbatim out of `fb_pipeline/browser/l3_inbox.py` Stage 2 (same
JS, same waits, same log messages) as part of the inbox parallel-fetch
refactor; L0 is new in Phase 2.

# code:inbox-parallel-fetch-001:locator-sidebar
# code:inbox-parallel-fetch-001:locator-direct
"""
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .constants import (
    LOADING_INDICATOR_SELECTORS,
    MESSAGE_REGION_SELECTOR,
    thread_card_selector,
)
from .scroll_helpers import scroll_sidebar_and_wait, sidebar_loading_snapshot
from .thread_detail_parser import extract_thread_messages
from .thread_list_parser import extract_visible_threads, parse_sidebar_time_token

MAX_STAGNANT_STAGE2_CLICK_RETRIES = 6
MAX_THREAD_LOADING_WAIT_MS = 45_000


def _sidebar_snapshot_progressed(before: dict, after: dict) -> bool:
    """Return whether a Stage 2 sidebar action produced observable progress.

    Virtualized inboxes do not always update ``scrollTop`` as cards are
    replaced, so the visible-card fingerprint is part of the check. Conversely
    a static position and identical rendered cards means further retries will
    only repeat the same failed click lookup.
    """
    return (
        before.get("scrollTop", -1) != after.get("scrollTop", -1)
        or before.get("fingerprint", "") != after.get("fingerprint", "")
    )


def _thread_panel_loading_count(page) -> int:
    """Count visible loading indicators inside the active message panel only."""
    try:
        result = page.evaluate(
            '''({messageSelector, loadingSelectors}) => {
                const panel = document.querySelector(messageSelector) || document.querySelector('div[role="main"]');
                if (!panel) return 0;
                return loadingSelectors.reduce((count, selector) => count + Array.from(
                    panel.querySelectorAll(selector)
                ).filter(el => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                }).length, 0);
            }''',
            {"messageSelector": MESSAGE_REGION_SELECTOR, "loadingSelectors": LOADING_INDICATOR_SELECTORS},
        )
        return int(result or 0)
    except Exception:
        return 0


@dataclass
class LocateResult:
    clicked: bool
    method: str
    attempts: int
    prev_fb_url: str
    pre_click_fingerprint: str


def normalize_preview_for_match(s: str) -> str:
    """Normalise a message/preview string for common-prefix comparison.

    Same rule as Stage 1's nested ``_normalize_msg`` in
    ``l3_inbox.discover_threads``: strip a leading ``[AD SOURCE]`` block,
    strip a leading ``you:``/``bạn:`` prefix, then reduce to lowercase
    alphanumerics only. Extracted here (rather than duplicated) so the L0
    direct-URL locator can reuse the exact same rule without editing
    ``l3_inbox.py``.
    """
    if not s:
        return ""
    s = re.sub(r'^---\s*\[AD SOURCE\]:.*?---\s*', '', s, flags=re.DOTALL)
    s = re.sub(r'^(you|bạn):\s*', '', s, flags=re.IGNORECASE)
    return ''.join(c.lower() for c in s if c.isalnum())


def _preview_matches(a: str, b: str) -> bool:
    """Common-prefix comparison of two already-normalised strings."""
    min_len = min(len(a), len(b))
    if min_len > 0:
        return a[:min_len] == b[:min_len]
    return a == b


PREVIEW_PROBE_LEN = 24


def _preview_matches_any(ui_norm: str, tail_norms: list[str]) -> bool:
    """Preview matches if it prefix-matches the last bubble, or a leading
    probe of it (first ``PREVIEW_PROBE_LEN`` chars) occurs inside any of the
    last bubbles, or a bubble's own leading probe occurs inside the preview.

    Retrospective [2026-09-16]: sidebar previews carry trailing label noise
    ("...gìđâuạ" + "tueintakeadid": time token, stage label, ad-id label) so a
    whole-preview substring test failed on the live 90d run ('Mai Hoa').
    """
    if not tail_norms:
        return False
    if _preview_matches(tail_norms[-1], ui_norm):
        return True
    probe = ui_norm[:PREVIEW_PROBE_LEN]
    if len(probe) >= 12 and any(probe in t for t in tail_norms if t):
        return True
    for t in tail_norms:
        tp = (t or "")[:PREVIEW_PROBE_LEN]
        if len(tp) >= 12 and tp in ui_norm:
            return True
    return False


GENERIC_INBOX_HEADERS = frozenset({"inbox", "hộp thư", "hộp thư đến", "messages", "tin nhắn"})


def _is_generic_inbox_header(header_text: str) -> bool:
    """Business Suite shows a generic mailbox title instead of the contact name
    right after a direct navigation; that header carries no identity signal."""
    return " ".join((header_text or "").casefold().split()) in GENERIC_INBOX_HEADERS


def _header_name_matches(name: str, header_text: str) -> bool:
    """Case-insensitive containment rule -- same one ``verify_thread_switch``
    uses to decide whether the active thread panel's header matches the
    expected thread name."""
    clean_name = (name or "").lower().strip()
    clean_header = (header_text or "").lower().strip()
    if not clean_name or not clean_header:
        return False
    return clean_name in clean_header or clean_header in clean_name


def locate_thread_direct(page, page_id: str, task, logger) -> LocateResult:
    """L0: navigate straight to the thread via the direct-URL shape
    ``l2_actions.navigate_to_thread`` already uses in production, then
    verify with a three-way check (all required): PSID still in
    ``page.url``, header name matches, and the last extracted message
    matches ``preview_text`` (same normalisation Stage 1 uses).

    Any single mismatch falls through to L1 (returns ``clicked=False``).

    # code:inbox-parallel-fetch-001:locator-direct
    """
    thread_record = task.record
    name = thread_record.thread_name
    psid = thread_record.selected_item_id or task.psid_hint

    if not psid:
        logger.info(
            f"L0 direct-URL skipped for '{name}': no selected_item_id or psid_hint available."
        )
        return LocateResult(clicked=False, method="direct_url", attempts=0,
                             prev_fb_url="", pre_click_fingerprint="")

    url = (
        f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
        f"&mailbox_id={page_id}&selected_item_id={psid}&thread_type=FB_MESSAGE"
    )
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as e:
        logger.info(f"L0 direct-URL navigation failed for '{name}' (psid={psid}): {e}")
        return LocateResult(clicked=False, method="direct_url", attempts=1,
                             prev_fb_url="", pre_click_fingerprint="")

    try:
        page.wait_for_selector(MESSAGE_REGION_SELECTOR, timeout=10000)
    except Exception:
        # The panel may still be legitimately empty/slow; verification below
        # decides pass/fail, this wait is best-effort only.
        pass

    # 1) PSID still present in the URL.
    try:
        url_psid = parse_qs(urlparse(page.url).query).get('selected_item_id', [''])[0]
    except Exception:
        url_psid = ""
    if not url_psid or url_psid != psid:
        logger.info(
            f"L0 direct-URL rejected for '{name}': psid={psid!r} not present in page.url "
            f"({page.url!r})."
        )
        return LocateResult(clicked=False, method="direct_url", attempts=1,
                             prev_fb_url="", pre_click_fingerprint="")

    # 2) Header name matches (same containment rule as verify_thread_switch).
    try:
        header_text = page.evaluate('''() => {
            let main = document.querySelector('div[role="main"]');
            if (!main) main = document.body;
            let h2s = Array.from(main.querySelectorAll('h2[dir="auto"], h1, h3, div[role="heading"]'));
            for(let h of h2s) {
                if (h.innerText && h.innerText.length > 0) return h.innerText.trim();
            }
            let main_text = main.innerText || "";
            return main_text.substring(0, 500);
        }''')
    except Exception:
        header_text = ""
    # Retrospective [2026-09-16]: after a fresh ``page.goto`` Business Suite
    # renders "Inbox" as the panel h1 (the same first-thread anomaly handled in
    # ``verify_thread_switch``), so the live 7d --workers 5 run rejected L0 on
    # every thread. Treat that generic header as neutral and let the PSID +
    # preview checks decide; only a *different* real name is a rejection.
    if not _header_name_matches(name, header_text) and not _is_generic_inbox_header(header_text):
        logger.info(
            f"L0 direct-URL rejected for '{name}': header {header_text!r} does not match thread_name."
        )
        return LocateResult(clicked=False, method="direct_url", attempts=1,
                             prev_fb_url="", pre_click_fingerprint="")

    # 3) Last extracted message matches preview_text.
    try:
        messages = extract_thread_messages(page)
    except Exception as e:
        logger.info(f"L0 direct-URL rejected for '{name}': failed to extract messages ({e}).")
        return LocateResult(clicked=False, method="direct_url", attempts=1,
                             prev_fb_url="", pre_click_fingerprint="")

    if not messages:
        logger.info(f"L0 direct-URL rejected for '{name}': no messages extracted to verify preview.")
        return LocateResult(clicked=False, method="direct_url", attempts=1,
                             prev_fb_url="", pre_click_fingerprint="")

    # Retrospective [2026-09-16]: a quoted reply renders as
    # "<quoted context> quoted reply link <actual message>", so the sidebar
    # preview is a *suffix/substring* of the last bubble, not a prefix (live
    # 90d run, thread 'Mai Hoa'). Reactions/attachments can also make the
    # preview refer to the second-to-last bubble. Accept a prefix match or a
    # substring match against any of the last three bubbles.
    ui_norm = normalize_preview_for_match(thread_record.preview_text or "")
    tail_norms = [
        normalize_preview_for_match(m.get("text", "") if isinstance(m, dict) else "")
        for m in messages[-3:]
    ]
    db_norm = tail_norms[-1] if tail_norms else ""
    if not _preview_matches_any(ui_norm, tail_norms):
        logger.info(
            f"L0 direct-URL rejected for '{name}': last message does not match preview_text "
            f"(last_msg_norm={db_norm!r} vs preview_norm={ui_norm!r})."
        )
        return LocateResult(clicked=False, method="direct_url", attempts=1,
                             prev_fb_url="", pre_click_fingerprint="")

    logger.info(f"L0 direct-URL verified for '{name}' via psid={psid}.")
    # prev_fb_url="" so the caller's verify_thread_switch treats this PSID as
    # a change even when thread_record.selected_item_id was already set.
    return LocateResult(clicked=True, method="direct_url", attempts=1,
                         prev_fb_url="", pre_click_fingerprint="")


def locate_thread(page, page_id: str, task, logger, absolute_top: float = 0) -> LocateResult:
    """Run the full locate ladder: L0 direct-URL, falling through to L1
    sidebar identity locate. Returns whichever step's ``LocateResult`` won
    (or L1's failure result if both steps fail).

    # code:inbox-parallel-fetch-001:locator-direct
    """
    l0_result = locate_thread_direct(page, page_id, task, logger)
    if l0_result.clicked:
        return l0_result

    l1_result = locate_thread_in_sidebar(page, task, logger, absolute_top=absolute_top)
    # Retrospective [2026-09-16]: when L0 already navigated this tab to the
    # target PSID but was rejected on a soft check, the sidebar click in L1
    # produces no URL change, so verify_thread_switch would report
    # "no_url_change". Reporting prev_fb_url="" lets the caller treat the PSID
    # already in the URL as the confirmed switch (it *is* the target PSID).
    if l1_result.clicked:
        psid = _task_psid(task)
        try:
            current = getattr(page, "url", "") or ""
        except Exception:
            current = ""
        if psid and psid == _selected_item_id_from_url(current) and l1_result.prev_fb_url == psid:
            l1_result.prev_fb_url = ""
    return l1_result


def _task_psid(task) -> str:
    record = getattr(task, "record", task)
    return (getattr(record, "selected_item_id", "") or getattr(task, "psid_hint", "") or "").strip()


def _selected_item_id_from_url(url: str) -> str:
    try:
        return parse_qs(urlparse(url).query).get("selected_item_id", [""])[0]
    except Exception:
        return ""


def locate_thread_in_sidebar(page, task_or_record, logger, absolute_top: float = 0) -> LocateResult:
    """Jump to the Stage 1 scroll hint, then find and click the thread card
    by identity (identity key -> PSID -> hovercard -> name -> name+preview),
    retrying against a virtualized sidebar list.

    ``task_or_record`` may be a ``ThreadTask`` (its ``.record`` and
    ``.absolute_top`` are used) or a bare ``ThreadRecord`` (in which case the
    ``absolute_top`` kwarg is used).
    """
    if hasattr(task_or_record, "record"):
        thread_record = task_or_record.record
        abs_top = task_or_record.absolute_top
    else:
        thread_record = task_or_record
        abs_top = absolute_top

    name = thread_record.thread_name

    if abs_top > 0:
        jump_target = max(0, int(abs_top) - 150)
        try:
            page.evaluate(f'''(pos) => {{
                let cards = Array.from(document.querySelectorAll('{thread_card_selector()}'));
                if (cards.length > 0) {{
                    let parent = cards[0].closest('div');
                    while(parent && parent.tagName !== 'BODY') {{
                        let style = window.getComputedStyle(parent);
                        if (parent.scrollHeight > parent.clientHeight || ['auto', 'scroll'].includes(style.overflowY)) {{
                            parent.scrollTop = pos;
                            return;
                        }}
                        parent = parent.parentElement;
                    }}
                }}
            }}''', jump_target)
            page.wait_for_timeout(1000)
        except Exception as e:
            logger.warning(f"Failed to jump to absoluteTop {jump_target}: {e}")

    prev_fb_url = ""
    try:
        prev_fb_url = parse_qs(urlparse(page.url).query).get('selected_item_id', [''])[0]
    except Exception:
        pass

    pre_click_fingerprint = page.evaluate('''() => {
        let r = document.querySelector(
            'div[aria-label*="Message list container"], ' +
            'div[role="region"][aria-label*="message"]'
        );
        return (!r) ? "" : (r.innerText || "").substring(0, 200);
    }''')

    # Use thread_card_selector imported at the top of the file
    clicked = False
    click_attempts = 0
    stagnant_click_retries = 0
    sidebar_scroll_round = 0
    target_time_parsed = None
    try:
        target_time_parsed = parse_sidebar_time_token(getattr(thread_record, "sidebar_time_text", "") or "")
    except Exception:
        target_time_parsed = None
    click_lookup_started = time.monotonic()
    while not clicked and click_attempts < 150:
        elapsed_click_wait_ms = int((time.monotonic() - click_lookup_started) * 1000)
        if elapsed_click_wait_ms >= MAX_THREAD_LOADING_WAIT_MS:
            logger.warning(
                f"Stopping Stage 2 lookup for '{name}' after "
                f"{elapsed_click_wait_ms}ms waiting for its loading indicator to clear."
            )
            break
        click_attempts += 1
        try:
            clicked = page.evaluate(r'''({sidebarIdentityKey, threadSelector, targetName, targetSelectedItemId, targetPreviewText, targetFbUrl}) => {
                let candidates = Array.from(document.querySelectorAll(threadSelector));
                function pickTimeToken(lines) {
                    for (let i = lines.length - 1; i >= 1; i--) {
                        const token = (lines[i] || '').trim();
                        if (!token) continue;
                        if (/^\d+[smhdw]$/i.test(token)) return token;
                        if (/^(today|yesterday|hôm nay|hôm qua)$/i.test(token)) return token;
                        if (/^(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)$/i.test(token)) return token;
                        if (/^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}$/i.test(token)) return token;
                    }
                    return '';
                }
                function getIdentity(el) {
                    const text = (el.innerText || '').trim();
                    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                    const name = lines[0] || '';
                    const sidebarTimeText = pickTimeToken(lines);
                    const previewLines = lines.slice(1).filter(line => line !== sidebarTimeText);
                    const hrefEl = el.closest('a[href]') || el.querySelector('a[href]');
                    const href = hrefEl ? (hrefEl.getAttribute('href') || '') : '';

                    let hovercard = el.getAttribute('data-hovercard') || '';
                    if (!hovercard) {
                        const hcEl = el.querySelector('[data-hovercard]');
                        if (hcEl) hovercard = hcEl.getAttribute('data-hovercard') || '';
                    }
                    let fbUrl = hovercard ? hovercard.split('?')[0] : '';

                    let selectedItemId = '';
                    try {
                        if (href) {
                            const absolute = new URL(href, window.location.origin);
                            selectedItemId = absolute.searchParams.get('selected_item_id') || '';
                        }
                    } catch (_) {}
                    const attrs = [];
                    for (const attr of Array.from(el.attributes || [])) {
                        if (!attr || !attr.name) continue;
                        if (attr.name.startsWith('data-') || attr.name.startsWith('aria-') || attr.name === 'href') {
                            attrs.push(`${attr.name}=${attr.value || ''}`);
                        }
                    }
                    const identityParts = [name, previewLines.join(' | '), sidebarTimeText, selectedItemId, href, fbUrl, attrs.join('|')].filter(Boolean);
                    return identityParts.join(' || ');
                }
                let matchCandidates = [];
                for (let c of candidates) {
                    if (sidebarIdentityKey && getIdentity(c) === sidebarIdentityKey) {
                        c.scrollIntoView({block: "center"});
                        c.click();
                        return true;
                    }

                    // Relaxed Match Prep
                    let norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const text = (c.innerText || '').trim();
                    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                    const elName = lines[0] || '';

                    let hovercard = c.getAttribute('data-hovercard') || '';
                    if (!hovercard) {
                        const hcEl = c.querySelector('[data-hovercard]');
                        if (hcEl) hovercard = hcEl.getAttribute('data-hovercard') || '';
                    }
                    let elFbUrl = hovercard ? hovercard.split('?')[0] : '';

                    const hrefEl = c.closest('a[href]') || c.querySelector('a[href]');
                    let elSelectedItemId = '';
                    if (hrefEl) {
                        try {
                            const absolute = new URL(hrefEl.getAttribute('href'), window.location.origin);
                            elSelectedItemId = absolute.searchParams.get('selected_item_id') || '';
                        } catch (_) {}
                    }

                    if (targetSelectedItemId && elSelectedItemId && targetSelectedItemId === elSelectedItemId) {
                        c.scrollIntoView({block: "center"});
                        c.click();
                        return true;
                    }

                    if (targetFbUrl && elFbUrl && targetFbUrl === elFbUrl) {
                        c.scrollIntoView({block: "center"});
                        c.click();
                        return true;
                    }

                    if (elName && norm(elName) === norm(targetName)) {
                        matchCandidates.push({c, lines});
                    }
                }

                // If we have exactly 1 name match, just click it.
                if (matchCandidates.length === 1) {
                    matchCandidates[0].c.scrollIntoView({block: "center"});
                    matchCandidates[0].c.click();
                    return true;
                }

                // If multiple name matches, fallback to preview text resolving
                let norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                for (let mc of matchCandidates) {
                    let preLines = mc.lines.slice(1).join(' ');
                    if (!targetPreviewText || norm(preLines).includes(norm(targetPreviewText)) || norm(targetPreviewText).includes(norm(preLines))) {
                        mc.c.scrollIntoView({block: "center"});
                        mc.c.click();
                        return true;
                    }
                }

                return false;
            }''', {
                "sidebarIdentityKey": thread_record.sidebar_identity_key,
                "threadSelector": thread_card_selector(),
                "targetName": thread_record.thread_name,
                "targetSelectedItemId": thread_record.selected_item_id,
                "targetPreviewText": thread_record.preview_text,
                "targetFbUrl": thread_record.fb_url
            })
        except Exception:
            pass

        if not clicked:
            panel_loading_count = _thread_panel_loading_count(page)
            if panel_loading_count:
                logger.info(
                    f"Stage 2 thread panel loading for '{name}' "
                    f"(indicators={panel_loading_count}, elapsed_ms={elapsed_click_wait_ms}); waiting."
                )
                page.wait_for_timeout(1000)
                continue
            if click_attempts <= 3:
                page.wait_for_timeout(500)
            else:
                # Retrospective [2026-09-16]: doc:inbox-fetch-pipeline-001 §5/§6
                # A worker tab does not share the orchestrator's sidebar scroll
                # position, so a blind `mouse.wheel` retry here (the old
                # behaviour) could not tell "still loading" apart from "scrolled
                # straight past the target". Reuse the same progressive
                # `scroll_sidebar_and_wait` round Stage 1 uses -- it already
                # waits out loading indicators and reports a stable fingerprint
                # -- then check for overshoot before trying the identity match
                # again on the next loop iteration.
                try:
                    sidebar_scroll_round += 1
                    before_snapshot = sidebar_loading_snapshot(page)
                    after_snapshot = scroll_sidebar_and_wait(
                        page, logger, scroll_round=sidebar_scroll_round, timeout_ms=60000
                    )

                    if click_attempts % 10 == 0:
                        logger.info(
                            f"Stage 2 progressive scroll for '{name}' "
                            f"(attempt {click_attempts}, round {sidebar_scroll_round}): "
                            f"count {before_snapshot.get('count')} -> {after_snapshot.get('count')}"
                        )

                    if _sidebar_snapshot_progressed(before_snapshot, after_snapshot):
                        stagnant_click_retries = 0
                    else:
                        stagnant_click_retries += 1
                        if stagnant_click_retries >= MAX_STAGNANT_STAGE2_CLICK_RETRIES:
                            logger.warning(
                                f"Stopping Stage 2 lookup for '{name}' after "
                                f"{stagnant_click_retries} consecutive sidebar no-progress retries."
                            )
                            break

                    # Overshoot stop: if the sidebar has scrolled past the
                    # target's own time bucket (its last visible card is more
                    # than one calendar day older than the target thread),
                    # further scrolling will only waste time -- the target has
                    # either already been passed or moved. Every parse is
                    # guarded so an unrecognised time token never triggers
                    # this stop.
                    try:
                        if target_time_parsed is not None:
                            target_days = target_time_parsed.get("days_ago")
                            visible_now = extract_visible_threads(page)
                            if target_days is not None and visible_now:
                                last_token = visible_now[-1].get("sidebarTimeText", "")
                                last_parsed = parse_sidebar_time_token(last_token)
                                last_days = last_parsed.get("days_ago")
                                if last_days is not None and (last_days - target_days) > 1:
                                    logger.info(
                                        f"Stage 2 sidebar overshoot for '{name}': last visible card "
                                        f"({last_token!r}, days_ago={last_days}) is more than one day "
                                        f"older than target (days_ago={target_days})."
                                    )
                                    return LocateResult(
                                        clicked=False,
                                        method="sidebar_identity",
                                        attempts=click_attempts,
                                        prev_fb_url=prev_fb_url,
                                        pre_click_fingerprint=pre_click_fingerprint,
                                    )
                    except Exception:
                        pass

                except Exception as e:
                    logger.error(f"Error executing progressive sidebar scroll: {e}")

    if not clicked:
        logger.warning(f"Failed to verify click for thread '{name}' in Stage 2 after {click_attempts} scroll attempts.")

    return LocateResult(
        clicked=clicked,
        method="sidebar_identity",
        attempts=click_attempts,
        prev_fb_url=prev_fb_url,
        pre_click_fingerprint=pre_click_fingerprint,
    )


__all__ = [
    "LocateResult",
    "locate_thread",
    "locate_thread_direct",
    "locate_thread_in_sidebar",
    "normalize_preview_for_match",
    "MAX_STAGNANT_STAGE2_CLICK_RETRIES",
    "MAX_THREAD_LOADING_WAIT_MS",
    "_sidebar_snapshot_progressed",
    "_thread_panel_loading_count",
]
