import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record
from fb_pipeline.persistence.l4_sqlite_store import setup_database


class TestInboxContracts(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)

    def tearDown(self):
        self.conn.close()

    # Gate 1 & 2: code:test-validation-001:l2-to-l3 and code:test-validation-001:l3-to-l1
    def test_build_thread_record_parses_visible_thread(self):
        record = build_thread_record("page1", {
            "domIndex": 3,
            "name": " User A ",
            "text": " User A \nHello there\nToday ",
        })
        self.assertEqual(record.page_id, "page1")
        self.assertEqual(record.thread_name, "User A")
        self.assertEqual(record.preview_text, "Hello there Today")
        self.assertEqual(record.dom_index, 3)
        self.assertTrue(record.thread_id.startswith("page1_"))

    # Gate 2: code:test-validation-001:l3-to-l1
    def test_enrich_thread_record_builds_mas_payload(self):
        thread_record = build_thread_record("page1", {
            "name": "User A",
            "text": "User A\nPreview",
        })
        enriched = enrich_thread_record(
            thread_record,
            [
                {"sender": "Customer", "text": "0912345678", "timestamp": "Today"},
                {"sender": "Page", "text": "Lớp tại Hà Nội", "timestamp": "Today"},
                {"sender": "Customer", "text": "", "timestamp": "Today"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected123",
            ad_ids=["ad_1"],
        )
        self.assertEqual(enriched.user_info["phone"], "0912345678")
        self.assertEqual(enriched.city, "Hà Nội")
        self.assertEqual(len(enriched.messages), 2)
        self.assertEqual(enriched.messages[0].seq, 0)
        self.assertEqual(enriched.mas_handoff.fb_url, "selected123")
        self.assertEqual(enriched.mas_handoff.seeker.city, "Hà Nội")
        self.assertEqual(enriched.mas_handoff.ad_ids, ["ad_1"])

    # Gate 3: code:test-validation-001:l1-to-l4
    def test_persist_thread_record_writes_all_boundaries(self):
        thread_record = enrich_thread_record(
            build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
            [
                {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
                {"sender": "Page", "text": "Địa chỉ: 40 Vương Thừa Vũ", "timestamp": "Today"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected456",
            ad_ids=["6930299765389"],
        )
        result = persist_thread_record(self.conn, thread_record, detect_city)
        self.assertEqual(result["messages_added"], 2)
        self.assertEqual(result["ad_ids_count"], 1)
        self.assertEqual(result["city"], "Hà Nội")

        thread = self.conn.execute("SELECT * FROM threads WHERE id = ?", (thread_record.thread_id,)).fetchone()
        self.assertEqual(thread["page_id"], "page1")

        user = self.conn.execute("SELECT * FROM users WHERE thread_id = ?", (thread_record.thread_id,)).fetchone()
        self.assertEqual(user["fb_url"], "selected456")
        self.assertEqual(user["city"], "Hà Nội")
        self.assertIsNotNone(user["last_synced_at"])

        ad = self.conn.execute("SELECT * FROM ad_posts WHERE ad_id = '6930299765389'").fetchone()
        self.assertEqual(ad["city"], "Hà Nội")

        msgs = self.conn.execute("SELECT content, seq FROM messages WHERE thread_id = ? ORDER BY seq", (thread_record.thread_id,)).fetchall()
        self.assertIn("--- [AD SOURCE]: Thiền miễn phí tại Hà Nội ---", msgs[0]["content"])
        self.assertEqual(msgs[1]["seq"], 1)

    # Gate 3: code:test-validation-001:l1-to-l4 (CRM Timing State)
    def test_persist_thread_record_only_refreshes_last_synced_at_without_new_customer_message(self):
        thread_record = enrich_thread_record(
            build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
            [
                {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
                {"sender": "Page", "text": "Địa chỉ: 40 Vương Thừa Vũ", "timestamp": "Today"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected456",
            ad_ids=["6930299765389"],
        )
        persist_thread_record(self.conn, thread_record, detect_city)

        stale_interaction = "2000-01-01 00:00:00"
        stale_synced = "2000-01-01 00:00:00"
        self.conn.execute(
            "UPDATE users SET last_interaction = ?, last_synced_at = ? WHERE thread_id = ?",
            (stale_interaction, stale_synced, thread_record.thread_id),
        )
        self.conn.commit()

        resynced_record = enrich_thread_record(
            build_thread_record("page1", {"name": "User A", "text": "User A\nPreview"}),
            [
                {"sender": "Customer", "text": "Xin chào", "timestamp": "Today"},
                {"sender": "Page", "text": "Lớp tiếp theo vào Chủ Nhật", "timestamp": "Tomorrow"},
            ],
            extract_user_info,
            detect_city,
            ad_context="Thiền miễn phí tại Hà Nội",
            fb_url="selected456",
            ad_ids=["6930299765389"],
        )
        persist_thread_record(self.conn, resynced_record, detect_city)

        user = self.conn.execute(
            "SELECT last_interaction, last_synced_at FROM users WHERE thread_id = ?",
            (thread_record.thread_id,),
        ).fetchone()
        self.assertEqual(user["last_interaction"], stale_interaction)
        self.assertNotEqual(user["last_synced_at"], stale_synced)


if __name__ == '__main__':
    unittest.main()
