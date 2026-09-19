#!/usr/bin/env python3
"""Poll HITL decisions without sending Facebook DMs.

Approval accepts one draft version for human review; Facebook delivery remains
a manual yogi action. This worker may observe Telegram decisions, but it must
never claim or execute outbound DM items through CDP.
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
# Deliberately empty: automated delivery is disabled by the care contract.
# Keep the loop entry point so deployments do not fail at import time, while
# removing the route that previously turned an approval into a CDP send.
OUTBOUND_QUEUE_TYPES: tuple[str, ...] = ()


def _signal_handler(signum, _frame):
    global _shutdown_requested
    logger.info("Received signal %s. Stopping HITL executor...", signum)
    _shutdown_requested = True


def telegram_poller_job() -> None:
    """Persist Telegram approval/rejection reactions before executor claims work."""
    from tools.l5_telegram_hitl import poll_telegram_updates

    poll_telegram_updates()


def hitl_execution_job(page_id: str, dry_run: bool = True) -> None:
    """Observe the disabled delivery boundary without changing queue state."""
    from tools.l5_action_queue import claim_next_action, finish_action, peek_next_approved

    _ = (page_id, dry_run, claim_next_action, finish_action, peek_next_approved)
    logger.info("HITL delivery is manual-only; no approved action is claimed or sent.")
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
    """Reject direct delivery calls: approval is not authorization to send."""
    _ = (item, fallback_page_id, dry_run)
    raise RuntimeError("Automated Facebook delivery is disabled; send the approved draft manually.")


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
