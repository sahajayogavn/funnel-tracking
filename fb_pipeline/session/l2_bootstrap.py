import json
import threading
from urllib.parse import parse_qs, urlparse

from fb_pipeline.contracts.l1_session import (
    AuthorizedSession,
    CDP_URL,
    CDPConnectionError,
    FACEBOOK_DOMAINS,
    FacebookAuthorizationError,
    PageAccessError,
    WORKER_TAB_ROLE_PREFIX,
)

# code:inbox-parallel-fetch-001:worker-session
# Tab lookup/creation enumerates and evaluates on every page in the CDP
# context, which races when several worker threads attach concurrently.
# Serialize the whole attach path (not just the tab-role scan) under one
# module-level lock.
_ATTACH_LOCK = threading.Lock()


def connect_to_cdp_browser(playwright, cdp_url: str = CDP_URL):
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        raise CDPConnectionError(
            f"Failed to connect via CDP at {cdp_url}. Make sure Chrome is running with --remote-debugging-port=9222"
        ) from exc

    if not browser.contexts:
        raise CDPConnectionError(f"CDP browser at {cdp_url} has no contexts")

    return browser


def attach_to_authorized_session(playwright, page_id: str, inbox_url: str,
                                 cdp_url: str = CDP_URL, prefer_new_tab: bool = True,
                                 tab_role: str | None = None) -> AuthorizedSession:
    """Attach one role to one reusable CDP tab.

    Roles keep scanning isolated from outbound work: ``scan_inbox``,
    ``scan_comments``, and any number of ``outbound:<worker-id>`` tabs.
    The marker lives in the page DOM and therefore survives scheduler cycles
    while the browser remains open.

    The whole attach path is serialized under a module-level lock
    (``_ATTACH_LOCK``): it enumerates and evaluates on every page in the CDP
    context, which races when several worker threads attach concurrently
    (`docs/architect/inbox-fetch-pipeline.md` §6).
    """
    with _ATTACH_LOCK:
        browser = connect_to_cdp_browser(playwright, cdp_url)
        context = browser.contexts[0]

        page = None
        if tab_role:
            for candidate in context.pages:
                try:
                    if candidate.evaluate("document.documentElement.dataset.masTabRole") == tab_role:
                        page = candidate
                        break
                except Exception:
                    continue
        if page:
            selected_existing_tab, created_tab = True, False
        elif prefer_new_tab:
            page = context.new_page()
            selected_existing_tab, created_tab = False, True
        else:
            # Reuse the already loaded inbox for the requested Page when possible.
            # This avoids a second Meta navigation during CDP recovery, which can
            # otherwise time out even though a healthy authenticated inbox tab is
            # still open.
            # Retrospective [2026-09-16]: role tabs (scan_inbox_worker:<i>,
            # outbound:<id>, ...) share the same inbox URL. Picking one here
            # made the fetch orchestrator and a Stage 2 worker drive the same
            # tab; the worker's direct-URL goto then destroyed the
            # orchestrator's Stage 1 execution context (live 90d run).
            unroled = [candidate for candidate in context.pages if not _tab_role_of(candidate)]
            page = next((candidate for candidate in unroled if inbox_url in (candidate.url or "")), None)
            page = page or (unroled[0] if unroled else None)
            if page is None:
                page = context.new_page()
                selected_existing_tab, created_tab = False, True
            else:
                selected_existing_tab, created_tab = True, False

        if created_tab or inbox_url not in (page.url or ""):
            # Meta frequently delays DOMContentLoaded long after the inbox shell is
            # usable. Commit-level navigation lets the caller's DOM readiness check
            # decide when scraping can safely begin.
            page.goto(inbox_url, wait_until="commit", timeout=30000)
            page.wait_for_timeout(3000)

        if tab_role:
            page.evaluate("role => { document.documentElement.dataset.masTabRole = role; document.title = `[MAS:${role}] ${document.title}`; }", tab_role)

        ensure_facebook_authorized(page)
        ensure_page_access(page, page_id)

        return AuthorizedSession(
            browser=browser,
            context=context,
            page=page,
            cdp_url=cdp_url,
            page_id=page_id,
            inbox_url=inbox_url,
            selected_existing_tab=selected_existing_tab,
            created_tab=created_tab,
            tab_role=tab_role,
        )


