"""
E2E tests for the ADK Sahaja Yoga Inbox MAS pipeline.
code:test-adk-e2e-001

Tests the full MessageClassifier → Responder sequential pipeline
using ADK's Runner + InMemorySessionService. Requires a live LLM
endpoint (OPENAI_API_BASE + OPENAI_API_KEY env vars).

Run with:
    OPENAI_API_BASE=$(echo "aHR0cDovLzEwLjAuMS40Mjo4MzE3L3Yx" | base64 -d) \\
    OPENAI_API_KEY=$(echo "aHVuZ2J1aS0yNTE2" | base64 -d) \\
    .venv/bin/python -m pytest tests/test_adk_e2e.py -v
"""
import asyncio
import os
import sys
import pytest

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# --- Skip if LLM not configured ---
SKIP_REASON = "OPENAI_API_BASE and OPENAI_API_KEY required for E2E tests"
requires_llm = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_BASE") and os.environ.get("OPENAI_API_KEY")),
    reason=SKIP_REASON,
)


@pytest.fixture(scope="module")
def session_service():
    """Create a shared InMemorySessionService for all tests."""
    from google.adk.sessions import InMemorySessionService

    return InMemorySessionService()


@pytest.fixture(scope="module")
def adk_runner(session_service):
    """Create a shared ADK Runner for the root_agent."""
    from google.adk.runners import Runner
    from adk_agents.agent import root_agent

    return Runner(
        agent=root_agent,
        app_name="test_inbox_mas",
        session_service=session_service,
    )


@pytest.fixture
def session(session_service):
    """Create a fresh InMemorySession for each test (await the coroutine)."""
    return asyncio.run(
        session_service.create_session(
            app_name="test_inbox_mas", user_id="tester"
        )
    )


def _run_pipeline(runner, session, message_text: str) -> dict:
    """Helper: send a message through the pipeline and collect results.

    Returns:
        dict with keys: events, classification, reply_text, authors
    """
    from google.genai import types

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=message_text)],
    )

    events = []
    for event in runner.run(
        user_id="tester",
        session_id=session.id,
        new_message=user_msg,
    ):
        events.append(event)

    # Extract results from events
    classification = ""
    reply_text = ""
    authors = []

    for event in events:
        author = getattr(event, "author", None)
        if author:
            authors.append(author)
        if hasattr(event, "content") and event.content and event.content.parts:
            text = event.content.parts[0].text if event.content.parts[0].text else ""
            if author == "MessageClassifier":
                classification = text
            elif author == "Responder":
                reply_text = text

    # Fallback: last event with text is the reply
    if not reply_text and events:
        for event in reversed(events):
            if hasattr(event, "content") and event.content and event.content.parts:
                text = event.content.parts[0].text
                if text and getattr(event, "author", "") != "MessageClassifier":
                    reply_text = text
                    break

    return {
        "events": events,
        "classification": classification,
        "reply_text": reply_text,
        "authors": authors,
    }


# ===================================================================
# Test Cases
# ===================================================================


@requires_llm
class TestPipelineSmoke:
    """Smoke tests: does the pipeline run at all?"""

    def test_pipeline_runs_without_error(self, adk_runner, session):
        """Pipeline completes without raising exceptions."""
        result = _run_pipeline(adk_runner, session, "Hello")
        assert len(result["events"]) > 0, "Pipeline produced no events"

    def test_pipeline_produces_reply(self, adk_runner, session):
        """Pipeline produces a non-empty reply."""
        result = _run_pipeline(adk_runner, session, "Tell me about meditation")
        assert result["reply_text"], "Pipeline produced empty reply"
        assert len(result["reply_text"]) > 10, "Reply is too short"


