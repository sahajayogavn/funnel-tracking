import logging
import os
import time

from fb_pipeline.contracts.l1_city_llm import detect_city_batch_llm, gather_signals_for_user, verify_city_program_batch_llm

logger = logging.getLogger("fetch_fb_city_classify")

def _get_llm_config_safe() -> dict | None:
    """Try to load LLM config. Returns None if credentials unavailable."""
    try:
        from tools.env_manager import load_credentials
        creds = load_credentials()
        api_base = creds.get("OPENAI_COMPATIBLE_URL") or os.environ.get("OPENAI_API_BASE", "")
        api_key = creds.get("OPENAI_COMPATIBLE_KEY") or os.environ.get("OPENAI_API_KEY", "")
        model = creds.get("OPENAI_COMPATIBLE_MODELS") or os.environ.get("ADK_MODEL", "gpt-5.4")
        if not api_base or not api_key:
            return None
        return {"api_base": api_base, "api_key": api_key, "model": model}
    except Exception as e:
        logger.debug(f"LLM config not available: {e}")
        return None

# code:tool-citydetect-001:stale-predicate
# A classification is stale when it never ran or when the seeker has written
# since (``last_interaction`` only moves on a new Customer message). Both
# columns are compared in local time: ``last_interaction`` is derived from
# Facebook's local display timestamps, so a UTC ``datetime('now')`` here would
# keep every fresh classification "stale" for seven hours.
STALE_CLASSIFICATION_SQL = (
    "(u.classification_verified_at IS NULL OR u.last_interaction > u.classification_verified_at)"
)


def count_stale_users(conn, page_id: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM users u JOIN threads t ON u.thread_id = t.id "
        f"WHERE t.page_id = ? AND {STALE_CLASSIFICATION_SQL}",
        (page_id,),
    ).fetchone()
    return int(row[0] if row else 0)


def _post_scrape_llm_city_classify(conn, page_id: str, thread_ids: list[str] | None = None,
                                   only_stale: bool = False, max_users: int | None = None) -> dict:
    """Run the LLM city/program classification for users of this page.

    ``thread_ids`` restricts the pass to those threads (a fetch's
    ``processed_thread_ids``); ``only_stale`` restricts it to users whose
    classification is missing or older than their last customer message
    (the scheduler's incremental ``[CLASSIFY]`` route). Commits after every
    batch so a long pass never holds the SQLite write lock against the
    crawler. Gracefully skips if LLM credentials are not available.
    """
    llm_config = _get_llm_config_safe()
    if not llm_config:
        logger.info("LLM city classification skipped: no LLM credentials available.")
        return {"llm_city_classify": "skipped", "reason": "no_credentials"}

    cursor = conn.cursor()
    sql = """
        SELECT u.thread_id, u.thread_name, u.city FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE t.page_id = ?
    """
    params: list = [page_id]
    if thread_ids is not None:
        if not thread_ids:
            return {"llm_city_classify": "done", "total": 0, "updated": 0, "reason": "no_processed_threads"}
        sql += f" AND u.thread_id IN ({','.join('?' for _ in thread_ids)})"
        params.extend(thread_ids)
    if only_stale:
        sql += f" AND {STALE_CLASSIFICATION_SQL}"
    sql += " ORDER BY u.last_interaction DESC"
    if max_users:
        sql += " LIMIT ?"
        params.append(int(max_users))
    cursor.execute(sql, params)
    users = cursor.fetchall()

    if not users:
        return {"llm_city_classify": "done", "total": 0, "updated": 0}

    logger.info(f"LLM city classification: processing {len(users)} users for page {page_id}")
    updated = 0
    errors = 0

    batches = []
    current_batch = []
    current_chars = 0
    MAX_CHARS = 80000

    for i, user_row in enumerate(users):
        thread_id = user_row["thread_id"]
        old_city = user_row["city"]
        try:
            signals = gather_signals_for_user(conn, thread_id)
            user_prompt_chunk = f"## Seeker: {signals['thread_name']}\n"
            user_prompt_chunk += f"Signal 1 (Customer messages): {' '.join(signals['customer_messages'])}\n"
            user_prompt_chunk += f"Signal 2 (Page messages): {' '.join(signals['page_messages'])}\n"
            user_prompt_chunk += f"Signal 3 (Ad content): {signals['ad_content']}\n\n"
            
            chunk_len = len(user_prompt_chunk)
            if current_chars + chunk_len > MAX_CHARS and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0
                
            current_batch.append({
                "thread_id": thread_id,
                "thread_name": signals["thread_name"],
                "old_city": old_city,
                "prompt_chunk": user_prompt_chunk
            })
            current_chars += chunk_len
        except Exception as e:
            logger.warning(f"Failed to gather signals for {thread_id}: {e}")
            errors += 1

    if current_batch:
        batches.append(current_batch)

    for b_idx, batch in enumerate(batches):
        logger.info(f"LLM city classification: processing batch {b_idx+1}/{len(batches)} ({len(batch)} users)")
        batch_payload = "\n".join([item["prompt_chunk"] for item in batch])
        results = detect_city_batch_llm(
            batch_payload=batch_payload,
            api_base=llm_config["api_base"],
            api_key=llm_config["api_key"],
            model=llm_config["model"],
        )
        verified_results = verify_city_program_batch_llm(
            batch_payload=batch_payload,
            proposed_results=results,
            api_base=llm_config["api_base"],
            api_key=llm_config["api_key"],
            model=llm_config["model"],
        )
        if not verified_results:
            logger.error("LLM verifier returned no usable results for batch %s; no classifications will be written.", b_idx + 1)
            errors += len(batch)
            continue
        name_to_res = {r.get("thread_name", ""): r for r in verified_results}
            
        for item in batch:
            thread_id = item["thread_id"]
            thread_name = item["thread_name"]
            old_city = item["old_city"]
            
            res = name_to_res.get(thread_name)
            if not res:
                logger.warning(f"LLM omitted result for {thread_name}")
                errors += 1
                continue
                
            if not res.get("verified"):
                logger.info("LLM verifier withheld classification for %s: %s", thread_name, res.get("proof", ""))
                cursor.execute(
                    "UPDATE users SET city = 'Unknown', program_code = NULL, classification_proof = ?, classification_verified_at = datetime('now','localtime') WHERE thread_id = ?",
                    (res.get("proof", "Insufficient evidence for City/Program."), thread_id),
                )
                updated += 1
                continue
            new_city = res.get("city", "Unknown")
            program_code = res.get("program_code")
            proof = res.get("proof", "")
            if new_city != "Unknown":
                cursor.execute(
                    "UPDATE users SET city = ?, program_code = ?, classification_proof = ?, classification_verified_at = datetime('now','localtime') WHERE thread_id = ?",
                    (new_city, program_code, proof, thread_id)
                )
                updated += 1
                logger.info(
                    f"LLM city batch_updated [{b_idx+1}/{len(batches)}] {thread_name}: "
                    f"{old_city} → {new_city} [{res.get('confidence', '')}] {res.get('reasoning', '')}"
                )
        
        conn.commit()
        if b_idx < len(batches) - 1:
            time.sleep(0.5)

    conn.commit()
    logger.info(f"LLM city classification done: {updated} updated, {errors} errors out of {len(users)} users.")
    return {"llm_city_classify": "done", "total": len(users), "updated": updated, "errors": errors}
