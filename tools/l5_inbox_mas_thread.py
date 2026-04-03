import logging
from tools.l5_inbox_mas_pipeline import run_adk_pipeline

logger = logging.getLogger("inbox_mas_thread")

def process_single_thread(cdp_page, page_id: str, thread_id: str,
                          thread_name: str, dry_run: bool = True) -> dict:
    """Process a single thread and draft a reply for human review.

    Args:
        cdp_page: Playwright page connected via CDP.
        page_id: Facebook Page ID.
        thread_id: Thread identifier.
        thread_name: Display name of the thread.
        dry_run: Legacy compatibility flag. Inbox reply handling remains draft-only.

    Returns:
        dict: Processing result.
    """
    from adk_agents.tools.seeker_tools import lookup_seeker, get_thread_messages
    from adk_agents.tools.l5_facebook_tools import (
        navigate_to_thread, send_reply_via_cdp, log_auto_reply,
        commit_reply_via_cdp, clear_composer_via_cdp
    )
    from adk_agents.tools.l5_stage_tools import evaluate_stage_gate
    from fb_pipeline.persistence.l4_sqlite_store import log_mas_decision
    from tools.l5_telegram_hitl import send_proposal_to_telegram


    if not dry_run:
        logger.warning("--live is ignored for inbox replies; drafting only for human review.")
    logger.info(f"[DRAFT] Processing thread: {thread_name}")

    # 1. Get messages from DB
    msg_result = get_thread_messages(thread_id)
    if msg_result["status"] != "success" or msg_result["count"] == 0:
        logger.warning(f"No messages found for thread {thread_name}")
        return {"status": "skipped", "reason": "no_messages"}

    # 2. Lookup seeker in CRM
    seeker = lookup_seeker(thread_id)

    # 3. Run ADK pipeline
    logger.info(f"Running ADK pipeline for {thread_name}...")
    adk_result = run_adk_pipeline(msg_result["messages"], seeker)

    classification = adk_result.get("classification", "")
    reply_text = adk_result.get("reply_text", "")

    logger.info(f"Classification: {classification[:100]}")
    logger.info(f"Generated reply: {reply_text[:100]}")

    if not reply_text:
        logger.warning(f"No reply generated for {thread_name}")
        return {"status": "no_reply", "classification": classification}

    latest_customer_message_timestamp = None
    latest_customer_message_text = ""
    for message in reversed(msg_result["messages"]):
        if message.get("sender") == "Customer":
            latest_customer_message_timestamp = message.get("timestamp")
            latest_customer_message_text = message.get("content", "")
            break

    # 4. Navigate to thread in FB inbox
    if not navigate_to_thread(cdp_page, page_id, thread_name, thread_id):
        logger.warning(f"Could not navigate to thread {thread_name}")
        return {"status": "nav_failed", "reply_text": reply_text}

    # 4.5. Real-time DOM Verification
    # Ensure the SQLite DB isn't stale compared to the real thread.
    # If the human already replied while the LLM was running, abort!
    latest_dom_sender = cdp_page.evaluate('''() => {
        let region = document.querySelector(
            'div[aria-label*="Message list container"], ' +
            'div[role="region"][aria-label*="message"]'
        );
        if (!region) return "Unknown";
        let bubble = region.querySelector('.x1fqp7bg');
        let messageArea = bubble ? bubble.parentElement : (region.querySelector('div.x1yrsyyn') || region);
        let topDivs = Array.from(messageArea.children);
        for (let i = topDivs.length - 1; i >= 0; i--) {
            let div = topDivs[i];
            if (div.classList.contains('x14vqqas') || div.querySelector('.x14vqqas')) continue;
            if (div.classList.contains('xcxhlts') || div.querySelector('.xcxhlts')) continue;
            if (!div.classList.contains('x1fqp7bg') && !div.querySelector('.x1fqp7bg')) continue;
            let htmlStr = (div.outerHTML || "").substring(0, 500);
            if (htmlStr.includes('x13a6bvl')) {
                let text = div.innerText.trim();
                let is_auto = text.includes("Chúng tôi có thể giúp gì cho bạn?") || 
                              text.includes("Bạn để lại Họ tên và Số điện thoại") ||
                              text.includes("Khóa học thiền ở Hà Nội") ||
                              text.includes("Thời gian: 20h-21h30");
                return is_auto ? "Auto_Page" : "Page";
            }
            if (htmlStr.includes('x1nhvcw1')) return "Customer";
            let avatar = div.querySelector('img.img[alt]');
            return avatar ? "Customer" : "Page";
        }
        return "Unknown";
    }''')
    
    if latest_dom_sender == "Page":
        logger.warning(f"DOM Verification Failed: Thread {thread_name} already has a Page reply in the DOM! Aborting.")
        return {"status": "abort_already_replied", "reply_text": reply_text}

    # 5. Draft reply into the composer
    drafted = send_reply_via_cdp(cdp_page, reply_text, dry_run=True)
    if not drafted:
        logger.warning(f"Could not draft reply for {thread_name}")
        return {"status": "draft_failed", "reply_text": reply_text}

    # 6. Log the drafted reply acknowledgement
    log_auto_reply(
        thread_id,
        reply_text,
        agent_name="responder",
        escalated=False,
        dry_run=True,
        customer_message_timestamp=latest_customer_message_timestamp,
    )

    logger.info(f"Reply drafted for {thread_name}: {reply_text[:60]}...")

    stage_result = evaluate_stage_gate(thread_id)
    if stage_result.get("promoted"):
        log_mas_decision(
            page_id,
            "stage_gate",
            "thread",
            thread_id,
            "promoted",
            stage_result.get("reason"),
            dry_run=dry_run,
            payload=stage_result,
        )
        logger.info(
            f"Stage promoted for {thread_name}: "
            f"{stage_result.get('from_stage')} -> {stage_result.get('to_stage')}"
        )

    combined_text = f"User: {latest_customer_message_text}\\n\\nOur Reply: {reply_text}"
    if len(combined_text) > 4000:
        combined_text = combined_text[:4000] + "... (truncated)"

    msg_id = send_proposal_to_telegram(
        route="inbox",
        thread_id=thread_id,
        proposed_text=combined_text,
        payload={
            "classification": classification,
            "msg_messages_json": msg_result["messages"],
            "seeker_dict": seeker,
            "proposals": [{
                "thread_id": thread_id,
                "seeker_name": thread_name,
                "message_text": reply_text
            }]
        }
    )

    if msg_id:
        logger.info(f"### ASYNC INBOX: Proposal {msg_id} queued to HITL DB. Relinquishing CDP tab... ###")


    return {
        "status": "drafted",
        "mode": "draft_only",
        "thread_name": thread_name,
        "classification": classification,
        "reply_text": reply_text,
        "stage_result": stage_result,
        "customer_message_timestamp": latest_customer_message_timestamp,
    }
