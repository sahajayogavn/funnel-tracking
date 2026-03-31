import os
from playwright.sync_api import sync_playwright

def run():
    print("Starting Playwright Debugger...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts
            if not contexts:
                print("No contexts found")
                return
            page = contexts[0].pages[0]
            
            js_code = """() => {
             let region = document.querySelector('div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]');
             return region ? region.innerText : "Region not found";
         }"""
            result = page.evaluate(js_code)
            print("--- FULL INNERTEXT OF MESSAGE CONTAINER ---")
            print(result)
                    
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    run()
