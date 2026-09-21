import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse
from .sender_validator import detect_sender
from .constants import MESSAGE_REGION_SELECTOR


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}


def _normalise_day_context(value: str | None) -> str | None:
    """Return an ISO day only when the rendered separator is self-contained.

    A clock label such as ``9:00 AM`` must never inherit the crawl date.  The
    same is true of separators such as ``Sep 10`` which omit a year: retaining
    them in ``raw_timestamp`` is useful evidence, but converting them would be
    an unsupported inference.  This helper intentionally recognises only
    unambiguous, year-bearing date labels.
    """
    text = (value or "").strip().replace("\u202f", " ").replace("\u00a0", " ")
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    english_match = re.fullmatch(
        r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text, re.IGNORECASE
    )
    vietnamese_match = re.fullmatch(
        r"(?:ngày\s*)?(\d{1,2})\s*(?:tháng|thg)\s*(\d{1,2})(?:\s*(?:năm)?\s*(\d{4}))?",
        text,
        re.IGNORECASE,
    )
    try:
        if iso_match:
            year, month, day = (int(part) for part in iso_match.groups())
        elif english_match:
            month_name, day_text, year_text = english_match.groups()
            month = _MONTHS.get(month_name.lower())
            if month is None:
                return None
            year, day = int(year_text), int(day_text)
        elif vietnamese_match and vietnamese_match.group(3):
            day_text, month_text, year_text = vietnamese_match.groups()
            year, month, day = int(year_text), int(month_text), int(day_text)
        else:
            return None
        return date(year, month, day).isoformat()
    except ValueError:
        return None

def verify_thread_switch(page, logger, name: str, prev_fb_url: str, pre_click_fingerprint: str,
                         is_first_thread: bool, thread_record) -> tuple[str, bool]:
    """Reject unbound/mismatched navigation, including the first thread.

    URL identity and a matching rendered heading must coexist in one poll.
    A name, changed text, hovercard link or arbitrary sidebar link alone does
    not bind a conversation to a Page-scoped recipient ID (PSID).
    """
    target = str(getattr(thread_record, "selected_item_id", "") or "").strip()
    page_id = str(getattr(thread_record, "page_id", "") or "").strip()
    if not target.isdigit() or not page_id.isdigit():
        logger.warning("thread_switch_failed reason=missing_numeric_page_or_recipient_id")
        return "", False
    for _ in range(20):
        try:
            snapshot = page.evaluate('''() => ({
                url: location.href,
                headings: Array.from(document.querySelectorAll(
                    'div[role="main"] h2[dir="auto"], div[role="main"] h1, '
                    + 'div[role="main"] h3, div[role="main"] [role="heading"]'
                )).filter(el => el.getClientRects().length).map(el => el.innerText.trim())
            })''')
            parsed_url = urlparse(snapshot["url"])
            qs = parse_qs(parsed_url.query)
            identity_matches = (parsed_url.hostname == "business.facebook.com"
                                and qs.get("selected_item_id") == [target]
                                and qs.get("asset_id") == [page_id])
            expected_name = " ".join(name.casefold().split())
            heading_matches = expected_name and any(
                " ".join(str(value).casefold().split()) == expected_name
                for value in snapshot.get("headings", [])
            )
            if identity_matches and heading_matches:
                logger.info("thread_switch_verified method=exact_page_recipient_and_heading")
                return target, True
        except Exception:
            pass
        page.wait_for_timeout(500)
    logger.warning("thread_switch_failed reason=identity_or_rendered_heading_unverified")
    return "", False


def extract_ad_context(page) -> str:
    # Do not broaden this ancestor walk into the message-list container.  That
    # would capture unrelated historical chat as "ad" text; ad IDs are shared
    # and the transcript could then be sent to another seeker's LLM call.
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
        
        is_already_at_top = (current_st <= 0)
        
        # Retrospective [Apr 2026]: Optimization for short threads
        # Previously, reaching the top (or short threads with no scrollbar) would stall here for 3 full cycles
        # (1500ms * 3 = 4.5s) waiting to confirm stability. By explicitly detecting `current_st <= 0`,
        # we can safely reduce the confirmation overhead to just 1 cycle, killing the "3 scrolls for nothing" behavior.
        if current_count == prev_msg_count and current_sh == prev_scroll_height:
            stable_rounds += 1
            # If we are already at the physical top (0) and it's stable once, we're done. 
            # No need to arbitrarily wait 3 full cycles for no reason.
            if stable_rounds >= (1 if is_already_at_top else 3):
                logger.info(f"Message count stable at {current_count} (scrollHeight={current_sh}, top={is_already_at_top}) after {scroll_up_round} scroll rounds. All messages loaded.")
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


