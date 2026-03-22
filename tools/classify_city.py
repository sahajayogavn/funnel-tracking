#!/usr/bin/env python3
"""
CLI tool: LLM-based city classification for seekers.
code:tool-citydetect-001:cli

Batch-classify or single-classify users' cities using an LLM
instead of keyword matching. Reads signals from FrankenSQLite
and calls the OpenAI-compatible API.

Usage:
    # Preview mode (dry-run, no DB writes)
    python tools/classify_city.py --action preview --limit 5

    # Classify all users with Unknown city
    python tools/classify_city.py --action classify_all

    # Classify one specific user
    python tools/classify_city.py --action classify_user --thread-id <thread_id>

    # Force re-classify all users (including those already classified)
    python tools/classify_city.py --action classify_all --force

Requires: OPENAI_COMPATIBLE_URL, OPENAI_COMPATIBLE_KEY, OPENAI_COMPATIBLE_MODELS
in .env (Base64-encoded, decoded via env_manager.py).
"""
import argparse
import json
import logging
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.env_manager import load_credentials
from fb_pipeline.contracts.l1_city_llm import detect_city_llm, gather_signals_for_user
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

# code:tool-citydetect-001:logging
os.makedirs('./logs/', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('./logs/classify_city.log'),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger("classify_city")


# code:tool-citydetect-001:get-llm-config
def get_llm_config() -> dict:
    """Load LLM credentials from .env via env_manager."""
    creds = load_credentials()
    api_base = creds.get("OPENAI_COMPATIBLE_URL") or os.environ.get("OPENAI_API_BASE", "")
    api_key = creds.get("OPENAI_COMPATIBLE_KEY") or os.environ.get("OPENAI_API_KEY", "")
    model = creds.get("OPENAI_COMPATIBLE_MODELS") or os.environ.get("ADK_MODEL", "gpt-5.4")

    if not api_base or not api_key:
        raise RuntimeError(
            "LLM credentials not found. Ensure OPENAI_COMPATIBLE_URL and "
            "OPENAI_COMPATIBLE_KEY are set in .env (Base64-encoded)."
        )

    return {"api_base": api_base, "api_key": api_key, "model": model}


# code:tool-citydetect-001:classify-one
def classify_user(conn, thread_id: str, llm_config: dict, dry_run: bool = True) -> dict:
    """Classify city for one user and optionally update DB.

    Returns dict: {thread_id, thread_name, old_city, new_city, confidence, reasoning, updated}
    """
    cursor = conn.cursor()
    cursor.execute("SELECT thread_name, city FROM users WHERE thread_id = ?", (thread_id,))
    user_row = cursor.fetchone()
    if not user_row:
        return {"thread_id": thread_id, "error": "User not found"}

    old_city = user_row["city"]
    signals = gather_signals_for_user(conn, thread_id)

    result = detect_city_llm(
        thread_name=signals["thread_name"],
        customer_messages=signals["customer_messages"],
        page_messages=signals["page_messages"],
        ad_content=signals["ad_content"],
        api_base=llm_config["api_base"],
        api_key=llm_config["api_key"],
        model=llm_config["model"],
    )

    new_city = result["city"]
    updated = False

    if not dry_run and new_city != "Unknown" and new_city != old_city:
        cursor.execute(
            "UPDATE users SET city = ? WHERE thread_id = ?",
            (new_city, thread_id)
        )
        conn.commit()
        updated = True
        logger.info(f"Updated {signals['thread_name']}: {old_city} → {new_city}")

    return {
        "thread_id": thread_id,
        "thread_name": signals["thread_name"],
        "old_city": old_city,
        "new_city": new_city,
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
        "updated": updated,
        "signals": {
            "customer_msg_count": len(signals["customer_messages"]),
            "page_msg_count": len(signals["page_messages"]),
            "has_ad_content": bool(signals["ad_content"]),
        },
    }


# code:tool-citydetect-001:classify-batch
def classify_all(conn, llm_config: dict, dry_run: bool = True,
                 force: bool = False, limit: int = 0) -> dict:
    """Batch classify all users.

    Args:
        conn: DB connection
        llm_config: LLM API config
        dry_run: If True, don't write to DB
        force: If True, re-classify users who already have a city
        limit: Max users to process (0 = all)

    Returns:
        dict with summary stats and per-user results
    """
    cursor = conn.cursor()

    if force:
        query = "SELECT thread_id FROM users ORDER BY last_interaction DESC"
    else:
        query = "SELECT thread_id FROM users WHERE city = 'Unknown' ORDER BY last_interaction DESC"

    if limit > 0:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    thread_ids = [r["thread_id"] for r in cursor.fetchall()]

    logger.info(f"classify_all: {len(thread_ids)} users to classify (force={force}, dry_run={dry_run})")

    results = []
    updated_count = 0
    error_count = 0

    for i, tid in enumerate(thread_ids):
        logger.info(f"[{i+1}/{len(thread_ids)}] Classifying {tid}...")
        try:
            result = classify_user(conn, tid, llm_config, dry_run=dry_run)
            results.append(result)
            if result.get("updated"):
                updated_count += 1
            # Rate limiting: small delay between API calls
            if i < len(thread_ids) - 1:
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error classifying {tid}: {e}")
            results.append({"thread_id": tid, "error": str(e)})
            error_count += 1

    # Summary
    city_distribution = {}
    for r in results:
        city = r.get("new_city", "Error")
        city_distribution[city] = city_distribution.get(city, 0) + 1

    changes = [r for r in results if r.get("old_city") != r.get("new_city") and r.get("new_city") != "Unknown"]

    return {
        "action": "classify_all",
        "total_users": len(thread_ids),
        "updated": updated_count,
        "errors": error_count,
        "dry_run": dry_run,
        "force": force,
        "city_distribution": city_distribution,
        "changes": changes,
        "results": results,
    }


# code:tool-citydetect-001:main
def main():
    parser = argparse.ArgumentParser(
        description="LLM-based city classification for seekers (code:tool-citydetect-001)"
    )
    parser.add_argument("--action", required=True,
                        choices=["classify_all", "classify_user", "preview"],
                        help="Action to perform")
    parser.add_argument("--thread-id", type=str, default=None,
                        help="Thread ID for classify_user action")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Preview mode: show results without DB writes")
    parser.add_argument("--force", action="store_true", default=False,
                        help="Re-classify users who already have a city assigned")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max users to process (0 = all)")

    args = parser.parse_args()

    # Preview = classify_all + dry_run + limit
    if args.action == "preview":
        args.action = "classify_all"
        args.dry_run = True
        if args.limit == 0:
            args.limit = 10  # Default preview to 10 users
        args.force = True  # Preview should show all users

    try:
        llm_config = get_llm_config()
        logger.info(f"LLM config: base={llm_config['api_base']}, model={llm_config['model']}")
    except RuntimeError as e:
        logger.error(str(e))
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        sys.exit(1)

    conn = get_db_connection()

    try:
        if args.action == "classify_user":
            if not args.thread_id:
                print(json.dumps({"success": False, "error": "--thread-id required for classify_user"}))
                sys.exit(1)
            result = classify_user(conn, args.thread_id, llm_config, dry_run=args.dry_run)
            print(json.dumps({"success": True, **result}, indent=2, ensure_ascii=False))

        elif args.action == "classify_all":
            result = classify_all(
                conn, llm_config,
                dry_run=args.dry_run,
                force=args.force,
                limit=args.limit,
            )
            # Print summary (exclude full results for readability)
            summary = {k: v for k, v in result.items() if k != "results"}
            print(json.dumps({"success": True, **summary}, indent=2, ensure_ascii=False))

            # Print detailed changes
            if result["changes"]:
                print(f"\n--- City Changes ({len(result['changes'])}) ---")
                for ch in result["changes"]:
                    print(f"  {ch['thread_name']}: {ch['old_city']} → {ch['new_city']} "
                          f"[{ch['confidence']}] {ch['reasoning']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