# code:inbox-parallel-fetch-001:worker-session
def stamp_tab_role(page, tab_role: str | None) -> None:
    """Re-apply the DOM role marker on *page*.

    Retrospective [2026-09-16]: the marker lives in ``document.documentElement``
    and is wiped by every full navigation (``page.goto``). The direct-URL locate
    (L0) navigates the worker tab, so without re-stamping each run would fail
    to find its role tabs and create new ones (observed: +2 tabs per run).
    """
    if not tab_role:
        return
    try:
        page.evaluate(
            "role => { document.documentElement.dataset.masTabRole = role;"
            " if (!document.title.startsWith('[MAS:')) document.title = `[MAS:${role}] ${document.title}`; }",
            tab_role,
        )
    except Exception:
        pass


# code:inbox-parallel-fetch-001:worker-session
def attach_worker_session(playwright, page_id: str, inbox_url: str, worker_index: int,
                           cdp_url: str = CDP_URL) -> AuthorizedSession:
    """Attach a parallel-fetch Stage 2 worker to its own long-lived role tab.

    Thin wrapper over ``attach_to_authorized_session`` with
    ``prefer_new_tab=True`` and ``tab_role="scan_inbox_worker:<worker_index>"``.
    The attach itself is serialized by the shared ``_ATTACH_LOCK`` inside
    ``attach_to_authorized_session``.
    """
    tab_role = f"{WORKER_TAB_ROLE_PREFIX}{worker_index}"
    return attach_to_authorized_session(
        playwright,
        page_id,
        inbox_url,
        cdp_url=cdp_url,
        prefer_new_tab=True,
        tab_role=tab_role,
    )


def _tab_role_of(page) -> str:
    """Return the DOM role marker of *page* ("" when unmarked or unreadable)."""
    try:
        role = page.evaluate("document.documentElement.dataset.masTabRole || ''")
    except Exception:
        return ""
    return role if isinstance(role, str) else ""


def ensure_facebook_authorized(page):
    current_url = getattr(page, "url", "") or ""
    lowered = current_url.lower()

    if any(domain in lowered for domain in FACEBOOK_DOMAINS):
        if any(marker in lowered for marker in ["/login", "checkpoint", "two_step_verification", "recover"]):
            raise FacebookAuthorizationError(f"Facebook session is not authorized: {current_url}")
        return

    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""

    if "log in" in title or "login" in title:
        raise FacebookAuthorizationError(f"Facebook session is not authorized: {current_url or title}")

    raise FacebookAuthorizationError(f"Page is not on a Facebook/Messenger surface: {current_url}")


def ensure_page_access(page, expected_page_id: str):
    current_url = getattr(page, "url", "") or ""
    actual_page_id = extract_asset_id(current_url)

    if actual_page_id and actual_page_id != expected_page_id:
        raise PageAccessError(
            f"CDP session is on asset_id={actual_page_id}, expected asset_id={expected_page_id}"
        )

    page_text = ""
    try:
        page_text = page.content()
    except Exception:
        page_text = ""

    denied_markers = [
        "you don't have access",
        "you do not have access",
        "you no longer have access",
        "this content isn't available",
        "this page isn't available",
        "page isn't available",
        "doesn't have permission to manage",
    ]
    lowered = page_text.lower()
    if any(marker in lowered for marker in denied_markers):
        raise PageAccessError(f"Facebook session does not have access to page {expected_page_id}")


def extract_asset_id(url: str):
    if not url:
        return None
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
    except Exception:
        return None
    values = qs.get("asset_id")
    return values[0] if values else None


def sanitize_storage_state_file(path: str):
    with open(path, "r") as f:
        state_data = json.load(f)

    cookies = state_data.get("cookies", [])
    fb_cookies = []
    for cookie in cookies:
        domain = cookie.get("domain", "")
        if any(fb_domain in domain for fb_domain in FACEBOOK_DOMAINS):
            cookie = dict(cookie)
            if cookie.get("expires", 0) < 0:
                cookie.pop("expires", None)
            fb_cookies.append(cookie)

    state_data["cookies"] = fb_cookies

    with open(path, "w") as f:
        json.dump(state_data, f, indent=2)
