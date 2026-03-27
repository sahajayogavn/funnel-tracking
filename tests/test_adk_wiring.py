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

    def test_responder_prompt_has_output_rule_and_anti_reflection(self):
        """Responder instruction must begin with OUTPUT RULE to suppress reasoning leaks."""
        from adk_agents.agent import responder

        instruction = responder.instruction
        assert instruction.startswith("OUTPUT RULE"), (
            "Responder instruction must start with 'OUTPUT RULE'"
        )
        assert "MUST NOT output any thoughts" in instruction
        assert "BAD" in instruction and "GOOD" in instruction
        assert responder.output_key == "reply_text"

    def test_run_inbox_cycle_renavigates_before_reply_loop(self):
        """run_inbox_cycle must call page.goto() for Step 2b before processing threads."""
        import tools.l5_inbox_mas_runner as runner_mod

        goto_calls = []

        class MockPage:
            url = "https://business.facebook.com/latest/inbox/all?asset_id=PAGE123"

            def goto(self, url, **kwargs):
                goto_calls.append(url)

            def wait_for_selector(self, sel, **kwargs):
                pass

            def wait_for_timeout(self, ms):
                pass

        class MockSession:
            page = MockPage()

            def close_page(self):
                pass

        class MockCursor:
            def execute(self, *a): pass
            def fetchone(self): return None

        class MockConn:
            def cursor(self): return MockCursor()
            def close(self): pass

        with patch.object(runner_mod, "attach_to_authorized_session", return_value=MockSession()), \
             patch.object(runner_mod, "get_db_connection", return_value=MockConn()), \
             patch.object(runner_mod, "scrape_inbox",
                          return_value={"new_threads": 0, "new_messages": 0, "skipped_threads": 0}), \
             patch.object(runner_mod, "record_fetch", return_value=None), \
             patch("adk_agents.tools.seeker_tools.find_unreplied_threads",
                   return_value={"status": "success", "count": 1,
                                 "threads": [{"thread_id": "t1", "thread_name": "Test"}]}), \
             patch.object(runner_mod, "process_single_thread",
                          return_value={"status": "success"}):
            runner_mod.run_inbox_cycle("PAGE123", dry_run=True, max_threads=1)

        assert len(goto_calls) >= 1, "Step 2b re-navigation goto() was never called"
        assert "PAGE123" in goto_calls[0]


class TestSanitizeReply:
    """Unit tests for the reply sanitizer — code:tool-inbox-mas-001:reply-sanitizer"""

    def _sanitize(self, text):
        from tools.l5_inbox_mas_runner import _sanitize_reply
        return _sanitize_reply(text)

    def test_strips_bold_heading_reasoning(self):
        raw = "**Crafting a warm reply**\n\nI need to write something.\nDạ bạn ơi 🙏"
        result = self._sanitize(raw)
        assert "**Crafting" not in result
        assert "I need to" not in result
        assert "Dạ bạn ơi 🙏" in result

    def test_preserves_clean_vietnamese_reply(self):
        clean = "Dạ bạn ơi, lớp thiền hoàn toàn miễn phí 🙏\nBạn gửi họ tên và SĐT nhé."
        assert self._sanitize(clean) == clean

    def test_returns_empty_string_for_pure_reasoning(self):
        leak = "**Crafting a message**\nI need to think about this.\nLet me write something."
        result = self._sanitize(leak)
        assert result == ""

    def test_strips_i_need_to_lines(self):
        raw = "I need to confirm the registration.\nDạ chị đã đăng ký thành công rồi ạ 🙏"
        result = self._sanitize(raw)
        assert "I need to" not in result
        assert "Dạ chị đã đăng ký" in result

    def test_empty_input_returns_empty(self):
        assert self._sanitize("") == ""
        assert self._sanitize(None) is None
