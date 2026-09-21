"""Evidence checks for human-approved DM delivery. No model calls.

# code:hitl-delivery-guard-001
"""
import json
import unicodedata
from urllib.parse import parse_qs, urlparse

from fb_pipeline.browser.inbox.constants import MESSAGE_REGION_SELECTOR
from fb_pipeline.browser.inbox.thread_detail_parser import extract_thread_messages

COMPOSER = 'div[role="textbox"][contenteditable="true"]'
DELIVERY_SNAPSHOT_TAIL = 1


class OutdatedAction(RuntimeError):
    pass


def _text(value):
    return unicodedata.normalize("NFC", str(value or "")).replace("\u200b", "").strip()


def conversation_snapshot(messages, *, complete=False, latest_count=DELIVERY_SNAPSHOT_TAIL):
    """Freeze only the latest delivery-relevant messages from a fetched transcript.

    Messenger virtualizes older history, so delivery cannot prove that its DOM
    currently materializes the same full transcript used by MAS.  A draft is
    instead valid while the latest stable Facebook event(s) are unchanged.
    """
    result = []
    for message in messages:
        if message.get("kind", "message") != "message":
            continue
        body = _text(message.get("content", message.get("body", message.get("text"))))
        if not body:
            continue
        result.append({
            "body": body,
            "sender": _text(message.get("sender") or "Unknown"),
            "source_id": _text(message.get("source_id")),
            "raw_timestamp": _text(message.get("raw_timestamp") or message.get("message_timestamp") or message.get("timestamp")),
            "day_context": _text(message.get("day_context")),
            "quoted_text": _text(message.get("quoted_text")),
            "reply_to_message_id": _text(message.get("reply_to_message_id")),
        })
    if latest_count is not None:
        if latest_count < 1:
            raise ValueError("latest_count must be positive or None")
        result = result[-latest_count:]
    return {
        "version": 2,
        "messages": result,
        "complete": complete,
        "latest_count": latest_count,
    }


def assert_context_current(snapshot, messages):
    expected = (snapshot or {}).get("messages")
    if not expected or snapshot.get("version") not in (1, 2):
        raise OutdatedAction("missing_context_snapshot: fetch and regenerate MAS proposal")
    # Version 1 snapshots were full DB histories.  Keep only their final event
    # too: the browser may correctly expose a virtualized tail rather than the
    # same full representation.  New v2 snapshots state this scope explicitly.
    latest_count = snapshot.get("latest_count") or DELIVERY_SNAPSHOT_TAIL
    expected = expected[-latest_count:]
    if not expected[-1].get("source_id"):
        raise OutdatedAction("unverifiable_context: latest message has no stable Facebook ID")
    observed = conversation_snapshot(messages, latest_count=latest_count)["messages"]
    if len(observed) != len(expected) or observed != expected:
        raise OutdatedAction("conversation_changed: new, edited, missing or unverifiable messages")


def assert_recipient(page, item):
    """Require the exact Page-scoped Facebook PSID in the live Inbox URL.

    A Facebook display name is editable and is not delivery identity evidence.
    ``asset_id`` scopes the Page and ``selected_item_id`` scopes the recipient.
    """
    page_id, thread_id = str(item.get("page_id") or ""), str(item.get("target_id") or "")
    prefix = page_id + "_"
    psid = thread_id[len(prefix):] if thread_id.startswith(prefix) else ""
    url = urlparse(page.url)
    query = parse_qs(url.query)
    if (url.hostname != "business.facebook.com" or not psid.isdigit()
            or len(psid) > 16 or query.get("asset_id") != [page_id]
            or query.get("selected_item_id") != [psid]):
        raise RuntimeError("recipient_identity_unverified: exact Page and Facebook thread ID required")


def read_live_messages(page, item):
    assert_recipient(page, item)
    # Inspect the latest end, not whichever history segment happens to be visible.
    at_bottom = page.evaluate("""selector => {
        const region = document.querySelector(selector);
        if (!region) return false;
        let scroller = region;
        while (scroller && !(scroller.scrollHeight > scroller.clientHeight && scroller.clientHeight > 100))
            scroller = scroller.parentElement;
        if (!scroller || scroller === document.body) scroller = region;
        scroller.scrollTop = scroller.scrollHeight;
        return scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop < 4;
    }""", MESSAGE_REGION_SELECTOR)
    if not at_bottom:
        raise RuntimeError("Cannot verify the latest end of the conversation")
    page.wait_for_timeout(400)
    messages = extract_thread_messages(page)
    assert_recipient(page, item)
    if not messages:
        raise RuntimeError("Empty live transcript; refusing to draft")
    return messages


