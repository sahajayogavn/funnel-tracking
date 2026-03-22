from fb_pipeline.contracts.l1_comments import CommentRecord, EnrichedPostRecord, PostRecord


def build_post_record(page_id: str, visible_post: dict) -> PostRecord:
    name = (visible_post.get("name") or "").strip()
    post_text_full = visible_post.get("text", "")
    post_lines = [l.strip() for l in post_text_full.split("\n") if l.strip()]
    preview_text = " ".join(post_lines[1:]) if len(post_lines) > 1 else ""
    return PostRecord(
        page_id=page_id,
        post_id=f"{page_id}_{abs(hash(name))}",
        post_name=name,
        preview_text=preview_text,
        post_lines=post_lines,
        dom_index=visible_post.get("domIndex", 0),
    )


def enrich_post_record(post_record: PostRecord, js_comments: list, extract_user_info, detect_city,
                       post_url: str = "") -> EnrichedPostRecord:
    normalized_comments = []
    for comment in js_comments:
        text = (comment.get("comment_text") or "").strip()
        if not text:
            continue
        normalized_comments.append(
            CommentRecord(
                commenter_name=(comment.get("commenter_name") or "Unknown").strip(),
                comment_text=text,
                comment_timestamp=comment.get("timestamp", ""),
                fb_profile_url=comment.get("profile_url", ""),
                fb_user_id=comment.get("fb_user_id", ""),
                is_reply=1 if comment.get("is_reply", False) else 0,
                comment_date=comment.get("timestamp", ""),
            )
        )

    user_info = extract_user_info([
        {"comment_text": comment.comment_text} for comment in normalized_comments
    ])
    all_comment_text = " ".join([comment.comment_text for comment in normalized_comments])
    city = detect_city(all_comment_text)

    return EnrichedPostRecord(
        page_id=post_record.page_id,
        post_id=post_record.post_id,
        post_name=post_record.post_name,
        preview_text=post_record.preview_text,
        post_lines=post_record.post_lines,
        dom_index=post_record.dom_index,
        post_url=post_url,
        comments=normalized_comments,
        user_info=user_info,
        city=city,
    )


def persist_post_record(conn, post_record: EnrichedPostRecord, logger=None) -> dict:
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM posts WHERE id = ?", (post_record.post_id,))
    existing_post = cursor.fetchone()

    comments_added = 0
    for comment in post_record.comments:
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO comments (post_id, commenter_name, comment_text, comment_timestamp, fb_profile_url, fb_user_id, is_reply, comment_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    post_record.post_id,
                    comment.commenter_name,
                    comment.comment_text,
                    comment.comment_timestamp,
                    comment.fb_profile_url,
                    comment.fb_user_id,
                    comment.is_reply,
                    comment.comment_date,
                )
            )
            if cursor.rowcount > 0:
                comments_added += 1
        except Exception as e:
            if logger:
                logger.debug(f"Duplicate or error inserting comment: {e}")

    cursor.execute(
        '''
        INSERT INTO posts (id, page_id, post_name, post_url, last_synced_time)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_synced_time=excluded.last_synced_time,
            post_url=excluded.post_url
        ''',
        (
            post_record.post_id,
            post_record.page_id,
            post_record.post_name,
            post_record.post_url,
            post_record.preview_text,
        )
    )

    for comment in post_record.comments:
        commenter = comment.commenter_name
        if commenter in ("Page", "Unknown", ""):
            continue
        try:
            cursor.execute(
                '''
                INSERT INTO comment_users (post_id, commenter_name, fb_user_id, fb_profile_url, phone, email, city, last_interaction)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(post_id, commenter_name) DO UPDATE SET
                    fb_user_id = COALESCE(excluded.fb_user_id, comment_users.fb_user_id),
                    fb_profile_url = COALESCE(excluded.fb_profile_url, comment_users.fb_profile_url),
                    phone = COALESCE(excluded.phone, comment_users.phone),
                    email = COALESCE(excluded.email, comment_users.email),
                    city = CASE WHEN excluded.city != 'Unknown' THEN excluded.city ELSE comment_users.city END,
                    last_interaction = datetime('now')
                ''',
                (
                    post_record.post_id,
                    commenter,
                    comment.fb_user_id,
                    comment.fb_profile_url,
                    post_record.user_info["phone"],
                    post_record.user_info["email"],
                    post_record.city,
                )
            )
        except Exception as e:
            if logger:
                logger.debug(f"Error upserting comment_user: {e}")

    conn.commit()
    return {
        "post_id": post_record.post_id,
        "comments_added": comments_added,
        "is_new_post": existing_post is None,
        "city": post_record.city,
    }
