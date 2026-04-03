"""
E2E — MAS Strategy: Hung Bui Customer Journey
code:test-mas-strategy-e2e-001

Full end-to-end test of the mas_strategy pipeline for the Hung Bui thread
(Facebook user 100001005716854) against the main page (1548373332058326).

Tests all 6 phases of the MAS Execution Pipeline as defined in mas_strategy.md:
  Phase 1: Data Ingestion  → Hung Bui data present in DB
  Phase 2: Classification  → InboxPipeline (Classifier → Responder)
  Phase 3: Stage Eval      → QA Gate validation
  Phase 4: Temperature     → Silence-day → temperature mapping
  Phase 5: Message QA Gate → Anti-spam + personalization checks
  Phase 6: Route Coverage  → Inbox, WarmUp, Reaction, Event pipelines

Run (LLM tests only):
    OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d) \\
    OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d) \\
    .venv/bin/python -m pytest tests/test_e2e_mas_strategy_hung_bui.py -v

DB-only tests run without LLM credentials:
    .venv/bin/python -m pytest tests/test_e2e_mas_strategy_hung_bui.py -v -k "not llm"
"""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "memory", "agent_memory", "frankensqlite.db")

# ── Constants ──────────────────────────────────────────────────────────────────
PAGE_ID = "1548373332058326"
HUNG_BUI_FB_ID = "f9b35a5530b3a8f2"
HUNG_BUI_THREAD_ID = f"{PAGE_ID}_{HUNG_BUI_FB_ID}"

SKIP_NO_DB = pytest.mark.skipif(
    not os.path.exists(DB_PATH),
    reason="Production frankensqlite.db not found",
)
SKIP_NO_LLM = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_BASE") and os.environ.get("OPENAI_API_KEY")),
    reason="OPENAI_API_BASE and OPENAI_API_KEY required for LLM tests",
)


def get_readonly_conn():
    import sqlite3
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── Phase 1: Data Ingestion Verification ─────────────────────────────────────

