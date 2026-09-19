"""City/program classification is a job of its own, not part of the crawl.

# code:test-validation-001:classify-decoupled
"""
import os
import sqlite3
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fb_pipeline.persistence.l4_sqlite_store import setup_database
from tools import l5_fetch_fb_city_classify as classify
from tools import l5_fetch_fb_messages as fetch_tool
from tools import l5_scheduler_routes as routes

PAGE = "1548373332058326"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    return conn


def _seed(conn, tid, name, last_interaction, verified_at=None, text="em ở Hà Nội"):
    conn.execute("INSERT INTO threads (id, page_id, thread_name) VALUES (?, ?, ?)", (tid, PAGE, name))
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, last_interaction, classification_verified_at, contact_extracted_at) VALUES (?, ?, 'Unknown', ?, ?, ?)",
        (tid, name, last_interaction, verified_at, verified_at),
    )
    conn.execute("INSERT INTO messages (thread_id, sender, content, seq) VALUES (?, 'Customer', ?, 0)", (tid, text))
    conn.commit()


class TestStalePredicate(unittest.TestCase):
    def test_counts_missing_and_outdated_classifications_only(self):
        conn = _db()
        _seed(conn, "t_never", "A", "2026-09-10 10:00:00", None)
        _seed(conn, "t_newer_msg", "B", "2026-09-17 10:00:00", "2026-09-16 09:00:00")
        _seed(conn, "t_fresh", "C", "2026-09-15 10:00:00", "2026-09-16 09:00:00")
        self.assertEqual(classify.count_stale_users(conn, PAGE), 2)

    def test_only_stale_pass_classifies_stale_users_and_clears_them(self):
        conn = _db()
        _seed(conn, "t_never", "A", "2026-09-10 10:00:00", None)
        _seed(conn, "t_fresh", "C", "2026-09-15 10:00:00", "2026-09-16 09:00:00")
        sent = []

        def fake_detect(**kwargs):
            sent.append(kwargs)
            return {"city": "Hà Nội", "program_code": None, "confidence": "high", "proof": "em ở Hà Nội"}

        with patch.object(classify, "_get_llm_config_safe", return_value={"api_base": "x", "api_key": "y", "model": "m"}), \
             patch.object(classify, "detect_city_llm", side_effect=fake_detect):
            result = classify._post_scrape_llm_city_classify(conn, PAGE, only_stale=True)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["thread_name"], "A")
        self.assertEqual(sent[0]["subject_id"], "t_never")
        row = conn.execute("SELECT city, classification_verified_at FROM users WHERE thread_id='t_never'").fetchone()
        self.assertEqual(row["city"], "Hà Nội")
        self.assertIsNotNone(row["classification_verified_at"])
        # Stamp is local time and newer than the last message: no longer stale.
        self.assertEqual(classify.count_stale_users(conn, PAGE), 0)

    def test_max_users_limits_the_pass(self):
        conn = _db()
        for i in range(3):
            _seed(conn, f"t{i}", f"N{i}", f"2026-09-1{i} 10:00:00", None)
        with patch.object(classify, "_get_llm_config_safe", return_value={"api_base": "x", "api_key": "y", "model": "m"}), \
             patch.object(classify, "detect_city_llm", return_value={"city": "Unknown", "program_code": None, "proof": "", "confidence": "low"}):
            result = classify._post_scrape_llm_city_classify(conn, PAGE, only_stale=True, max_users=2)
        self.assertEqual(result["total"], 2)

    def test_missing_real_name_pass_targets_only_empty_real_name(self):
        conn = _db()
        _seed(conn, "t_missing", "Missing Name", "2026-09-10 10:00:00", "2026-09-11 10:00:00")
        _seed(conn, "t_known", "Known Name", "2026-09-10 10:00:00", "2026-09-11 10:00:00")
        conn.execute("UPDATE users SET real_name=? WHERE thread_id='t_known'", ("Nguyễn An",))
        conn.commit()
        sent = []

        def fake_detect(**kwargs):
            sent.append(kwargs["thread_name"])
            return {"city": "Hà Nội", "program_code": None, "full_name": None,
                    "phone": None, "confidence": "high", "proof": "em ở Hà Nội"}

        with patch.object(classify, "_get_llm_config_safe", return_value={"provider": "google", "api_key": "k", "model": "m"}), \
             patch.object(classify, "detect_city_llm", side_effect=fake_detect):
            result = classify._post_scrape_llm_city_classify(conn, PAGE, only_missing_real_name=True)

        self.assertEqual(result["total"], 1)
        self.assertEqual(sent, ["Missing Name"])

    def test_workers_limits_concurrent_llm_requests(self):
        conn = _db()
        for i in range(3):
            _seed(conn, f"t{i}", f"N{i}", f"2026-09-1{i} 10:00:00", None)
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_detect(**kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return {"city": "Unknown", "program_code": None, "proof": "", "confidence": "low"}

        with patch.object(classify, "_get_llm_config_safe", return_value={"api_base": "x", "api_key": "y", "model": "m"}), \
             patch.object(classify, "detect_city_llm", side_effect=fake_detect):
            result = classify._post_scrape_llm_city_classify(conn, PAGE, only_stale=True, workers=2)

        self.assertEqual(result["updated"], 3)
        self.assertEqual(peak, 2)

    def test_llm_contact_data_preserves_facebook_name_and_stores_real_name(self):
        conn = _db()
        _seed(conn, "t_contact", "Thuy Bui", "2026-09-10 10:00:00", None,
              text="Bùi thị Thúy, SĐT: o904069868")
        detected = {
            "city": "Hà Nội", "program_code": None,
            "full_name": "Bùi Thị Thúy", "phone": "0904069868",
            "confidence": "high", "proof": "Bùi thị Thúy, SĐT: o904069868",
        }
        with patch.object(classify, "_get_llm_config_safe", return_value={"api_base": "x", "api_key": "y", "model": "m"}), \
             patch.object(classify, "detect_city_llm", return_value=detected):
            classify._post_scrape_llm_city_classify(conn, PAGE, only_stale=True)

        user = conn.execute("SELECT thread_name, real_name, phone FROM users WHERE thread_id='t_contact'").fetchone()
        thread = conn.execute("SELECT thread_name FROM threads WHERE id='t_contact'").fetchone()
        self.assertEqual(user["thread_name"], "Thuy Bui")
        self.assertEqual(user["real_name"], "Bùi Thị Thúy")
        self.assertEqual(user["phone"], "0904069868")
        self.assertEqual(thread["thread_name"], "Thuy Bui")

    def test_api_error_remains_stale_and_does_not_overwrite_existing_profile(self):
        conn = _db()
        _seed(conn, "t_retry", "Known Seeker", "2026-09-10 10:00:00", "2026-09-11 10:00:00")
        conn.execute(
            "UPDATE users SET city=?, program_code=?, real_name=?, phone=?, classification_proof=? WHERE thread_id=?",
            ("Hà Nội", "14h30-CN-Vương Thừa Vũ-HN", "Nguyễn An", "0904000000",
             "API error: 401 Unauthorized", "t_retry"),
        )
        conn.commit()
        self.assertEqual(classify.count_stale_users(conn, PAGE), 1)

        failed = {
            "city": "Unknown", "program_code": None, "full_name": None, "phone": None,
            "confidence": "low", "reasoning": "API error: 401 Unauthorized",
        }
        with patch.object(classify, "_get_llm_config_safe", return_value={"provider": "google", "api_key": "k", "model": "m"}), \
             patch.object(classify, "detect_city_llm", return_value=failed):
            result = classify._post_scrape_llm_city_classify(conn, PAGE, only_stale=True)

        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["errors"], 1)
        row = conn.execute(
            "SELECT city, program_code, real_name, phone, classification_proof FROM users WHERE thread_id='t_retry'"
        ).fetchone()
        self.assertEqual((row["city"], row["program_code"], row["real_name"], row["phone"]),
                         ("Hà Nội", "14h30-CN-Vương Thừa Vũ-HN", "Nguyễn An", "0904000000"))
        self.assertTrue(row["classification_proof"].startswith("RETRYABLE_LLM_FAILURE:"))
        self.assertEqual(classify.count_stale_users(conn, PAGE), 1)

    def test_historical_free_text_fallback_is_retried_for_missing_program_and_contact(self):
        conn = _db()
        _seed(conn, "t_partial", "Partial JSON", "2026-09-10 10:00:00", "2026-09-11 10:00:00")
        conn.execute(
            "UPDATE users SET city=?, classification_proof=? WHERE thread_id=?",
            ("Hà Nội", "Extracted from free-text response", "t_partial"),
        )
        conn.commit()
        self.assertEqual(classify.count_stale_users(conn, PAGE), 1)


