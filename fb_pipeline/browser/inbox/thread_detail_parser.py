import re
from urllib.parse import parse_qs, urlparse
from .sender_validator import detect_sender
from .constants import MESSAGE_REGION_SELECTOR

def verify_thread_switch(page, logger, name: str, prev_fb_url: str, pre_click_fingerprint: str,
                         is_first_thread: bool, thread_record) -> tuple[str, bool]:
    fb_url = ""
    url_changed = False
    name_matched = False
    target_item_id = getattr(thread_record, "selected_item_id", "")

    for _poll in range(20):
        try:
            current_qs = parse_qs(urlparse(page.url).query)
            candidate = current_qs.get('selected_item_id', [''])[0]
            
            header_text = page.evaluate('''() => {
                let main = document.querySelector('div[role="main"]');
                if (!main) main = document.body;
                let h2s = Array.from(main.querySelectorAll('h2[dir="auto"], h1, h3, div[role="heading"]'));
                for(let h of h2s) {
                    if (h.innerText && h.innerText.length > 0) return h.innerText.trim();
                }
                let main_text = main.innerText || "";
                return main_text.substring(0, 500);
            }''')

            clean_name = name.lower().strip()
            clean_header = header_text.lower().strip()
            if clean_name and clean_name in clean_header:
                name_matched = True
            elif clean_header and clean_header in clean_name:
                name_matched = True

            if candidate and target_item_id and candidate == target_item_id:
                fb_url = candidate
                url_changed = True
            elif candidate and candidate != prev_fb_url:
                fb_url = candidate
                url_changed = True

            if url_changed and name_matched:
                logger.info(f"thread_switch_verified method=selected_item_id_and_name_match thread='{name}'")
                return fb_url, True

        except Exception:
            pass
        page.wait_for_timeout(500)

    if not url_changed:
        try:
            current_qs = parse_qs(urlparse(page.url).query)
            fb_url = current_qs.get('selected_item_id', [''])[0]
        except Exception:
            fb_url = ""

    panel_refreshed = False
    
    def normalize_name(s):
        return re.sub(r'[^\w]', '', s).lower()
    
    for _poll in range(20):
        header_text = page.evaluate('''() => {
            let main = document.querySelector('div[role="main"]');
            if (!main) main = document.body;
            let h2s = Array.from(main.querySelectorAll('h2[dir="auto"], h1, h3, div[role="heading"], span[dir="auto"]'));
            for(let h of h2s) {
                if (h.innerText && h.innerText.length > 0) return h.innerText.trim();
            }
            return (main.innerText || "").substring(0, 500);
        }''')
        clean_name = normalize_name(name)
        clean_header = normalize_name(header_text)
        if clean_name and (clean_name in clean_header or clean_header in clean_name):
            name_matched = True
        else:
            if _poll == 19:
                logger.error(f"[DEBUG] verify_thread_switch FAIL. raw_name='{name}' raw_header='{header_text}' clean_name='{clean_name}' clean_header='{clean_header}'")

        post_click_fingerprint = page.evaluate('''() => {
            let r = document.querySelector(
                'div[aria-label*="Message list container"], ' +
                'div[role="region"][aria-label*="message"]'
            );
            if (!r) return "";
            return (r.innerText || "").substring(0, 200);
        }''')
        if post_click_fingerprint != pre_click_fingerprint:
            panel_refreshed = True
            logger.info(f"thread_switch_verified method=panel_fingerprint thread='{name}'")
            return fb_url, True
        page.wait_for_timeout(500)

    if not panel_refreshed and is_first_thread:
        logger.info(f"thread_switch_verified method=first_thread_already_loaded thread='{name}'")
        return fb_url, True

    if not panel_refreshed and not url_changed and not name_matched:
        logger.warning(f"thread_switch_failed thread='{name}' reason=no_url_change_no_panel_refresh_no_name_match")
        return "", False
        
    if name_matched:
        logger.info(f"thread_switch_verified method=name_matched_fallback thread='{name}'")
        return fb_url, True

    logger.warning(f"thread_switch_failed thread='{name}' reason=name_not_matched_in_center_panel")
    return "", False


def extract_ad_context(page) -> str:
    return page.evaluate('''() => {
        let links = Array.from(document.querySelectorAll('a, div[role="button"]'));
        let target = links.find(a =>
            a.innerText && (
            a.innerText.includes("Xem bài viết") ||
            a.innerText.includes("View ad") ||
            a.innerText.includes("replied to an ad") ||
            a.innerText.includes("reply to your ad")
            )
        );
        if (!target) return "";
        let container = target;
        for(let i=0; i<4; i++) {
             if(container.parentElement) container = container.parentElement;
        }
        return container.innerText.trim();
    }''')


