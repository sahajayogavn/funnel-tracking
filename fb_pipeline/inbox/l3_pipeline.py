import hashlib

from fb_pipeline.contracts.l1_inbox import (
    EnrichedThreadRecord,
    InboxMessage,
    MasHandoff,
    SeekerInfo,
    ThreadRecord,
    detect_city,
    detect_city_smart,
    extract_user_info,
    parse_ad_ids,
)


def _compute_thread_id(page_id: str, visible_thread: dict, name: str, preview_text: str,
                       sidebar_time_text: str, sidebar_identity_key: str, selected_item_id: str) -> str:
    stable_key = (
        selected_item_id
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
        ),
        thread_name=name,
        preview_text=preview_text,
        thread_lines=thread_lines,
        dom_index=visible_thread.get("domIndex", 0),
        sidebar_time_text=sidebar_time_text,
        sidebar_time_kind=sidebar_time_kind,
        sidebar_identity_key=sidebar_identity_key,
        selected_item_id=selected_item_id,
    )


def enrich_thread_record(thread_record: ThreadRecord, js_messages: list, extract_user_info,
                         detect_city, ad_context: str = "", fb_url: str = "",
                         ad_ids: list | None = None) -> EnrichedThreadRecord:
    db_msgs = [{"sender": m.get("sender"), "content": m.get("text", "")} for m in js_messages]
    user_info = extract_user_info(db_msgs, thread_record.thread_name, ad_context)
    # code:tool-citydetect-001:smart-detect-integration
    # Use LLM-first city detection with all message signals
    city = detect_city_smart(
        ad_context, db_msgs,
        thread_name=thread_record.thread_name,
        customer_messages=db_msgs,
    )
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
        sidebar_time_kind=thread_record.sidebar_time_kind,
        sidebar_identity_key=thread_record.sidebar_identity_key,
        selected_item_id=thread_record.selected_item_id,
        fb_url=fb_url,
        ad_context=ad_context,
        ad_ids=list(ad_ids or []),
        user_info=user_info,
        city=city,
        messages=normalized_messages,
        mas_handoff=mas_handoff,
    )


def persist_thread_record(conn, thread_record: EnrichedThreadRecord, detect_city) -> dict:
    cursor = conn.cursor()
    messages_added = 0
    new_customer_message_added = False
    ad_context = thread_record.ad_context

    for idx, msg in enumerate(thread_record.messages):
        msg_content_to_save = msg.content
        sender_to_save = msg.sender
        
        # Heuristically classify Facebook's automated responses
        if sender_to_save == "Page":
            if ("Chúng tôi có thể giúp gì cho bạn?" in msg_content_to_save or 
                "Bạn để lại Họ tên và Số điện thoại để đăng ký" in msg_content_to_save or
                "Khóa học thiền ở Hà Nội" in msg_content_to_save or
                "Thời gian: 20h-21h30" in msg_content_to_save):
                sender_to_save = "Auto_Page"

        if messages_added == 0 and ad_context:
            msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{msg_content_to_save}"
            # Also ensure ad_source injections don't shadow Customer intent
            if sender_to_save == "Page": sender_to_save = "Auto_Page"
            
        cursor.execute(
            "INSERT OR IGNORE INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
            (
                thread_record.thread_id,
                sender_to_save,
                msg_content_to_save,
                msg.message_timestamp,
                msg.seq if msg.seq is not None else idx,
            )
        )
        if cursor.rowcount > 0:
            messages_added += 1
            if msg.sender == "Customer":
                new_customer_message_added = True

    cursor.execute('''
        INSERT INTO threads (id, page_id, thread_name, last_synced_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_synced_time=excluded.last_synced_time
    ''', (
        thread_record.thread_id,
        thread_record.page_id,
        thread_record.thread_name,
        thread_record.preview_text,
    ))

    for aid in thread_record.ad_ids:
        cursor.execute('''
            INSERT OR IGNORE INTO user_ad_ids (thread_id, ad_id)
            VALUES (?, ?)
        ''', (thread_record.thread_id, aid))
        if ad_context:
            ad_city = detect_city(ad_context, [])
            cursor.execute('''
                INSERT INTO ad_posts (ad_id, ad_content, city, resolved_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(ad_id) DO UPDATE SET
                    ad_content = CASE WHEN excluded.ad_content != '' THEN excluded.ad_content ELSE ad_posts.ad_content END,
                    city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE ad_posts.city END,
                    resolved_at = datetime('now')
            ''', (aid, ad_context, ad_city))

    user_info = thread_record.user_info
    if new_customer_message_added:
        cursor.execute('''
            INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, last_interaction, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(thread_id) DO UPDATE SET
                phone = COALESCE(excluded.phone, users.phone),
                email = COALESCE(excluded.email, users.email),
                fb_url = COALESCE(excluded.fb_url, users.fb_url),
                city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE users.city END,
                last_interaction = datetime('now'),
                last_synced_at = datetime('now')
        ''', (
            thread_record.thread_id,
            thread_record.thread_name,
            user_info.get("phone"),
            user_info.get("email"),
            thread_record.fb_url,
            thread_record.city,
        ))
    else:
        cursor.execute('''
            INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(thread_id) DO UPDATE SET
                phone = COALESCE(excluded.phone, users.phone),
                email = COALESCE(excluded.email, users.email),
                fb_url = COALESCE(excluded.fb_url, users.fb_url),
                city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE users.city END,
                last_synced_at = datetime('now')
        ''', (
            thread_record.thread_id,
            thread_record.thread_name,
            user_info.get("phone"),
            user_info.get("email"),
            thread_record.fb_url,
            thread_record.city,
        ))

    conn.commit()
    return {
        "thread_id": thread_record.thread_id,
        "messages_added": messages_added,
        "ad_ids_count": len(thread_record.ad_ids),
        "city": thread_record.city,
        "mas_handoff": _mas_handoff_to_dict(thread_record.mas_handoff),
    }


def scrape_inbox(page, page_id: str, time_range: str, max_threads: int, conn, logger,
                 record_fetch, extract_ad_id_labels, extract_user_info, detect_city) -> dict:
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
        detect_city,
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
