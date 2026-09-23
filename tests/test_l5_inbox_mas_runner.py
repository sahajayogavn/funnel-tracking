import os
import sys
import logging
from unittest.mock import patch, MagicMock

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


class TestRunAdkPipeline:
    def test_load_knowledge_context_includes_all_sources(self):
        from tools.l5_inbox_mas_context import KNOWLEDGE_FILES, load_knowledge_context

        result = load_knowledge_context()

        for relative_path in KNOWLEDGE_FILES:
            assert f"## Source: {relative_path}" in result

    def test_run_adk_pipeline_populates_session_state(self):
        from tools import l5_inbox_mas_pipeline as runner_mod

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
                    {"sender": "Customer", "content": "Xin chào"},
                    {"sender": "Page", "content": "Chào bạn"},
                ],
                seeker_context={"name": "Lan", "city": "Hà Nội", "lead_stage": "Seeker"},
            )

        assert session_service.calls, "create_session was not called"
        state = session_service.calls[0]["state"]
        assert "| Customer] Xin chào" in state["thread_messages"]
        assert "| Page] Chào bạn" in state["thread_messages"]
        assert '"name": "Lan"' in state["seeker_context"]
        assert state["knowledge_context"] == ""

        assert runner_instances, "Runner was not created"
        assert runner_instances[0].kwargs["session_service"] is session_service
        assert runner_instances[0].run_calls[0]["session_id"] == "session-1"

        assert result["thread_messages"] == state["thread_messages"]
        assert result["knowledge_context"] == ""

    def test_run_adk_pipeline_injects_message_reaction_annotation(self):
        from tools import l5_inbox_mas_pipeline as runner_mod

        session_service = DummySessionService()
        with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
             patch("google.adk.runners.Runner", side_effect=lambda **kw: DummyRunner(**kw)), \
             patch("google.genai.types", DummyTypes):
            runner_mod.run_adk_pipeline(
                [{"sender": "Page", "content": "Cảm ơn bạn", "reactions": [
                    {"actor": "Seeker", "emoji": "👍", "count": 2},
                ]}],
                {"name": "Lan"},
            )

        text = session_service.calls[0]["state"]["thread_messages"]
        assert "[?? | Reaction on preceding message] Seeker: thả 👍 ×2" in text
        assert "annotation, not a message body" in text

    def test_run_adk_care_pipeline_injects_shared_care_session_state(self):
        from tools import l5_inbox_mas_pipeline as mod

        session_service = DummySessionService()
        with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
             patch("google.adk.runners.Runner", side_effect=lambda **kw: DummyRunner(**kw)), \
             patch("google.genai.types", DummyTypes):
            result = mod.run_adk_care_pipeline(
                [{"sender": "Customer", "content": "Em bận tuần này"}],
                {"name": "Lan", "city": "Hà Nội", "lead_stage": "Seeker"},
                care_purpose="warmup",
                care_brief={"warmup_strategy": {"type": "manual_warmup"}, "operator_instruction": "Hỏi thăm nhẹ nhàng"},
            )

        state = session_service.calls[0]["state"]
        assert state["care_purpose"] == "warmup"
        assert '"manual_warmup"' in state["care_brief"]
        assert '"manual_warmup"' in state["warmup_brief"]
        assert '"name": "Lan"' in state["warmup_brief"]
        assert state["reminder_brief"] == ""
        assert result["thread_messages"] == state["thread_messages"]

    def test_orchestrator_pass_reads_final_reply_and_breakdown_state(self, monkeypatch):
        """PASS: the orchestrator's own final turn is the reply; the specialists'
        output_key writes (forwarded into orchestrator session state by ADK's
        AgentTool, code:agent-mas-002:orchestrator) populate the audit fields."""
        from tools import l5_inbox_mas_pipeline as mod

        session_service = DummySessionService()

        def events(_runner, **_kwargs):
            # Simulates AgentTool forwarding each specialist's output_key state_delta
            # into the orchestrator's own session before its final turn.
            session_service._session.state.update({
                "conversation_analysis": "intent: hỏi lịch học",
                "knowledge_brief": "Lớp Hà Nội tối thứ 3",
                "draft_reply": "Dạ chào bạn",
                "qa_verdict": "PASS",
            })
            yield type("E", (), {"author": "InboxOrchestrator",
                                 "content": DummyContent(parts=[DummyPart("Dạ chào bạn")])})()

        monkeypatch.setattr(mod, "run_runner", events)
        with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
             patch("google.adk.runners.Runner", side_effect=lambda **kw: DummyRunner(**kw)), \
             patch("google.genai.types", DummyTypes):
            result = mod.run_adk_pipeline([{"sender": "Customer", "content": "Xin chào"}], {"name": "Thuy Do", "city": "Hà Nội"})
        assert result["reply_text"] == "Dạ chào bạn"
        assert result["classification"] == "intent: hỏi lịch học"
        assert result["draft_reply"] == "Dạ chào bạn"
        assert result["qa_verdict"] == "PASS"
        assert result["escalation_reason"] == ""

    def test_orchestrator_escalate_sets_escalation_fields_not_reply(self, monkeypatch):
        """ESCALATE: no reply is drafted; the sentinel is parsed into
        escalation_reason/escalation_note instead of being sanitized as a reply."""
        from tools import l5_inbox_mas_pipeline as mod

        session_service = DummySessionService()

        def events(_runner, **_kwargs):
            session_service._session.state.update({"qa_verdict": "ESCALATE: sensitive: seeker hỏi về sức khỏe tâm thần"})
            yield type("E", (), {"author": "InboxOrchestrator",
                                 "content": DummyContent(parts=[DummyPart(
                                     "[ESCALATE: sensitive] Seeker hỏi về sức khỏe tâm thần, cần người phụ trách."
                                 )])})()

        monkeypatch.setattr(mod, "run_runner", events)
        with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
             patch("google.adk.runners.Runner", side_effect=lambda **kw: DummyRunner(**kw)), \
             patch("google.genai.types", DummyTypes):
            result = mod.run_adk_pipeline([{"sender": "Customer", "content": "Câu hỏi nhạy cảm"}], {"name": "Thuy Do", "city": "Hà Nội"})
        assert result["escalation_reason"] == "sensitive"
        assert "sức khỏe tâm thần" in result["escalation_note"]
        assert not result["reply_text"].startswith("[ESCALATE")

    def test_loop_budget_exhaustion_surfaces_non_convergence(self, monkeypatch):
        """The Python-enforced loop guard (_orchestrator_loop_guard,
        code:agent-mas-002:loop-budget), not the model, is what must stop an
        unbounded loop; this only checks the pipeline correctly parses whatever
        sentinel the model emits once that guard has spoken."""
        from tools import l5_inbox_mas_pipeline as mod

        session_service = DummySessionService()

        def events(_runner, **_kwargs):
            session_service._session.state.update({"_orchestrator_loop_count": 31})
            yield type("E", (), {"author": "InboxOrchestrator",
                                 "content": DummyContent(parts=[DummyPart(
                                     "[ESCALATE: non_convergence] Không hội tụ được câu trả lời phù hợp sau 30 lượt thử."
                                 )])})()

        monkeypatch.setattr(mod, "run_runner", events)
        with patch("google.adk.sessions.InMemorySessionService", return_value=session_service), \
             patch("google.adk.runners.Runner", side_effect=lambda **kw: DummyRunner(**kw)), \
             patch("google.genai.types", DummyTypes):
            result = mod.run_adk_pipeline([{"sender": "Customer", "content": "Lịch học?"}], {"name": "Thuy Do", "city": "Hà Nội"})
        assert result["escalation_reason"] == "non_convergence"
        assert result["loop_count"] == 31


