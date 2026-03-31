import os, sys, time, json
from playwright.sync_api import sync_playwright

def inspect():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            
            # Go directly to Hà Thanh's thread
            page.goto("https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326&selected_item_id=100025546875062")
            page.wait_for_timeout(5000)
            
            html = page.evaluate('''() => {
                let region = document.querySelector(
                    'div[aria-label*="Message list container"], ' +
                    'div[role="region"][aria-label*="message"]'
                );
                if (!region) return "Region not found";
                // Keep only structural classes and roles to minimize output
                function cleanNode(node) {
                    let clone = node.cloneNode(false); // shallow
                    // keep classes and role
                    return clone.outerHTML;
                }
                
                let structure = [];
                function traverse(node, depth) {
                    if(depth > 8) return;
                    if(node.nodeType === 1) { // element
                        let c = String(node.className || "");
                        if(c.includes('x1fqp7bg') || c.includes('x14vqqas') || depth < 4) {
                            structure.push("  ".repeat(depth) + cleanNode(node));
                        }
                        for(let child of node.children) {
                            traverse(child, depth+1);
                        }
                    }
                }
                traverse(region, 0);
                return structure.join("\\n");
            }''')
            
            with open("dom_structure.txt", "w") as f:
                f.write(html)
            print("Successfully dumped dom structure.")
        except Exception as e:
            print(f"Error: {e}")

inspect()
