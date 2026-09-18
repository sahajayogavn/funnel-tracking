#!/usr/bin/env python3
"""Independent executor for human-approved outbound Facebook actions.

This process intentionally owns only the delivery boundary: it polls Telegram
approval reactions, claims approved ``action_queue`` items, and drives the
already-authorized Facebook CDP browser.  It does not fetch Inbox data, run
MAS/ADK, classify seekers, or run proactive scheduler routes.
"""
import argparse
import logging
import os
import signal
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.contracts.l1_inbox import parse_page_id
from fb_pipeline.session.l2_activity_lock import scheduler_browser_cycle

logger = logging.getLogger("hitl_execution")
_shutdown_requested = False
OUTBOUND_QUEUE_TYPES = (
    "reply_message",
    "reply_comment",
    "proactive_comment",
    "proactive_message",
)


def _signal_handler(signum, _frame):
    global _shutdown_requested
    logger.info("Received signal %s. Stopping HITL executor...", signum)
    _shutdown_requested = True


def telegram_poller_job() -> None:
    """Persist Telegram approval/rejection reactions before executor claims work."""
    from tools.l5_telegram_hitl import poll_telegram_updates

    poll_telegram_updates()


def hitl_execution_job(page_id: str, dry_run: bool = True) -> None:
    """Claim and execute only FIFO action-queue items humans approved."""
    from tools.l5_action_queue import claim_next_action, finish_action, peek_next_approved

    for queue_type in OUTBOUND_QUEUE_TYPES:
        if dry_run:
            # A dry run never claims work or attaches to the browser.
            preview = peek_next_approved(queue_type)
            if preview:
                logger.info(
                    "[DRY-RUN] Would execute %s item #%s for '%s': %s",
                    queue_type, preview["id"], preview.get("target_name"),
                    (preview.get("action_text") or "")[:80],
                )
            continue
        # Do not claim while a fetch worker holds the shared browser activity lock.
        with scheduler_browser_cycle("[HITL]", page_id, logger) as may_run:
            if not may_run:
                continue
            item = claim_next_action(queue_type)
            if not item:
                continue
            try:
                _execute_approved_action(item, page_id, dry_run=False)
            except Exception as exc:
                logger.exception("Action queue item %s failed", item["id"])
                finish_action(item["id"], str(exc))
            else:
                finish_action(item["id"])

def _execute_approved_action(item: dict, fallback_page_id: str, dry_run: bool = False) -> None:
    """The sole CDP delivery boundary; approval was already persisted."""
    if item.get("reaction_type"):
        raise RuntimeError("Live Facebook reaction executor is not configured")
    if item["queue_type"] in {"reply_comment", "proactive_comment"}:
        raise RuntimeError("Live Facebook comment executor is not configured")

    from playwright.sync_api import sync_playwright
    from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session, stamp_tab_role
    from fb_pipeline.browser.l2_actions import navigate_to_thread, send_reply_via_cdp, commit_reply_via_cdp

    page_id = item.get("page_id") or fallback_page_id
    with sync_playwright() as playwright:
        session = attach_to_authorized_session(
            playwright, page_id, f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
            tab_role=f"outbound:{item['id']}",
        )
        try:
            if not navigate_to_thread(session.page, page_id, item.get("target_name") or "", item.get("target_id")):
                raise RuntimeError("Facebook inbox thread could not be opened")
            if not send_reply_via_cdp(session.page, item.get("action_text") or "", dry_run=dry_run):
                raise RuntimeError("Facebook composer could not be filled")
            if dry_run:
                logger.info("[DRY-RUN] Skipping send for action queue item %s", item["id"])
                return
            if not commit_reply_via_cdp(session.page):
                raise RuntimeError("Facebook message could not be sent")
            if item["queue_type"] == "reply_message":
                from adk_agents.tools.l5_facebook_tools import log_auto_reply

                log_auto_reply(
                    item.get("target_id") or "", item.get("action_text") or "",
                    agent_name="human_approved_executor", dry_run=False,
                    customer_message_timestamp=item.get("payload", {}).get("customer_message_timestamp"),
                )
        finally:
            # Full Facebook navigations wipe the role marker. Re-stamp before
            # the next poll so this action reuses its tab instead of creating a
            # growing set of fresh tabs with partially initialized Meta state.
            stamp_tab_role(session.page, session.tab_role)


def run_hitl_loop(page_id: str, dry_run: bool, interval_seconds: int = 30) -> None:
    """Run the independent HITL worker until SIGINT/SIGTERM."""
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be at least 1")
    global _shutdown_requested
    _shutdown_requested = False
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    logger.info("HITL executor started: page=%s mode=%s interval=%ss", page_id, "dry-run" if dry_run else "live", interval_seconds)
    while not _shutdown_requested:
        started_at = time.monotonic()
        telegram_poller_job()
        hitl_execution_job(page_id, dry_run=dry_run)
        remaining = interval_seconds - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)
    logger.info("HITL executor stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run only the human-approved Facebook action executor.")
    parser.add_argument("--page-id", required=True, help="Facebook Page ID or Business Suite URL with asset_id")
    parser.add_argument("--live", action="store_true", help="Execute approved actions in Facebook (default: dry-run)")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    parser.add_argument("--once", action="store_true", help="Poll and execute one cycle, then exit")
    args = parser.parse_args()
    page_id = parse_page_id(args.page_id)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    if args.once:
        telegram_poller_job()
        hitl_execution_job(page_id, dry_run=not args.live)
        return
    run_hitl_loop(page_id, dry_run=not args.live, interval_seconds=args.interval)


if __name__ == "__main__":
    main()