@requires_llm
class TestClassifierAgent:
    """Tests focused on the MessageClassifier sub-agent."""

    def test_classifier_produces_classification(self, adk_runner, session):
        """Classifier outputs a classification string."""
        result = _run_pipeline(
            adk_runner, session,
            "Hello, I want to learn about Sahaja Yoga meditation"
        )
        assert result["classification"], "Classifier produced no output"

    def test_classifier_detects_intent(self, adk_runner, session):
        """Classifier identifies intent keywords."""
        result = _run_pipeline(
            adk_runner, session,
            "Where are the classes? What time?"
        )
        classification_lower = result["classification"].lower()
        # Should contain at least one expected intent keyword
        intent_keywords = ["question", "greeting", "registration", "intent"]
        assert any(
            kw in classification_lower for kw in intent_keywords
        ), f"Classification missing intent: {result['classification']}"

    def test_classifier_detects_vietnamese(self, adk_runner, session):
        """Classifier detects Vietnamese language."""
        result = _run_pipeline(
            adk_runner, session,
            "Xin chào, cho mình hỏi về lớp thiền"
        )
        classification_lower = result["classification"].lower()
        assert "vi" in classification_lower, (
            f"Classifier did not detect Vietnamese: {result['classification']}"
        )


@requires_llm
class TestResponderAgent:
    """Tests focused on the Responder sub-agent."""

    def test_responder_mentions_free(self, adk_runner, session):
        """Responder mentions classes are free when asked."""
        result = _run_pipeline(
            adk_runner, session,
            "Are the meditation classes free?"
        )
        reply_lower = result["reply_text"].lower()
        assert "free" in reply_lower or "miễn phí" in reply_lower, (
            f"Reply doesn't mention free: {result['reply_text']}"
        )

    def test_responder_asks_registration_details(self, adk_runner, session):
        """Responder asks for name/phone when user wants to register."""
        result = _run_pipeline(
            adk_runner, session,
            "I want to sign up for the meditation class in HCMC"
        )
        reply_lower = result["reply_text"].lower()
        # Should ask for at least one registration detail
        registration_keywords = ["name", "phone", "number", "contact", "register"]
        assert any(
            kw in reply_lower for kw in registration_keywords
        ), f"Reply doesn't ask for registration details: {result['reply_text']}"

    def test_responder_replies_in_vietnamese(self, adk_runner, session):
        """Responder replies in Vietnamese when user writes in Vietnamese."""
        result = _run_pipeline(
            adk_runner, session,
            "Xin chào, mình muốn tìm hiểu về thiền Sahaja Yoga. Lớp học có miễn phí không?"
        )
        reply = result["reply_text"]
        # Vietnamese text contains diacritical marks
        vietnamese_chars = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
        has_vietnamese = any(c in vietnamese_chars for c in reply.lower())
        assert has_vietnamese, (
            f"Reply is not in Vietnamese: {reply}"
        )


@requires_llm
class TestPipelineTrajectory:
    """Tests for the agent execution trajectory (order of sub-agents)."""

    def test_classifier_fires_before_responder(self, adk_runner, session):
        """MessageClassifier runs before Responder in the pipeline."""
        result = _run_pipeline(
            adk_runner, session,
            "Hello, tell me about your classes"
        )
        authors = result["authors"]

        # Find the positions
        classifier_idx = None
        responder_idx = None
        for i, author in enumerate(authors):
            if author == "MessageClassifier" and classifier_idx is None:
                classifier_idx = i
            if author == "Responder" and responder_idx is None:
                responder_idx = i

        assert classifier_idx is not None, (
            f"MessageClassifier not found in authors: {authors}"
        )
        assert responder_idx is not None, (
            f"Responder not found in authors: {authors}"
        )
        assert classifier_idx < responder_idx, (
            f"Classifier (idx={classifier_idx}) should fire before "
            f"Responder (idx={responder_idx}): {authors}"
        )

    def test_both_output_keys_populated(self, adk_runner, session):
        """Both classification and reply_text are populated after pipeline."""
        result = _run_pipeline(
            adk_runner, session,
            "What is Sahaja Yoga meditation?"
        )
        assert result["classification"], "classification output_key is empty"
        assert result["reply_text"], "reply_text output_key is empty"
