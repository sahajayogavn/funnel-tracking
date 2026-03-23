"""
E2E Full Pipeline Tests — L1 → L3 → L4 → MAS integration.
code:test-e2e-pipeline-001

Tests 1-12: No LLM required (pure Python + in-memory SQLite)
Tests 13-15: Require live LLM (OPENAI_API_BASE + OPENAI_API_KEY)

Run:
    # L1-L4 only (no LLM):
    .venv/bin/python -m pytest tests/test_e2e_full_pipeline.py -v -k "not MAS"

    # MAS integration (needs LLM):
    OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d) \\
    OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d) \\
    .venv/bin/python -m pytest tests/test_e2e_full_pipeline.py -v
"""
import asyncio
import os
import sys
import unittest

import pytest

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.fixtures.fixture_loader import create_test_db, load_fixture, seed_fixture_into_db

from fb_pipeline.contracts.l1_inbox import (
    detect_city,
    extract_user_info,
    parse_ad_ids,
    parse_page_id,
)
from fb_pipeline.inbox.l3_pipeline import (
    build_thread_record,
    enrich_thread_record,
    persist_thread_record,
)

# --- Skip marker for LLM-dependent tests ---
SKIP_REASON = "OPENAI_API_BASE and OPENAI_API_KEY required for MAS tests"
requires_llm = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_BASE") and os.environ.get("OPENAI_API_KEY")),
    reason=SKIP_REASON,
)

# The full FB URL for the Hung Bui thread
HUNG_BUI_URL = (
    "https://business.facebook.com/latest/inbox/all/"
    "?nav_ref=manage_page_ap_plus_inbox_message_button"
    "&asset_id=1548373332058326"
    "&business_id="
    "&mailbox_id=1548373332058326"
    "&selected_item_id=100001005716854"
    "&thread_type=FB_MESSAGE"
)


# ===================================================================
# Phase 2: L1 Contract Round-Trip Tests (Tests 1-4)
# ===================================================================


class TestL1ContractRoundTrip(unittest.TestCase):
    """L1 contract helpers with real-world data patterns."""

    def test_parse_page_id_from_hung_bui_url(self):
        """Test 1: Parse asset_id from the full FB Business Suite URL."""
        page_id = parse_page_id(HUNG_BUI_URL)
        self.assertEqual(page_id, "1548373332058326")

    def test_extract_user_info_with_phone_and_email(self):
        """Test 2: Extract phone/email from realistic Vietnamese messages."""
        messages = [
            {"sender": "Customer", "content": "Chào bạn! Mình muốn đăng ký lớp thiền"},
            {"sender": "Page", "content": "Bạn vui lòng gửi họ tên và SĐT nhé ạ"},
            {"sender": "Customer", "content": "Bùi Duy Hưng"},
            {"sender": "Customer", "content": "0901234567"},
            {"sender": "Customer", "content": "hungbui@gmail.com"},
        ]
        info = extract_user_info(messages, "Hung Bui")
        self.assertEqual(info["phone"], "0901234567")
        self.assertEqual(info["email"], "hungbui@gmail.com")

    def test_detect_city_from_ad_context(self):
        """Test 3: Detect city from ad context and page messages."""
        # From ad context
        self.assertEqual(
            detect_city("Thiền định miễn phí tại Hà Nội", []),
            "Hà Nội"
        )
        # From page message mentioning HCM landmark
        msgs = [{"sender": "Page", "content": "Địa chỉ: 02 Xô Viết Nghệ Tĩnh, Bình Thạnh"}]
        self.assertEqual(detect_city("", msgs), "TP. Hồ Chí Minh")

        # Unknown when no city keywords
        self.assertEqual(detect_city("Lớp thiền miễn phí", []), "Unknown")

    def test_parse_ad_ids_from_labels(self):
        """Test 4: Extract ad IDs from label text."""
        text = "Intake ​ ad_id.6930299765389 ad_id.6892367141614 messenger_ads"
        result = parse_ad_ids(text)
        self.assertEqual(len(result), 2)
        self.assertIn("6930299765389", result)
        self.assertIn("6892367141614", result)

        # No ad IDs
        self.assertEqual(parse_ad_ids("No ads here"), [])


# ===================================================================
# Phase 2: L3 Pipeline Enrichment Tests (Tests 5-7)
# ===================================================================


