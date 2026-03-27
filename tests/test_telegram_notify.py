import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class DummyResponse:
    def raise_for_status(self):
        return None


class TestTelegramNotify:
    def test_non_trigger_classification_skips_notification(self, monkeypatch):
        import tools.l5_inbox_mas_runner as runner

        called = []
        monkeypatch.setattr(runner.requests, "post", lambda *a, **k: called.append((a, k)))

        runner._notify_telegram_if_needed("Lan", "Intent: greeting", "Xin chào")

        assert called == []

    def test_trigger_classification_sends_notification(self, monkeypatch):
        import tools.l5_inbox_mas_runner as runner

        requests_called = []
        monkeypatch.setattr(
            "tools.env_manager.load_credentials",
            lambda: {"TELEGRAM_BOT_TOKEN": "bot-123", "TELEGRAM_CHAT_ID": "chat-456"},
        )
        monkeypatch.setattr(
            runner.requests,
            "post",
            lambda *a, **k: requests_called.append({"args": a, "kwargs": k}) or DummyResponse(),
        )

        runner._notify_telegram_if_needed("Hung Bui", "Intent: register | urgent follow-up", "Mời bạn xác nhận thông tin")

        assert len(requests_called) == 1
        call = requests_called[0]
        assert call["args"][0] == "https://api.telegram.org/botbot-123/sendMessage"
        assert call["kwargs"]["json"]["chat_id"] == "chat-456"
        assert "Thread: Hung Bui" in call["kwargs"]["json"]["text"]
        assert "Classification: Intent: register | urgent follow-up" in call["kwargs"]["json"]["text"]
        assert call["kwargs"]["timeout"] == 10

    def test_missing_credentials_logs_warning_and_skips(self, monkeypatch, caplog):
        import tools.l5_inbox_mas_runner as runner

        monkeypatch.setattr("tools.env_manager.load_credentials", lambda: {})
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setattr(runner.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not post")))

        with caplog.at_level("WARNING"):
            runner._notify_telegram_if_needed("Lan", "Need to escalate to phone follow-up", "reply")

        assert "missing bot token or chat id" in caplog.text

    def test_process_single_thread_invokes_notification_after_stage_check(self, monkeypatch):
        import tools.l5_inbox_mas_runner as runner

        monkeypatch.setattr("adk_agents.tools.seeker_tools.get_thread_messages", lambda thread_id: {
            "status": "success",
            "count": 1,
            "messages": [{"sender": "Customer", "content": "Xin chào", "timestamp": "2026-03-24T10:00:00"}],
        })
        monkeypatch.setattr("adk_agents.tools.seeker_tools.lookup_seeker", lambda thread_id: {"name": "Lan"})
        monkeypatch.setattr(runner, "run_adk_pipeline", lambda messages, seeker: {
            "classification": "register and urgent",
            "reply_text": "Xin mời bạn gửi số điện thoại",
        })
        monkeypatch.setattr("adk_agents.tools.facebook_tools.navigate_to_thread", lambda page, page_id, thread_name, thread_id: True)
        monkeypatch.setattr("adk_agents.tools.facebook_tools.send_reply_via_cdp", lambda page, reply_text, dry_run=True: True)
        auto_reply_calls = []
        monkeypatch.setattr("adk_agents.tools.facebook_tools.log_auto_reply", lambda *a, **k: auto_reply_calls.append({"args": a, "kwargs": k}) or None)
        monkeypatch.setattr("adk_agents.tools.l5_stage_tools.evaluate_stage_gate", lambda thread_id: {
            "promoted": True,
            "from_stage": "Seeker",
            "to_stage": "Seeker_Public_Program",
            "reason": "valid_contact_and_program_detected",
        })
        monkeypatch.setattr("fb_pipeline.persistence.l4_sqlite_store.log_mas_decision", lambda *a, **k: None)

        notify_calls = []
        monkeypatch.setattr(runner, "_notify_telegram_if_needed", lambda thread_name, classification, reply_text: notify_calls.append((thread_name, classification, reply_text)))

        from unittest.mock import MagicMock
        cdp_page = MagicMock()
        cdp_page.evaluate.return_value = "Customer"
        result = runner.process_single_thread(cdp_page, "page-1", "thread-1", "Lan", dry_run=True)

        assert result["status"] == "drafted"
        assert result["stage_result"]["promoted"] is True
        assert auto_reply_calls[0]["kwargs"]["customer_message_timestamp"] == "2026-03-24T10:00:00"
        assert notify_calls == [("Lan", "register and urgent", "Xin mời bạn gửi số điện thoại")]
