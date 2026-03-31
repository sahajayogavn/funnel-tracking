import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    async with async_playwright() as p:
         browser = await p.chromium.connect_over_cdp("http://localhost:9222")
         contexts = browser.contexts
         if not contexts:
             return
         page = contexts[0].pages[0]
         
         # Navigate to the specific thread
         url = "https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326&mailbox_id=1548373332058326&selected_item_id=100002612557916&thread_type=FB_MESSAGE"
         print("Navigating...")
         await page.goto(url, wait_until="networkidle")
         await page.wait_for_timeout(5000)
         
         js = """() => {
             let region = document.querySelector('div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]');
             if (!region) return "Region not found";
             
             let elements = region.querySelectorAll('*');
             let output = [];
             for(let e of elements) {
                 if(e.classList.contains('x1fqp7bg') || e.classList.contains('x14vqqas')) {
                    output.push({cls: e.className, t: e.innerText.trim()});
                 }
             }
             return {
                raw_text: region.innerText,
                classes: output
             };
         }"""
         result = await page.evaluate(js)
         print(result)

asyncio.run(main())
