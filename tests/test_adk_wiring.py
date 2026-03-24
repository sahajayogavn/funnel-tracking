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


class TestAdkWiring:
    def test_root_agent_is_inbox_pipeline_for_backwards_compatibility(self):
        from adk_agents import agent

        assert agent.root_agent is agent.inbox_pipeline

    def test_run_adk_pipeline_injects_session_state(self):
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

        state = session_service.calls[0]["state"]
        assert state["thread_messages"] == "[Customer] Xin chào\n[Page] Chào bạn"
        assert '"name": "Lan"' in state["seeker_context"]
        assert state["knowledge_context"] == "KB BODY"
        assert runner_instances[0].kwargs["app_name"] == "sahajayoga_inbox"
        assert runner_instances[0].run_calls[0]["session_id"] == "session-1"
        assert result["knowledge_context"] == "KB BODY"

    def test_route_agents_are_wired_into_scheduler_calls(self, monkeypatch):
        import tools.l5_scheduler as sched

        captured = []

        monkeypatch.setattr(
            sched,
            "_run_adk_route",
            lambda agent, app_name, user_id, state, prompt: captured.append({
                "agent": agent.name,
                "app_name": app_name,
                "user_id": user_id,
                "state": state,
                "prompt": prompt,
            }) or [{"author": "X", "text": "like"}, {"author": "Y", "text": "Warm hello"}, {"author": "Z", "text": "Event hello"}],
        )

        reaction = sched.run_adk_reactor({"content": "Cảm ơn nhiều", "sender": "Customer", "item_type": "message"})
        warmup = sched.run_adk_warmup_composer({"name": "Lan"}, {"type": "cool_step_1", "cool_step": 1}, "KB")
        event = sched.run_adk_event_advertiser({"name": "Thiền Âm nhạc", "city": "Hà Nội"}, {"name": "Lan"}, "KB")

        assert reaction == "like"
        assert warmup == "Event hello"
        assert event == "Event hello"
        assert [item["agent"] for item in captured] == ["Reactor", "WarmUpComposer", "EventAdvertiser"]
        assert captured[0]["app_name"] == "sahajayoga_reactor"
        assert captured[1]["state"]["strategy_type"] == "cool_step_1"
        assert captured[1]["state"]["cool_step"] == 1
        assert '"knowledge_context": "KB"' in captured[1]["state"]["warmup_brief"]
        assert "event_details" in captured[2]["state"]
        assert '"knowledge_context": "KB"' in captured[2]["state"]["event_details"]
