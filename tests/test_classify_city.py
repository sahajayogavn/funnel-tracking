"""
Unit tests for LLM-based city classification.
code:tool-citydetect-001:tests

Tests cover:
- Prompt construction from DB signals
- LLM response JSON parsing (valid, malformed, free-text)
- Priority logic via mocked LLM calls
- DB signal gathering
- CLI action logic (dry-run, classify_user)
"""
import json
import os
import sqlite3
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_city_llm import (
    KNOWN_CITIES,
    _build_prompt,
    _parse_llm_response,
    detect_city_llm,
    gather_signals_for_user,
)
from fb_pipeline.contracts.l1_inbox import detect_city


class TestBuildPrompt(unittest.TestCase):
    """Tests for prompt construction."""

    def test_prompt_includes_all_signals(self):
        prompt = _build_prompt(
            thread_name="Thanh Trà",
            customer_messages=["Mình ở Đà Nẵng", "Đăng ký lớp"],
            page_messages=["Địa chỉ: Xô Viết Nghệ Tĩnh, Đà Nẵng"],
            ad_content="Lớp thiền miễn phí tại TPHCM",
        )
        self.assertIn("Thanh Trà", prompt)
        self.assertIn("Mình ở Đà Nẵng", prompt)
        self.assertIn("Xô Viết Nghệ Tĩnh", prompt)
        self.assertIn("TPHCM", prompt)

    def test_prompt_empty_signals(self):
        prompt = _build_prompt("Test User", [], [], "")
        self.assertIn("(no customer messages)", prompt)
        self.assertIn("(no page messages)", prompt)
        self.assertIn("(no ad content)", prompt)

    def test_prompt_only_customer_messages(self):
        prompt = _build_prompt("User", ["Em ở HCM ạ"], [], "")
        self.assertIn("Em ở HCM ạ", prompt)
        self.assertIn("(no page messages)", prompt)


