import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from fb_pipeline.contracts.l1_city_llm import detect_city_llm, gather_signals_for_user

logger = logging.getLogger("fetch_fb_city_classify")

def _get_llm_config_safe() -> dict | None:
    """Try to load LLM config. Returns None if credentials unavailable."""
    try:
        from tools.l5_inbox_mas_context import get_llm_config
        return get_llm_config()
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
    "(u.classification_verified_at IS NULL OR u.contact_extracted_at IS NULL "
    "OR u.last_interaction > u.classification_verified_at "
    # Historical runs wrote API errors as if they were verified Unknown values.
    # Keep those records eligible until Gemini produces an actual classification.
    "OR u.classification_proof LIKE 'API error:%' "
    "OR u.classification_proof LIKE 'API timeout%' "
    "OR u.classification_proof LIKE 'Response parse error:%' "
    "OR u.classification_proof = 'Extracted from free-text response' "
    "OR u.classification_proof LIKE 'RETRYABLE_LLM_FAILURE:%')"
)


def count_stale_users(conn, page_id: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM users u JOIN threads t ON u.thread_id = t.id "
        f"WHERE t.page_id = ? AND {STALE_CLASSIFICATION_SQL}",
        (page_id,),
    ).fetchone()
    return int(row[0] if row else 0)


def _retryable_detection_failure(result: dict) -> bool:
    """True when the result is transport/config failure, not a valid Unknown.

    `Unknown` with normal reasoning is a completed classification and waits for
    a new customer message.  An API/timeout/parser failure must retry on the
    next `mas-classify` epoch without overwriting known City/Program/Name/Phone.
    """
    reason = str(result.get("reasoning") or "").strip().lower()
    return reason.startswith(("api error:", "api timeout", "response parse error:"))


def _post_scrape_llm_city_classify(conn, page_id: str, thread_ids: list[str] | None = None,
                                   only_stale: bool = False, max_users: int | None = None,
                                   workers: int = 10, only_missing_real_name: bool = False) -> dict:
    """Run the LLM city/program/contact classification for users of this page.

    ``thread_ids`` restricts the pass to those threads (a fetch's
    ``processed_thread_ids``); ``only_stale`` restricts it to users whose
    classification is missing or older than their last customer message
    (the scheduler's incremental ``[CLASSIFY]`` route). ``only_missing_real_name``
    is the explicit operator retry path for contact extraction. Commits after
    every batch so a long pass never holds the SQLite write lock against the
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
    if only_missing_real_name:
        sql += " AND (u.real_name IS NULL OR trim(u.real_name) = '')"
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

    # Classify exactly one conversation per request. A classification must
    # preserve the relationship between a seeker's reply, the Page's replies,
    # and the particular ad/post they interacted with. SQLite connections are
    # not thread-safe, so gather signals and persist results here; only the
    # independent LLM requests run concurrently.
    work_items = []
    for user_row in users:
        thread_id = user_row["thread_id"]
        try:
            signals = gather_signals_for_user(conn, thread_id)
            work_items.append((thread_id, user_row["city"], signals))
        except Exception as e:
            logger.warning(f"Failed to gather classification signals for {thread_id}: {e}")
            errors += 1

    def detect(item):
        thread_id, old_city, signals = item
        result = detect_city_llm(
            thread_name=signals["thread_name"],
            customer_messages=signals["customer_messages"],
            page_messages=signals["page_messages"],
            ad_content=signals["ad_content"],
            llm_config=llm_config, subject_id=thread_id, page_id=page_id,
            trigger="scheduler",
        )
        return thread_id, old_city, signals, result

    worker_count = max(1, int(workers))
    logger.info("LLM city classification: using %s worker(s)", min(worker_count, len(work_items)))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="city-classify") as executor:
        futures = {executor.submit(detect, item): item[0] for item in work_items}
        for future in as_completed(futures):
            thread_id = futures[future]
            try:
                thread_id, old_city, signals, result = future.result()
            except Exception as e:
                logger.warning(f"Failed to classify {thread_id}: {e}")
                errors += 1
                continue

            if _retryable_detection_failure(result):
                reason = str(result.get("reasoning") or "LLM request failed").strip()
                cursor.execute(
                    "UPDATE users SET classification_proof = ? WHERE thread_id = ?",
                    (f"RETRYABLE_LLM_FAILURE: {reason}", thread_id),
                )
                conn.commit()
                errors += 1
                logger.warning(
                    "LLM classification failed for %s; retained existing City/Program/Name/Phone "
                    "and will retry next mas-classify epoch: %s",
                    signals["thread_name"], reason,
                )
                continue

            new_city = result.get("city", "Unknown")
            program_code = result.get("program_code")
            full_name = result.get("full_name")
            phone = result.get("phone")
            proof = result.get("proof") or result.get("reasoning") or "No supporting evidence returned."
            cursor.execute(
                "UPDATE users SET city = ?, program_code = ?, "
                "real_name = COALESCE(?, real_name), phone = COALESCE(?, phone), "
                "classification_proof = ?, classification_verified_at = datetime('now','localtime'), "
                "contact_extracted_at = datetime('now','localtime') "
                "WHERE thread_id = ?",
                (new_city, program_code, full_name, phone, proof, thread_id),
            )
            # Keep Facebook's display name in `threads.thread_name` intact so
            # operators can continue to find the thread in Meta Inbox. The
            # customer-provided registration name is stored in `real_name`.
            conn.commit()
            updated += 1
            enrichment = [f"program={program_code or '-'}"]
            if full_name:
                enrichment.append(f"name={full_name}")
            # Do not write the full phone number to logs; the persisted users.phone
            # field contains the normalized value for CRM use.
            enrichment.append(f"phone={'detected' if phone else '-'}")
            logger.info("LLM classified %s: city %s → %s [%s]; %s; %s",
                        signals["thread_name"], old_city, new_city,
                        result.get("confidence", ""), ", ".join(enrichment), proof)

    logger.info(f"LLM city classification done: {updated} updated, {errors} errors out of {len(users)} users.")
    return {"llm_city_classify": "done", "total": len(users), "updated": updated, "errors": errors}