def prepare_draft(page, item, *, auto_send=False):
    """Guard, fill one scoped composer, guard again, optionally commit."""
    snapshot = item.get("payload", {}).get("conversation_snapshot")
    messages = read_live_messages(page, item)
    assert_context_current(snapshot, messages)
    text = (item.get("action_text") or "").replace("\r", "")
    if not text.strip():
        raise RuntimeError("Empty approved draft")
    box = page.locator(COMPOSER + ':visible')
    if box.count() != 1 or box.inner_text().strip():
        raise RuntimeError("Composer is ambiguous or already contains an operator draft")
    # fill inserts text without keyboard Enter, including for multiline drafts.
    box.fill(text)
    try:
        assert_context_current(snapshot, read_live_messages(page, item))
        if _text(box.inner_text()) != _text(text):
            raise RuntimeError("Composer text changed after filling")
        assert_recipient(page, item)
        if auto_send:
            box.press("Enter")
            # Enter alone is not delivery confirmation. Require a new matching
            # Page bubble and an empty composer, otherwise leave uncertain.
            for _ in range(10):
                page.wait_for_timeout(500)
                latest = read_live_messages(page, item)
                if (not box.inner_text().strip() and latest[-1].get("sender") == "Page"
                        and _text(latest[-1].get("text", latest[-1].get("content"))) == _text(text)
                        and conversation_snapshot(latest) != conversation_snapshot(messages)):
                    assert_context_current(snapshot, latest[:-1])
                    record_mas_delivery(item, latest[-1], "auto_send")
                    return "sent"
            raise RuntimeError("send_unconfirmed: Enter was pressed; verify Facebook before retrying")
        page.bring_to_front()
        return "drafted"
    except Exception:
        # Remove only our unchanged unsent text, and only in the same verified
        # recipient. Never clear an operator's edits or another conversation.
        try:
            assert_recipient(page, item)
            if _text(box.inner_text()) == _text(text):
                box.fill("")
        except Exception:
            pass
        raise


def set_delivery_result(item, outcome, reason=""):
    """Persist draft handoff separately from confirmed sending, without a schema rebuild."""
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    payload = dict(item.get("payload") or {})
    payload["delivery_status"] = outcome
    if outcome == "outdated":
        payload["fetch_request"] = "pending"
    status = {"drafted": "executing", "outdated": "rejected"}[outcome]
    conn = get_db_connection()
    try:
        conn.execute(
            """UPDATE action_queue SET status=?, payload_json=?, error_text=?, updated_at=datetime('now')
               WHERE id=? AND status='executing'""",
            (status, json.dumps(payload, ensure_ascii=False), reason or None, item["id"]),
        )
        conn.commit()
    finally:
        conn.close()


