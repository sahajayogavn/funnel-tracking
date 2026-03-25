import os
import sys
import logging
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


class TestProcessSingleThread:
    def test_empty_reply_returns_no_reply_without_drafting_or_logging(self, monkeypatch):
        import tools.l5_inbox_mas_runner as runner

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
        monkeypatch.setattr("adk_agents.tools.facebook_tools.navigate_to_thread", lambda *a, **k: navigate_calls.append((a, k)) or True)
        monkeypatch.setattr("adk_agents.tools.facebook_tools.send_reply_via_cdp", lambda *a, **k: draft_calls.append((a, k)) or True)
        monkeypatch.setattr("adk_agents.tools.facebook_tools.log_auto_reply", lambda *a, **k: log_calls.append((a, k)) or None)

        result = runner.process_single_thread(object(), "page-1", "thread-1", "Lan", dry_run=True)

        assert result == {"status": "no_reply", "classification": "Intent: greeting"}
        assert navigate_calls == []
        assert draft_calls == []
        assert log_calls == []

    def test_successful_processing_returns_drafted_and_logs_customer_boundary(self, monkeypatch):
        import tools.l5_inbox_mas_runner as runner

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
        monkeypatch.setattr("adk_agents.tools.facebook_tools.navigate_to_thread", lambda page, page_id, thread_name: True)

        draft_calls = []
        log_calls = []
        monkeypatch.setattr("adk_agents.tools.facebook_tools.send_reply_via_cdp", lambda *a, **k: draft_calls.append((a, k)) or True)
        monkeypatch.setattr("adk_agents.tools.facebook_tools.log_auto_reply", lambda *a, **k: log_calls.append({"args": a, "kwargs": k}) or None)
        monkeypatch.setattr("adk_agents.tools.l5_stage_tools.evaluate_stage_gate", lambda thread_id: {"promoted": False})
        monkeypatch.setattr("fb_pipeline.persistence.l4_sqlite_store.log_mas_decision", lambda *a, **k: None)
        monkeypatch.setattr(runner, "_notify_telegram_if_needed", lambda *a, **k: None)

        result = runner.process_single_thread(object(), "page-1", "thread-1", "Lan", dry_run=False)

        assert result["status"] == "drafted"
        assert result["mode"] == "draft_only"
        assert result["customer_message_timestamp"] == "2026-03-25T09:02:00"
        assert len(draft_calls) == 1
        assert draft_calls[0][1]["dry_run"] is True
        assert len(log_calls) == 1
        assert log_calls[0]["kwargs"]["customer_message_timestamp"] == "2026-03-25T09:02:00"
        assert log_calls[0]["kwargs"]["dry_run"] is True

    def test_failed_draft_returns_draft_failed_without_logging(self, monkeypatch):
        import tools.l5_inbox_mas_runner as runner

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
        monkeypatch.setattr("adk_agents.tools.facebook_tools.navigate_to_thread", lambda page, page_id, thread_name: True)
        monkeypatch.setattr("adk_agents.tools.facebook_tools.send_reply_via_cdp", lambda *a, **k: False)

        log_calls = []
        monkeypatch.setattr("adk_agents.tools.facebook_tools.log_auto_reply", lambda *a, **k: log_calls.append((a, k)) or None)

        result = runner.process_single_thread(object(), "page-1", "thread-1", "Lan", dry_run=True)

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
        monkeypatch.setattr(runner, "run_inbox_cycle", lambda page_id, dry_run=True, max_threads=5: called.update({
            "page_id": page_id,
            "dry_run": dry_run,
            "max_threads": max_threads,
        }) or {"status": "complete"})

        with caplog.at_level(logging.WARNING):
            runner.main()

        assert called["dry_run"] is True
        assert "ignored" in caplog.text
