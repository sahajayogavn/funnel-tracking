from datetime import datetime, timedelta
import re

from .constants import (
    MONTH_DAY_RE,
    DAY_NAMES,
    TIME_TODAY,
    TIME_YESTERDAY,
    thread_card_selector,
)

def extract_visible_threads(page) -> list[dict]:
    selector = thread_card_selector()
    return page.evaluate(
        r'''(config) => {
            const threadSelector = config.threadSelector;
            const candidates = Array.from(document.querySelectorAll(threadSelector));

            let scroller = null;
            if (candidates.length > 0) {
                let parent = candidates[0].closest('div');
                while(parent && parent.tagName !== 'BODY') {
                    if (parent.scrollHeight > parent.clientHeight) {
                        scroller = parent;
                        break;
                    }
                    parent = parent.parentElement;
                }
            }

            function pickTimeToken(lines) {
                for (let i = lines.length - 1; i >= 1; i--) {
                    const token = (lines[i] || '').trim();
                    if (!token) continue;
                    if (/^\d+[smhdw]$/i.test(token)) return token;
                    if (/^(today|yesterday|hôm nay|hôm qua)$/i.test(token)) return token;
                    if (/^(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)$/i.test(token)) return token;
                    if (/^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|th(?:g|áng)?)\s*\d{1,2}(?:(?:,\s*|\s+)\d{4})?$/i.test(token)) return token;
                    if (/^\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|th(?:g|áng)?)(?:(?:,\s*|\s+)\d{4})?$/i.test(token)) return token;
                    if (/^\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?$/.test(token)) return token;
                }
                
                // Fallback: if no regex strictly matches but the last line is very short (like a date), pick it
                if (lines.length > 1) {
                    const lastToken = (lines[lines.length - 1] || '').trim();
                    if (lastToken.length >= 3 && lastToken.length <= 15) return lastToken;
                }
                return '';
            }

            return candidates.map((el, idx) => {
                let absoluteTop = 0;
                if (scroller) {
                    const elRect = el.getBoundingClientRect();
                    const scRect = scroller.getBoundingClientRect();
                    absoluteTop = elRect.top - scRect.top + scroller.scrollTop;
                }
                
                const text = (el.innerText || '').trim();
                const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                const name = lines[0] || '';
                const sidebarTimeText = pickTimeToken(lines);
                const previewLines = lines.slice(1).filter(line => line !== sidebarTimeText);
                const hrefEl = el.closest('a[href]') || el.querySelector('a[href]');
                const href = hrefEl ? (hrefEl.getAttribute('href') || '') : '';
                
                let hovercard = el.getAttribute('data-hovercard') || '';
                if (!hovercard) {
                    const hcEl = el.querySelector('[data-hovercard]');
                    if (hcEl) hovercard = hcEl.getAttribute('data-hovercard') || '';
                }
                
                let fbUrl = '';
                if (hovercard) {
                    fbUrl = hovercard.split('?')[0];
                }

                let selectedItemId = '';
                try {
                    if (href) {
                        const absolute = new URL(href, window.location.origin);
                        selectedItemId = absolute.searchParams.get('selected_item_id') || '';
                    }
                } catch (_) {}

                const attrs = [];
                for (const attr of Array.from(el.attributes || [])) {
                    if (!attr || !attr.name) continue;
                    if (attr.name.startsWith('data-') || attr.name.startsWith('aria-') || attr.name === 'href') {
                        attrs.push(`${attr.name}=${attr.value || ''}`);
                    }
                }
                const identityParts = [name, previewLines.join(' | '), sidebarTimeText, selectedItemId, href, fbUrl, attrs.join('|')].filter(Boolean);
                const sidebarIdentityKey = identityParts.join(' || ');
                return {
                    domIndex: idx,
                    name,
                    text,
                    lines,
                    previewText: previewLines.join(' ').trim(),
                    sidebarTimeText,
                    sidebarIdentityKey,
                    selectedItemId,
                    href,
                    fbUrl,
                    absoluteTop,
                };
            }).filter(item => item.name || item.text);
        }''',
        {"threadSelector": selector},
    )