def record_mas_delivery(item, message, send_mode):
    """Record the actual Facebook message id of a delivered MAS proposal."""
    if (item.get("payload") or {}).get("source") != "inbox_mas":
        return
    source_id = _text(message.get("source_id"))
    if not source_id:
        return
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO mas_message_provenance
               (message_source_id, page_id, thread_id, action_queue_id, send_mode)
               VALUES (?, ?, ?, ?, ?)""",
            (source_id, item["page_id"], item["target_id"], item["id"], send_mode),
        )
        conn.commit()
    finally:
        conn.close()


def refresh_outdated_actions(page_id):
    """Consume durable targeted fetch requests. Retry failed fetches next tick.

    Caller owns the shared browser lock. No MAS rerun/approval is implied.
    """
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    from fb_pipeline.contracts.l1_inbox import ThreadRecord
    from fb_pipeline.inbox.l3_pipeline import enrich_thread_record, persist_thread_record
    from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session, stamp_tab_role
    from fb_pipeline.browser.l2_actions import navigate_to_thread
    from playwright.sync_api import sync_playwright
    import logging
    logger = logging.getLogger("hitl_execution")
    conn = get_db_connection()
    try:
        rows = conn.execute("""SELECT * FROM action_queue WHERE page_id=? AND status='rejected'
            AND json_extract(payload_json, '$.fetch_request')='pending' ORDER BY id LIMIT 5""", (page_id,)).fetchall()
        if not rows:
            return
        with sync_playwright() as playwright:
            for row in rows:
                item = dict(row)
                payload = json.loads(item["payload_json"])
                item["payload"] = payload
                session = None
                try:
                    session = attach_to_authorized_session(playwright, page_id,
                        f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
                        tab_role=f"hitl_refresh:{item['id']}")
                    if not navigate_to_thread(session.page, page_id, item["target_name"], item["target_id"]):
                        raise RuntimeError("Targeted refresh could not open thread")
                    assert_recipient(session.page, item)
                    # This is a tail refresh; read_live_messages goes to the
                    # bottom. Scanning all history first is wasteful and does
                    # not prove completeness of a virtualized conversation.
                    messages = read_live_messages(session.page, item)
                    from fb_pipeline.contracts.l1_fetch_integrity import check_snapshot, compare_snapshots, compare_stored
                    issues = check_snapshot(messages)
                    if not issues:
                        issues.extend(compare_snapshots(messages, read_live_messages(session.page, item)))
                    if not issues:
                        source_ids = [m["source_id"] for m in messages]
                        placeholders = ",".join("?" for _ in source_ids)
                        saved = conn.execute(
                            "SELECT m.source_id, m.thread_id, m.sender, m.sender_confidence, m.message_at, m.time_precision "
                            "FROM messages m JOIN threads t ON t.id=m.thread_id "
                            f"WHERE t.page_id=? AND m.source_id IN ({placeholders})",
                            (page_id, *source_ids),
                        ).fetchall()
                        issues.extend(compare_stored(messages, [dict(r) for r in saved], item["target_id"]))
                    if issues:
                        payload["fetch_request"] = "needs_review"
                        raise RuntimeError("fetch_integrity_failed: " + json.dumps(issues, ensure_ascii=False))
                    record = ThreadRecord(page_id, item["target_id"], payload.get("recipient_name") or item["target_name"], "", [], None,
                                          selected_item_id=item["target_id"].split("_")[-1])
                    enriched = enrich_thread_record(record, messages,
                        extract_user_info=lambda *_: {"phone": None, "email": None},
                        fb_url=record.selected_item_id)
                    persist_thread_record(conn, enriched)
                    payload["fetch_request"] = "completed"
                    payload.pop("fetch_error", None)
                    logger.info("Refetched thread %s for Out-date action #%s; regenerate MAS on web", item["target_id"], item["id"])
                except Exception as exc:
                    payload["fetch_error"] = str(exc)
                    logger.exception("Targeted refetch pending for Out-date action #%s", item["id"])
                finally:
                    if session:
                        stamp_tab_role(session.page, session.tab_role)
                conn.execute("UPDATE action_queue SET payload_json=?, updated_at=datetime('now') WHERE id=?",
                             (json.dumps(payload, ensure_ascii=False), item["id"]))
                conn.commit()
    finally:
        conn.close()


def reconcile_drafts(page_id):
    """Observe handed-off tabs; never navigate them or press Enter."""
    from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
    from fb_pipeline.session.l2_bootstrap import connect_to_cdp_browser
    from tools.l5_action_queue import finish_action
    from playwright.sync_api import sync_playwright
    import logging
    logger = logging.getLogger("hitl_execution")
    conn = get_db_connection()
    try:
        rows = conn.execute("""SELECT * FROM action_queue WHERE page_id=? AND status='executing'
            AND json_extract(payload_json, '$.delivery_status')='drafted'""", (page_id,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return
    with sync_playwright() as playwright:
        browser = connect_to_cdp_browser(playwright)
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload_json"])
            try:
                page = next((tab for context in browser.contexts for tab in context.pages
                    if tab.evaluate("document.documentElement.dataset.masTabRole") == f"outbound:{item['id']}"), None)
                if page is None:
                    logger.warning("Draft #%s tab unavailable; manual verification required", item["id"])
                    continue
                messages = read_live_messages(page, item)
                box = page.locator(COMPOSER + ':visible')
                snapshot = item["payload"]["conversation_snapshot"]
                # Confirm exactly one appended approved message; unknown sender
                # or edited operator text stays awaiting manual verification.
                if (not box.inner_text().strip() and messages[-1].get("sender") == "Page"
                        and _text(messages[-1].get("text", messages[-1].get("content"))) == _text(item["action_text"])):
                    assert_context_current(snapshot, messages[:-1])
                    record_mas_delivery(item, messages[-1], "human_enter")
                    finish_action(item["id"])
                    logger.info("Confirmed manual send for action #%s", item["id"])
                    continue
                try:
                    assert_context_current(snapshot, messages)
                except OutdatedAction as exc:
                    if _text(box.inner_text()) == _text(item["action_text"]):
                        assert_recipient(page, item)
                        box.fill("")
                    set_delivery_result(item, "outdated", f"Out-date: {exc}")
            except Exception:
                logger.exception("Draft #%s requires verification; not resending", item["id"])
