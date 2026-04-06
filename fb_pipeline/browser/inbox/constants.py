import re

THREAD_LIST_CONTAINER_SELECTORS = [
    'div[data-pagelet="GenericBizInboxThreadListViewBody"]',
    'div[data-pagelet="BizP13NInboxUinifiedThreadListView"]',
    'div[aria-label="Inbox"]',
]

THREAD_CARD_SELECTORS = [
    'div._5_n1',
    'div[role="listitem"]',
    'a[role="link"][href*="selected_item_id"]',
]

MESSAGE_REGION_SELECTOR = 'div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]'

LOADING_INDICATOR_SELECTORS = [
    '[aria-busy="true"]',
    'div[role="progressbar"]',
    'svg[aria-label*="Loading"]',
]

DAY_NAMES = {
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}

TIME_TODAY = {"today", "hôm nay"}
TIME_YESTERDAY = {"yesterday", "hôm qua"}
MONTH_DAY_RE = re.compile(r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|th(?:g|áng)?)\s*\d{1,2}(?:(?:,\s*|\s+)\d{4})?$', re.I)
MONTH_DAY_REV_RE = re.compile(r'^\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|th(?:g|áng)?)(?:(?:,\s*|\s+)\d{4})?$', re.I)
SLASH_DATE_RE = re.compile(r'^(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?$', re.I)
def thread_card_selector() -> str:
    return ", ".join(THREAD_CARD_SELECTORS)
