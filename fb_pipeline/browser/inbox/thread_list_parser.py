from datetime import datetime, timedelta

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

            function pickTimeToken(lines) {
                for (let i = lines.length - 1; i >= 1; i--) {
                    const token = (lines[i] || '').trim();
                    if (!token) continue;
                    if (/^\d+[smhdw]$/i.test(token)) return token;
                    if (/^(today|yesterday|hôm nay|hôm qua)$/i.test(token)) return token;
                    if (/^(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)$/i.test(token)) return token;
                    if (/^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}$/i.test(token)) return token;
                }
                return '';
            }

            return candidates.map((el, idx) => {
                const text = (el.innerText || '').trim();
                const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                const name = lines[0] || '';
                const sidebarTimeText = pickTimeToken(lines);
                const previewLines = lines.slice(1).filter(line => line !== sidebarTimeText);
                const hrefEl = el.closest('a[href]') || el.querySelector('a[href]');
                const href = hrefEl ? (hrefEl.getAttribute('href') || '') : '';
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
                const identityParts = [name, previewLines.join(' | '), sidebarTimeText, selectedItemId, href, attrs.join('|')].filter(Boolean);
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
                };
            }).filter(item => item.name || item.text);
        }''',
        {"threadSelector": selector},
    )

def parse_sidebar_time_token(token: str, now: datetime | None = None) -> dict:
    token = (token or "").strip()
    if not token:
        return {"kind": "unknown", "token": "", "days_ago": None, "parsed_at": None}

    now = now or datetime.now()
    lower = token.lower()
    if lower in TIME_TODAY:
        return {"kind": "today", "token": token, "days_ago": 0, "parsed_at": now.date().isoformat()}
    if lower in TIME_YESTERDAY:
        return {"kind": "yesterday", "token": token, "days_ago": 1, "parsed_at": (now.date() - timedelta(days=1)).isoformat()}
    if lower in DAY_NAMES:
        return {"kind": "weekday", "token": token, "days_ago": 6, "parsed_at": None}
    if MONTH_DAY_RE.match(token):
        parsed = datetime.strptime(f"{token} {now.year}", "%b %d %Y")
        days_ago = (now - parsed).days
        if days_ago < 0:
            parsed = datetime.strptime(f"{token} {now.year - 1}", "%b %d %Y")
            days_ago = (now - parsed).days
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
