import hashlib
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from fb_pipeline.contracts.l1_inbox import (
    EnrichedThreadRecord,
    InboxMessage,
    MasHandoff,
    SeekerInfo,
    ThreadRecord,
    detect_city,
    detect_city_and_program_smart,
    detect_city_smart,
    extract_user_info,
    parse_ad_ids,
)
from fb_pipeline.browser.inbox.thread_list_parser import (
    is_conversation_name,
    parse_sidebar_time_token,
)



# code:inbox-thread-identity-001:canonical-id
def canonical_thread_id(page_id: str, psid: str) -> str:
    """The id Stage 2 persists for a thread: ``<page_id>_<sha256(psid)[:16]>``.

    Retrospective [2026-09-17]: Stage 1 looked the thread up by a provisional
    id hashed from the sidebar card (name|preview|time|attrs), which never
    equals this post-click id, so the "already synced" skip never fired and
    every run re-crawled all threads in range. Both stages now derive the id
    from the PSID through this one function.
    """
    return f"{page_id}_{hashlib.sha256(str(psid).encode('utf-8')).hexdigest()[:16]}"


def _compute_thread_id(page_id: str, visible_thread: dict, name: str, preview_text: str,
                       sidebar_time_text: str, sidebar_identity_key: str, selected_item_id: str, fb_url: str = "") -> str:
    fb_uid = ""
    if fb_url:
        try:
            parsed = urlparse(fb_url)
            opts = parse_qs(parsed.query)
            if 'id' in opts:
                fb_uid = opts['id'][0]
            elif 'profile.php' not in fb_url:
                path_parts = [p for p in parsed.path.strip('/').split('/') if p]
                if path_parts:
                    fb_uid = path_parts[0]
                else:
                    fb_uid = fb_url
            else:
                fb_uid = fb_url
        except Exception:
            fb_uid = fb_url

    stable_key = (
        fb_uid
        or selected_item_id
        or sidebar_identity_key
        or "|".join([
            page_id,
            name,
            preview_text,
            sidebar_time_text,
        ])
    )
    digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:16]
    return f"{page_id}_{digest}"



def build_thread_record(page_id: str, visible_thread: dict) -> ThreadRecord:
    name = (visible_thread.get("name") or "").strip()
    thread_text_full = visible_thread.get("text", "")
    thread_lines = [l.strip() for l in thread_text_full.split('\n') if l.strip()]
    sidebar_time_text = (visible_thread.get("sidebarTimeText") or "").strip()
    sidebar_time_kind = (visible_thread.get("sidebarTimeKind") or "").strip()
    sidebar_identity_key = (visible_thread.get("sidebarIdentityKey") or "").strip()
    selected_item_id = (visible_thread.get("selectedItemId") or "").strip()
    fb_url = (visible_thread.get("fbUrl") or "").strip()

    preview_lines = list(thread_lines[1:]) if len(thread_lines) > 1 else []
    if sidebar_time_text:
        preview_lines = [line for line in preview_lines if line.strip() != sidebar_time_text]
    preview_text = " ".join(preview_lines).strip()

    return ThreadRecord(
        page_id=page_id,
        thread_id=_compute_thread_id(
            page_id,
            visible_thread,
            name,
            preview_text,
            sidebar_time_text,
            sidebar_identity_key,
            selected_item_id,
            fb_url,
        ),
        thread_name=name,
        preview_text=preview_text,
        thread_lines=thread_lines,
        dom_index=visible_thread.get("domIndex", 0),
        sidebar_time_text=sidebar_time_text,
        sidebar_timestamp_ms=visible_thread.get("sidebarTimestampMs"),
        sidebar_time_kind=sidebar_time_kind,
        sidebar_identity_key=sidebar_identity_key,
        selected_item_id=selected_item_id,
        fb_url=fb_url,
    )