class TestL3PipelineEnrichment(unittest.TestCase):
    """L3 build → enrich → MasHandoff using fixture data."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture("hung_bui_thread")

    def test_build_thread_record_from_fixture(self):
        """Test 5: Build thread record from fixture visible_thread."""
        meta = self.fixture["metadata"]
        record = build_thread_record(meta["page_id"], self.fixture["visible_thread"])

        self.assertEqual(record.page_id, "1548373332058326")
        self.assertEqual(record.thread_name, "Hung Bui")
        self.assertTrue(record.thread_id.startswith("1548373332058326_"))
        self.assertEqual(record.dom_index, 0)
        self.assertIn("lớp thiền", record.preview_text)

    def test_enrich_produces_complete_mas_handoff(self):
        """Test 6: Full enrichment → MasHandoff with seeker, messages, ad_ids."""
        meta = self.fixture["metadata"]
        thread_record = build_thread_record(meta["page_id"], self.fixture["visible_thread"])

        enriched = enrich_thread_record(
            thread_record,
            self.fixture["js_messages"],
            extract_user_info=extract_user_info,
            detect_city=detect_city,
            ad_context=self.fixture["ad_context"],
            fb_url=meta["fb_url"],
            ad_ids=self.fixture["ad_ids"],
        )

        # Verify enriched record
        self.assertEqual(enriched.city, "Hà Nội")
        self.assertEqual(enriched.user_info["phone"], "0901234567")
        self.assertEqual(enriched.fb_url, "100001005716854")
        self.assertEqual(enriched.ad_ids, ["6930299765389"])

        # Verify MasHandoff
        handoff = enriched.mas_handoff
        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.thread_name, "Hung Bui")
        self.assertEqual(handoff.seeker.name, "Hung Bui")
        self.assertEqual(handoff.seeker.city, "Hà Nội")
        self.assertEqual(handoff.seeker.phone, "0901234567")
        self.assertEqual(len(handoff.messages), 8)

        # Verify message ordering
        senders = [m.sender for m in handoff.messages]
        self.assertEqual(senders[0], "Customer")
        self.assertIn("Page", senders)

    def test_enriched_messages_skip_empty(self):
        """Test 7: Empty messages are filtered out during enrichment."""
        meta = self.fixture["metadata"]
        thread_record = build_thread_record(meta["page_id"], self.fixture["visible_thread"])

        # Add empty messages to the JS messages
        js_messages_with_empty = self.fixture["js_messages"] + [
            {"sender": "Customer", "text": "", "timestamp": "Mar 22, 2026"},
            {"sender": "Customer", "text": "   ", "timestamp": "Mar 22, 2026"},
        ]

        enriched = enrich_thread_record(
            thread_record,
            js_messages_with_empty,
            extract_user_info=extract_user_info,
            detect_city=detect_city,
        )

        # Should still have 8 messages (empty ones filtered)
        self.assertEqual(len(enriched.messages), 8)


# ===================================================================
# Phase 3: L4 Persistence and Read-back Tests (Tests 8-12)
# ===================================================================


class TestL4PersistenceAndReadback(unittest.TestCase):
    """L4 write + L5 seeker_tools read-back using in-memory SQLite."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture("hung_bui_thread")

    def setUp(self):
        """Fresh in-memory DB for each test."""
        self.conn = create_test_db()
        self.persist_result = seed_fixture_into_db(self.conn, self.fixture)
        self.thread_id = self.persist_result["thread_id"]

    def tearDown(self):
        self.conn.close()

    def test_persist_and_readback_thread(self):
        """Test 8: Persist and read back thread via raw SQL."""
        # Check thread was written
        thread = self.conn.execute(
            "SELECT * FROM threads WHERE id = ?", (self.thread_id,)
        ).fetchone()
        self.assertIsNotNone(thread)
        self.assertEqual(thread["page_id"], "1548373332058326")
        self.assertEqual(thread["thread_name"], "Hung Bui")

        # Check messages were written
        msgs = self.conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY seq",
            (self.thread_id,)
        ).fetchall()
        self.assertEqual(len(msgs), 8)
        self.assertEqual(msgs[0]["sender"], "Customer")

        # Check persist result
        self.assertEqual(self.persist_result["messages_added"], 8)
        self.assertEqual(self.persist_result["city"], "Hà Nội")
        self.assertEqual(self.persist_result["ad_ids_count"], 1)

    def test_persist_and_lookup_seeker(self):
        """Test 9: Persist → lookup_seeker() returns correct profile."""
        # Monkey-patch get_db_connection to use our test DB
        import fb_pipeline.persistence.l4_sqlite_store as store_mod
        original_get_db = store_mod.get_db_connection

        def mock_get_db(*args, **kwargs):
            return self.conn
        store_mod.get_db_connection = mock_get_db

        try:
            from adk_agents.tools.l5_seeker_tools import lookup_seeker
            # Force reimport to pick up patched connection
            import importlib
            import adk_agents.tools.l5_seeker_tools as st_mod
            importlib.reload(st_mod)

            result = st_mod.lookup_seeker(self.thread_id)
            self.assertEqual(result["status"], "found")
            self.assertEqual(result["name"], "Hung Bui")
            self.assertEqual(result["phone"], "0901234567")
            self.assertEqual(result["city"], "Hà Nội")
        finally:
            store_mod.get_db_connection = original_get_db

    def test_persist_and_get_thread_messages(self):
        """Test 10: Persist → get_thread_messages() returns correct messages."""
        import fb_pipeline.persistence.l4_sqlite_store as store_mod
        original_get_db = store_mod.get_db_connection

        def mock_get_db(*args, **kwargs):
            return self.conn
        store_mod.get_db_connection = mock_get_db

        try:
            import importlib
            import adk_agents.tools.l5_seeker_tools as st_mod
            importlib.reload(st_mod)

            result = st_mod.get_thread_messages(self.thread_id)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["count"], 8)

            # Verify message content
            messages = result["messages"]
            self.assertEqual(messages[0]["sender"], "Customer")
            self.assertIn("Sahaja Yoga", messages[0]["content"])

            # Last message should be phone number
            self.assertEqual(messages[-1]["content"], "0901234567")
        finally:
            store_mod.get_db_connection = original_get_db

    def test_persist_and_find_unreplied_threads(self):
        """Test 11: Persist → find_unreplied_threads() finds the new thread."""
        import fb_pipeline.persistence.l4_sqlite_store as store_mod
        original_get_db = store_mod.get_db_connection

        def mock_get_db(*args, **kwargs):
            return self.conn
        store_mod.get_db_connection = mock_get_db

        try:
            import importlib
            import adk_agents.tools.l5_seeker_tools as st_mod
            importlib.reload(st_mod)

            result = st_mod.find_unreplied_threads("1548373332058326")
            self.assertEqual(result["status"], "success")
            self.assertGreater(result["count"], 0)

            thread_ids = [t["thread_id"] for t in result["threads"]]
            self.assertIn(self.thread_id, thread_ids)
        finally:
            store_mod.get_db_connection = original_get_db

    def test_log_auto_reply_marks_thread_replied(self):
        """Test 12: After log_auto_reply(), thread is no longer unreplied."""
        import fb_pipeline.persistence.l4_sqlite_store as store_mod
        original_get_db = store_mod.get_db_connection

        # Wrap conn so that close() is a no-op (production code calls conn.close())
        class NonCloseableConn:
            def __init__(self, real_conn):
                self._conn = real_conn
            def close(self):
                pass  # Ignore close — keep test DB alive
            def __getattr__(self, name):
                return getattr(self._conn, name)

        def mock_get_db(*args, **kwargs):
            return NonCloseableConn(self.conn)
        store_mod.get_db_connection = mock_get_db

        try:
            import importlib
            import adk_agents.tools.l5_seeker_tools as st_mod
            import adk_agents.tools.l5_facebook_tools as ft_mod
            importlib.reload(st_mod)
            importlib.reload(ft_mod)

            # First verify it's unreplied
            result = st_mod.find_unreplied_threads("1548373332058326")
            self.assertGreater(result["count"], 0)

            # Log auto-reply
            log_result = ft_mod.log_auto_reply(
                self.thread_id,
                "Chào bạn! Cảm ơn bạn đã quan tâm.",
                agent_name="responder"
            )
            self.assertEqual(log_result["status"], "logged")

            # Now it should no longer be unreplied
            result2 = st_mod.find_unreplied_threads("1548373332058326")
            thread_ids = [t["thread_id"] for t in result2.get("threads", [])]
            self.assertNotIn(self.thread_id, thread_ids)
        finally:
            store_mod.get_db_connection = original_get_db


