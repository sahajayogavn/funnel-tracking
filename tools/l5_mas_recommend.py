# code:tool-mas-recommend-001:cli
"""MAS-backed recommendation generator for user-selected seeker threads.

Thin CLI entrypoint that the web dashboard (`POST /api/action-queue/recommendations`)
spawns when the operator clicks "⚡ Chạy đề xuất MAS". Unlike
`l5_inbox_mas_runner.py` (which scans the whole inbox via CDP), this tool:

- takes an explicit list of thread IDs chosen in the UI,
- NEVER opens a browser,
- runs the real ADK agents (BatchInboxAgent / WarmUpComposer / EventAdvertiser),
- sanitizes every reply with `_sanitize_reply()`,
- and ONLY enqueues proposals into `action_queue` with status='pending'
  (payload.source = 'inbox_mas') for human approval.

Usage:
    .venv/bin/python tools/l5_mas_recommend.py \
        --thread-ids 1548373332058326_1000,1548373332058326_2000 \
        --type all            # reply | warmup | event | all
        [--regenerate]        # replace the current pending/approved draft

Output: a single JSON document on stdout. All logs go to stderr.
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
from tools.l5_action_queue import active_proposal_status, enqueue_action, replace_action
from tools.l5_inbox_mas_context import load_knowledge_context, setup_llm_env
from tools.l5_inbox_mas_pipeline import _sanitize_reply, run_adk_batch_pipeline
from tools.l5_scheduler_adk import run_adk_event_advertiser, run_adk_warmup_composer
from adk_agents.tools.l5_event_tools import get_upcoming_events
from adk_agents.tools.l5_seeker_tools import get_thread_messages, lookup_seeker
from adk_agents.tools.l5_warmup_tools import select_warmup_strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("tools.mas_recommend")

DEFAULT_PAGE_ID = "1548373332058326"
SOURCE = "inbox_mas"
MAX_MESSAGES_PER_THREAD = 15
VALID_TYPES = ("all", "reply", "warmup", "event")


# code:tool-mas-recommend-001:pending-guard
# `active_proposal_status` (tools/l5_action_queue.py) is the single source of
# truth for the "one live proposal per target+queue_type" invariant: it ignores
# only the terminal statuses (executed/rejected/failed), so an *approved*
# proposal still blocks a duplicate the same way a *pending* one does.
#
# `--regenerate` relaxes this for pending/approved drafts: the operator picked
# an existing queue item and wants MAS to try again. The old draft is only
# superseded once a sanitized new draft exists (`replace_action` swaps them in
# one transaction), so a failed LLM call never leaves the seeker with no
# proposal. An `executing` draft is never replaced: the worker already owns it.


def _guard(thread_id: str, queue_type: str, kind: str, regenerate: bool,
           payload_type: Optional[str] = None) -> Optional[str]:
    """Skip reason for `thread_id`, or None when MAS may produce a new draft."""
    status = active_proposal_status(thread_id, queue_type, payload_type)
    if status is None:
        return None
    if status == "executing":
        return f"executing_{kind}_exists"
    return None if regenerate else f"pending_{kind}_exists"


def _enqueue(regenerate: bool, **kwargs: Any) -> tuple[int, list[int]]:
    """enqueue_action, or replace_action when regenerating. Returns (id, superseded_ids)."""
    if not regenerate:
        return enqueue_action(**kwargs), []
    new_id, old = replace_action(**kwargs)
    if old:
        logger.info("Regenerated %s for %s: #%s supersedes %s", kwargs["queue_type"], kwargs["target_id"], new_id, old)
    return new_id, old


def _days_dormant(seeker: dict) -> int:
    last = seeker.get("last_interaction")
    if not last:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return max(0, (datetime.now() - datetime.strptime(str(last)[:19], fmt)).days)
        except ValueError:
            continue
    return 0


def _load_thread(thread_id: str) -> Optional[dict]:
    """Assemble seeker profile + recent messages for one thread. None if no messages."""
    msg_result = get_thread_messages(thread_id)
    if msg_result.get("status") != "success" or not msg_result.get("messages"):
        logger.warning("No messages found for thread %s", thread_id)
        return None
    seeker = lookup_seeker(thread_id)
    messages = msg_result["messages"][-MAX_MESSAGES_PER_THREAD:]
    thread_name = seeker.get("name") or _thread_name_from_db(thread_id) or "Seeker"
    return {
        "thread_id": thread_id,
        "thread_name": thread_name,
        "seeker": seeker,
        "messages": messages,
        "latest_customer_timestamp": next(
            (m.get("timestamp") for m in reversed(msg_result["messages"]) if m.get("sender") == "Customer"),
            None,
        ),
    }


def _thread_name_from_db(thread_id: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT thread_name FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return row["thread_name"] if row else None
    finally:
        conn.close()


def _proposal(action_id: int, queue_type: str, thread: dict, text: str, kind: str,
              superseded: Optional[list[int]] = None) -> dict:
    return {
        "id": action_id,
        "queueType": queue_type,
        "targetId": thread["thread_id"],
        "targetName": thread["thread_name"],
        "actionText": text,
        "kind": kind,
        "supersededIds": superseded or [],
    }


# code:tool-mas-recommend-001:reply
def recommend_replies(threads: list[dict], page_id: str, regenerate: bool = False) -> tuple[list[dict], list[dict]]:
    """Run BatchInboxAgent once for all threads and enqueue reply_message proposals."""
    created: list[dict] = []
    skipped: list[dict] = []
    batch: list[dict] = []
    for t in threads:
        reason = _guard(t["thread_id"], "reply_message", "reply", regenerate)
        if reason:
            skipped.append({"threadId": t["thread_id"], "reason": reason})
        else:
            batch.append(t)
    if not batch:
        return created, skipped

    logger.info("Running BatchInboxAgent for %d thread(s)", len(batch))
    results = run_adk_batch_pipeline(batch)
    by_id = {r.get("thread_id"): r for r in results if isinstance(r, dict) and r.get("thread_id")}

    for t in batch:
        llm = by_id.get(t["thread_id"])
        reply_text = _sanitize_reply((llm or {}).get("reply_text", "") or "")
        classification = (llm or {}).get("classification", "")
        if not reply_text:
            skipped.append({"threadId": t["thread_id"], "reason": "no_reply"})
            continue
        if reply_text.strip() == "[OUT_OF_SCOPE]":
            skipped.append({"threadId": t["thread_id"], "reason": "out_of_scope", "classification": classification})
            continue
        action_id, old = _enqueue(
            regenerate,
            queue_type="reply_message", page_id=page_id, target_type="thread",
            target_id=t["thread_id"], target_name=t["thread_name"], action_text=reply_text,
            payload={
                "source": SOURCE,
                "trigger": "manual_recommendation",
                "classification": classification,
                "customer_message_timestamp": t["latest_customer_timestamp"],
                "seeker": t["seeker"],
            },
        )
        created.append(_proposal(action_id, "reply_message", t, reply_text, "reply", old))
    return created, skipped


# code:tool-mas-recommend-001:warmup
def recommend_warmups(threads: list[dict], page_id: str, knowledge_context: str,
                      regenerate: bool = False) -> tuple[list[dict], list[dict]]:
    """Run WarmUpComposer per thread and enqueue proactive_message (type=warmup)."""
    created: list[dict] = []
    skipped: list[dict] = []
    for t in threads:
        reason = _guard(t["thread_id"], "proactive_message", "warmup", regenerate, "warmup")
        if reason:
            skipped.append({"threadId": t["thread_id"], "reason": reason})
            continue
        seeker = t["seeker"]
        stage = seeker.get("lead_stage") or "Intake"
        # Operator picked this seeker explicitly, so ignore the "too soon" gate
        # that the scheduler applies and fall back to a manual strategy.
        strategy = select_warmup_strategy(stage, _days_dormant(seeker)) or {"type": "manual_warmup", "cool_step": None}
        text = _sanitize_reply(run_adk_warmup_composer(seeker, strategy, knowledge_context))
        if not text:
            skipped.append({"threadId": t["thread_id"], "reason": "no_reply"})
            continue
        action_id, old = _enqueue(
            regenerate,
            queue_type="proactive_message", page_id=page_id, target_type="thread",
            target_id=t["thread_id"], target_name=t["thread_name"], action_text=text,
            payload={
                "source": SOURCE, "type": "warmup", "stage": stage,
                "city": seeker.get("city"), "strategy": strategy.get("type"),
            },
        )
        created.append(_proposal(action_id, "proactive_message", t, text, "warmup", old))
    return created, skipped


def _pick_event(city: Optional[str]) -> Optional[dict]:
    upcoming = get_upcoming_events(city=city if city and city != "all" else None)
    if upcoming.get("status") == "success" and upcoming.get("events"):
        return upcoming["events"][0]
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, name, city, event_date, description FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# code:tool-mas-recommend-001:event
def recommend_events(threads: list[dict], page_id: str, knowledge_context: str, city: Optional[str],
                     regenerate: bool = False) -> tuple[list[dict], list[dict]]:
    """Run EventAdvertiser per thread and enqueue proactive_message (type=event)."""
    created: list[dict] = []
    skipped: list[dict] = []
    event = _pick_event(city)
    if not event:
        logger.warning("No event found in `events` table; skipping event recommendations")
        return created, [{"threadId": t["thread_id"], "reason": "no_event"} for t in threads]
    for t in threads:
        reason = _guard(t["thread_id"], "proactive_message", "event", regenerate, "event")
        if reason:
            skipped.append({"threadId": t["thread_id"], "reason": reason})
            continue
        text = _sanitize_reply(run_adk_event_advertiser(event, t["seeker"], knowledge_context))
        if not text:
            skipped.append({"threadId": t["thread_id"], "reason": "no_reply"})
            continue
        action_id, old = _enqueue(
            regenerate,
            queue_type="proactive_message", page_id=page_id, target_type="thread",
            target_id=t["thread_id"], target_name=t["thread_name"], action_text=text,
            payload={
                "source": SOURCE, "type": "event", "event_id": event.get("id"),
                "eventTitle": event.get("name"), "city": event.get("city"),
            },
        )
        created.append(_proposal(action_id, "proactive_message", t, text, "event", old))
    return created, skipped


# code:tool-mas-recommend-001:llm-preflight
def _check_llm_reachable(timeout: float = 8.0) -> Optional[str]:
    """Return an error string if the OpenAI-compatible endpoint is definitely down.

    Lets the web route fall back to templates immediately instead of waiting on
    LiteLLM retries and then reporting every thread as `no_reply`. Only hard
    failures (DNS, connection refused) count; HTTP errors or a slow handshake
    mean the host is up, so we proceed and let the real call decide.
    """
    import socket
    import urllib.error
    import urllib.request

    base = os.environ["OPENAI_API_BASE"].rstrip("/")
    req = urllib.request.Request(
        f"{base}/models",
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "User-Agent": os.environ.get("LLM_USER_AGENT", "sahajayoga-mas/1.0"),
        },
    )
    try:
        urllib.request.urlopen(req, timeout=timeout).read(1)
        return None
    except urllib.error.HTTPError:
        return None  # server answered (even 4xx) → reachable
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
            return f"LLM endpoint unreachable ({base}): {reason}"
        logger.warning("LLM preflight inconclusive (%s); proceeding", reason)
        return None
    except (TimeoutError, socket.timeout) as exc:
        logger.warning("LLM preflight timed out (%s); proceeding", exc)
        return None
    except Exception as exc:
        return f"LLM endpoint unreachable ({base}): {exc}"


# code:tool-mas-recommend-001:run
def run(thread_ids: list[str], rec_type: str = "all", page_id: str = DEFAULT_PAGE_ID,
        city: Optional[str] = None, regenerate: bool = False) -> dict:
    setup_llm_env()
    if not os.environ.get("OPENAI_API_BASE") or not os.environ.get("OPENAI_API_KEY"):
        return {"status": "error", "error": "LLM credentials missing (OPENAI_API_BASE/OPENAI_API_KEY)"}
    reachability_error = _check_llm_reachable()
    if reachability_error:
        return {"status": "error", "error": reachability_error}

    threads = [t for t in (_load_thread(tid) for tid in thread_ids) if t]
    missing = [tid for tid in thread_ids if tid not in {t["thread_id"] for t in threads}]
    if not threads:
        return {"status": "error", "error": "No messages found for the selected threads", "missing": missing}

    knowledge_context = load_knowledge_context()
    proposals: list[dict] = []
    skipped: list[dict] = [{"threadId": tid, "reason": "no_messages"} for tid in missing]

    if rec_type in ("all", "reply"):
        c, s = recommend_replies(threads, page_id, regenerate)
        proposals += c; skipped += s
    if rec_type in ("all", "warmup"):
        c, s = recommend_warmups(threads, page_id, knowledge_context, regenerate)
        proposals += c; skipped += s
    if rec_type in ("all", "event"):
        c, s = recommend_events(threads, page_id, knowledge_context, city, regenerate)
        proposals += c; skipped += s

    return {
        "status": "ok",
        "engine": SOURCE,
        "count": len(proposals),
        "proposals": proposals,
        "skipped": skipped,
        "supersededCount": sum(len(p["supersededIds"]) for p in proposals),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MAS agents for selected threads and enqueue HITL proposals")
    parser.add_argument("--thread-ids", required=True, help="Comma-separated thread IDs")
    parser.add_argument("--type", default="all", choices=VALID_TYPES)
    parser.add_argument("--page-id", default=DEFAULT_PAGE_ID)
    parser.add_argument("--city", default=None)
    parser.add_argument("--regenerate", action="store_true",
                        help="Replace the seeker's current pending/approved draft with a fresh one")
    args = parser.parse_args()

    thread_ids = [t.strip() for t in args.thread_ids.split(",") if t.strip()]
    try:
        result = run(thread_ids, args.type, args.page_id, args.city, args.regenerate)
    except Exception as exc:  # surface as JSON so the web route can fall back
        logger.exception("MAS recommendation failed")
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
