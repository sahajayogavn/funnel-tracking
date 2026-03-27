import logging
from playwright.sync_api import sync_playwright
from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session

logging.basicConfig(level=logging.INFO)

def test_live_detect():
    url = "https://business.facebook.com/latest/inbox/all?page_id=1548373332058326&asset_id=1548373332058326&selected_item_id=100004002066759&mailbox_id=1548373332058326&thread_type=FB_MESSAGE"
    page_id = "1548373332058326"
    
    with sync_playwright() as p:
        logging.info(f"Connecting to CDP and navigating to URL...")
        session = attach_to_authorized_session(p, page_id, url)
        cdp_page = session.page
        cdp_page.wait_for_timeout(6000)
        
        logging.info("Executing DOM Real-time Sender Detection...")
        result_json = cdp_page.evaluate('''() => {
            let customer_name = "Unknown";
            
            // 1. Get customer name from their chat bubble avatars
            let customerImages = document.querySelectorAll('div.x1nhvcw1 img.img[alt]');
            for (let i = customerImages.length - 1; i >= 0; i--) { // Prefer most recent
                let alt = customerImages[i].getAttribute('alt');
                if (alt && alt !== "Avatar") {
                    if (alt.startsWith("Seen by ")) {
                        let match = alt.match(/Seen by (.*?) at/);
                        if (match) customer_name = match[1];
                    } else {
                        customer_name = alt;
                    }
                    if (customer_name !== "Unknown" && customer_name.trim() !== "") break;
                }
            }
            
            // 2. Fallback to any generic avatar in the message region
            if (customer_name === "Unknown") {
                let avatars = document.querySelectorAll('img.img[alt]');
                for (let img of avatars) {
                    let alt = img.getAttribute('alt');
                    if (alt && alt !== "Avatar" && !alt.includes("Page")) {
                        customer_name = alt;
                        break;
                    }
                }
            }
            
            // 3. Last resort fallback to h2 thread title
            if (customer_name === "Unknown") {
                let h2s = Array.from(document.querySelectorAll('h2')).map(el => el.innerText.trim()).filter(t => t.length > 0);
                if (h2s.length >= 3) {
                     customer_name = h2s[2]; // Typically ['Chats', 'Bộ lọc', 'Thu Hạnh Phạm']
                }
            }

            let region = document.querySelector(
                'div[aria-label*="Message list container"], ' +
                'div[role="region"][aria-label*="message"]'
            );
            if (!region) return JSON.stringify({ sender: "NO_REGION", text: "", html: "", customer_name: customer_name });
            
            let bubble = region.querySelector('.x1fqp7bg');
            let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
            let topDivs = Array.from(messageArea.children);
            for (let i = topDivs.length - 1; i >= 0; i--) {
                let div = topDivs[i];
                if (div.classList.contains('x14vqqas') || div.querySelector('.x14vqqas')) continue;
                if (div.classList.contains('xcxhlts') || div.querySelector('.xcxhlts')) continue;
                if (!div.classList.contains('x1fqp7bg') && !div.querySelector('.x1fqp7bg')) continue;
                
                let htmlStr = (div.outerHTML || "").substring(0, 500);
                let text = div.innerText.trim();
                
                // IGNORE SYSTEM MESSAGES like "Lead stage set to Qualified"
                // Real messages contain either x13a6bvl (Page) or x1nhvcw1 (Customer)
                if (!htmlStr.includes('x13a6bvl') && !htmlStr.includes('x1nhvcw1')) {
                    continue;
                }
                
                if (htmlStr.includes('x13a6bvl')) {
                    let is_auto = text.includes("Chúng tôi có thể giúp gì cho bạn?") || 
                                  text.includes("Bạn để lại Họ tên và Số điện thoại") ||
                                  text.includes("Khóa học thiền ở Hà Nội") ||
                                  text.includes("Thời gian: 20h-21h30");
                    return JSON.stringify({ sender: is_auto ? "Auto_Page" : "Page", text: text, html: "", customer_name: customer_name });
                }
                if (htmlStr.includes('x1nhvcw1')) {
                    return JSON.stringify({ sender: "Customer", text: text, html: "", customer_name: customer_name });
                }
            }
            return JSON.stringify({ sender: "Unknown", text: "", html: "", customer_name: customer_name });
        }''')
        
        import json
        result = json.loads(result_json)
        sender = result['sender']
        text = result['text']
        html = result.get('html', '')
        customer_name = result.get('customer_name', 'Unknown')
        
        logging.info(f"Customer Name Extracted: '{customer_name}'")
        logging.info(f"Latest DOM Sender evaluates to: '{sender}'")
        logging.info(f"Message evaluated: '{text}'")
        
        if sender in ["Customer", "Auto_Page"]:
            logging.info("--- RESULT: THREAD IS UNREPLIED --- (The human admin has not replied yet)")
        elif sender == "Page":
            logging.info("--- RESULT: THREAD IS REPLIED --- (A human admin already replied)")
        else:
            logging.info("--- RESULT: COULD NOT DETERMINE ---")

if __name__ == "__main__":
    test_live_detect()
