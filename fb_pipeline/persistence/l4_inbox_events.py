"""Keep Inbox system/ad/post observations outside the conversational timeline."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fb_pipeline.contracts.l1_message_kind import KIND_SYSTEM_BANNER, classify_message_kind


def source_target(url):
    """An ad id and a post id are different namespaces; never conflate them."""
    parsed = urlparse(url or "")
    if parsed.scheme not in {"https", "http"} or not (
        parsed.hostname == "facebook.com" or (parsed.hostname or "").endswith(".facebook.com")
    ):
        return None, None
    query = parse_qs(parsed.query)
    if parsed.hostname in {"l.facebook.com", "lm.facebook.com"} and query.get("u"):
        return source_target(query["u"][0])
    ad_id = (query.get("ad_id") or [None])[0]
    if ad_id and ad_id.isdigit():
        return "ad", ad_id
    post_id = (query.get("story_fbid") or query.get("fbid") or [None])[0]
    match = re.search(r"/(?:posts|videos)/(\w+)", parsed.path)
    post_id = post_id or (match.group(1) if match else None)
    if post_id and re.fullmatch(r"[\w]+", post_id):
        return "post", post_id
    return None, None


def save_system_events(conn, record, messages):
    """Caller verifies recipient/snapshot first. Does not commit caller's transaction."""
    count = 0
    for message in messages:
        text = message.get("text") or message.get("body") or ""
        if (message.get("kind") or classify_message_kind(text)) != KIND_SYSTEM_BANNER:
            continue
        links = message.get("source_links") or []
        # Keep events without a link too: absence of a target is explicit,
        # and a later resolved link is a separate source observation.
        for link in links or [{}]:
            url = link.get("url") or ""
            target_type, target_id = source_target(url)
            payload = {key: message.get(key) for key in (
                "source_id", "text", "body", "raw_timestamp", "day_context",
                "time_precision", "sender_evidence", "source_links")}
            identity = [record.page_id, record.thread_id, message.get("source_id"), text,
                        message.get("day_context"), message.get("raw_timestamp"), url]
            event_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            conn.execute(
                "INSERT INTO inbox_system_events "
                "(event_id,page_id,thread_id,event_type,target_type,target_id,target_url,observed_at,payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET event_type=excluded.event_type",
                (event_id, record.page_id, record.thread_id, "ad_post_interaction" if target_id or re.search(r"replied to (?:an ad|a post)|đã trả lời.*(?:quảng cáo|bài viết)", text, re.I) else "system_banner",
                 target_type, target_id, url or None, datetime.now(timezone.utc).isoformat(),
                 json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
            if target_type == "ad":
                conn.execute("INSERT INTO user_ad_ids (thread_id,ad_id) VALUES (?,?) "
                             "ON CONFLICT(thread_id,ad_id) DO NOTHING", (record.thread_id, target_id))
            count += 1
    return count
