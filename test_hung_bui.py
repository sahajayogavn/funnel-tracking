import json
import time
from playwright.sync_api import sync_playwright

def inspect():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326")
        page.wait_for_timeout(5000)
        
        # Click on Hung Bui
        try:
            thread_el = page.locator("text='Hung Bui'").first
            thread_el.click(force=True)
            page.wait_for_timeout(5000)
            print("Successfully clicked Hung Bui thread")
        except Exception as e:
            print(f"Could not click Hung Bui: {e}")
            
        data = page.evaluate('''() => {
            let region = document.querySelector('div[aria-label*="Message list container"]');
            if(!region) return "NO REGION";
            
            let results = [];
            let currentTimestamp = "";
            let elements = region.querySelectorAll('.x14vqqas, .x1fqp7bg');
            let processedBubbles = new Set();
            
            for (let el of elements) {
                if (el.classList.contains('x14vqqas')) {
                    let ts = el.innerText.trim();
                    if (ts && ts.length < 50) currentTimestamp = ts;
                    continue;
                }
                if (el.classList.contains('x1fqp7bg')) {
                    let isNested = false;
                    let p = el.parentElement;
                    while(p && p !== region) {
                        if (p.classList.contains('x1fqp7bg') || processedBubbles.has(p)) {
                            isNested = true;
                            break;
                        }
                        p = p.parentElement;
                    }
                    if (isNested) continue;
                    processedBubbles.add(el);
                    
                    let sender = "Unknown";
                    let outerWrapper = el.parentElement || el;
                    let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                    
                    if (htmlStr.includes('x13a6bvl')) {
                        sender = "Page";
                    } else if (htmlStr.includes('x1nhvcw1')) {
                        sender = "Customer";
                    } else {
                        let avatar = el.querySelector('img.img[alt]');
                        sender = avatar ? "Customer" : "Page";
                    }
                    
                    let textContainers = el.querySelectorAll('.x1y1aw1k');
                    let texts = [];
                    if (textContainers.length > 0) {
                        for (let tc of textContainers) {
                            let t = tc.innerText.trim();
                            if (t) texts.push(t);
                        }
                    } else {
                        let spans = el.querySelectorAll('span > span');
                        let found = false;
                        if (spans.length > 0) {
                            for (let sp of spans) {
                                let t = sp.innerText.trim();
                                if (t) { texts.push(t); found = true; }
                            }
                        }
                        if (!found) {
                            let text = el.innerText.trim();
                            if (text && text.length > 2 && text.length < 2000) texts.push(text);
                        }
                    }
                    
                    for(let segment of texts) {
                        results.push({sender, text: segment, timestamp: currentTimestamp});
                    }
                }
            }
            return results;
        }''')
        
        with open("hungbui_test_output.json", "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

inspect()
print("Done writing hungbui_test_output.json")
