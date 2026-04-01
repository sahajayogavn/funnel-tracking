import sys
import logging
from playwright.sync_api import sync_playwright
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session
from fb_pipeline.browser.l3_inbox import _wait_for_inbox_shell, _wait_for_initial_threads, _extract_visible_threads

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("test_fetch")

def main():
    with sync_playwright() as p:
        page_id = "1548373332058326"
        inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
        try:
            session = attach_to_authorized_session(p, page_id, inbox_url)
        except Exception as e:
            print(f"Error attaching: {e}")
            return
            
        page = session.page
        inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
        
        if "inbox/all" not in page.url:
            print("Navigating to inbox...")
            page.goto(inbox_url, wait_until="networkidle", timeout=60000)
            
        _wait_for_inbox_shell(page, logger)
        _wait_for_initial_threads(page, logger)
        
        threads = _extract_visible_threads(page)
        
        print(f"--- FETCHED {len(threads)} AT ONCE ---")
        
        for idx, t in enumerate(threads[:10]): # Limit to first 10 for display
            name = t.get("name", "")
            preview = t.get("previewText", "")
            time_text = t.get("sidebarTimeText", "")
            
            sender = "Customer"
            preview_clean = preview.lower().strip()
            if preview_clean.startswith("you:") or preview_clean.startswith("bạn:") or preview_clean.startswith("bạn đã gửi"):
                sender = "Admin (Page)"
                
            print(f"[{idx+1}] Thread: {name}")
            print(f"    Sender: {sender}")
            print(f"    Last Message: {preview}")
            print(f"    Date/Time: {time_text}")
            print("-" * 40)
            
if __name__ == "__main__":
    main()
