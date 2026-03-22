from dataclasses import dataclass


CDP_URL = "http://127.0.0.1:9222"
FACEBOOK_DOMAINS = ("facebook.com", "messenger.com")


@dataclass
class AuthorizedSession:
    browser: object
    context: object
    page: object
    cdp_url: str
    page_id: str
    inbox_url: str
    selected_existing_tab: bool = False
    created_tab: bool = False

    def close_page(self):
        if not self.created_tab:
            return
        try:
            self.page.close()
        except Exception:
            pass


class BrowserBootstrapError(RuntimeError):
    pass


class CDPConnectionError(BrowserBootstrapError):
    pass


class FacebookAuthorizationError(BrowserBootstrapError):
    pass


class PageAccessError(BrowserBootstrapError):
    pass


__all__ = [
    "AuthorizedSession",
    "BrowserBootstrapError",
    "CDPConnectionError",
    "CDP_URL",
    "FACEBOOK_DOMAINS",
    "FacebookAuthorizationError",
    "PageAccessError",
]
