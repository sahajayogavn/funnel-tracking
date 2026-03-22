import json
from urllib.parse import parse_qs, urlparse

from fb_pipeline.contracts.l1_session import (
    AuthorizedSession,
    CDP_URL,
    CDPConnectionError,
    FACEBOOK_DOMAINS,
    FacebookAuthorizationError,
    PageAccessError,
)


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
                                 cdp_url: str = CDP_URL, prefer_new_tab: bool = True) -> AuthorizedSession:
    browser = connect_to_cdp_browser(playwright, cdp_url)
    context = browser.contexts[0]

    if prefer_new_tab:
        page = context.new_page()
        selected_existing_tab = False
        created_tab = True
    else:
        page = context.pages[0] if context.pages else context.new_page()
        selected_existing_tab = bool(context.pages)
        created_tab = not selected_existing_tab

    page.goto(inbox_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

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
    )


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
        "permission",
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