class TestSanitizeReply:
    """Unit tests for the reply sanitizer — code:tool-inbox-mas-001:reply-sanitizer
    # Gate 5: code:test-validation-001:l5-to-hitl
    """


class TestProcessSingleThread:
    def test_empty_reply_returns_no_reply_without_drafting_or_logging(self, monkeypatch):
        import tools.l5_inbox_mas_thread as runner

        monkeypatch.setattr("adk_agents.tools.seeker_tools.get_thread_messages", lambda thread_id: {
            "status": "success",
            "count": 1,
            "messages": [{"sender": "Customer", "content": "Xin chào", "timestamp": "2026-03-25T10:00:00"}],
        })
        monkeypatch.setattr("adk_agents.tools.seeker_tools.lookup_seeker", lambda thread_id: {"name": "Lan"})
        monkeypatch.setattr(runner, "run_adk_pipeline", lambda messages, seeker: {
            "classification": "Intent: greeting",
            "reply_text": "",
        })

        navigate_calls = []
        draft_calls = []
        log_calls = []
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.navigate_to_thread", lambda *a, **k: navigate_calls.append((a, k)) or True)
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.send_reply_via_cdp", lambda *a, **k: draft_calls.append((a, k)) or True)
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.log_auto_reply", lambda *a, **k: log_calls.append((a, k)) or None)

        cdp_page = MagicMock()
        cdp_page.evaluate.return_value = "Customer"
        result = runner.process_single_thread(cdp_page, "page-1", "thread-1", "Lan", dry_run=True)

        assert result == {"status": "no_reply", "classification": "Intent: greeting"}
        assert navigate_calls == []
        assert draft_calls == []
        assert log_calls == []

    def test_successful_processing_returns_drafted_and_logs_customer_boundary(self, monkeypatch):
        import tools.l5_inbox_mas_thread as runner

        monkeypatch.setattr("adk_agents.tools.seeker_tools.get_thread_messages", lambda thread_id: {
            "status": "success",
            "count": 3,
            "messages": [
                {"sender": "Customer", "content": "Xin chào", "timestamp": "2026-03-25T09:00:00"},
                {"sender": "Page", "content": "Chào bạn", "timestamp": "2026-03-25T09:01:00"},
                {"sender": "Customer", "content": "Cho mình lịch học", "timestamp": "2026-03-25T09:02:00"},
            ],
        })
        monkeypatch.setattr("adk_agents.tools.seeker_tools.lookup_seeker", lambda thread_id: {"name": "Lan"})
        monkeypatch.setattr(runner, "run_adk_pipeline", lambda messages, seeker: {
            "classification": "Intent: schedule",
            "reply_text": "Mời bạn xem lịch học mới nhất ạ",
        })
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.navigate_to_thread", lambda page, page_id, thread_name, thread_id: True)

        draft_calls = []
        log_calls = []
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.send_reply_via_cdp", lambda *a, **k: draft_calls.append((a, k)) or True)
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.log_auto_reply", lambda *a, **k: log_calls.append({"args": a, "kwargs": k}) or None)
        monkeypatch.setattr("adk_agents.tools.l5_stage_tools.evaluate_stage_gate", lambda thread_id: {"promoted": False})
        monkeypatch.setattr("fb_pipeline.persistence.l4_sqlite_store.log_mas_decision", lambda *a, **k: None)
        monkeypatch.setattr("tools.l5_telegram_hitl.send_proposal_to_telegram", lambda *a, **k: None)

        cdp_page = MagicMock()
        cdp_page.evaluate.return_value = "Customer"
        result = runner.process_single_thread(cdp_page, "page-1", "thread-1", "Lan", dry_run=False)

        assert result["status"] == "drafted"
        assert result["mode"] == "draft_only"
        assert result["customer_message_timestamp"] == "2026-03-25T09:02:00"
        assert len(draft_calls) == 1
        assert draft_calls[0][1]["dry_run"] is True
        assert len(log_calls) == 1
        assert log_calls[0]["kwargs"]["customer_message_timestamp"] == "2026-03-25T09:02:00"
        assert log_calls[0]["kwargs"]["dry_run"] is True

    def test_failed_draft_returns_draft_failed_without_logging(self, monkeypatch):
        import tools.l5_inbox_mas_thread as runner

        monkeypatch.setattr("adk_agents.tools.seeker_tools.get_thread_messages", lambda thread_id: {
            "status": "success",
            "count": 1,
            "messages": [{"sender": "Customer", "content": "Xin chào", "timestamp": "2026-03-25T10:00:00"}],
        })
        monkeypatch.setattr("adk_agents.tools.seeker_tools.lookup_seeker", lambda thread_id: {"name": "Lan"})
        monkeypatch.setattr(runner, "run_adk_pipeline", lambda messages, seeker: {
            "classification": "Intent: greeting",
            "reply_text": "Xin chào bạn",
        })
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.navigate_to_thread", lambda page, page_id, thread_name, thread_id: True)
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.send_reply_via_cdp", lambda *a, **k: False)

        log_calls = []
        monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.log_auto_reply", lambda *a, **k: log_calls.append((a, k)) or None)

        cdp_page = MagicMock()
        cdp_page.evaluate.return_value = "Customer"
        result = runner.process_single_thread(cdp_page, "page-1", "thread-1", "Lan", dry_run=True)

        assert result["status"] == "draft_failed"
        assert log_calls == []