class TestFetchDoesNotClassifyUnderLock(unittest.TestCase):
    """The LLM pass must never run while ``inbox_fetch_cli`` is held."""

    def _run(self, classify_city):
        events = []

        class _Hold:
            def __init__(self, *a, **k): pass
            def __enter__(self): events.append("lock_acquired")
            def __exit__(self, *a): events.append("lock_released")

        def fake_impl(*a, **k):
            events.append("scrape")
            return {"success": True, "method": "cdp_direct",
                    "data": {"stats": {"processed_thread_ids": ["t1"], "threads_seen": 1}}}

        def fake_classify(conn, page_id, thread_ids=None, **k):
            events.append(("classify", tuple(thread_ids or [])))
            return {"llm_city_classify": "done", "total": 1, "updated": 1, "errors": 0}

        with patch("fb_pipeline.session.l2_activity_lock.hold_activity", _Hold), \
             patch("fb_pipeline.session.l2_activity_lock.wait_until_idle", return_value=True), \
             patch.object(fetch_tool, "_fetch_messages_impl", side_effect=fake_impl), \
             patch.object(fetch_tool, "_post_scrape_llm_city_classify", side_effect=fake_classify), \
             patch.object(fetch_tool, "get_db_connection", return_value=_db()):
            result = fetch_tool.fetch_messages(PAGE, "default", "7d", use_cdp=True, workers=2,
                                               classify_city=classify_city)
        return events, result

    def test_default_fetch_never_calls_llm(self):
        events, result = self._run(classify_city=False)
        self.assertEqual(events, ["lock_acquired", "scrape", "lock_released"])
        self.assertTrue(result["success"])

    def test_opt_in_classify_runs_after_lock_release(self):
        events, result = self._run(classify_city=True)
        self.assertEqual(events, ["lock_acquired", "scrape", "lock_released", ("classify", ("t1",))])
        self.assertEqual(result["data"]["stats"]["llm_city"]["updated"], 1)

    def test_scrape_time_city_detection_is_keyword_only(self):
        # No LLM env, no network: the crawl-side detector is the keyword matcher.
        self.assertIs(fetch_tool.detect_city.__wrapped__ if hasattr(fetch_tool.detect_city, "__wrapped__") else None, None)
        with patch("fb_pipeline.contracts.l1_inbox.detect_city_smart", side_effect=AssertionError("LLM called")):
            self.assertEqual(fetch_tool.detect_city("", [{"sender": "Customer", "content": "em ở Đà Nẵng"}]), "Đà Nẵng")


class TestClassifyRoute(unittest.TestCase):
    def test_route_needs_no_browser_lock_and_skips_when_nothing_stale(self):
        conn = _db()
        with patch("fb_pipeline.persistence.l4_sqlite_store.get_db_connection", return_value=conn), \
             patch("fb_pipeline.session.l2_activity_lock.scheduler_browser_cycle",
                   side_effect=AssertionError("browser lock must not be taken")):
            result = routes.run_classify_cycle(PAGE, background=False)
        self.assertEqual(result, {"status": "noop", "stale": 0})

    def test_overlapping_tick_is_skipped(self):
        self.assertTrue(routes._classify_lock.acquire(blocking=False))
        try:
            result = routes.run_classify_cycle(PAGE, background=False)
        finally:
            routes._classify_lock.release()
        self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