def parse_sidebar_time_token(token: str, now: datetime | None = None) -> dict:
    # Retrospective [2026-04-06]: Exact Time Regex Calculation
    # Fix: Ported robust JS datetime evaluation regexes (`4:50 PM`, `Sun 10:12 PM`) directly into the Python scraping pipeline.
    # Root Cause: Previously, standard English/Slash regexes routinely failed against Vietnamese relative strings, causing the parser to return 'unknown'. This forced `l3_pipeline.py` to unilaterally execute `datetime('now')`. Since the scraper processed the oldest threads at the bottom of the DOM last, the oldest threads were assigned the absolutely newest `datetime('now')` execution timestamps, physically inverting the SQL chronological order relative to Facebook.
    token = (token or "").strip()
    if not token:
        return {"kind": "unknown", "token": "", "days_ago": None, "parsed_at": None}

    now = now or datetime.now()
    lower = token.lower()
    
    # 10 mins, 2 hrs, etc (Facebook relative)
    import re
    rel_match = re.match(r'^(\d+)\s*(m|h|d|w)(?:ins?)?(?:rs?)?(?:s)?(?: ago)?$', lower)
    if not rel_match:
        rel_match = re.match(r'^(\d+)\s*(phút|giờ|ngày|tuần).*$', lower)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2)
        delta = timedelta()
        if unit.startswith('m') or unit == 'phút': delta = timedelta(minutes=val)
        elif unit.startswith('h') or unit == 'giờ': delta = timedelta(hours=val)
        elif unit.startswith('d') or unit == 'ngày': delta = timedelta(days=val)
        elif unit.startswith('w') or unit == 'tuần': delta = timedelta(weeks=val)
        parsed = now - delta
        return {"kind": "relative", "token": token, "days_ago": parsed.days, "parsed_at": parsed.isoformat(sep=" ", timespec="seconds")}

    # Time only: "4:32 pm" (Today)
    time_match = re.match(r'^(\d{1,2}):(\d{2})\s*([ap]m)$', lower)
    if time_match:
        hr = int(time_match.group(1))
        mn = int(time_match.group(2))
        ampm = time_match.group(3)
        if ampm == 'pm' and hr < 12: hr += 12
        if ampm == 'am' and hr == 12: hr = 0
        parsed = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if parsed > now + timedelta(minutes=5): parsed -= timedelta(days=1)
        return {"kind": "time_today", "token": token, "days_ago": 0, "parsed_at": parsed.isoformat(sep=" ", timespec="seconds")}

    # Day + Time: "sun 10:12 pm" (Within last 7 days)
    day_time_match = re.match(r'^(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+(\d{1,2}):(\d{2})\s*([ap]m)$', lower)
    if day_time_match:
        day_str = day_time_match.group(1)
        hr = int(day_time_match.group(2))
        mn = int(day_time_match.group(3))
        ampm = day_time_match.group(4)
        if ampm == 'pm' and hr < 12: hr += 12
        if ampm == 'am' and hr == 12: hr = 0
        days_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
        target_weekday = days_map[day_str]
        current_weekday = now.weekday()
        days_diff = current_weekday - target_weekday
        if days_diff <= 0: days_diff += 7
        parsed = now.replace(hour=hr, minute=mn, second=0, microsecond=0) - timedelta(days=days_diff)
        return {"kind": "day_time", "token": token, "days_ago": days_diff, "parsed_at": parsed.isoformat(sep=" ", timespec="seconds")}

    # Existing fallbacks
    from fb_pipeline.browser.inbox.constants import DAY_NAMES
    if lower in TIME_TODAY:
        return {"kind": "today", "token": token, "days_ago": 0, "parsed_at": now.date().isoformat()}
    if lower in TIME_YESTERDAY:
        return {"kind": "yesterday", "token": token, "days_ago": 1, "parsed_at": (now.date() - timedelta(days=1)).isoformat()}
    if lower in DAY_NAMES:
        return {"kind": "weekday", "token": token, "days_ago": 6, "parsed_at": None}
    from fb_pipeline.browser.inbox.constants import SLASH_DATE_RE, MONTH_DAY_RE, MONTH_DAY_REV_RE
    
    if SLASH_DATE_RE.match(lower):
        m = SLASH_DATE_RE.match(lower)
        d, mo_num, y = m.groups()
        y = y if y else str(now.year)
        if len(y) == 2: y = "20" + y
        try:
            parsed = datetime(int(y), int(mo_num), int(d))
            days_ago = (now - parsed).days
            return {"kind": "slash_day", "token": token, "days_ago": days_ago, "parsed_at": parsed.date().isoformat()}
        except Exception: pass

    token_clean = lower.replace(",", "")
    def viet_month_repl(m):
        mo_dict = {'1':'jan', '2':'feb', '3':'mar', '4':'apr', '5':'may', '6':'jun', '7':'jul', '8':'aug', '9':'sep', '10':'oct', '11':'nov', '12':'dec'}
        return mo_dict.get(m.group(1), 'jan')
    token_clean = re.sub(r'th(?:g|áng)?\s*(\d{1,2})', viet_month_repl, token_clean, flags=re.I)

    if MONTH_DAY_RE.match(token_clean) or MONTH_DAY_REV_RE.match(token_clean):
        has_year = bool(re.search(r'\d{4}', token_clean))
        parts = token_clean.split()
        if parts[0].isdigit() and len(parts) >= 2:
            parts[0], parts[1] = parts[1], parts[0]
            token_clean = " ".join(parts)
        try:
            parsed = datetime.strptime(token_clean if has_year else f"{token_clean} {now.year}", "%b %d %Y")
            if not has_year and (now - parsed).days < 0:
                parsed = datetime.strptime(f"{token_clean} {now.year - 1}", "%b %d %Y")
            days_ago = (now - parsed).days
        except Exception as e:
            return {"kind": "unknown", "token": token, "days_ago": None, "parsed_at": None, "error": str(e)}
        return {"kind": "month_day", "token": token, "days_ago": days_ago, "parsed_at": parsed.date().isoformat()}
        
    return {"kind": "unknown", "token": token, "days_ago": None, "parsed_at": None}

