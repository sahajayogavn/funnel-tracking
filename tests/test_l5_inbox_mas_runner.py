import os
import sys
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class DummySession:
    def __init__(self):
        self.id = "session-1"


class DummySessionService:
    def __init__(self):
        self.calls = []

    async def create_session(self, **kwargs):
        self.calls.append(kwargs)
        return DummySession()


class DummyRunner:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        return []


class DummyPart:
    def __init__(self, text=None):
        self.text = text


class DummyContent:
    def __init__(self, role=None, parts=None):
        self.role = role
        self.parts = parts or []


class DummyTypes:
    Content = DummyContent
    Part = DummyPart


class TestRunAdkPipeline:
    def test_load_knowledge_context_includes_all_sources(self):
        from tools.l5_inbox_mas_runner import KNOWLEDGE_FILES, load_knowledge_context

        result = load_knowledge_context()

        for relative_path in KNOWLEDGE_FILES:
            assert f"## Source: {relative_path}" in result

    def test_run_adk_pipeline_populates_session_state(self):
        from tools import l5_inbox_mas_runner as runner_mod

        session_service = DummySessionService()
        runner_instances = []

        def runner_factory(**kwargs):
            runner = DummyRunner(**kwargs)
            runner_instances.append(runner)
            return runner

        with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
             patch("google.adk.runners.Runner", side_effect=runner_factory), \
             patch("google.genai.types", DummyTypes), \
             patch.object(runner_mod, "load_knowledge_context", return_value="KB BODY"):
            result = runner_mod.run_adk_pipeline(
                thread_messages=[
                    {"sender": "Customer", "content": "Xin chào"},
                    {"sender": "Page", "content": "Chào bạn"},
                ],
                seeker_context={"name": "Lan", "city": "Hà Nội", "lead_stage": "Seeker"},
            )

        assert session_service.calls, "create_session was not called"
        state = session_service.calls[0]["state"]
        assert state["thread_messages"] == "[Customer] Xin chào\n[Page] Chào bạn"
        assert '"name": "Lan"' in state["seeker_context"]
        assert state["knowledge_context"] == "KB BODY"

        assert runner_instances, "Runner was not created"
        assert runner_instances[0].kwargs["session_service"] is session_service
        assert runner_instances[0].run_calls[0]["session_id"] == "session-1"

        assert result["thread_messages"] == state["thread_messages"]
        assert result["knowledge_context"] == "KB BODY"
