#!/usr/bin/env python3
# code:tool-test-crawler-001:pre-run-test
import os
import sys
import json
import argparse
from playwright.sync_api import sync_playwright

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_PATH = os.path.join(PROJECT_ROOT, "tests", "fixtures", "inbox_thread_testcase_100052384037366.json")
TARGET_URL = "https://business.facebook.com/latest/inbox/all?asset_id=1548373332058326&mailbox_id=1548373332058326&selected_item_id=100052384037366&thread_type=FB_MESSAGE"

def parse_args():
    parser = argparse.ArgumentParser(description="Pre-run Inbox Crawler Test")
    parser.add_argument("--update", action="store_true", help="Update the golden testcase file with current correctly extracted messages.")
    parser.add_argument("--cdp-url", type=str, default="http://localhost:9222", help="CDP URL for Playwright connection")
    return parser.parse_args()

def extract_messages(page):
    print("Scrolling up in message panel to load all messages...")
    prev_msg_count = 0
    prev_scroll_height = 0
    stable_rounds = 0
    max_scroll_up_rounds = 50
    
    for scroll_up_round in range(1, max_scroll_up_rounds + 1):
        scroll_info = page.evaluate('''() => {
            let region = document.querySelector(
                'div[aria-label*="Message list container"], ' +
                'div[role="region"][aria-label*="message"]'
            );
            if (!region) return {count: 0, scrollHeight: 0, scrollTop: 0};
            let bubble = region.querySelector('.x1fqp7bg');
            let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
            let count = 0;
            if (messageArea) {
                for (let div of messageArea.children) {
                    count++;
                }
            }
            let scrollable = region;
            let el = region;
            while (el) {
                if (el.scrollHeight > el.clientHeight && el.clientHeight > 100) {
                    scrollable = el;
                    break;
                }
                el = el.parentElement;
            }
            return {
                count: count,
                scrollHeight: scrollable.scrollHeight,
                scrollTop: scrollable.scrollTop,
                scrollableTag: scrollable.tagName
            };
        }''')
        current_count = scroll_info.get("count", 0) if isinstance(scroll_info, dict) else 0
        current_sh = scroll_info.get("scrollHeight", 0) if isinstance(scroll_info, dict) else 0
        current_st = scroll_info.get("scrollTop", 0) if isinstance(scroll_info, dict) else 0
        
        if current_count == prev_msg_count and current_sh == prev_scroll_height:
            stable_rounds += 1
            if stable_rounds >= 3:
                print(f"Message count stable at {current_count} (scrollHeight={current_sh}) after {scroll_up_round} scroll rounds. All messages loaded.")
                break
        else:
            if current_count != prev_msg_count or current_sh != prev_scroll_height:
                print(f"Scroll-up round {scroll_up_round}: count {prev_msg_count}→{current_count}, scrollHeight {prev_scroll_height}→{current_sh}, scrollTop={current_st}.")
            stable_rounds = 0
            prev_msg_count = current_count
            prev_scroll_height = current_sh

        try:
            page.evaluate('''() => {
                let region = document.querySelector(
                    'div[aria-label*="Message list container"], ' +
                    'div[role="region"][aria-label*="message"]'
                );
                if (!region) return;
                let scrollable = region;
                let el = region;
                while (el) {
                    if (el.scrollHeight > el.clientHeight && el.clientHeight > 100) {
                        scrollable = el;
                        break;
                    }
                    el = el.parentElement;
                }
                let newTop = Math.max(0, scrollable.scrollTop - 800);
                scrollable.scrollTop = newTop;
                scrollable.dispatchEvent(new Event('scroll', {bubbles: true}));
            }''')
            page.mouse.wheel(0, -2000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        
    print(f"Scroll-up complete. Final element count: {prev_msg_count}.")
    
    js_messages = page.evaluate('''() => {
        let region = document.querySelector(
            'div[aria-label*="Message list container"], ' +
            'div[role="region"][aria-label*="message"]'
        );
        if (!region) return [];
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
    
    return js_messages

def run_test(args):
    print("Starting Playwright Debugger for Pre-Run Test...")
    with sync_playwright() as p:
        try:
            print(f"Connecting to CDP at {args.cdp_url}...")
            browser = p.chromium.connect_over_cdp(args.cdp_url)
            contexts = browser.contexts
            if not contexts:
                print("Error: No browser contexts found.")
                sys.exit(1)
            
            page = contexts[0].pages[0]
            
            print(f"Navigating to {TARGET_URL}...")
            page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
            
            print("Giving page a few seconds to stabilize...")
            page.wait_for_timeout(5000)
            
            extracted_messages = extract_messages(page)
            print(f"Extracted {len(extracted_messages)} messages.")
            
            if not extracted_messages:
                print("Error: Extracted 0 messages. Ensure the URL is valid, the DOM hasn't changed drastically, or the page is fully loaded.")
                # Save page content for debug
                with open("debug_pre_run_fail_dom.html", "w") as f:
                    f.write(page.content())
                print("Saved debug DOM to debug_pre_run_fail_dom.html")
                sys.exit(1)
            
            if args.update:
                os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
                with open(FIXTURE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(extracted_messages, f, ensure_ascii=False, indent=4)
                print(f"SUCCESS: Golden file updated at {FIXTURE_PATH}")
                sys.exit(0)
            
            if not os.path.exists(FIXTURE_PATH):
                print(f"Error: Golden file not found at {FIXTURE_PATH}. Run with --update first.")
                sys.exit(1)
                
            with open(FIXTURE_PATH, 'r', encoding='utf-8') as f:
                golden_messages = json.load(f)
                
            print(f"Loaded {len(golden_messages)} messages from Golden fixture.")
            
            golden_texts = [f"{m['sender']}:{m['text']}" for m in golden_messages]
            extracted_texts = [f"{m['sender']}:{m['text']}" for m in extracted_messages]
            
            missing_messages = []
            for m in golden_texts:
                if m not in extracted_texts:
                    missing_messages.append(m)
                    
            if missing_messages:
                print("!!! ERROR: The extraction logic failed to find the following messages from the Golden fixture !!!")
                for missing in missing_messages:
                    print(f"  - Missing: {missing}")
                print("\nPossible causes: DOM elements have changed (e.g. .x1fqp7bg, .x1y1aw1k).")
                sys.exit(1)
            
            print("SUCCESS: Extraction logic passed. All Golden messages successfully extracted.")
                    
        except Exception as e:
            print(f"Fatal Error: {e}")
            sys.exit(1)

if __name__ == "__main__":
    args = parse_args()
    run_test(args)