def scroll_up_message_panel(page, logger, name: str) -> int:
    try:
        page.mouse.move(900, 400)
    except Exception:
        pass

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
            for (let div of messageArea.children) {
                count++;
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
                logger.info(f"Message count stable at {current_count} (scrollHeight={current_sh}) after {scroll_up_round} scroll rounds. All messages loaded.")
                break
        else:
            if current_count != prev_msg_count or current_sh != prev_scroll_height:
                logger.info(f"Scroll-up round {scroll_up_round}: count {prev_msg_count}→{current_count}, scrollHeight {prev_scroll_height}→{current_sh}, scrollTop={current_st}.")
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
        except Exception:
            pass
        page.wait_for_timeout(1500)
    
    logger.info(f"Scroll-up complete for '{name}'. Final element count: {prev_msg_count}.")
    return prev_msg_count


def extract_thread_messages(page) -> list[dict]:
    """Extract raw messages from DOM and process sender validation via module"""
    raw_messages = page.evaluate('''() => {
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

                let outerWrapper = el.parentElement || el;
                let htmlStr = outerWrapper.outerHTML.substring(0, 500);
                
                let targetNode = el.querySelector('div[dir="auto"]') || el.querySelector('.x1y1aw1k') || el;
                let bgNode = targetNode;
                let bg = window.getComputedStyle(bgNode).backgroundColor;
                while ((bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') && bgNode && bgNode !== document.body) {
                    bgNode = bgNode.parentElement;
                    bg = window.getComputedStyle(bgNode).backgroundColor;
                }

                let textContainers = el.querySelectorAll('.x1y1aw1k');
                let texts = [];
                let seenSegment = new Set();
                if (textContainers.length > 0) {
                    for (let tc of textContainers) {
                        let t = tc.innerText.trim();
                        if (t && !seenSegment.has(t)) {
                            texts.push(t);
                            seenSegment.add(t);
                        }
                    }
                } else {
                    let spans = el.querySelectorAll('span > span');
                    let found = false;
                    if (spans.length > 0) {
                        for (let sp of spans) {
                            let t = sp.innerText.trim();
                            if (t && !seenSegment.has(t)) { texts.push(t); found = true; seenSegment.add(t); }
                        }
                    }
                    if (!found) {
                        let text = el.innerText.trim();
                        if (text && text.length > 2 && text.length < 2000 && !seenSegment.has(text)) {
                            texts.push(text);
                            seenSegment.add(text);
                        }
                    }
                }

                for(let segment of texts) {
                    results.push({htmlStr, bg, text: segment, timestamp: currentTimestamp});
                }
            }
        }
        return results;
    }''')

    final_messages = []
    for raw in raw_messages:
        text = (raw.get("text") or "").strip()
        if not text:
            continue
            
        low_text = text.lower()
        # Filter out Facebook system boundary messages that lack proper bubble styling
        if "assigned this conversation" in low_text or "đã giao cuộc trò chuyện" in low_text or "đã chỉ định cuộc trò chuyện" in low_text:
            continue
        if "resolved this conversation" in low_text or "đã giải quyết cuộc trò chuyện" in low_text:
            continue
        if "you can now call each other" in low_text or "giờ đây, các bạn có thể gọi" in low_text:
            continue
            
        sender = detect_sender(raw["htmlStr"], raw["bg"])
        final_messages.append({
            "sender": sender,
            "text": text,
            "timestamp": raw["timestamp"]
        })

    return final_messages


def extract_ad_id_labels(page) -> list:
    labels_text = page.evaluate('''() => {
        let sidebar = null;

        let headings = document.querySelectorAll('span, h3, h4, div');
        for (let h of headings) {
            let t = (h.innerText || "").trim();
            if (t === "Labels" || t === "Nhãn" || t === "Label") {
                sidebar = h.closest('div[class*="x1n2onr6"]') || h.parentElement?.parentElement;
                break;
            }
        }

        if (sidebar) {
            let text = (sidebar.innerText || "").trim();
            if (text.includes("ad_id")) return text;
        }

        let detailPanels = document.querySelectorAll(
            'div[aria-label*="detail"], div[aria-label*="contact"], ' +
            'div[role="complementary"], aside'
        );
        let allText = "";
        for (let panel of detailPanels) {
            let t = (panel.innerText || "").trim();
            if (t.includes("ad_id")) {
                allText += " " + t;
            }
        }
        if (allText) return allText.trim();

        let labels = document.querySelectorAll('[role="listitem"]');
        let labelText = "";
        for (let label of labels) {
            let t = (label.innerText || label.textContent || "").trim();
            if (t.includes("ad_id")) {
                labelText += " " + t;
            }
        }
        return labelText.trim();
    }''')
    raw = re.findall(r'ad_id\.?(\d{5,})', labels_text)
    return list(dict.fromkeys(raw))
