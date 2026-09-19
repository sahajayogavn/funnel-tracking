import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class DummySession:
    def __init__(self, state=None):
        self.id = "session-1"
        self.state = state or {}


class DummySessionService:
    def __init__(self):
        self.calls = []
        self._session = None

    async def create_session(self, **kwargs):
        self.calls.append(kwargs)
        self._session = DummySession(state=dict(kwargs.get("state") or {}))
        return self._session

    async def get_session(self, **kwargs):
        return self._session


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


def _care_result_from_terminal_state(monkeypatch, final_state, final_text):
    """Run Care runtime with a deterministic terminal session, no model call."""
    from tools import l5_inbox_mas_pipeline as pipeline

    session_service = DummySessionService()

    def runner_factory(**kwargs):
        return DummyRunner(**kwargs)

    def fake_run_runner(_runner, **_kwargs):
        session_service._session.state.update(final_state)
        part = type("Part", (), {"text": final_text})()
        content = type("Content", (), {"parts": [part]})()
        return [type("Event", (), {"content": content, "author": "CareOrchestrator"})()]

    with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
         patch("google.adk.runners.Runner", side_effect=runner_factory), \
         patch("google.genai.types", DummyTypes):
        monkeypatch.setattr(pipeline, "run_runner", fake_run_runner)
        return pipeline._run_adk_care_pipeline(
            [{"sender": "Customer", "content": "Em muốn biết thêm về lớp.", "message_at": "2026-09-18 09:00:00"}],
            {"thread_id": "thread-1", "name": "Lan", "city": "Hà Nội"},
            "warmup",
            {"warmup_strategy": {"type": "manual_warmup"}, "knowledge_context": "Verified facts."},
            now_context="Bây giờ là 2026-09-18 10:00 Asia/Ho_Chi_Minh.",
        )


class TestAdkWiring:
    def test_root_agent_is_the_inbox_orchestrator(self):
        from adk_agents import agent

        assert agent.root_agent is agent.inbox_orchestrator
        tool_names = [t.name for t in agent.inbox_orchestrator.tools if hasattr(t, "name")]
        assert tool_names == ["ConversationAnalyst", "KnowledgeLibrarian", "ReplyComposer", "ReplyQAReviewer"]
        assert agent.inbox_orchestrator.before_tool_callback == [
            agent.adk_before_tool, agent._orchestrator_loop_guard,
        ]

    def test_care_orchestrator_requires_analysis_knowledge_composer_and_qa(self):
        from adk_agents import agent

        tool_names = [t.name for t in agent.care_orchestrator.tools if hasattr(t, "name")]
        assert tool_names == [
            "ConversationAnalyst", "KnowledgeLibrarian", "ClassReminderComposer",
            "WarmUpComposer", "EventAdvertiser", "ReplyQAReviewer",
        ]
        assert "You MUST use this workflow in order" in agent.care_orchestrator.instruction
        assert "Call ReplyQAReviewer" in agent.care_orchestrator.instruction
        assert agent.class_reminder_composer.output_key == "draft_reply"
        assert agent.warmup_composer.output_key == "draft_reply"

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
             patch("google.genai.types", DummyTypes):
            result = runner_mod.run_adk_pipeline(
                thread_messages=[
                    {"sender": "Customer", "content": "Xin chào", "message_at": "2026-09-17 09:00:00"},
                    {"sender": "Page", "content": "Chào bạn", "message_at": "2026-09-17 09:05:00"},
                ],
                seeker_context={"name": "Lan", "city": "Hà Nội", "lead_stage": "Seeker"},
            )

        state = session_service.calls[0]["state"]
        assert state["thread_messages"] == "[2026-09-17 09:00 | Customer] Xin chào\n[2026-09-17 09:05 | Page] Chào bạn"
        assert state["now_context"].startswith("Bây giờ là")
        assert '"name": "Lan"' in state["seeker_context"]
        assert state["knowledge_context"] == ""
        assert runner_instances[0].kwargs["app_name"] == "sahajayoga_inbox"
        assert runner_instances[0].run_calls[0]["session_id"] == "session-1"
        assert result["knowledge_context"] == ""

    def test_route_agents_are_wired_into_scheduler_calls(self, monkeypatch):
        import tools.l5_scheduler_adk as sched

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

    def test_inbox_specialists_require_full_page_register(self):
        from adk_agents.agent import reply_composer, reply_qa_reviewer

        assert 'Never mirror customer shorthand such as "b", "m", or "b/m"' in reply_composer.instruction
        assert 'never open with "Ừ"' in reply_composer.instruction
        assert 'never the\none-letter texting forms "b", "m", or "b/m"' in reply_qa_reviewer.instruction
        assert 'prior\n"cô"/"chú" address' in reply_qa_reviewer.instruction

    def test_qa_treats_librarian_brief_as_verified_run_evidence(self):
        from adk_agents.agent import reply_qa_reviewer

        assert "knowledge_brief returned by KnowledgeLibrarian is verified" in reply_qa_reviewer.instruction

    def test_librarian_owns_dynamic_knowledge_retrieval(self):
        from adk_agents.agent import knowledge_librarian

        assert any(getattr(tool, "name", getattr(tool, "__name__", "")) == "get_knowledge"
                   for tool in knowledge_librarian.tools)
        assert "First call get_knowledge" in knowledge_librarian.instruction