def enrich_thread_record(thread_record: ThreadRecord, js_messages: list, extract_user_info,
                         detect_city=None, ad_context: str = "", fb_url: str = "",
                         ad_ids: list | None = None) -> EnrichedThreadRecord:
    db_msgs = [{"sender": m.get("sender"), "content": m.get("text", "")} for m in js_messages]
    user_info = extract_user_info(db_msgs, thread_record.thread_name, ad_context)
    # Classification is intentionally deferred to one batch + independent
    # verification pass after crawling. This local fallback must never make a
    # per-thread LLM request or invent a programme choice.
    city = None
    program_code = None
    normalized_messages = []
    for idx, msg in enumerate(js_messages):
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        normalized_messages.append(
            InboxMessage(
                sender=msg.get("sender", "Unknown"),
                content=text,
                message_timestamp=msg.get("timestamp", ""),
                seq=idx,
            )
        )

    seeker = SeekerInfo(
        name=thread_record.thread_name,
        phone=user_info["phone"],
        email=user_info["email"],
        city=city,
        program_code=program_code,
        lead_stage="Intake",
    )
    mas_handoff = MasHandoff(
        thread_id=thread_record.thread_id,
        thread_name=thread_record.thread_name,
        page_id=thread_record.page_id,
        fb_url=fb_url,
        seeker=seeker,
        ad_context=ad_context,
        ad_ids=list(ad_ids or []),
        messages=normalized_messages,
        temperature="warm",
        cool_step=0,
    )
    return EnrichedThreadRecord(
        page_id=thread_record.page_id,
        thread_id=thread_record.thread_id,
        thread_name=thread_record.thread_name,
        preview_text=thread_record.preview_text,
        thread_lines=thread_record.thread_lines,
        dom_index=thread_record.dom_index,
        sidebar_time_text=thread_record.sidebar_time_text,
        sidebar_timestamp_ms=thread_record.sidebar_timestamp_ms,
        sidebar_time_kind=thread_record.sidebar_time_kind,
        sidebar_identity_key=thread_record.sidebar_identity_key,
        selected_item_id=thread_record.selected_item_id,
        fb_url=fb_url,
        ad_context=ad_context,
        ad_ids=list(ad_ids or []),
        user_info=user_info,
        city=city,
        program_code=program_code,
        messages=normalized_messages,
        mas_handoff=mas_handoff,
    )


