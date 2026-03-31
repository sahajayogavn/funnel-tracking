import json
from playwright.sync_api import sync_playwright

def inspect():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326&selected_item_id=100025546875062")
        page.wait_for_timeout(5000)
            
        data = page.evaluate('''() => {
            let region = document.querySelector('div[aria-label*="Message list container"]');
            if(!region) return "NO REGION";
            let bubble = region.querySelector('.x1fqp7bg');
            if(!bubble) return "NO BUBBLE";
            let parent = bubble.parentElement;
            let results = [];
            for(let div of parent.children) {
                if(div.classList.contains('x1fqp7bg') || div.querySelector('.x1fqp7bg') || div.classList.contains('x14vqqas')) {
                    results.push({
                       outerHTML: div.outerHTML.substring(0, 150),
                       innerText: div.innerText.trim()
                    });
                }
            }
            return results;
        }''')
        print("BUBBLES DUMP:", json.dumps(data, indent=2, ensure_ascii=False))

inspect()
