import sqlite3
import json
import logging
from playwright.sync_api import sync_playwright
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session
from fb_pipeline.inbox.l3_pipeline import enrich_thread_record, persist_thread_record

logging.basicConfig(level=logging.INFO)

def force_sync():
    url = "https://business.facebook.com/latest/inbox/all?page_id=1548373332058326&asset_id=1548373332058326&selected_item_id=100004002066759"
    with sync_playwright() as p:
        session = attach_to_authorized_session(p, "1548373332058326", url)
        cdp_page = session.page
        cdp_page.wait_for_timeout(6000)
        
        js_messages = cdp_page.evaluate('''() => {
            let region = document.querySelector(
                'div[aria-label*="Message list container"], ' +
                'div[role="region"][aria-label*="message"]'
            );
            if (!region) return [];
            let results = [];
            let bubble = region.querySelector('.x1fqp7bg');
            let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
            let topDivs = messageArea.children;
            let currentTimestamp = "";
            for (let div of topDivs) {
                if (div.classList.contains('x14vqqas') || div.querySelector('.x14vqqas')) {
                    let tsEl = div.classList.contains('x14vqqas') ? div : div.querySelector('.x14vqqas');
                    if (tsEl) {
                        let ts = tsEl.innerText.trim();
                        if (ts && ts.length < 50) currentTimestamp = ts;
                    }
                    continue;
                }
                if (div.classList.contains('xcxhlts') || div.querySelector('.xcxhlts')) continue;
                if (!div.classList.contains('x1fqp7bg') && !div.querySelector('.x1fqp7bg')) continue;
                
                let sender = "Unknown";
                let outerWrapper = div.querySelector('.xuk3077') || div;
                let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                if (htmlStr.includes('x13a6bvl')) sender = "Page";
                else if (htmlStr.includes('x1nhvcw1')) sender = "Customer";
                else {
                    let avatar = div.querySelector('img.img[alt]');
                    sender = avatar ? "Customer" : "Page";
                }
                
                let text = div.innerText.trim();
                if (text && text.length > 0) {
                    results.push({sender, text, timestamp: currentTimestamp});
                }
            }
            return results;
        }''')
        
        logging.info(f"Scraped {len(js_messages)} Messages")
        
        class Dummy: pass
        tr = Dummy()
        tr.thread_id = "1548373332058326_100004002066759"
        tr.page_id = "1548373332058326"
        tr.thread_name = "Anh Viet Tran"
        tr.preview_text = "..."
        tr.updated_time = "Now"
        tr.dom_index = 0
        
        enriched = enrich_thread_record(tr, js_messages, fb_url="100004002066759")
        
        conn = sqlite3.connect("memory/agent_memory/frankensqlite.db")
        persist_thread_record(conn, enriched)
        conn.commit()
        conn.close()
        logging.info("Synced to DB successfully!")

if __name__ == "__main__":
    force_sync()
