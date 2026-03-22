from fb_pipeline.browser.l2_actions import navigate_to_thread, send_reply_via_cdp
from fb_pipeline.browser.l3_comments import scrape_comments_ui
from fb_pipeline.browser.l3_inbox import extract_ad_id_labels, scrape_inbox_ui

scrape_comments = scrape_comments_ui
scrape_inbox = scrape_inbox_ui

__all__ = [
    "extract_ad_id_labels",
    "navigate_to_thread",
    "scrape_comments",
    "scrape_comments_ui",
    "scrape_inbox",
    "scrape_inbox_ui",
    "send_reply_via_cdp",
]