# ===================================================================
# Phase 4: MAS Pipeline Integration Tests (Tests 13-15)
# ===================================================================


@requires_llm
class TestMASPipelineIntegration(unittest.TestCase):
    """Full ADK classify → respond E2E using fixture data + live LLM."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture("hung_bui_thread")

    def _run_mas_pipeline(self, messages_text: str) -> dict:
        """Helper: run ADK pipeline with message text."""
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        from adk_agents.agent import root_agent

        session_service = InMemorySessionService()
        runner = Runner(
            agent=root_agent,
            app_name="test_e2e_pipeline",
            session_service=session_service,
        )
        session = asyncio.run(
            session_service.create_session(
                app_name="test_e2e_pipeline", user_id="e2e_tester"
            )
        )

        user_msg = types.Content(
            role="user",
            parts=[types.Part(text=messages_text)],
        )

        result = {"classification": "", "reply_text": "", "events": []}
        for event in runner.run(
            user_id="e2e_tester",
            session_id=session.id,
            new_message=user_msg,
        ):
            result["events"].append(event)
            if hasattr(event, "content") and event.content and event.content.parts:
                text = event.content.parts[0].text or ""
                author = getattr(event, "author", "")
                if author == "MessageClassifier":
                    result["classification"] = text
                elif author == "Responder":
                    result["reply_text"] = text

        # Fallback: last text is reply
        if not result["reply_text"]:
            for event in reversed(result["events"]):
                if hasattr(event, "content") and event.content and event.content.parts:
                    text = event.content.parts[0].text
                    if text and getattr(event, "author", "") != "MessageClassifier":
                        result["reply_text"] = text
                        break

        return result

    def test_mas_classifies_hung_bui_thread(self):
        """Test 13: ADK classifier produces meaningful classification output."""
        conversation = "\n".join([
            f"[{m['sender']}] {m['text']}"
            for m in self.fixture["js_messages"]
        ])

        result = self._run_mas_pipeline(
            f"Process this inbox thread:\n{conversation}"
        )

        self.assertTrue(result["classification"], "Classifier produced no output")

        # Classifier should produce substantive analysis (not just a few words)
        self.assertGreater(
            len(result["classification"]), 20,
            f"Classification too short: {result['classification']}"
        )

        # Should contain at least one intent-related keyword
        classification_lower = result["classification"].lower()
        intent_keywords = [
            "intent", "registration", "inquiry", "greeting", "question",
            "sentiment", "classify", "language", "vietnamese", "urgency",
            "interest", "meditation", "sign up", "register", "đăng ký",
        ]
        has_intent_keyword = any(kw in classification_lower for kw in intent_keywords)
        self.assertTrue(
            has_intent_keyword,
            f"Classification lacks intent keywords: {result['classification'][:200]}"
        )

    def test_mas_responds_in_vietnamese(self):
        """Test 14: Responder replies in Vietnamese to Vietnamese input."""
        conversation = "\n".join([
            f"[{m['sender']}] {m['text']}"
            for m in self.fixture["js_messages"]
        ])

        result = self._run_mas_pipeline(
            f"Process this inbox thread:\n{conversation}"
        )

        reply = result["reply_text"]
        self.assertTrue(reply, "No reply generated")

        # Vietnamese text contains diacritical marks
        vn_chars = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
        has_vietnamese = any(c in vn_chars for c in reply.lower())
        self.assertTrue(has_vietnamese, f"Reply not in Vietnamese: {reply}")

    def test_full_pipeline_fixture_to_response(self):
        """Test 15: Full fixture → persist → lookup → ADK → reply text."""
        # Step 1: Persist fixture data
        conn = create_test_db()
        persist_result = seed_fixture_into_db(conn, self.fixture)
        thread_id = persist_result["thread_id"]

        # Step 2: Read back messages
        msgs = conn.execute(
            "SELECT sender, content FROM messages WHERE thread_id = ? ORDER BY seq",
            (thread_id,)
        ).fetchall()

        # Step 3: Build conversation text
        conversation = "\n".join([
            f"[{m['sender']}] {m['content']}" for m in msgs
        ])

        # Step 4: Read seeker profile
        user = conn.execute(
            "SELECT * FROM users WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        conn.close()

        seeker_context = (
            f"Name: {user['thread_name']}, Phone: {user['phone']}, "
            f"City: {user['city']}, Stage: {user['lead_stage']}"
        )

        # Step 5: Run MAS pipeline
        result = self._run_mas_pipeline(
            f"Process this inbox thread.\n\n"
            f"Thread messages:\n{conversation}\n\n"
            f"Seeker context:\n{seeker_context}"
        )

        # Verify full pipeline worked
        self.assertTrue(result["classification"], "No classification")
        self.assertTrue(result["reply_text"], "No reply")
        self.assertGreater(len(result["reply_text"]), 10, "Reply too short")


if __name__ == "__main__":
    unittest.main()