def extract_thread_messages(page, *, observed_at: str | None = None) -> list[dict]:
    """Extract raw Inbox events without collapsing evidence into ``text``.

    ``text`` remains the backwards-compatible message body for the current
    ingestion caller.  The accompanying fields deliberately retain what the
    DOM actually showed (day/time labels, reply context, and reactions).  A
    later persistence layer can store those fields without having to infer
    them again from an ambiguous display string.
    """
    # Reactions are observations made while crawling, not events that happened
    # at the timestamp displayed beside the target message.  Supplying this
    # outside the page script also makes replay fixtures deterministic.
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    raw_messages = page.evaluate(r'''(observedAt) => {
        let region = document.querySelector(
            'div[aria-label*="Message list container"], ' +
            'div[role="region"][aria-label*="message"]'
        );
        if (!region) return [];
        let results = [];
        let currentDayContext = "";
        let currentTimeLabel = "";
        let currentTimestampRaw = "";
        let elements = region.querySelectorAll('.x14vqqas, .x1fqp7bg');
        let processedBubbles = new Set();

        function isValidTimestamp(ts) {
            if (!ts || ts.length > 50) return false;
            let lower = ts.toLowerCase();
            if (/(\.com|\.me|\.vn|\.pdf|\.gl|mib|kib|mb|kb|audio call|cuộc gọi|facebook|zalo|hỏi|xem|chi tiết|chia sẻ|đăng nhập|trả lời|reply|like|love)/i.test(lower)) {
                return false;
            }
            let hasTime = /\b\d{1,2}:\d{2}(?:\s*[ap]m)?\b/i.test(lower);
            let hasMonth = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|tháng|thg)\b/i.test(lower);
            let hasSlashDate = /\b\d{1,2}[\/\.-]\d{1,2}(?:[\/\.-]\d{2,4})?\b/.test(lower);
            let hasRelativeDay = /\b(today|yesterday|hôm nay|hôm qua|now|vừa xong)\b/i.test(lower);
            let hasWeekday = /\b(mon|tue|wed|thu|fri|sat|sun|thứ\s*[2-7]|chủ nhật)\b/i.test(lower);
            return hasTime || hasMonth || hasSlashDate || hasRelativeDay || hasWeekday;
        }

        function timestampParts(ts) {
            let value = (ts || '').trim();
            let timeMatch = value.match(/\b\d{1,2}:\d{2}(?:\s*[ap]m)?\b/i);
            let hasTime = Boolean(timeMatch);
            let hasDate = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|tháng|thg)\b/i.test(value) ||
                /\b\d{1,2}[\/\.-]\d{1,2}(?:[\/\.-]\d{2,4})?\b/.test(value) ||
                /\b(today|yesterday|hôm nay|hôm qua)\b/i.test(value) ||
                /\b(mon|tue|wed|thu|fri|sat|sun|thứ\s*[2-7]|chủ nhật)\b/i.test(value);
            let day = hasDate ? value.replace(/\b\d{1,2}:\d{2}(?:\s*[ap]m)?\b/ig, '').replace(/[\s,]+$/g, '').trim() : '';
            return {hasDate, hasTime, day, time: timeMatch ? timeMatch[0].trim() : ''};
        }

        function timestampSnapshot() {
            let timestamp = currentDayContext && currentTimeLabel
                ? currentDayContext + ' ' + currentTimeLabel
                : (currentDayContext || currentTimeLabel || '');
            let precision = currentDayContext && currentTimeLabel ? 'date_time'
                : currentDayContext ? 'date_only'
                : currentTimeLabel ? 'time_only' : 'unknown';
            return {
                timestamp,
                raw_timestamp: currentTimestampRaw || timestamp,
                day_context: currentDayContext || null,
                time_precision: precision,
            };
        }

        function evidenceText(node, stopAt) {
            let values = [];
            let current = node;
            for (let i = 0; current && i < 7; i++, current = current.parentElement) {
                for (let attr of ['aria-label', 'data-testid', 'data-message-id', 'data-messageid', 'data-ft']) {
                    let value = current.getAttribute && current.getAttribute(attr);
                    if (value) values.push(attr + '=' + value);
                }
                if (current === stopAt) break;
            }
            return values.join(' | ');
        }

        function sourceId(node, stopAt) {
            let current = node;
            for (let i = 0; current && i < 7; i++, current = current.parentElement) {
                for (let attr of ['data-message-id', 'data-messageid', 'data-fbid', 'data-mid']) {
                    let value = current.getAttribute && current.getAttribute(attr);
                    if (value) return value;
                }
                let dataFt = current.getAttribute && current.getAttribute('data-ft');
                if (dataFt) {
                    try {
                        let parsed = JSON.parse(dataFt);
                        let id = parsed.message_id || parsed.mid || parsed.mf_story_key;
                        if (id) return String(id);
                    } catch (_) {}
                }
                if (current === stopAt) break;
            }
            return null;
        }

        function sourceIdForBody(node, bubble, bodySegments) {
            // A Facebook ID on a wrapper which contains several visible text
            // segments identifies the *cluster*, not any particular message.
            // It must not be copied to each sibling, otherwise source-id
            // upsert silently drops real turns.  Return an ID only when its
            // owning DOM node contains precisely this candidate body segment.
            let current = node;
            for (let i = 0; current && i < 7; i++, current = current.parentElement) {
                let id = sourceId(current, current);
                if (id) {
                    let owned = bodySegments.filter(segment => current.contains(segment.node));
                    if (owned.length === 1 && owned[0].node === node) return id;
                }
                if (current === bubble) break;
            }
            return null;
        }

        function isReplyOrQuote(node, stopAt) {
            // Deliberately do not inspect the common bubble wrapper.  A
            // generic "Reply" action, or "You replied", describes the
            // current message's relationship and is not evidence that its
            // own body is a quotation.  Only a quote-specific label marks a
            // subtree that must be excluded from the outgoing body.
            let current = node;
            for (let i = 0; current && current !== stopAt && i < 6; i++, current = current.parentElement) {
                let evidence = evidenceText(current, current).toLowerCase();
                if (/(?:quoted\s+reply|quoted\b|quote\b|replying\s+to|trích\s+dẫn)/i.test(evidence)) return true;
            }
            return false;
        }

        function reactionControl(img, bubble) {
            let current = img;
            for (let i = 0; current && i < 7; i++, current = current.parentElement) {
                let evidence = evidenceText(current, current).toLowerCase();
                if (/(?:reacted|reaction|thả cảm xúc|bày tỏ cảm xúc)/i.test(evidence)) {
                    return current;
                }
                if (current === bubble) break;
            }
            return null;
        }

        function reactionTargetId(control, bubbleTargetId) {
            // A reaction control can have its own data-message-id.  That is an
            // identity for the reaction evidence, never proof that it is the
            // message the reaction targets.  Only dedicated target attributes
            // may override the already-bounded target bubble ID.
            let current = control;
            for (let i = 0; current && i < 4; i++, current = current.parentElement) {
                for (let attr of ['data-target-message-id', 'data-reaction-target-id', 'data-target-id']) {
                    let value = current.getAttribute && current.getAttribute(attr);
                    if (value) return value;
                }
            }
            return bubbleTargetId || null;
        }

        function parseReaction(img, bubble, targetId) {
            let emoji = (img.getAttribute('alt') || '').trim();
            if (!emoji || !['❤', '❤️', '👍', '😆', '😂', '😮', '😢', '😡', 'Like', 'Love', 'Haha', 'Wow', 'Sad', 'Angry'].includes(emoji)) return null;
            let evidence = evidenceText(img, bubble);
            let label = evidence.match(/aria-label=([^|]+)/i);
            let observed = label ? label[1].trim() : '';
            let actor = 'unknown';
            let actorRole = 'unknown';
            let actorMatch = observed.match(/^(.+?)\s+(?:reacted|đã thả cảm xúc|đã bày tỏ cảm xúc)\b/i);
            if (actorMatch) {
                actor = actorMatch[1].trim();
                if (/^(you|bạn)$/i.test(actor)) actorRole = 'Page';
            }
            let control = reactionControl(img, bubble);
            let resolvedTarget = reactionTargetId(control, targetId);
            let targetType = /(?:conversation|thread|cuộc trò chuyện)/i.test(observed)
                ? 'thread' : (resolvedTarget ? 'message' : 'unknown');
            let targetMessageId = targetType === 'message' ? resolvedTarget : null;
            let parseConfidence = actor !== 'unknown' && targetType !== 'unknown' ? 'explicit'
                : (actor !== 'unknown' || targetType !== 'unknown' ? 'partial' : 'unknown');
            return {
                // Canonical persistence contract.
                // Do not reuse the target bubble's message id as a reaction
                // id.  It is absent unless Facebook actually attaches one to
                // the reaction control itself.
                source_id: control ? sourceId(img, control) : null,
                actor,
                actor_role: actorRole,
                emoji,
                target_type: targetType,
                target_message_id: targetMessageId,
                observed_at: observedAt || null,
                occurred_at: null,
                raw_label: observed || evidence || null,
                parse_confidence: parseConfidence,
                // Aliases retain compatibility for adapters that consumed
                // the preliminary audit vocabulary.
                target_id: targetMessageId,
                target_scope: targetType,
                evidence: observed || evidence || null,
            };
        }

        function explicitSender(node, stopAt, bodySegments = []) {
            // An actor label on a shared wrapper cannot identify every child
            // message. Stop before that boundary, just as sourceIdForBody does.
            let labels = [];
            for (let current = node; current; current = current.parentElement) {
                if (bodySegments.filter(segment => current.contains(segment.node)).length > 1) break;
                let label = current.getAttribute && current.getAttribute('aria-label');
                if (label) labels.push('aria-label=' + label);
                if (current === stopAt) break;
            }
            let evidence = labels.join(' | ');
            // Only a first-party self label is strong enough to assert Page.
            // Bubble alignment/colour remains available as a candidate in
            // Python, but must not be persisted as an actor fact.
            let pageMatch = /(?:^|[=\s|])(you sent|you replied|bạn đã gửi|bạn đã trả lời)\b/i.test(evidence);
            // Customer is asserted only by a platform sender label such as
            // "Lan sent a message".  This deliberately does not inspect the
            // message body, colour, alignment, or a guessed profile name.
            let customerMatch = evidence.match(
                /(?:^|[=\s|])((?!(?:you|bạn)\b)[^|=\n]{1,160}?)\s+(?:sent\s+(?:a\s+)?message|replied(?:\s+to)?|đã\s+gửi(?:\s+(?:một\s+)?tin\s+nhắn)?|đã\s+trả\s+lời)\b/i
            );
            if (pageMatch && customerMatch) return {sender: null, evidence};
            if (pageMatch) return {sender: 'Page', evidence};
            if (customerMatch) return {sender: 'Customer', evidence};
            return {sender: null, evidence: evidence || null};
        }

        function directBodyText(container, bubble) {
            // Parent text containers often include a nested quoted-reply
            // subtree in innerText.  Read only direct text that is not owned
            // by a nested text container or a quote/reply subtree.
            let values = [];
            let walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
            let textNode;
            while ((textNode = walker.nextNode())) {
                let value = (textNode.nodeValue || '').trim();
                if (!value) continue;
                let parent = textNode.parentElement;
                let nestedTextContainer = null;
                let quoted = false;
                for (let current = parent; current && current !== container; current = current.parentElement) {
                    if (current.classList && current.classList.contains('x1y1aw1k')) nestedTextContainer = current;
                    if (isReplyOrQuote(current, bubble)) { quoted = true; break; }
                }
                if (!quoted && !nestedTextContainer) values.push(value);
            }
            return values.join('\n').trim();
        }

        function replyTargetId(node, stopAt) {
            let current = node;
            for (let i = 0; current && i < 7; i++, current = current.parentElement) {
                let target = current.getAttribute && (
                    current.getAttribute('data-reply-to-message-id') ||
                    current.getAttribute('data-reply-to')
                );
                if (target) return target;
                if (current === stopAt) break;
            }
            return null;
        }

        function quotedSender(node, stopAt) {
            let current = node;
            for (let i = 0; current && i < 7; i++, current = current.parentElement) {
                let direct = current.getAttribute && (
                    current.getAttribute('data-quoted-sender') ||
                    current.getAttribute('data-sender-name')
                );
                if (direct) return {sender: direct.trim(), confidence: 'explicit'};
                let label = current.getAttribute && current.getAttribute('aria-label');
                if (label) {
                    let match = label.match(/(?:quoted reply from|replying to)\s+(.+)$/i);
                    if (match && match[1].trim()) return {sender: match[1].trim(), confidence: 'explicit'};
                }
                if (current === stopAt) break;
            }
            return {sender: 'unknown', confidence: 'unknown'};
        }

        for (let el of elements) {
            if (el.classList.contains('x14vqqas')) {
                let ts = el.innerText.trim();
                if (isValidTimestamp(ts)) {
                    let parts = timestampParts(ts);
                    if (parts.hasDate) {
                        currentDayContext = parts.day || ts;
                        // A new date separator starts a new group.  Do not
                        // combine it with a clock from the previous group.
                        if (!parts.hasTime) currentTimeLabel = "";
                    }
                    if (parts.hasTime) currentTimeLabel = parts.time;
                    currentTimestampRaw = parts.hasDate && !parts.hasTime
                        ? ts
                        : (currentDayContext && !parts.hasDate
                            ? currentDayContext + " | " + ts : ts);
                }
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

                let htmlContainer = el.closest ? (el.closest('.x1y1aw1k') || el.parentElement.parentElement || el) : el;
                let htmlStr = htmlContainer.outerHTML.substring(0, 800);
                
                // --- SENDER DETECTION FIX (Apr 2026) ---
                // Problem: Facebook rotates its DOM structure, which broke background detection and container boundary resolution.
                // When `role="row"` enveloped the whole chat, all messages shared `htmlStr` and sender detection failed.
                // Solution: Find the explicit colored bubble by checking children, mitigating the empty background bleed.
                let bgNode = el;
                let bg = 'rgba(0, 0, 0, 0)';
                let bgImg = 'none';
                
                let children = el.querySelectorAll('*');
                for (let child of children) {
                    let childBg = window.getComputedStyle(child).backgroundColor;
                    let childBgImg = window.getComputedStyle(child).backgroundImage;
                    if ((childBg && childBg !== 'rgba(0, 0, 0, 0)' && childBg !== 'transparent') || 
                        (childBgImg && childBgImg !== 'none')) {
                        bgNode = child;
                        bg = childBg;
                        bgImg = childBgImg;
                        break;
                    }
                }
                
                // Upward fallback if transparent
                if (bg === 'rgba(0, 0, 0, 0)' && bgImg === 'none') {
                    let tempNode = el.querySelector('div[dir="auto"]') || el.querySelector('.x1y1aw1k') || el;
                    bgNode = tempNode;
                    bg = window.getComputedStyle(bgNode).backgroundColor;
                    bgImg = window.getComputedStyle(bgNode).backgroundImage;
                    let maxDepth = 6;
                    let depth = 0;
                    while ((bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') && (bgImg === 'none' || !bgImg) && bgNode && bgNode !== document.body && depth < maxDepth) {
                        bgNode = bgNode.parentElement;
                        if (!bgNode) break;
                        bg = window.getComputedStyle(bgNode).backgroundColor;
                        bgImg = window.getComputedStyle(bgNode).backgroundImage;
                        depth++;
                    }
                }

                if (bgImg && bgImg !== 'none') {
                    htmlStr += " HAS_BG_IMAGE_INDICATOR_XX";
                }

                let textContainers = Array.from(el.querySelectorAll('.x1y1aw1k'));
                let bodySegments = [];
                let quoteSegments = [];
                if (textContainers.length > 0) {
                    for (let tc of textContainers) {
                        // A container that owns another text container has an
                        // ambiguous innerText.  Its leaf children are emitted
                        // independently and any direct body text is extracted
                        // below without the nested quote subtree.
                        let hasNestedTextContainer = textContainers.some(other => other !== tc && tc.contains(other));
                        if (!hasNestedTextContainer) {
                            let t = tc.innerText.trim();
                            if (t) {
                                if (isReplyOrQuote(tc, el)) quoteSegments.push({text: t, node: tc});
                                else bodySegments.push({text: t, node: tc});
                            }
                        } else {
                            let directText = directBodyText(tc, el);
                            if (directText) bodySegments.push({text: directText, node: tc});
                        }
                    }
                } else {
                    let spans = el.querySelectorAll('span > span');
                    let found = false;
                    if (spans.length > 0) {
                        for (let sp of spans) {
                            let t = sp.innerText.trim();
                            if (t) {
                                if (isReplyOrQuote(sp, el)) quoteSegments.push({text: t, node: sp});
                                else bodySegments.push({text: t, node: sp});
                                found = true;
                            }
                        }
                    }
                    if (!found) {
                        let text = el.innerText.trim();
                        if (text && text.length > 2 && text.length < 2000) {
                            bodySegments.push({text, node: el});
                        }
                    }
                }

                // --- REACTION EXTRACTION EXTENSION (Apr 2026) ---
                // Retrospective [Apr 2026]: Extracting Reactions dynamically inside nested wrappers
                // Facebook groups multiple messages together (and sometimes Zalo previews) inside `.x1y1aw1k`.
                // However, the reaction container itself (e.g. span.x1f6kntn) is NOT inside the innermost text container `.x1y1aw1k`,
                // but rests at the bottom of the `.x1fqp7bg` bubble cluster. 
                // Using `.querySelectorAll(':scope .x1y1aw1k img')` ensures we strictly isolate nested text emojis.
                // Any emoji `img` that is OUTSIDE the inner `.x1y1aw1k` bound is safely categorized as a reaction icon.
                let allImgs = el.querySelectorAll('img');
                let textImgs = Array.from(el.querySelectorAll(':scope .x1y1aw1k img'));
                let reactions = [];
                let bodySourceIds = bodySegments.map(segment => sourceIdForBody(segment.node, el, bodySegments));
                // The bubble ID is a target only when the DOM has exactly one
                // message candidate; otherwise its scope is a cluster.
                let bubbleSourceId = bodySegments.length === 1
                    ? bodySourceIds[0]
                    : null;
                for (let img of allImgs) {
                    if (!textImgs.includes(img)) {
                        let reaction = parseReaction(img, el, bubbleSourceId);
                        if (reaction) reactions.push(reaction);
                    }
                }

                let quoteText = quoteSegments.length ? quoteSegments.map(segment => segment.text).join('\n') : null;
                let replyTarget = null;
                for (let quote of quoteSegments) {
                    let target = replyTargetId(quote.node, el);
                    if (target) { replyTarget = target; break; }
                }
                let quoteEvidence = quoteSegments.length ? evidenceText(quoteSegments[0].node, el) : '';
                let quoteSenderInfo = quoteSegments.length
                    ? quotedSender(quoteSegments[0].node, el)
                    : {sender: 'unknown', confidence: 'unknown'};
                let metadata = timestampSnapshot();
                // Each text container is an individual event candidate.  Do
                // not concatenate it with a quote or a sibling bubble: doing
                // so makes an attribution claim the DOM did not establish.
                for (let segment of bodySegments) {
                    let senderInfo = explicitSender(segment.node, el, bodySegments);
                    let segmentIndex = bodySegments.indexOf(segment);
                    results.push({
                        htmlStr, bg, text: segment.text, body: segment.text,
                        sender: senderInfo.sender,
                        sender_confidence: senderInfo.sender ? 'explicit' : 'unknown',
                        sender_evidence: senderInfo.evidence,
                        source_id: bodySourceIds[segmentIndex],
                        reply_to_message_id: replyTarget,
                        quoted_sender: quoteSenderInfo.sender,
                        quoted_sender_confidence: quoteSenderInfo.confidence,
                        quoted_text: quoteText,
                        quote_evidence: quoteEvidence || null,
                        reactions,
                        ...metadata,
                    });
                }
                // A reaction can be observed without a text bubble.  Keep a
                // structured event rather than manufacturing an emoji text.
                if (!bodySegments.length && reactions.length) {
                    let senderInfo = explicitSender(el, htmlContainer);
                    results.push({
                        htmlStr, bg, text: '', body: '', source_id: bubbleSourceId,
                        sender: senderInfo.sender,
                        sender_confidence: senderInfo.sender ? 'explicit' : 'unknown',
                        sender_evidence: senderInfo.evidence,
                        reply_to_message_id: null,
                        quoted_sender: quoteSenderInfo.sender,
                        quoted_sender_confidence: quoteSenderInfo.confidence,
                        quoted_text: quoteText, quote_evidence: quoteEvidence || null,
                        reactions, ...metadata,
                    });
                }
            }
        }
        return results;
    }''', observed_at)

    final_messages = []
    for raw in raw_messages:
        text = (raw.get("body") if raw.get("body") is not None else raw.get("text") or "").replace('\u200b', '').strip()
        reactions = raw.get("reactions") or []
        if not text and not reactions:
            continue
            
        low_text = text.lower()
        # Filter out Facebook system boundary messages that lack proper bubble styling
        if "assigned this conversation" in low_text or "đã giao cuộc trò chuyện" in low_text or "đã chỉ định cuộc trò chuyện" in low_text:
            continue
        if "resolved this conversation" in low_text or "đã giải quyết cuộc trò chuyện" in low_text:
            continue
        if "you can now call each other" in low_text or "giờ đây, các bạn có thể gọi" in low_text:
            continue
        if "lead stage set to" in low_text or "trạng thái khách hàng được đặt" in low_text: # Lead stage notifications
            continue
        if low_text.strip() == "learn more" or low_text.strip() == "tìm hiểu thêm": # Frequently embedded ad CTA button text
            continue
        if low_text.strip() in ("close", "đóng", "previous", "next", "trước", "tiếp", "improve ai response"): # System/UI buttons
            continue
        if "previous\n[quoted reply/link]: close\n[quoted reply/link]: next" in low_text:
            continue

        raw_sender_confidence = raw.get("sender_confidence")
        explicit_sender = raw.get("sender") if raw_sender_confidence == "explicit" else None
        # Sender colour/alignment is not evidence of identity.  Preserve it
        # only as a diagnostic candidate; downstream must treat the actor as
        # unknown unless Facebook exposed an explicit self/page signal.
        sender_candidate = detect_sender(raw.get("htmlStr", ""), raw.get("bg", ""))
        sender = explicit_sender or "Unknown"
        sender_confidence = "explicit" if explicit_sender else "unknown"
        raw_day_context = raw.get("day_context")
        day_context = _normalise_day_context(raw_day_context)
        time_precision = raw.get("time_precision", "unknown")
        # A non-ISO day label is evidence, not a resolved calendar day.  Keep
        # the label in raw_timestamp/timestamp, but make the uncertainty
        # machine-readable for persistence and downstream formatters.
        if raw_day_context and not day_context and time_precision == "date_time":
            time_precision = "unresolved_day_time"
        elif raw_day_context and not day_context and time_precision == "date_only":
            time_precision = "unresolved_day"
        print(f"DEBUG_COLOR_VAL text='{low_text[:20]}' bg='{raw.get('bg', '')}' sender='{sender}' confidence='{sender_confidence}' candidate='{sender_candidate}'", flush=True)
        final_messages.append({
            "sender": sender,
            "text": text,
            "body": text,
            "sender_confidence": sender_confidence,
            "sender_candidate": sender_candidate,
            "sender_evidence": raw.get("sender_evidence"),
            "source_id": raw.get("source_id"),
            "timestamp": raw.get("timestamp", ""),
            "raw_timestamp": raw.get("raw_timestamp") or raw.get("timestamp", ""),
            "day_context": day_context,
            "time_precision": time_precision,
            "reply_to_message_id": raw.get("reply_to_message_id"),
            "quoted_sender": raw.get("quoted_sender") or "unknown",
            "quoted_sender_confidence": raw.get("quoted_sender_confidence") or (
                "explicit" if raw.get("quoted_sender") else "unknown"
            ),
            "quoted_text": raw.get("quoted_text"),
            "quote_evidence": raw.get("quote_evidence"),
            "reactions": reactions,
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


def is_valid_timestamp_text(ts: str | None) -> bool:
    """Validate if a scraped string is a genuine Facebook message timestamp."""
    if not ts or len(ts.strip()) > 50:
        return False
    lower = ts.strip().lower()
    if re.search(r'(\.com|\.me|\.vn|\.pdf|\.gl|mib|kib|mb|kb|audio call|cuộc gọi|facebook|zalo|hỏi|xem|chi tiết|chia sẻ|đăng nhập|trả lời|reply|like|love)', lower):
        return False
    has_time = bool(re.search(r'\b\d{1,2}:\d{2}(?:\s*[ap]m)?\b', lower))
    has_month = bool(re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|tháng|thg)\b', lower))
    has_slash = bool(re.search(r'\b\d{1,2}[\/\.-]\d{1,2}(?:[\/\.-]\d{2,4})?\b', lower))
    has_rel = bool(re.search(r'\b(today|yesterday|hôm nay|hôm qua|now|vừa xong)\b', lower))
    has_day = bool(re.search(r'\b(mon|tue|wed|thu|fri|sat|sun|thứ\s*[2-7]|chủ nhật)\b', lower))
    return has_time or has_month or has_slash or has_rel or has_day


__all__ = [
    "extract_ad_context",
    "extract_ad_id_labels",
    "extract_messages",
    "is_valid_timestamp_text",
    "scroll_up_message_panel",
    "verify_thread_switch",
]
