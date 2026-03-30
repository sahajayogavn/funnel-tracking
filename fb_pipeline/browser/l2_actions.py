import logging

logger = logging.getLogger("fb_pipeline.browser.actions")


def send_reply_via_cdp(page, reply_text: str, dry_run: bool = True) -> bool:
    """Type a draft reply in the active FB inbox thread without sending it."""
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
        
        # Strip \r explicitly to prevent accidental raw Enter presses from triggering send!
        safe_text = reply_text.replace('\r', '')
        lines = safe_text.split('\n')
        for i, line in enumerate(lines):
            if line:
                page.keyboard.type(line, delay=5)
            if i < len(lines) - 1:
                page.keyboard.press("Shift+Enter")
                page.wait_for_timeout(100) # Yield for React to mount the new DOM element
                
        page.wait_for_timeout(500)
        logger.info(
            "Draft reply typed and left unsent%s: %s...",
            "" if dry_run else " (send disabled)",
            reply_text[:80],
        )
        return True
    except Exception as e:
        logger.error(f"Draft typing failed: {e}")
        return False

def commit_reply_via_cdp(page) -> bool:
    """Press Enter to send the drafted reply."""
    try:
        reply_selector = (
            'div[aria-label*="Reply"], '
            'div[aria-label*="Nhắn tin"], '
            'div[aria-label*="Trả lời"], '
            'div[role="textbox"][contenteditable="true"]'
        )
        reply_box = page.wait_for_selector(reply_selector, timeout=8000)
        if not reply_box:
            logger.warning("Reply box not found for commit")
            return False
        reply_box.click()
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        logger.info("Reply committed and sent via CDP.")
        return True
    except Exception as e:
        logger.error(f"Commit typing failed: {e}")
        return False

def clear_composer_via_cdp(page) -> bool:
    """Clear the contents of the composer box."""
    try:
        reply_selector = (
            'div[aria-label*="Reply"], '
            'div[aria-label*="Nhắn tin"], '
            'div[aria-label*="Trả lời"], '
            'div[role="textbox"][contenteditable="true"]'
        )
        reply_box = page.wait_for_selector(reply_selector, timeout=8000)
        if not reply_box:
            logger.warning("Reply box not found for clear")
            return False
        reply_box.click()
        page.keyboard.press("Meta+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(500)
        logger.info("Composer cleared via CDP.")
        return True
    except Exception as e:
        logger.error(f"Clear composer failed: {e}")
        return False



def navigate_to_thread(page, page_id: str, thread_name: str, thread_id: str = None) -> bool:
    """Navigate to a specific thread in FB Business Suite inbox."""
    try:
        direct_nav_success = False
        clean_thread_id = None
        
        if thread_id:
            clean_thread_id = thread_id.split("_")[-1] if "_" in thread_id else thread_id
            # Real Facebook IDs are usually 15-16 digits. Python hashes are 18-19.
            if len(clean_thread_id) > 16:
                logger.info(f"Thread ID {clean_thread_id} appears to be a local hash. Skipping direct URL navigation.")
            else:
                url_suffix = f"&mailbox_id={page_id}&selected_item_id={clean_thread_id}&thread_type=FB_MESSAGE"
                inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}{url_suffix}"

                logger.info(f"Navigating directly to thread {thread_name} using thread_id {clean_thread_id}")
                page.goto(inbox_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                
                # Verify that the thread actually loaded in the central pane
                try:
                    # The SPA might reject invalid IDs and redirect, so we verify the parameter stayed in the URL
                    if clean_thread_id not in page.url:
                        logger.warning(f"URL mismatch after navigation. Expected {clean_thread_id} in {page.url}. Falling back to sidebar click.")
                    else:
                        # Wait for the composer to be visible to ensure the central pane has loaded
                        reply_selector = (
                            'div[aria-label*="Reply"], '
                            'div[aria-label*="Nhắn tin"], '
                            'div[aria-label*="Trả lời"], '
                            'div[role="textbox"][contenteditable="true"]'
                        )
                        composer = page.locator(reply_selector).first
                        if composer.is_visible(timeout=8000):
                            logger.info(f"Verified thread {thread_name} loaded (URL matched and composer is ready).")
                            direct_nav_success = True
                            return True
                        else:
                            logger.warning(f"Composer not visible for thread {thread_name} via direct URL. Falling back to sidebar click.")
                except Exception as e:
                    logger.warning(f"Error during direct URL verification for {thread_name}: {e}. Falling back to sidebar click.")

        # Fallback: Sidebar click navigation
        logger.info(f"Using sidebar click navigation for thread {thread_name}")
        base_inbox_url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}"
        if "inbox" not in page.url or (thread_id and not direct_nav_success):
            page.goto(base_inbox_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

        page.wait_for_selector('div._5_n1', timeout=10000)
        page.wait_for_timeout(1000)

        thread_el = page.locator('div._5_n1').filter(has_text=thread_name).first
        if thread_el:
            thread_el.click(force=True, timeout=5000)
            page.wait_for_timeout(2000)
            
            reply_selector = (
                'div[aria-label*="Reply"], '
                'div[aria-label*="Nhắn tin"], '
                'div[aria-label*="Trả lời"], '
                'div[role="textbox"][contenteditable="true"]'
            )
            composer = page.locator(reply_selector).first
            if composer.is_visible(timeout=10000):
                logger.info(f"Navigated to thread: {thread_name} via sidebar click and verified composer.")
                return True
            else:
                logger.error(f"Failed to verify composer after clicking {thread_name} in sidebar. Aborting.")
                return False

        logger.warning(f"Thread '{thread_name}' not found in visible sidebar list. Cannot navigate.")
        return False
    except Exception as e:
        logger.error(f"Navigation failed: {e}")
        return False


__all__ = ["navigate_to_thread", "send_reply_via_cdp", "commit_reply_via_cdp", "clear_composer_via_cdp"]
