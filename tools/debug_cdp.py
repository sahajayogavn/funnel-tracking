import sys
from playwright.sync_api import sync_playwright

def test_cdp():
    with sync_playwright() as p:
        print("Connecting to CDP at http://localhost:9222 ...")
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222", timeout=10000)
            print("Connected successfully!")
            print(f"Contexts: {len(browser.contexts)}")
            if browser.contexts:
                context = browser.contexts[0]
                print(f"Pages: {len(context.pages)}")
                for i, page in enumerate(context.pages):
                    print(f"  Page {i}: {page.title()} ({page.url})")
        except Exception as e:
            print(f"Connection failed: {e}")

if __name__ == "__main__":
    test_cdp()