# code:bug-inbox-thread-name-001:persist-valid-fb-name
def persist_thread_record(conn, thread_record: EnrichedThreadRecord, detect_city=None) -> dict:
    # The display name can change between crawls.  A valid name read from the
    # live conversation must replace a previously persisted navigation label
    # such as "All messages" for the same stable Facebook identity.
    if not is_conversation_name(thread_record.thread_name):
        raise ValueError(
            f"Refusing to persist an inbox navigation label as a seeker name: "
            f"{thread_record.thread_name!r}"
        )

    cursor = conn.cursor()
    messages_added = 0
    new_customer_message_added = False
    ad_context = thread_record.ad_context

    cursor.execute("SELECT sender, content, seq FROM messages WHERE thread_id=? ORDER BY seq ASC", (thread_record.thread_id,))
    existing_msgs = cursor.fetchall()
    
    import re
    def _normalize(s):
        s = str(s)
        s = re.sub(r'^---\s*\[AD SOURCE\]:.*?---\s*', '', s, flags=re.DOTALL)
        # Legacy rows may carry a literal backslash-n instead of a newline.
        s = s.replace('\\n', '\n')
        # Reactions are added after the fact; they must not make an old
        # message look new. Drop the tag and any now-empty quoted marker.
        s = re.sub(r':::REACTION_[A-Z]+:::', '', s)
        s = re.sub(r'(\[Quoted Reply/Link\]:\s*)+$', '', s.strip())
        return re.sub(r'\s+', '', s.lower())

    # code:bug-inbox-message-dedup-002
    # Retrospective [2026-09-17]: "Auto_Page" is assigned at save time (canned
    # replies, and the AD SOURCE-prefixed first row), while the scraper always
    # reports "Page". Comparing the raw sender made every such row look new,
    # so "<name> replied to an ad." was appended again on every crawl.
    def _normalize_sender(s):
        s = _normalize(s)
        return "page" if s == "auto_page" else s

    existing_list = [(_normalize_sender(row['sender']), _normalize(row['content'])) for row in existing_msgs]
    existing_set = set(existing_list)
    
    new_tuples = []
    for msg in thread_record.messages:
        content = msg.content
        sender = msg.sender
        if sender == "Page":
            if ("Chúng tôi có thể" in content or 
                "Họ tên và Số điện thoại" in content or
                "Khóa học thiền ở Hà Nội" in content or
                "Thời gian: 20h-21h30" in content):
                sender = "Auto_Page"
        new_tuples.append((_normalize_sender(sender), _normalize(content)))
        
    max_overlap = min(len(existing_list), len(new_tuples))
    best_overlap = 0
    # Try all possible overlap lengths. We want the LARGEST overlap.
    for i in range(1, max_overlap + 1):
        if existing_list[-i:] == new_tuples[:i]:
            best_overlap = i
            
    next_seq = existing_msgs[-1]['seq'] + 1 if existing_msgs else 0
    candidate_msgs = thread_record.messages[best_overlap:]

    msgs_to_insert = []
    for msg in candidate_msgs:
        content = msg.content
        sender = msg.sender
        if sender == "Page":
            if ("Chúng tôi có thể" in content or 
                "Họ tên và Số điện thoại" in content or
                "Khóa học thiền ở Hà Nội" in content or
                "Thời gian: 20h-21h30" in content):
                sender = "Auto_Page"
                
        sig = (_normalize_sender(sender), _normalize(content))
        if sig not in existing_set:
            msg.sender = sender
            msgs_to_insert.append(msg)
            existing_set.add(sig)

    for idx, msg in enumerate(msgs_to_insert):
        msg_content_to_save = msg.content
        sender_to_save = msg.sender
        
        if sender_to_save == "Page":
            if ("Chúng tôi có thể" in msg_content_to_save or 
                "Họ tên và Số điện thoại" in msg_content_to_save or
                "Khóa học thiền ở Hà Nội" in msg_content_to_save or
                "Thời gian: 20h-21h30" in msg_content_to_save):
                sender_to_save = "Auto_Page"

        if messages_added == 0 and ad_context:
            msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{msg_content_to_save}"
            if sender_to_save == "Page": sender_to_save = "Auto_Page"
            
        cursor.execute(
            "INSERT OR IGNORE INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
            (
                thread_record.thread_id,
                sender_to_save,
                msg_content_to_save,
                msg.message_timestamp,
                next_seq + idx,
            )
        )
        if cursor.rowcount > 0:
            messages_added += 1
            if msg.sender == "Customer":
                new_customer_message_added = True
    # The sidebar describes the thread's most recent message regardless of
    # sender. Prefer it over `users.last_interaction`, which intentionally
    # tracks customer activity only and therefore cannot reproduce Inbox order.
    last_message_at = None
    if thread_record.sidebar_timestamp_ms:
        last_message_at = datetime.fromtimestamp(thread_record.sidebar_timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    sidebar_time = parse_sidebar_time_token(thread_record.sidebar_time_text)
    if not last_message_at and sidebar_time.get("parsed_at") and " " in sidebar_time["parsed_at"]:
        last_message_at = sidebar_time["parsed_at"].replace("T", " ")
    if not last_message_at:
        for message in reversed(thread_record.messages):
            parsed_message_time = parse_sidebar_time_token(message.message_timestamp or "")
            parsed_at = parsed_message_time.get("parsed_at")
            if parsed_at and " " in parsed_at:
                last_message_at = parsed_at.replace("T", " ")
                break

    cursor.execute('''
        INSERT INTO threads (id, page_id, thread_name, last_synced_time, inbox_sort_index, last_message_at)
        VALUES (?, ?, ?, datetime('now'), ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            thread_name=excluded.thread_name,
            last_synced_time=excluded.last_synced_time,
            inbox_sort_index=excluded.inbox_sort_index,
            last_message_at=COALESCE(excluded.last_message_at, threads.last_message_at)
    ''', (
        thread_record.thread_id,
        thread_record.page_id,
        thread_record.thread_name,
        thread_record.dom_index,
        last_message_at,
    ))

    for aid in thread_record.ad_ids:
        cursor.execute('''
            INSERT OR IGNORE INTO user_ad_ids (thread_id, ad_id)
            VALUES (?, ?)
        ''', (thread_record.thread_id, aid))
        if ad_context:
            ad_city = detect_city(ad_context, []) if detect_city else None
            cursor.execute('''
                INSERT INTO ad_posts (ad_id, ad_content, city, resolved_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(ad_id) DO UPDATE SET
                    ad_content = CASE WHEN excluded.ad_content != '' THEN excluded.ad_content ELSE ad_posts.ad_content END,
                    city = CASE WHEN excluded.city IS NOT NULL THEN excluded.city ELSE ad_posts.city END,
                    resolved_at = datetime('now')
            ''', (aid, ad_context, ad_city))

    user_info = thread_record.user_info
    
    # Chronological Sorting Integrity Logic (Rule 11)
    # Never use datetime('now') blindly for historical scraping, it breaks UI order.
    # Priority 1: Check if the latest message has an exact, valid timestamp (like "8:20 AM" or "Feb 6, 2026, 1:58 PM")
    interaction_time_sql = "datetime('now')"
    latest_msg_ts = None
    for msg in reversed(thread_record.messages):
        ts_cand = (msg.message_timestamp or "").strip()
        if ts_cand:
            t_cand_data = parse_sidebar_time_token(ts_cand)
            if t_cand_data and t_cand_data.get("parsed_at") and " " in t_cand_data.get("parsed_at", ""):
                latest_msg_ts = t_cand_data["parsed_at"].replace("T", " ")
                break

    if latest_msg_ts:
        interaction_time_sql = f"'{latest_msg_ts}'"
    elif thread_record.sidebar_time_text:
        time_data = parse_sidebar_time_token(thread_record.sidebar_time_text)
        if time_data and time_data.get("parsed_at"):
            parsed_dt = time_data["parsed_at"]
            if "T" in parsed_dt:
                parsed_dt = parsed_dt.replace("T", " ")
            elif len(parsed_dt) == 10: # YYYY-MM-DD
                import datetime as dt_mod
                now = dt_mod.datetime.now()
                # If date is today, never place it in the future! Use current time staggered backward.
                if time_data.get("kind") == "today" or time_data.get("days_ago") == 0 or parsed_dt == now.strftime("%Y-%m-%d"):
                    staggered = now - dt_mod.timedelta(minutes=thread_record.dom_index)
                else:
                    staggered = now.replace(hour=23, minute=59, second=59) - dt_mod.timedelta(minutes=thread_record.dom_index)
                parsed_dt = f"{parsed_dt} {staggered.strftime('%H:%M:%S')}"
            interaction_time_sql = f"'{parsed_dt}'"

    if new_customer_message_added:
        cursor.execute(f'''
            INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, program_code, last_interaction, last_synced_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, {{interaction_time}}, datetime('now'))
            ON CONFLICT(thread_id) DO UPDATE SET
                thread_name=excluded.thread_name,
                phone = COALESCE(excluded.phone, users.phone),
                email = COALESCE(excluded.email, users.email),
                fb_url = COALESCE(excluded.fb_url, users.fb_url),
                last_interaction = {{interaction_time}},
                last_synced_at = datetime('now')
        '''.replace('{interaction_time}', interaction_time_sql), (
            thread_record.thread_id,
            thread_record.thread_name,
            user_info.get("phone"),
            user_info.get("email"),
            thread_record.fb_url,

        ))
    else:
        cursor.execute('''
            INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, program_code, last_synced_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, datetime('now'))
            ON CONFLICT(thread_id) DO UPDATE SET
                thread_name=excluded.thread_name,
                phone = COALESCE(excluded.phone, users.phone),
                email = COALESCE(excluded.email, users.email),
                fb_url = COALESCE(excluded.fb_url, users.fb_url),
                last_synced_at = datetime('now')
        ''', (
            thread_record.thread_id,
            thread_record.thread_name,
            user_info.get("phone"),
            user_info.get("email"),
            thread_record.fb_url,
        ))

    conn.commit()
    return {
        "thread_id": thread_record.thread_id,
        "messages_added": messages_added,
        "ad_ids_count": len(thread_record.ad_ids),
        "city": thread_record.city,
        "program_code": thread_record.program_code,
        "mas_handoff": _mas_handoff_to_dict(thread_record.mas_handoff),
    }


def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels, extract_user_info, detect_city=None,
                 skip_navigation: bool = False, force_refresh: bool = False) -> dict:
    from fb_pipeline.browser.l3_inbox import scrape_inbox_ui

    return scrape_inbox_ui(
        page,
        page_id,
        time_range,
        max_threads,
        conn,
        logger,
        record_fetch,
        extract_ad_id_labels,
        extract_user_info,
        detect_city=detect_city,
        skip_navigation=skip_navigation,
        force_refresh=force_refresh,
    )


def _mas_handoff_to_dict(mas_handoff: MasHandoff | None) -> dict:
    if mas_handoff is None:
        return {}
    return {
        "thread_id": mas_handoff.thread_id,
        "thread_name": mas_handoff.thread_name,
        "page_id": mas_handoff.page_id,
        "fb_url": mas_handoff.fb_url,
        "seeker": {
            "name": mas_handoff.seeker.name,
            "phone": mas_handoff.seeker.phone,
            "email": mas_handoff.seeker.email,
            "city": mas_handoff.seeker.city,
            "lead_stage": mas_handoff.seeker.lead_stage,
        },
        "ad_context": mas_handoff.ad_context,
        "ad_ids": list(mas_handoff.ad_ids),
        "temperature": mas_handoff.temperature,
        "cool_step": mas_handoff.cool_step,
        "messages": [
            {
                "sender": message.sender,
                "content": message.content,
                "message_timestamp": message.message_timestamp,
                "seq": message.seq,
            }
            for message in mas_handoff.messages
        ],
    }