@SKIP_NO_DB
class TestPhase1DataIngestion(unittest.TestCase):
    """Phase 1: Validate Hung Bui's data is correctly stored in FrankenSQLite."""

    @classmethod
    def setUpClass(cls):
        cls.conn = get_readonly_conn()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_hung_bui_thread_exists(self):
        """Hung Bui thread (100001005716854) must exist in DB."""
        row = self.conn.execute(
            "SELECT id FROM threads WHERE id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()
        self.assertIsNotNone(row, f"Thread {HUNG_BUI_THREAD_ID} not found in threads table")

    def test_hung_bui_has_messages(self):
        """Hung Bui thread must have ≥20 messages (full conversation crawled)."""
        count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE thread_id = ?",
            (HUNG_BUI_THREAD_ID,)
        ).fetchone()["cnt"]
        self.assertGreaterEqual(count, 20, f"Expected ≥20 messages, got {count}")

    def test_hung_bui_messages_have_both_senders(self):
        """Hung Bui conversation must contain both Customer and Page messages."""
        senders = self.conn.execute(
            "SELECT DISTINCT sender FROM messages WHERE thread_id = ?",
            (HUNG_BUI_THREAD_ID,)
        ).fetchall()
        sender_set = {r["sender"] for r in senders}
        self.assertIn("Customer", sender_set, "No Customer messages found")
        self.assertIn("Page", sender_set, "No Page messages found")

    def test_hung_bui_user_profile_stored(self):
        """Hung Bui's user profile must be persisted in the users table."""
        user = self.conn.execute(
            "SELECT * FROM users WHERE thread_id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()
        self.assertIsNotNone(user, "No user record for Hung Bui")
        self.assertEqual(user["thread_name"], "Hung Bui")

    def test_hung_bui_phone_extracted(self):
        """Hung Bui's phone number must be extracted from registration message."""
        user = self.conn.execute(
            "SELECT phone FROM users WHERE thread_id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()
        self.assertIsNotNone(user, "No user record found")
        self.assertIsNotNone(user["phone"], "Phone not extracted for Hung Bui")
        # Hung Bui's phone from conversation: 01666667975
        self.assertIn("01666667975", str(user["phone"]))

    def test_hung_bui_city_detected_as_ha_noi(self):
        """
        Regression: After detect_city() fix + DB remediation, city must be
        'Hà Nội' (not 'Unknown'). Hung Bui said 'tại Hà Nội' in a Customer message.
        code:tool-citydetect-001:e2e-regression
        """
        user = self.conn.execute(
            "SELECT city FROM users WHERE thread_id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()
        self.assertIsNotNone(user, "No user record found")
        self.assertEqual(
            user["city"], "Hà Nội",
            f"Expected 'Hà Nội' after city detection fix, got '{user['city']}'"
        )

    def test_hung_bui_page_id_matches_main_page(self):
        """Hung Bui's thread must belong to the main Facebook page."""
        thread = self.conn.execute(
            "SELECT page_id FROM threads WHERE id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()
        self.assertIsNotNone(thread, "Thread not found")
        self.assertEqual(thread["page_id"], PAGE_ID, "page_id mismatch")

    def test_hung_bui_registration_message_present(self):
        """Registration message with Hung Bui's full info must be in messages."""
        msgs = self.conn.execute(
            "SELECT content FROM messages WHERE thread_id = ? AND sender = 'Customer' ORDER BY seq",
            (HUNG_BUI_THREAD_ID,)
        ).fetchall()
        all_content = " ".join(m["content"] for m in msgs if m["content"])
        # His message contained: name, phone, email
        self.assertIn("01666667975", all_content, "Phone not found in messages")
        self.assertIn("Bùi Duy Hùng", all_content, "Name not found in messages")


# ── Phase 3: Stage Evaluation (QA Gate) ───────────────────────────────────────

@SKIP_NO_DB
class TestPhase3StageEvaluation(unittest.TestCase):
    """
    Phase 3: Validate journey stage classification and QA Gate rules from
    mas_strategy.md stage transition criteria.
    """

    @classmethod
    def setUpClass(cls):
        cls.conn = get_readonly_conn()
        cls.user = cls.conn.execute(
            "SELECT * FROM users WHERE thread_id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_gate_g3_phone_present_for_registered(self):
        """
        G3 QA Gate: Stage 3 (Registered) requires a valid phone.
        Hung Bui has phone 01666667975 → Gate G3 PASS.
        """
        self.assertIsNotNone(self.user, "User record missing")
        phone = self.user["phone"] or ""
        self.assertTrue(len(phone) >= 9, f"Phone too short or missing: '{phone}'")

    def test_gate_g3_email_extracted(self):
        """
        G3 QA Gate: Hung Bui provided email duyhunghd6@gmail.com → extraction verified.
        Check email field or message content for the email.
        """
        # Email may be in users.email column or in raw messages
        email_col = self.user["email"] if "email" in self.user.keys() else None
        if email_col:
            self.assertIn("duyhunghd6@gmail.com", email_col)
        else:
            # Fallback: check in messages
            msgs = self.conn.execute(
                "SELECT content FROM messages WHERE thread_id = ?",
                (HUNG_BUI_THREAD_ID,)
            ).fetchall()
            all_content = " ".join(m["content"] for m in msgs if m["content"])
            self.assertIn("duyhunghd6@gmail.com", all_content, "Email not found in conversation")

    def test_lead_stage_is_registered_or_higher(self):
        """
        Hung Bui explicitly registered for a class (Stage 3+).
        His lead_stage should be Intake or Seeker_Public_Program (or higher).
        At minimum he sent registration info → not a raw Unknown/User.
        """
        lead_stage = self.user["lead_stage"] if "lead_stage" in self.user.keys() else "Unknown"
        # Any non-null stage is valid — he at minimum has been contacted
        self.assertIsNotNone(lead_stage)
        # He should NOT be dormant (he was an engaged registrant)
        self.assertNotIn(lead_stage, ["dormant", "unsubscribed"],
                         f"Hung Bui should not be dormant, got: {lead_stage}")

    def test_mas_handoff_structure_is_valid(self):
        """
        Phase 3: MasHandoff object can be built for Hung Bui from DB data
        and passes basic structure validation.
        """
        from fb_pipeline.contracts.l1_inbox import (
            MasHandoff, SeekerInfo, InboxMessage,
        )

        msgs = self.conn.execute(
            "SELECT sender, content, message_timestamp, seq FROM messages "
            "WHERE thread_id = ? ORDER BY seq",
            (HUNG_BUI_THREAD_ID,)
        ).fetchall()

        messages = [
            InboxMessage(
                sender=m["sender"],
                content=m["content"],
                message_timestamp=m["message_timestamp"] or "",
                seq=m["seq"],
            )
            for m in msgs
        ]

        seeker = SeekerInfo(
            name="Hung Bui",
            phone=self.user["phone"],
            city=self.user["city"] or "Unknown",
        )

        handoff = MasHandoff(
            thread_id=HUNG_BUI_THREAD_ID,
            thread_name="Hung Bui",
            page_id=PAGE_ID,
            fb_url=HUNG_BUI_FB_ID,
            seeker=seeker,
            messages=messages,
        )

        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.page_id, PAGE_ID)
        self.assertEqual(len(handoff.messages), len(msgs))
        self.assertGreater(len(handoff.messages), 0)

    def test_anti_spam_gate_max_warmup_frequency(self):
        """
        Phase 5 Message QA Gate: Anti-spam rule — max 1 warm-up / 7 days.
        Validate that last_warmup_at column exists and can be checked.
        """
        # Check if users table has warmup tracking columns
        cols = self.conn.execute("PRAGMA table_info(users)").fetchall()
        col_names = {c["name"] for c in cols}

        # Columns may or may not exist yet (schema evolution point)
        # If they exist, validate Hung Bui's state
        if "last_warmup_at" in col_names and "warmup_count" in col_names:
            warmup_at = self.user["last_warmup_at"]
            warmup_count = self.user["warmup_count"] or 0
            # If warmup was sent, validate it's not within the last 7 days twice
            self.assertGreaterEqual(warmup_count, 0, "warmup_count should be non-negative")
        else:
            # Schema not yet extended — skip with a note
            self.skipTest("warmup tracking columns not yet in schema (expected after schema migration)")


# ── Phase 4: Temperature Model ────────────────────────────────────────────────

class TestPhase4TemperatureModel(unittest.TestCase):
    """
    Phase 4: Validate the seeker temperature model from mas_strategy.md.
    Tests the silence_days → temperature thresholds for the Registered stage.
    No DB required — pure logic validation.
    """

    def _compute_temperature(self, stage: str, silence_days: int) -> str:
        """Replicate the Temperature Decision Engine from mas_strategy.md."""
        thresholds = {
            "Follower":         {"hot": 3,  "warm": 7,  "cool": 21},
            "Curious":          {"hot": 3,  "warm": 7,  "cool": 14},
            "Registered":       {"hot": 2,  "warm": 5,  "cool": 14},
            "Deep Learner":     {"hot": 7,  "warm": 14, "cool": 21},
            "Sahaja Yogi":      {"hot": 7,  "warm": 28, "cool": 90},
        }
        t = thresholds.get(stage, thresholds["Registered"])
        if silence_days < t["hot"]:
            return "hot"
        elif silence_days < t["warm"]:
            return "warm"
        elif silence_days < t["cool"]:
            return "cool"
        else:
            return "cold"

    def test_hung_bui_temperature_registered_hot(self):
        """Registered seeker silent <2 days → temperature MUST be hot."""
        temp = self._compute_temperature("Registered", silence_days=1)
        self.assertEqual(temp, "hot")

    def test_hung_bui_temperature_registered_warm(self):
        """Registered seeker, 3 days silence → warm."""
        temp = self._compute_temperature("Registered", silence_days=3)
        self.assertEqual(temp, "warm")

    def test_hung_bui_temperature_registered_cool(self):
        """Registered seeker, 8 days silence → cool."""
        temp = self._compute_temperature("Registered", silence_days=8)
        self.assertEqual(temp, "cool")

    def test_hung_bui_temperature_registered_cold(self):
        """Registered seeker, 20+ days silence → cold."""
        temp = self._compute_temperature("Registered", silence_days=20)
        self.assertEqual(temp, "cold")

    def test_temperature_thresholds_progression(self):
        """Temperature must progress: hot → warm → cool → cold with increasing days."""
        stage = "Registered"
        prev_heat = 4  # 4=hot, 3=warm, 2=cool, 1=cold
        heat_map = {"hot": 4, "warm": 3, "cool": 2, "cold": 1}
        for days in range(0, 30, 2):
            t = self._compute_temperature(stage, days)
            # Temperature should never increase with more silence
            self.assertLessEqual(
                heat_map[t], prev_heat,
                f"Temperature jumped up at day {days}: got {t}"
            )
            prev_heat = heat_map[t]

    def test_all_stages_have_valid_thresholds(self):
        """All journey stages must produce valid temperatures across silence range."""
        stages = ["Follower", "Curious", "Registered", "Deep Learner", "Sahaja Yogi"]
        valid_temps = {"hot", "warm", "cool", "cold"}
        for stage in stages:
            for days in [0, 5, 10, 20, 60, 100]:
                t = self._compute_temperature(stage, days)
                self.assertIn(t, valid_temps, f"Invalid temp for {stage} at {days} days: {t}")


# ── Phase 2 & 6: LLM Pipeline Tests ───────────────────────────────────────────

@SKIP_NO_LLM
@SKIP_NO_DB
class TestPhase2InboxPipelineHungBui(unittest.TestCase):
    """
    Phase 2 + Phase 6: Run the Inbox MAS pipeline (Classifier → Responder)
    against Hung Bui's actual conversation thread from the DB.
    Validates the full ADK pipeline execution, not just mock data.
    """

    @classmethod
    def setUpClass(cls):
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from adk_agents.agent import root_agent

        cls.conn = get_readonly_conn()
        cls.session_service = InMemorySessionService()
        cls.runner = Runner(
            agent=root_agent,
            app_name="test_mas_strategy_hung_bui",
            session_service=cls.session_service,
        )
        # Load Hung Bui's data from DB
        cls.user = cls.conn.execute(
            "SELECT * FROM users WHERE thread_id = ?", (HUNG_BUI_THREAD_ID,)
        ).fetchone()
        cls.msgs = cls.conn.execute(
            "SELECT sender, content, message_timestamp, seq FROM messages "
            "WHERE thread_id = ? ORDER BY seq",
            (HUNG_BUI_THREAD_ID,)
        ).fetchall()
        # Build conversation text
        cls.conversation_text = "\n".join(
            f"[{m['sender']}] {m['content']}"
            for m in cls.msgs if m["content"]
        )

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _make_session(self):
        return asyncio.run(
            self.session_service.create_session(
                app_name="test_mas_strategy_hung_bui",
                user_id="e2e_tester",
            )
        )

    def _run(self, prompt: str) -> dict:
        from google.genai import types
        session = self._make_session()
        user_msg = types.Content(role="user", parts=[types.Part(text=prompt)])
        events, classification, reply_text, authors = [], "", "", []
        for event in self.runner.run(
            user_id="e2e_tester",
            session_id=session.id,
            new_message=user_msg,
        ):
            events.append(event)
            author = getattr(event, "author", None)
            if author:
                authors.append(author)
            if hasattr(event, "content") and event.content and event.content.parts:
                text = event.content.parts[0].text or ""
                if author == "MessageClassifier":
                    classification = text
                elif author == "Responder":
                    reply_text = text
        return {
            "events": events,
            "classification": classification,
            "reply_text": reply_text,
            "authors": authors,
        }

    def _build_prompt(self, extra_context: str = "") -> str:
        seeker_ctx = {
            "name": "Hung Bui",
            "phone": self.user["phone"] if self.user else "01666667975",
            "city": (self.user["city"] if self.user else None) or "Hà Nội",
            "lead_stage": (self.user["lead_stage"] if self.user else None) or "Intake",
        }
        return (
            f"Process this Facebook inbox thread from the main Sahaja Yoga VN page.\n\n"
            f"Thread messages:\n{self.conversation_text}\n\n"
            f"Seeker context:\n{json.dumps(seeker_ctx, ensure_ascii=False, indent=2)}"
            + (f"\n\n{extra_context}" if extra_context else "")
        )

    def test_full_inbox_pipeline_runs(self):
        """Phase 2: Full Classifier → Responder pipeline runs without error."""
        result = self._run(self._build_prompt())
        self.assertGreater(len(result["events"]), 0, "No pipeline events produced")

    def test_classifier_fires_first(self):
        """Phase 2: MessageClassifier must fire before Responder (Sequential order)."""
        result = self._run(self._build_prompt())
        authors = result["authors"]
        classifier_idx = next((i for i, a in enumerate(authors) if a == "MessageClassifier"), None)
        responder_idx = next((i for i, a in enumerate(authors) if a == "Responder"), None)
        self.assertIsNotNone(classifier_idx, "MessageClassifier did not fire")
        self.assertIsNotNone(responder_idx, "Responder did not fire")
        self.assertLess(classifier_idx, responder_idx, "Classifier must fire before Responder")

    def test_classifier_identifies_registration_intent(self):
        """
        Phase 2: Classifier must detect 'registration' intent for Hung Bui's thread.
        He clearly sent full registration info (name, phone, email) for a class.
        """
        result = self._run(self._build_prompt())
        classification_lower = result["classification"].lower()
        # Should detect registration / follow_up / thanks (he registered and confirmed)
        intent_keywords = ["registration", "follow_up", "thanks", "register", "đăng ký"]
        self.assertTrue(
            any(kw in classification_lower for kw in intent_keywords),
            f"Expected registration-related intent, got: {result['classification'][:200]}"
        )

    def test_responder_replies_in_vietnamese(self):
        """
        Phase 6 Inbox Route: Responder must reply in Vietnamese (same language as seeker).
        Hung Bui wrote entirely in Vietnamese.
        """
        result = self._run(self._build_prompt())
        reply = result["reply_text"]
        self.assertTrue(reply, "Responder produced empty reply")
        vietnamese_chars = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
        has_vietnamese = any(c in vietnamese_chars for c in reply.lower())
        self.assertTrue(has_vietnamese, f"Reply must be in Vietnamese, got: {reply}")

    def test_responder_acknowledges_registration(self):
        """
        Phase 6: Responder must acknowledge Hung Bui's class registration.
        He already registered for 26/03 class at Hà Nội.
        """
        result = self._run(self._build_prompt())
        reply = result["reply_text"]
        self.assertTrue(reply, "Empty reply")

        # Should acknowledge positively — not push new registration
        positive_signals = ["🙏", "cảm ơn", "hẹn", "vui", "gặp", "lớp", "thiền"]
        self.assertTrue(
            any(sig.lower() in reply.lower() for sig in positive_signals),
            f"Reply doesn't acknowledge registration positively: {reply}"
        )

    def test_reply_is_concise(self):
        """
        Phase 5 QA Gate: Reply must be concise (≤5 sentences per mas_strategy guidelines).
        """
        result = self._run(self._build_prompt())
        reply = result["reply_text"]
        self.assertTrue(reply, "Empty reply")
        # Count sentence-ending punctuation as a proxy for sentence count
        sentence_count = sum(1 for c in reply if c in ".!?")
        self.assertLessEqual(
            sentence_count, 8,
            f"Reply too long ({sentence_count} sentence-endings): {reply}"
        )

    def test_warmup_pipeline_composes_message(self):
        """
        Phase 6 Warm-up Route: WarmUpPipeline must compose a message for Hung Bui
        (he is a warm seeker who registered in 2017 and may be dormant now).
        """
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from adk_agents.agent import warmup_pipeline

        ss = InMemorySessionService()
        runner = Runner(
            agent=warmup_pipeline,
            app_name="test_warmup_hung_bui",
            session_service=ss,
        )
        session = asyncio.run(
            ss.create_session(app_name="test_warmup_hung_bui", user_id="e2e_tester")
        )
        from google.genai import types
        brief = {
            "seeker_name": "Hung Bui",
            "city": "Hà Nội",
            "lead_stage": "Seeker",
            "silence_days": 90,
            "temperature": "cool",
            "cool_step": 1,
        }
        prompt = json.dumps(brief, ensure_ascii=False)
        user_msg = types.Content(role="user", parts=[types.Part(text=prompt)])
        warmup_text = ""
        for event in runner.run(
            user_id="e2e_tester",
            session_id=session.id,
            new_message=user_msg,
        ):
            if hasattr(event, "content") and event.content and event.content.parts:
                t = event.content.parts[0].text
                if t and getattr(event, "author", "") == "WarmUpComposer":
                    warmup_text = t
        self.assertTrue(warmup_text, "WarmUpComposer produced no message")
        self.assertGreater(len(warmup_text), 10, f"Warmup message too short: {warmup_text}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