@pytest.mark.parametrize("qa_verdict", ["", "REPAIR: incorrect date", "ESCALATE: knowledge_gap"])
def test_care_runtime_fails_closed_when_qa_does_not_pass(monkeypatch, qa_verdict):
    """The terminal orchestrator text cannot turn an unreviewed draft into a DM."""
    draft = "Mời bạn ghé lớp thiền vào tối nay nhé."
    result = _care_result_from_terminal_state(monkeypatch, {
        "conversation_analysis": "Eligible warm-up.",
        "knowledge_context": "Verified facts.",
        "draft_reply": draft,
        "qa_verdict": qa_verdict,
    }, draft)

    assert result["reply_text"] == ""


def test_care_runtime_uses_passed_draft_not_later_terminal_text(monkeypatch):
    """PASS must apply to the exact content that the adapter would enqueue."""
    result = _care_result_from_terminal_state(monkeypatch, {
        "conversation_analysis": "Eligible warm-up.",
        "knowledge_context": "Verified facts.",
        "draft_reply": "Mời bạn ghé lớp lúc 19h nhé.",
        "qa_verdict": "PASS",
    }, "Mời bạn ghé lớp lúc 20h nhé.")

    assert result["reply_text"] == "Mời bạn ghé lớp lúc 19h nhé."


def test_care_runtime_sends_full_snapshot_to_qa_and_repair_to_next_composer(monkeypatch):
    """The QA call must not rely on specialist history for core evidence."""
    from tools import l5_inbox_mas_pipeline as pipeline

    session_service = DummySessionService()
    prompts = []
    composer_calls = 0

    def runner_factory(**kwargs):
        return DummyRunner(**kwargs)

    def fake_run_runner(runner, **kwargs):
        nonlocal composer_calls
        prompt = kwargs["new_message"].parts[0].text
        prompts.append((runner.kwargs["agent"].name, prompt))
        state = session_service._session.state
        name = runner.kwargs["agent"].name
        if name == "ConversationAnalyst":
            state["conversation_analysis"] = "Seeker asked about the selected session."
        elif name == "KnowledgeLibrarian":
            state["knowledge_brief"] = "Verified class is Sunday 14:30."
        elif name == "WarmUpComposer":
            composer_calls += 1
            state["draft_reply"] = "Mời bạn ghé lớp lúc 19h nhé." if composer_calls == 1 else "Mời bạn ghé lớp lúc 14h30 nhé."
        elif name == "ReplyQAReviewer":
            state["qa_verdict"] = "REPAIR: correct the time to 14h30" if composer_calls == 1 else "PASS"
        return []

    with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
         patch("google.adk.runners.Runner", side_effect=runner_factory), \
         patch("google.genai.types", DummyTypes):
        monkeypatch.setattr(pipeline, "run_runner", fake_run_runner)
        result = pipeline._run_adk_care_pipeline(
            [{"sender": "Customer", "content": "Em muốn hỏi lớp Chủ Nhật", "message_at": "2026-09-19 09:00:00"}],
            {"thread_id": "thread-qa", "name": "Lan", "city": "Hà Nội"},
            "warmup",
            {
                "operator_instruction": "Mời đúng lớp Chủ Nhật đã chọn.",
                "warmup_strategy": {"type": "manual_warmup"},
                "conversation_state": {"state": "open_question", "last_customer_at": "2026-09-19 09:00:00"},
                "verified_session": {"program_code": "14h30-CN-Vương Thừa Vũ-HN", "time_label": "14h30"},
            },
            now_context="Bây giờ là 19/09/2026 10:00 Asia/Ho_Chi_Minh.",
        )

    qa_prompts = [prompt for name, prompt in prompts if name == "ReplyQAReviewer"]
    assert len(qa_prompts) == 2
    assert "Em muốn hỏi lớp Chủ Nhật" in qa_prompts[0]
    assert "Mời đúng lớp Chủ Nhật đã chọn." in qa_prompts[0]
    assert '"state": "open_question"' in qa_prompts[0]
    assert "Verified class is Sunday 14:30." in qa_prompts[0]
    second_composer_prompt = [prompt for name, prompt in prompts if name == "WarmUpComposer"][1]
    assert "Mandatory QA correction for this rewrite: correct the time to 14h30" in second_composer_prompt
    assert result["reply_text"] == "Mời bạn ghé lớp lúc 14h30 nhé."


def test_applied_seeker_update_refreshes_the_active_session_profile(monkeypatch):
    from adk_agents.tools import l5_orchestrator_tools as tools

    monkeypatch.setattr(tools, "record_seeker_field_change", lambda *a, **k: {
        "status": "applied", "field": "city", "new_value": "Đà Nẵng",
    })
    context = type("Context", (), {"state": {"seeker_context": '{"city": "Hà Nội"}'}})()

    result = tools.propose_seeker_update(
        "thread-1", "city", "Đà Nẵng", 4, 0.98, "Seeker says they are in Đà Nẵng.", context,
    )

    assert result["status"] == "applied"
    assert '"city": "Đà Nẵng"' in context.state["seeker_context"]
    assert '"city_source": "mas"' in context.state["seeker_context"]



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

    def test_final_reply_guard_rejects_peer_shorthand_and_casual_openers(self):
        from tools.l5_inbox_mas_pipeline import _is_safe_final_reply

        assert not _is_safe_final_reply("Ừ b nhé, lớp học vẫn diễn ra.")
        assert not _is_safe_final_reply("Mình sẽ báo m sau nhé.")
        assert not _is_safe_final_reply("Ok, mình sẽ kiểm tra lịch lớp.")
        assert _is_safe_final_reply("Dạ bạn nhé, mình sẽ kiểm tra lịch lớp và phản hồi bạn sớm.")
