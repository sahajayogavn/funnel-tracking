from playwright.sync_api import sync_playwright

def main():
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            contexts = browser.contexts
            if not contexts:
                print("No browser contexts found.")
                return
            context = contexts[0]
            pages = context.pages
            for page in pages:
                if "business.facebook.com" in page.url:
                    print(f"Checking URL: {page.url}")
                    
                    target_name = "Hung Bui"
                    is_visible = page.get_by_text(target_name).first.is_visible()
                    print(f"Text '{target_name}' is visible: {is_visible}")
                    
                    target_name2 = "Phương Trang"
                    is_visible2 = page.get_by_text(target_name2).first.is_visible()
                    print(f"Text '{target_name2}' is visible: {is_visible2}")
            browser.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
