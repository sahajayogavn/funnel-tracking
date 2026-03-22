import logging

logger = logging.getLogger("fb_pipeline.browser.actions")


def send_reply_via_cdp(page, reply_text: str, dry_run: bool = True) -> bool:
    """Type a reply in the currently active FB inbox thread via CDP."""
    try:
        reply_selector = (
            'div[aria-label*="Reply"], '
            'div[aria-label*="Nhắn tin"], '
            'div[aria-label*="Trả lời"], '
            'div[role="textbox"][contenteditable="true"]'
        )

        reply_box = page.wait_for_selector(reply_selector, timeout=8000)
        if not reply_box:
            logger.warning("Reply box not found")
            return False

        reply_box.click()
        page.wait_for_timeout(500)
        page.keyboard.type(reply_text, delay=30)
        page.wait_for_timeout(500)

        if dry_run:
            logger.info(f"[DRY-RUN] Reply typed (NOT sent): {reply_text[:80]}...")
            return True

        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        logger.info(f"[LIVE] Reply sent: {reply_text[:80]}...")
        return True
    except Exception as e:
        logger.error(f"Reply failed: {e}")
        return False



def navigate_to_thread(page, page_id: str, thread_name: str) -> bool:
    """Navigate to a specific thread in FB Business Suite inbox."""
    try:
        inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"

        if "inbox" not in page.url:
            page.goto(inbox_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

        page.wait_for_selector('div._5_n1', timeout=10000)
        page.wait_for_timeout(1000)

        thread_el = page.locator('div._5_n1').filter(has_text=thread_name).first
        if thread_el:
            thread_el.click(force=True, timeout=5000)
            page.wait_for_timeout(2000)
            logger.info(f"Navigated to thread: {thread_name}")
            return True

        logger.warning(f"Thread '{thread_name}' not found in visible list")
        return False
    except Exception as e:
        logger.error(f"Navigation failed: {e}")
        return False


__all__ = ["navigate_to_thread", "send_reply_via_cdp"]
