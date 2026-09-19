#!/usr/bin/env python3
"""Deliver web/Telegram-approved DMs: draft by default, --auto-send to commit."""
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
OUTBOUND_QUEUE_TYPES = ("reply_message", "proactive_message")


def _signal_handler(signum, _frame):
    global _shutdown_requested
    logger.info("Received signal %s. Stopping HITL executor...", signum)
    _shutdown_requested = True


def telegram_poller_job() -> None:
    """Persist Telegram approval/rejection reactions before executor claims work."""
    from tools.l5_telegram_hitl import poll_telegram_updates

    poll_telegram_updates()


def hitl_execution_job(page_id: str, dry_run: bool = True, auto_send: bool = False) -> None:
    """Web approval is sufficient; Telegram polling is an optional input."""
    from tools.l5_action_queue import claim_next_action, finish_action, peek_next_approved
    from tools.l5_delivery_guard import OutdatedAction, set_delivery_result, refresh_outdated_actions, reconcile_drafts

    if not dry_run:
        with scheduler_browser_cycle("[HITL-DRAFTS]", page_id, logger) as may_run:
            if may_run:
                reconcile_drafts(page_id)

    for queue_type in OUTBOUND_QUEUE_TYPES:
        if dry_run:
            # A dry run never claims work or attaches to the browser.
            preview = peek_next_approved(queue_type, page_id=page_id)
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
            item = claim_next_action(queue_type, page_id=page_id)
            if not item:
                continue
            try:
                outcome = _execute_approved_action(item, page_id, dry_run=False, auto_send=auto_send)
            except OutdatedAction as exc:
                set_delivery_result(item, "outdated", f"Out-date: {exc}")
                logger.warning("Action #%s Out-date; targeted refetch requested: %s", item["id"], exc)
            except Exception as exc:
                logger.exception("Action queue item %s failed", item["id"])
                finish_action(item["id"], str(exc))
            else:
                if outcome == "drafted":
                    set_delivery_result(item, "drafted")
                    logger.info("Action #%s drafted in Facebook; waiting for operator Enter", item["id"])
                else:
                    finish_action(item["id"])

    if not dry_run:
        with scheduler_browser_cycle("[HITL-REFETCH]", page_id, logger) as may_run:
            if may_run:
                refresh_outdated_actions(page_id)

def _execute_approved_action(item: dict, fallback_page_id: str, dry_run: bool = False,
                             auto_send: bool = False) -> str:
    if dry_run:
        return "preview"
    if (item.get("status") != "executing" or not item.get("approved_at")
            or item.get("queue_type") not in OUTBOUND_QUEUE_TYPES
            or item.get("page_id") != fallback_page_id):
        raise RuntimeError("Only claimed, human-approved DMs for this Page may execute")
    from playwright.sync_api import sync_playwright
    from fb_pipeline.session.l2_bootstrap import attach_to_authorized_session, stamp_tab_role
    from fb_pipeline.browser.l2_actions import navigate_to_thread
    from tools.l5_delivery_guard import prepare_draft, OutdatedAction, assert_recipient

    if not item.get("payload", {}).get("conversation_snapshot"):
        raise OutdatedAction("missing_context_snapshot: refetch and regenerate the proposal")
    page_id = item["page_id"]
    with sync_playwright() as playwright:
        session = attach_to_authorized_session(playwright, page_id,
            f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}",
            tab_role=f"outbound:{item['id']}")
        try:
            if not navigate_to_thread(session.page, page_id, item.get("target_name") or "", item.get("target_id")):
                raise RuntimeError("Facebook inbox thread could not be opened")
            assert_recipient(session.page, item)
            return prepare_draft(session.page, item, auto_send=auto_send)
        finally:
            stamp_tab_role(session.page, session.tab_role)


def run_hitl_loop(page_id: str, dry_run: bool, interval_seconds: int = 30, auto_send: bool = False) -> None:
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
        try:
            run_cycle(page_id, dry_run, auto_send)
        except Exception:
            logger.exception("HITL cycle failed; retained queue state, retrying next tick")
        remaining = interval_seconds - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)
    logger.info("HITL executor stopped.")


def run_cycle(page_id, dry_run, auto_send=False):
    # Web approvals are processed before any network wait on Telegram.
    hitl_execution_job(page_id, dry_run=dry_run, auto_send=auto_send)
    if not dry_run:
        try:
            telegram_poller_job()
        except Exception:
            logger.exception("Telegram poll failed; web-approved actions will still execute")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run only the human-approved Facebook action executor.")
    parser.add_argument("--page-id", required=True, help="Facebook Page ID or Business Suite URL with asset_id")
    parser.add_argument("--live", action="store_true", help="Open approved Facebook drafts (default: read-only preview)")
    delivery = parser.add_mutually_exclusive_group()
    delivery.add_argument("--draft-only", action="store_true", help="Fill composer without Enter (default)")
    delivery.add_argument("--auto-send", action="store_true", help="Press Enter after recipient and freshness checks")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    parser.add_argument("--once", action="store_true", help="Poll and execute one cycle, then exit")
    args = parser.parse_args()
    page_id = parse_page_id(args.page_id)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    if args.once:
        run_cycle(page_id, not args.live, args.auto_send)
        return
    run_hitl_loop(page_id, dry_run=not args.live, interval_seconds=args.interval, auto_send=args.auto_send)


if __name__ == "__main__":
    main()
