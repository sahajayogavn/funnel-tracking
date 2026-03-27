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
            
            # Create a new page instead of searching
            page = context.new_page()
            
            url = "https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326&mailbox_id=1548373332058326&selected_item_id=100001005716854&thread_type=FB_MESSAGE"
            print(f"Navigating to {url}")
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            
            # Dump all spans that might contain the name "Hung"
            found_elements = page.evaluate('''() => {
                let els = document.querySelectorAll('*');
                let results = [];
                for (let el of els) {
                    if (el.innerText && el.innerText.includes('Hung Bui') && el.children.length === 0) {
                        results.push({
                            tag: el.tagName,
                            class: el.className,
                            role: el.getAttribute('role'),
                            text: el.innerText
                        });
                    }
                }
                return results;
            }''')
            
            print(f"Found {len(found_elements)} elements containing 'Hung Bui':")
            for el in found_elements:
                print(el)
                
            page.close()
            browser.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
