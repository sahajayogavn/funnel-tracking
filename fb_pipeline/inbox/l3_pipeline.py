import hashlib
import json
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from fb_pipeline.contracts.l1_message_kind import classify_message_kind, KIND_MESSAGE
from fb_pipeline.contracts.l1_message_time import resolve_message_at
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
from fb_pipeline.contracts.l1_city_llm import sanitize_ad_content
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
        # `domIndex` is emitted only by sidebar discovery.  A targeted/detail
        # fetch has no Inbox position and must not be treated as position zero.
        dom_index=visible_thread.get("domIndex"),
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
    # Never attach the surrounding Inbox transcript to a shared ad id.  Apart
    # from corrupting the ad record, that would leak another seeker's messages
    # into Signal 3 for every later classification using this ad.
    ad_context = sanitize_ad_content(ad_context)
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
        reactions = list(msg.get("reactions") or [])
        # A reaction-only DOM observation has no conversational body, but it
        # is still evidence and must reach the separate reaction persistence
        # path. It is never made into a synthetic emoji message.
        if not text and not reactions:
            continue
        normalized_messages.append(
            InboxMessage(
                # The scraper may not be able to establish an actor.  Persist
                # that uncertainty instead of defaulting it to the seeker.
                sender=msg.get("sender") or "Unknown",
                content=text,
                message_timestamp=msg.get("timestamp", ""),
                seq=idx,
                source_id=msg.get("source_id") or None,
                sender_confidence=msg.get("sender_confidence") or "unknown",
                raw_timestamp=msg.get("raw_timestamp") or msg.get("timestamp", ""),
                day_context=msg.get("day_context") or "",
                time_precision=msg.get("time_precision") or "unknown",
                reply_to_message_id=msg.get("reply_to_message_id") or None,
                quoted_sender=msg.get("quoted_sender") or None,
                quoted_sender_confidence=msg.get("quoted_sender_confidence") or "unknown",
                quoted_text=msg.get("quoted_text") or None,
                sender_evidence=msg.get("sender_evidence") or None,
                quote_evidence=msg.get("quote_evidence") or None,
                reactions=reactions,
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


def _persist_crawled_reactions(cursor, thread_id: str, reactions: list[dict],
                               message_source_id: str | None = None) -> None:
    """Persist browser-observed reactions without attributing missing facts.

    This intentionally writes a separate evidence table from the legacy
    ``reactions`` table, which tracks the application's own outbound actions.
    A reaction attached by the DOM to a bubble is not proof that the bubble's
    sender performed it, so target and actor are copied only from the parser's
    structured observation and otherwise remain ``unknown``/NULL.
    """
    for reaction in reactions or []:
        if not isinstance(reaction, dict):
            continue
        # The enclosing message source id is a direct DOM association, not an
        # actor/target inference. It remains a source reference only; target
        # fields below stay unknown unless the parser observed them explicitly.
        source_id = (
            reaction.get("source_id") or message_source_id or ""
        ).strip() or None
        actor = (reaction.get("actor") or "unknown").strip() or "unknown"
        emoji = (reaction.get("emoji") or "unknown").strip() or "unknown"
        target_type = (reaction.get("target_type") or "unknown").strip() or "unknown"
        target_message_id = (reaction.get("target_message_id") or "").strip() or None
        observed_at = (reaction.get("observed_at") or "").strip() or None
        occurred_at = (reaction.get("occurred_at") or "").strip() or None
        raw_label = (reaction.get("raw_label") or "").strip() or None
        parse_confidence = (reaction.get("parse_confidence") or "unknown").strip() or "unknown"
        actor_role = (reaction.get("actor_role") or "unknown").strip() or "unknown"
        target_scope = (reaction.get("target_scope") or target_type).strip() or "unknown"
        evidence = (reaction.get("evidence") or "").strip() or None

        # The key is a snapshot evidence fingerprint, not an invented Facebook
        # id.  It permits different people/emojis on the same target while
        # making an unchanged re-crawl idempotent.
        key_material = json.dumps(
            {
                "source_id": source_id,
                "actor": actor,
                "emoji": emoji,
                "target_type": target_type,
                "target_message_id": target_message_id,
                "observed_at": observed_at,
                "occurred_at": occurred_at,
                "raw_label": raw_label,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        reaction_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        cursor.execute(
            """INSERT INTO crawled_message_reactions
               (thread_id, reaction_key, source_id, actor, actor_role, emoji, target_type,
                target_message_id, target_scope, observed_at, occurred_at, raw_label,
                evidence, parse_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(thread_id, reaction_key) DO UPDATE SET
                   actor=excluded.actor, actor_role=excluded.actor_role,
                   target_type=excluded.target_type, target_message_id=excluded.target_message_id,
                   target_scope=excluded.target_scope, observed_at=excluded.observed_at,
                   occurred_at=excluded.occurred_at, raw_label=excluded.raw_label,
                   evidence=excluded.evidence, parse_confidence=excluded.parse_confidence""",
            (
                thread_id,
                reaction_key,
                source_id,
                actor,
                actor_role,
                emoji,
                target_type,
                target_message_id,
                target_scope,
                observed_at,
                occurred_at,
                raw_label,
                evidence,
                parse_confidence,
            ),
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
    # Defend the persistence boundary too; records can be constructed by
    # callers other than `enrich_thread_record`.
    ad_context = sanitize_ad_content(thread_record.ad_context)

    cursor.execute(
        """SELECT id, sender, content, seq, source_id, day_context
           FROM messages WHERE thread_id=? ORDER BY seq ASC""",
        (thread_record.thread_id,),
    )
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

    # `Auto_Page` only exists in legacy rows created by a former content-based
    # sender heuristic. Treat it as Page for *matching those legacy rows*, but
    # never create or infer it for a newly observed message.
    def _normalize_sender(s):
        s = _normalize(s)
        return "page" if s == "auto_page" else s

    def _fingerprint(sender, content, source_id=None, day_context=""):
        """Return the strongest available identity without inventing one.

        A Facebook source id wins.  Without it, a parser-provided absolute day
        plus sender/body makes repeated short acknowledgements on different
        days distinct.  Older rows without either retain the ordered-overlap
        fallback below; they are never globally deduped by body text.
        """
        normalized_sender = _normalize_sender(sender)
        normalized_content = _normalize(content)
        source_id = (source_id or "").strip()
        if source_id:
            return ("source", source_id)
        day_context = (day_context or "").strip()
        if len(day_context) == 10 and day_context[4:5] == "-" and day_context[7:8] == "-":
            return ("day", normalized_sender, normalized_content, day_context)
        return ("body", normalized_sender, normalized_content)

    def _body_fingerprint(sender, content, day_context=""):
        """Fallback identity when a source id is absent or unusable.

        This is deliberately only used for ordered snapshot alignment.  It is
        not a global body dedupe key: repeated acknowledgements remain
        separate events unless they are part of the exact re-fetched overlap.
        """
        normalized_sender = _normalize_sender(sender)
        normalized_content = _normalize(content)
        day_context = (day_context or "").strip()
        if len(day_context) == 10 and day_context[4:5] == "-" and day_context[7:8] == "-":
            return ("day", normalized_sender, normalized_content, day_context)
        return ("body", normalized_sender, normalized_content)

    # A DOM wrapper id is not a Facebook *message* id when two bodies from the
    # same snapshot claim it.  The unique DB index would otherwise silently
    # erase a sibling, or an upsert could overwrite the wrong body.  Keep both
    # bodies and treat that id as unavailable for this observation; a later
    # parser boundary fix can provide a real per-message id.  Never mint a
    # synthetic Facebook id from the wrapper or body text.
    existing_by_source_id = {
        row["source_id"]: row["id"]
        for row in existing_msgs
        if row["source_id"]
    }
    
    # Inbox includes operational rows (assignment, labels, etc.) in its DOM.
    # They are not conversation messages and must neither be saved nor affect
    # overlap/sequence calculation for the actual message timeline.
    # Reactions can arrive on an already persisted bubble or without a body
    # bubble in the current viewport. Persist them independently before the
    # conversational-body filter below; otherwise pure reaction observations
    # disappear at the normalizer boundary.
    for msg in thread_record.messages:
        _persist_crawled_reactions(
            cursor, thread_record.thread_id, msg.reactions, msg.source_id
        )

    conversation_messages = [
        msg for msg in thread_record.messages
        if msg.content and classify_message_kind(msg.content) == KIND_MESSAGE
    ]

    # Only conversational bodies participate in message identity.  A
    # reaction-only observation may refer to an enclosing bubble source id,
    # but it is not a sibling body and must not make that message id ambiguous.
    source_id_counts = Counter(
        (msg.source_id or "").strip()
        for msg in conversation_messages
        if (msg.source_id or "").strip()
    )
    ambiguous_source_ids = {
        source_id for source_id, count in source_id_counts.items() if count > 1
    }

    def _observed_source_id(msg):
        source_id = (msg.source_id or "").strip()
        return source_id if source_id and source_id not in ambiguous_source_ids else ""
        
    max_overlap = min(len(existing_msgs), len(conversation_messages))
    best_overlap = 0
    def _overlap_matches(existing_row, incoming_msg):
        """Match only an ordered re-fetched prefix without trusting collisions."""
        incoming_source_id = _observed_source_id(incoming_msg)
        if incoming_source_id:
            return _fingerprint(
                existing_row["sender"], existing_row["content"],
                existing_row["source_id"], existing_row["day_context"],
            ) == _fingerprint(
                incoming_msg.sender, incoming_msg.content,
                incoming_source_id, incoming_msg.day_context,
            )
        # A colliding wrapper id is no evidence of identity.  Fall back to the
        # same ordered body/day comparison used for snapshots without ids so a
        # known prefix is not duplicated, while unmatched siblings are kept.
        return _body_fingerprint(
            existing_row["sender"], existing_row["content"], existing_row["day_context"],
        ) == _body_fingerprint(
            incoming_msg.sender, incoming_msg.content, incoming_msg.day_context,
        )

    # Try all possible overlap lengths. We want the LARGEST overlap.
    for i in range(1, max_overlap + 1):
        if all(
            _overlap_matches(existing_msgs[-i + offset], incoming_msg)
            for offset, incoming_msg in enumerate(conversation_messages[:i])
        ):
            best_overlap = i
            
    next_seq = existing_msgs[-1]['seq'] + 1 if existing_msgs else 0
    candidate_msgs = conversation_messages[best_overlap:]

    def _message_time_evidence(msg):
        """Resolve canonical time from the exact evidence persisted with a row."""
        if msg.time_precision in {"time_only", "unresolved_day_time", "unresolved_day"}:
            return None, True
        return resolve_message_at(msg.message_timestamp or msg.raw_timestamp, datetime.now())

    # Source-id observations are authoritative for identity, so a re-crawl
    # updates its evidence in place.  No text/content rule is used to decide
    # who sent it.  This runs before append logic because a parser correction
    # can legitimately change a message body while keeping its source id.
    for msg in conversation_messages:
        source_id = _observed_source_id(msg)
        existing_id = existing_by_source_id.get(source_id)
        if not source_id or existing_id is None:
            continue
        message_at, message_at_approx = _message_time_evidence(msg)
        cursor.execute(
            """UPDATE messages
               SET sender=?, content=?, message_timestamp=?,
                   message_at=?, message_at_approx=?,
                   sender_confidence=?, raw_timestamp=?, day_context=?,
                   time_precision=?, reply_to_message_id=?, quoted_sender=?,
                   quoted_sender_confidence=?, quoted_text=?, sender_evidence=?,
                   quote_evidence=?
               WHERE id=?""",
            (
                msg.sender or "Unknown",
                msg.content,
                msg.message_timestamp,
                message_at,
                1 if message_at_approx else 0,
                msg.sender_confidence or "unknown",
                msg.raw_timestamp or msg.message_timestamp,
                msg.day_context or "",
                msg.time_precision or "unknown",
                msg.reply_to_message_id,
                msg.quoted_sender,
                msg.quoted_sender_confidence or "unknown",
                msg.quoted_text,
                msg.sender_evidence,
                msg.quote_evidence,
                existing_id,
            ),
        )
    # Do not use a global (sender, body) set here.  It erased later "Dạ" /
    # "Vâng" turns that happened to repeat an earlier customer message.  The
    # largest ordered overlap removes a re-fetched prefix; all remaining
    # observed events are retained unless their real source id already exists.
    msgs_to_insert = []
    seen_source_ids = set(existing_by_source_id)
    for msg in candidate_msgs:
        source_id = _observed_source_id(msg)
        if source_id and source_id in seen_source_ids:
            continue
        msgs_to_insert.append(msg)
        if source_id:
            seen_source_ids.add(source_id)

    for idx, msg in enumerate(msgs_to_insert):
        msg_content_to_save = msg.content
        sender_to_save = msg.sender or "Unknown"

        if messages_added == 0 and ad_context:
            msg_content_to_save = f"--- [AD SOURCE]: {ad_context} ---\n\n{msg_content_to_save}"
            
        # code:inbox-msg-kind-001 / code:inbox-msg-abs-time-001
        # Candidates were filtered by their original Inbox row above.  The
        # optional AD-context prefix is metadata, not a system-banner kind.
        msg_kind = KIND_MESSAGE
        # A clock with no reliable day context is not an event timestamp.  In
        # particular, do not silently anchor "9:00 AM" to crawl day: that can
        # turn an old customer turn into a fresh one for the conversation gate.
        # Keep the raw label/evidence and let it remain unknown until a source
        # snapshot provides a date.
        message_at, message_at_approx = _message_time_evidence(msg)
        cursor.execute(
            """INSERT OR IGNORE INTO messages
               (thread_id, sender, content, message_timestamp, seq, kind,
                message_at, message_at_approx, source_id, sender_confidence,
                raw_timestamp, day_context, time_precision, reply_to_message_id,
                quoted_sender, quoted_sender_confidence, quoted_text, sender_evidence,
                quote_evidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_record.thread_id,
                sender_to_save,
                msg_content_to_save,
                msg.message_timestamp,
                next_seq + idx,
                msg_kind,
                message_at,
                1 if message_at_approx else 0,
                _observed_source_id(msg) or None,
                msg.sender_confidence or "unknown",
                msg.raw_timestamp or msg.message_timestamp,
                msg.day_context or "",
                msg.time_precision or "unknown",
                msg.reply_to_message_id,
                msg.quoted_sender,
                msg.quoted_sender_confidence or "unknown",
                msg.quoted_text,
                msg.sender_evidence,
                msg.quote_evidence,
            )
        )
        if cursor.rowcount > 0:
            messages_added += 1
            # Only a genuine customer turn may move `last_interaction`; a
            # re-scraped "replied to an ad." banner must not.
            if msg.sender == "Customer" and msg_kind == KIND_MESSAGE:
                new_customer_message_added = True
    # Prefer the last real DOM message.  The sidebar can instead reflect an
    # operational event such as an assignment banner, which must not make an
    # old conversation look newly active or reorder the Inbox snapshot.
    last_message_at = None
    for message in reversed(conversation_messages):
        if message.time_precision in {"time_only", "unresolved_day_time", "unresolved_day"}:
            continue
        parsed_message_time = parse_sidebar_time_token(message.message_timestamp or "")
        parsed_at = parsed_message_time.get("parsed_at")
        if parsed_at and " " in parsed_at:
            last_message_at = parsed_at.replace("T", " ")
            break
    if not last_message_at and thread_record.sidebar_timestamp_ms:
        last_message_at = datetime.fromtimestamp(thread_record.sidebar_timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    sidebar_time = parse_sidebar_time_token(thread_record.sidebar_time_text)
    if not last_message_at and sidebar_time.get("parsed_at") and " " in sidebar_time["parsed_at"]:
        last_message_at = sidebar_time["parsed_at"].replace("T", " ")

    cursor.execute('''
        INSERT INTO threads (id, page_id, thread_name, last_synced_time, inbox_sort_index, last_message_at)
        VALUES (?, ?, ?, datetime('now'), ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            thread_name=excluded.thread_name,
            last_synced_time=excluded.last_synced_time,
            -- A targeted detail refresh does not have a sidebar ordinal.
            -- Retain the Stage-1 order in that case.
            inbox_sort_index=COALESCE(excluded.inbox_sort_index, threads.inbox_sort_index),
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
    for msg in reversed(conversation_messages):
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
                    staggered = now - dt_mod.timedelta(minutes=thread_record.dom_index or 0)
                else:
                    staggered = now.replace(hour=23, minute=59, second=59) - dt_mod.timedelta(minutes=thread_record.dom_index or 0)
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
                "source_id": message.source_id,
                "sender_confidence": message.sender_confidence,
                "raw_timestamp": message.raw_timestamp,
                "day_context": message.day_context,
                "time_precision": message.time_precision,
                "reply_to_message_id": message.reply_to_message_id,
                "quoted_sender": message.quoted_sender,
                "quoted_sender_confidence": message.quoted_sender_confidence,
                "quoted_text": message.quoted_text,
                "sender_evidence": message.sender_evidence,
                "quote_evidence": message.quote_evidence,
                "reactions": list(message.reactions),
            }
            for message in mas_handoff.messages
        ],
    }
