import argparse
import logging
from playwright.sync_api import sync_playwright
import json

from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session

logging.basicConfig(level=logging.INFO)

def inspect_threads():
    page_id = "1548373332058326"
    urls = {
        "unreplied_customer": f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}&selected_item_id=100001005716854",
        "replied_page": f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}&selected_item_id=100004466777830"
    }

    with sync_playwright() as p:
        session = attach_to_authorized_session(p, page_id, urls["unreplied_customer"])
        cdp_page = session.page
        
        results = {}
        for name, url in urls.items():
            cdp_page.goto(url, wait_until="networkidle")
            cdp_page.wait_for_timeout(4000)
            
            # Grab the parsed messages using the exact JS from l3_inbox.py
            parsed_messages = cdp_page.evaluate('''() => {
                let region = document.querySelector(
                    'div[aria-label*="Message list container"], ' +
                    'div[role="region"][aria-label*="message"]'
                );
                if (!region) return [];
                let results = [];
                let bubble = region.querySelector('.x1fqp7bg');
                let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
                let topDivs = messageArea.children;
                for (let div of topDivs) {
                    if (div.classList.contains('x14vqqas') || div.querySelector('.x14vqqas')) continue;
                    if (div.classList.contains('xcxhlts') || div.querySelector('.xcxhlts')) continue;
                    if (!div.classList.contains('x1fqp7bg') && !div.querySelector('.x1fqp7bg')) continue;
                    
                    let sender = "Unknown";
                    let outerWrapper = div.querySelector('.xuk3077') || div;
                    let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                    let has_page_class = htmlStr.includes('x13a6bvl');
                    let has_cust_class = htmlStr.includes('x1nhvcw1');
                    let has_avatar = !!div.querySelector('img.img[alt]');
                    
                    if (has_page_class) {
                        sender = "Page";
                    } else if (has_cust_class) {
                        sender = "Customer";
                    } else {
                        sender = has_avatar ? "Customer" : "Page";
                    }
                    
                    let text = div.innerText.trim();
                    results.push({
                        sender: sender, 
                        text: text.substring(0, 50),
                        debug: {
                            page_class: has_page_class,
                            cust_class: has_cust_class,
                            avatar: has_avatar
                        }
                    });
                }
                return results.slice(-5); // last 5 messages
            }''')
            results[name] = parsed_messages

        with open("tmp_dom_results.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
            
        print("DOM dumped to tmp_dom_results.json")

if __name__ == "__main__":
    inspect_threads()
