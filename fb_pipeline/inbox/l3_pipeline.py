from fb_pipeline.contracts.l1_inbox import (
    EnrichedThreadRecord,
    InboxMessage,
    MasHandoff,
    SeekerInfo,
    ThreadRecord,
    detect_city,
    extract_user_info,
    parse_ad_ids,
)


def build_thread_record(page_id: str, visible_thread: dict) -> ThreadRecord:
    name = (visible_thread.get("name") or "").strip()
    thread_text_full = visible_thread.get("text", "")
    thread_lines = [l.strip() for l in thread_text_full.split('\n') if l.strip()]
    preview_text = " ".join(thread_lines[1:]) if len(thread_lines) > 1 else ""
    return ThreadRecord(
        page_id=page_id,
        thread_id=f"{page_id}_{abs(hash(name))}",
        thread_name=name,
        preview_text=preview_text,
        thread_lines=thread_lines,
        dom_index=visible_thread.get("domIndex", 0),
    )


def enrich_thread_record(thread_record: ThreadRecord, js_messages: list, extract_user_info,
                         detect_city, ad_context: str = "", fb_url: str = "",
                         ad_ids: list | None = None) -> EnrichedThreadRecord:
    db_msgs = [{"sender": m.get("sender"), "content": m.get("text", "")} for m in js_messages]
    user_info = extract_user_info(db_msgs, thread_record.thread_name, ad_context)
    city = detect_city(ad_context, db_msgs)
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
    )
    return EnrichedThreadRecord(
        page_id=thread_record.page_id,
        thread_id=thread_record.thread_id,
        thread_name=thread_record.thread_name,
        preview_text=thread_record.preview_text,
        thread_lines=thread_record.thread_lines,
        dom_index=thread_record.dom_index,
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
    ad_context = thread_record.ad_context

    for idx, msg in enumerate(thread_record.messages):
        msg_content_to_save = msg.content
        if messages_added == 0 and ad_context:
            msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{msg_content_to_save}"
        cursor.execute(
            "INSERT OR IGNORE INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, ?, ?, ?, ?)",
            (
                thread_record.thread_id,
                msg.sender,
                msg_content_to_save,
                msg.message_timestamp,
                msg.seq if msg.seq is not None else idx,
            )
        )
        if cursor.rowcount > 0:
            messages_added += 1

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
    cursor.execute('''
        INSERT INTO users (thread_id, thread_name, phone, email, fb_url, city, last_interaction)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(thread_id) DO UPDATE SET
            phone = COALESCE(excluded.phone, users.phone),
            email = COALESCE(excluded.email, users.email),
            fb_url = COALESCE(excluded.fb_url, users.fb_url),
            city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE users.city END,
            last_interaction = datetime('now')
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