class TestMainCompatibility:
    def test_live_flag_is_ignored_with_warning(self, monkeypatch, caplog):
        import tools.l5_inbox_mas_runner as runner

        monkeypatch.setattr(sys, "argv", [
            "l5_inbox_mas_runner.py", "--page-id", "123", "--once", "--live"
        ])
        monkeypatch.setattr(runner, "setup_llm_env", lambda: None)
        monkeypatch.setattr(runner, "parse_page_id", lambda value: value)
        called = {}
        monkeypatch.setattr(runner, "run_inbox_cycle", lambda page_id, dry_run=True, max_threads=5, target_thread=None, target_city=None: called.update({
            "page_id": page_id,
            "dry_run": dry_run,
            "max_threads": max_threads,
        }) or {"status": "complete"})

        with caplog.at_level(logging.WARNING):
            runner.main()

        assert called["dry_run"] is True
        assert "ignored" in caplog.text


def test_grounded_draft_is_not_discarded_for_spurious_qa_knowledge_gap():
    from tools.l5_inbox_mas_pipeline import _is_spurious_knowledge_gap

    assert _is_spurious_knowledge_gap("knowledge_gap", {
        "conversation_analysis": "Seeker asks about the online class.",
        "knowledge_context": "Online class: Tue/Thu/Sat, 21:00.",
        "draft_reply": "Dạ chúng cháu gửi cô thông tin lớp online ạ.",
    })
    assert not _is_spurious_knowledge_gap("knowledge_gap", {
        "conversation_analysis": "", "knowledge_context": "", "draft_reply": "",
    })