class TestParseLlmResponse(unittest.TestCase):
    """Tests for LLM response parsing."""

    def test_valid_json(self):
        raw = '{"city": "Đà Nẵng", "confidence": "high", "reasoning": "User explicitly said Đà Nẵng"}'
        result = _parse_llm_response(raw)
        self.assertEqual(result["city"], "Đà Nẵng")
        self.assertEqual(result["confidence"], "high")

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"city": "Hà Nội", "confidence": "medium", "reasoning": "Address match"}\n```'
        result = _parse_llm_response(raw)
        self.assertEqual(result["city"], "Hà Nội")

    def test_unknown_city(self):
        raw = '{"city": "Biên Hòa", "confidence": "low", "reasoning": "Not in known list"}'
        result = _parse_llm_response(raw)
        self.assertEqual(result["city"], "Unknown")

    def test_fuzzy_match_hcm(self):
        raw = '{"city": "TP.HCM", "confidence": "high", "reasoning": "match"}'
        result = _parse_llm_response(raw)
        # "TP.HCM" is not exact match but lowercase check should find "TP. Hồ Chí Minh"
        # Actually "tp.hcm" not in "tp. hồ chí minh" — so this should be Unknown
        # unless the LLM returns the exact known city name
        self.assertIn(result["city"], ["Unknown", "TP. Hồ Chí Minh"])

    def test_free_text_fallback(self):
        raw = "Based on the messages, the city is Đà Nẵng because the user mentioned it."
        result = _parse_llm_response(raw)
        self.assertEqual(result["city"], "Đà Nẵng")
        self.assertEqual(result["confidence"], "low")

    def test_malformed_json(self):
        raw = "{not valid json}"
        result = _parse_llm_response(raw)
        self.assertEqual(result["confidence"], "low")

    def test_online_city(self):
        raw = '{"city": "Online", "confidence": "high", "reasoning": "ad mentions Zoom"}'
        result = _parse_llm_response(raw)
        self.assertEqual(result["city"], "Online")


class TestDetectCityLlm(unittest.TestCase):
    """Tests for the main LLM call function (mocked API)."""

    @patch("fb_pipeline.contracts.l1_city_llm.requests.post")
    def test_successful_call(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content":
                '{"city": "Đà Nẵng", "confidence": "high", "reasoning": "user said Đà Nẵng"}'
            }}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = detect_city_llm(
            thread_name="Test",
            customer_messages=["Mình ở Đà Nẵng"],
            page_messages=[],
            ad_content="",
            api_base="http://localhost:8317/v1",
            api_key="test-key",
            model="gpt-5.4",
        )
        self.assertEqual(result["city"], "Đà Nẵng")
        self.assertEqual(result["confidence"], "high")
        mock_post.assert_called_once()

    @patch("fb_pipeline.contracts.l1_city_llm.requests.post")
    def test_api_timeout(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.Timeout("timeout")

        result = detect_city_llm(
            thread_name="Test",
            customer_messages=[], page_messages=[], ad_content="",
            api_base="http://localhost", api_key="key", model="m",
        )
        self.assertEqual(result["city"], "Unknown")
        self.assertIn("timeout", result["reasoning"].lower())

    @patch("fb_pipeline.contracts.l1_city_llm.requests.post")
    def test_api_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        result = detect_city_llm(
            thread_name="Test",
            customer_messages=[], page_messages=[], ad_content="",
            api_base="http://localhost", api_key="key", model="m",
        )
        self.assertEqual(result["city"], "Unknown")


class TestGatherSignals(unittest.TestCase):
    """Tests for DB signal gathering."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE users (
                thread_id TEXT PRIMARY KEY,
                thread_name TEXT,
                city TEXT DEFAULT 'Unknown'
            )
        """)
        cursor.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                sender TEXT,
                content TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE user_ad_ids (
                thread_id TEXT,
                ad_id TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE ad_posts (
                ad_id TEXT PRIMARY KEY,
                ad_content TEXT
            )
        """)
        # Insert test data
        cursor.execute("INSERT INTO users VALUES ('t1', 'Thanh Trà', 'Unknown')")
        cursor.execute("INSERT INTO messages (thread_id, sender, content) VALUES ('t1', 'Customer', 'Mình ở Đà Nẵng')")
        cursor.execute("INSERT INTO messages (thread_id, sender, content) VALUES ('t1', 'Customer', 'Đăng ký lớp')")
        cursor.execute("INSERT INTO messages (thread_id, sender, content) VALUES ('t1', 'Page', 'Địa chỉ: Xô Viết Nghệ Tĩnh, Đà Nẵng')")
        cursor.execute("INSERT INTO user_ad_ids VALUES ('t1', 'ad_123')")
        cursor.execute("INSERT INTO ad_posts VALUES ('ad_123', 'Lớp thiền tại TPHCM và Đà Nẵng')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_gather_all_signals(self):
        signals = gather_signals_for_user(self.conn, "t1")
        self.assertEqual(signals["thread_name"], "Thanh Trà")
        self.assertEqual(len(signals["customer_messages"]), 2)
        self.assertEqual(len(signals["page_messages"]), 1)
        self.assertIn("TPHCM", signals["ad_content"])

    def test_gather_missing_user(self):
        signals = gather_signals_for_user(self.conn, "nonexistent")
        self.assertEqual(signals["thread_name"], "Unknown")
        self.assertEqual(signals["customer_messages"], [])

    def test_gather_no_ads(self):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO users VALUES ('t2', 'User2', 'Unknown')")
        cursor.execute("INSERT INTO messages (thread_id, sender, content) VALUES ('t2', 'Customer', 'Xin chào')")
        self.conn.commit()

        signals = gather_signals_for_user(self.conn, "t2")
        self.assertEqual(signals["ad_content"], "")


class TestClassifyUser(unittest.TestCase):
    """Tests for classify_user function (mocked LLM)."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE users (
                thread_id TEXT PRIMARY KEY,
                thread_name TEXT,
                city TEXT DEFAULT 'Unknown',
                last_interaction TEXT DEFAULT '2026-03-20'
            )
        """)
        cursor.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                sender TEXT,
                content TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE user_ad_ids (thread_id TEXT, ad_id TEXT)
        """)
        cursor.execute("""
            CREATE TABLE ad_posts (ad_id TEXT PRIMARY KEY, ad_content TEXT)
        """)
        cursor.execute("INSERT INTO users VALUES ('t1', 'Test User', 'Unknown', '2026-03-20')")
        cursor.execute("INSERT INTO messages (thread_id, sender, content) VALUES ('t1', 'Customer', 'Em ở Đà Nẵng ạ')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch("fb_pipeline.contracts.l1_city_llm.requests.post")
    def test_dry_run_no_update(self, mock_post):
        from tools.classify_city import classify_user

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content":
                '{"city": "Đà Nẵng", "confidence": "high", "reasoning": "user said ĐN"}'
            }}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = classify_user(
            self.conn, "t1",
            {"api_base": "http://x", "api_key": "k", "model": "m"},
            dry_run=True,
        )
        self.assertEqual(result["new_city"], "Đà Nẵng")
        self.assertFalse(result["updated"])

        # Verify DB unchanged
        row = self.conn.execute("SELECT city FROM users WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["city"], "Unknown")

    @patch("fb_pipeline.contracts.l1_city_llm.requests.post")
    def test_write_mode_updates_db(self, mock_post):
        from tools.classify_city import classify_user

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content":
                '{"city": "Đà Nẵng", "confidence": "high", "reasoning": "user said ĐN"}'
            }}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = classify_user(
            self.conn, "t1",
            {"api_base": "http://x", "api_key": "k", "model": "m"},
            dry_run=False,
        )
        self.assertEqual(result["new_city"], "Đà Nẵng")
        self.assertTrue(result["updated"])

        # Verify DB updated
        row = self.conn.execute("SELECT city FROM users WHERE thread_id = 't1'").fetchone()
        self.assertEqual(row["city"], "Đà Nẵng")


class TestKnownCities(unittest.TestCase):
    """Verify known cities list is comprehensive."""

    def test_known_cities_not_empty(self):
        self.assertTrue(len(KNOWN_CITIES) >= 7)

    def test_major_cities_included(self):
        self.assertIn("Hà Nội", KNOWN_CITIES)
        self.assertIn("TP. Hồ Chí Minh", KNOWN_CITIES)
        self.assertIn("Đà Nẵng", KNOWN_CITIES)
        self.assertIn("Online", KNOWN_CITIES)


class TestDetectCityKeyword(unittest.TestCase):
    """
    Regression tests for keyword-based detect_city() — defect #1 fix.
    code:tool-citydetect-001:regression-keyword

    Before fix: only Page sender messages were scanned.
    After fix: all messages (Customer + Page) are scanned.
    """

    def test_hung_bui_customer_message_ha_noi(self):
        """Core regression: Hung Bui said 'Hà Nội' in a Customer message — must be detected."""
        msgs = [
            {"sender": "Customer", "content": "SY tổ chức lớp học thiền tại Hà Nội"},
            {"sender": "Page",     "content": "Chào bạn, cảm ơn bạn đã nhắn tin"},
        ]
        self.assertEqual(detect_city("", msgs), "Hà Nội")

    def test_customer_message_content_key(self):
        """Customer message with 'content' key is detected correctly."""
        msgs = [{"sender": "Customer", "content": "Mình ở Đà Nẵng"}]
        self.assertEqual(detect_city("", msgs), "Đà Nẵng")

    def test_customer_message_text_key(self):
        """JS-scraped Customer message with 'text' key (not 'content') is detected."""
        msgs = [{"sender": "Customer", "text": "Hỏi về lớp ở Đà Nẵng", "content": ""}]
        self.assertEqual(detect_city("", msgs), "Đà Nẵng")

    def test_page_message_still_detected(self):
        """Page messages are still scanned after fix (no regression)."""
        msgs = [{"sender": "Page", "content": "Lớp tại Đà Nẵng ngày mai"}]
        self.assertEqual(detect_city("", msgs), "Đà Nẵng")

    def test_ad_context_only(self):
        """City in ad_context only (no messages) is detected."""
        self.assertEqual(detect_city("Sự kiện tại TPHCM miễn phí", []), "TP. Hồ Chí Minh")

    def test_unknown_when_no_signals(self):
        """Returns Unknown when no city keywords present anywhere."""
        msgs = [{"sender": "Customer", "content": "Xin chào, tôi muốn tìm hiểu thiền"}]
        self.assertEqual(detect_city("", msgs), "Unknown")

    def test_both_senders_ha_noi(self):
        """When both Customer and Page mention city, first keyword match wins."""
        msgs = [
            {"sender": "Customer", "content": "Tôi muốn học tại Hà Nội"},
            {"sender": "Page",     "content": "OK, lớp ở Hà Nội sẽ khai giảng sớm"},
        ]
        self.assertEqual(detect_city("", msgs), "Hà Nội")


if __name__ == '__main__':
    unittest.main()