def is_thread_older_than_range(parsed_time: dict, max_days: int) -> bool:
    days_ago = parsed_time.get("days_ago")
    if days_ago is None:
        return False
    return days_ago > max_days

def validate_quick_fetch_cache(visible_threads: list, conn, logger, page_id: str) -> bool:
    """Validate if the top visible threads exactly match the database to trigger a dynamic cache hit."""
    if not visible_threads:
        logger.info("Quick Cache: No visible threads found.")
        return False
        
    cursor = conn.cursor()
    threads_to_check = visible_threads[:3]
    from fb_pipeline.inbox.l3_pipeline import build_thread_record
    
    hit_logs = []
    
    for vt in threads_to_check:
        thread_record = build_thread_record(page_id, vt)
        cursor.execute("SELECT last_synced_time FROM threads WHERE id = ?", (thread_record.thread_id,))
        row = cursor.fetchone()
        
        if not row:
            logger.info(f"Quick Cache Miss: Thread {thread_record.thread_id} not found in DB.")
            return False
            
        def _normalize(s):
            return ''.join(c.lower() for c in s if c.isalnum())
            
        db_orig = row[0] or ""
        ui_orig = thread_record.preview_text or ""
        db_norm = _normalize(db_orig)
        ui_norm = _normalize(ui_orig)
        
        min_len = min(len(db_norm), len(ui_norm))
        if min_len > 0:
            if db_norm[:min_len] != ui_norm[:min_len]:
                logger.info(f"Quick Cache Miss: Thread {thread_record.thread_name} preview mismatch. DB_NORM='{db_norm}' vs UI_NORM='{ui_norm}'")
                return False
        elif db_norm != ui_norm:
            logger.info(f"Quick Cache Miss: Thread {thread_record.thread_name} preview mismatch (empty). DB='{db_orig}' vs UI='{ui_orig}'")
            return False
            
        preview_lower = (thread_record.preview_text or "").strip().lower()
        sender_label = "Customer"
        if preview_lower.startswith("you:") or preview_lower.startswith("bạn:"):
            sender_label = "Admin reply"
            last_msg_row = cursor.execute(
                "SELECT sender FROM messages WHERE thread_id = ? ORDER BY seq DESC LIMIT 1",
                (thread_record.thread_id,)
            ).fetchone()
            if last_msg_row and last_msg_row[0] != "Page":
                logger.info(f"Quick Cache Miss: Thread {thread_record.thread_name} has admin preview but DB last msg is {last_msg_row[0]}")
                return False
        
        prev_30 = (thread_record.preview_text or "").replace("\n", " ")[:30].strip()
        dt_text = vt.get("sidebarTimeText", "")
        hit_logs.append(f"[{thread_record.thread_name}]: {prev_30}... [{dt_text}] [{sender_label}]")
                
    logger.info("CACHE HIT => Skip scraping for these threads:\n" + "\n".join(hit_logs))
    return True
