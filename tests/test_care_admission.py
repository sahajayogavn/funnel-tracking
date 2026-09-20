import json
import sqlite3

import pytest

from tools import l5_care_admission as admission
from fb_pipeline.persistence import l4_llm_trace as trace
from fb_pipeline.persistence.l4_sqlite_store import setup_database


INSTRUCTION = "Soạn tin nhắc lịch học phù hợp cho seeker đã chọn. Chỉ đề xuất khi có lịch đã được xác thực và seeker còn phù hợp để nhận tin. Đây là lớp học gấp, nên hoàn toàn được phép giục liên. tục."


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    path = tmp_path / "audit.db"
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    with connect() as conn:
        setup_database(conn)
    monkeypatch.setattr(trace, "get_db_connection", connect)
    monkeypatch.setattr(admission, "get_llm_config", lambda: {"provider": "google", "model": "test-gemini"})
    return connect


@pytest.mark.parametrize("output,allowed,error", [
    ({"allow_repeat": True, "evidence": "hoàn toàn được phép giục liên. tục", "reason": "Cho phép giục liên tục."}, True, False),
    ({"allow_repeat": False, "evidence": "", "reason": "Không cho phép nhắc lại."}, False, False),
    ({"allow_repeat": "true", "evidence": "gấp", "reason": "x"}, False, True),
    ({"allow_repeat": True, "evidence": "invented permission", "reason": "x"}, False, True),
    ({"allow_repeat": True, "evidence": "", "reason": "x"}, False, True),
    ({"allow_repeat": True, "evidence": "gấp"}, False, True),
    ([], False, True), ("invalid JSON", False, True),
])
def test_interpretation_strict_contract_and_durable_trace(audit_db, monkeypatch, output, allowed, error):
    def generate(**kwargs):
        assert json.loads(kwargs["user_prompt"])["operator_instruction"] == INSTRUCTION
        assert "punctuation/typing mistakes" in kwargs["system_prompt"]
        return (output if isinstance(output, str) else json.dumps(output)), {"prompt_tokens": 42, "completion_tokens": 15}
    monkeypatch.setattr(admission, "generate_text", generate)
    with trace.span(trigger="web", route="recommend", trace_id="38-test"):
        result = admission.interpret_repeat_permission(INSTRUCTION, page_id="page", thread={"thread_id": "t"},
            session={"session_date": "2026-09-20"}, sent_reminders=[{"id": 299}])
    assert result["allow_repeat"] is allowed
    assert bool(result.get("error")) is error
    with audit_db() as conn:
        row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["trace_id"] == "38-test" and row["subject_id"] == "t"
    assert row["agent_name"] == "CareInstructionInterpreter"
    assert row["status"] == ("error" if error else "ok")
    assert row["tokens_in"] == 42
    assert json.loads(row["state_json"])["sent_reminders"] == [{"id": 299}]


def test_interpreter_timeout_is_not_misreported_as_denied_permission(audit_db, monkeypatch):
    def fail(**kwargs):
        raise TimeoutError("model timeout")
    monkeypatch.setattr(admission, "generate_text", fail)
    result = admission.interpret_repeat_permission(INSTRUCTION, page_id="page", thread={"thread_id": "t"},
        session={}, sent_reminders=[])
    assert result["allow_repeat"] is False and result["error"] == "model timeout"
    with audit_db() as conn:
        assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "timeout"


def test_deterministic_skip_is_explicitly_not_model_inference(audit_db):
    with trace.span(trigger="web", route="recommend", trace_id="37-test"):
        admission.record_care_decision(page_id="page", thread={"thread_id": "t"}, instruction=INSTRUCTION,
            purpose="class_reminder", reason="reminder_already_sent_for_session", note="Đã nhắc hôm qua.",
            evidence={"sent_reminders": [{"id": 299}]})
    with audit_db() as conn:
        row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["model"] == "deterministic" and row["status"] == "skipped"
    assert row["tokens_in"] == row["tokens_out"] == 0
    assert row["trace_id"] == "37-test" and row["page_id"] == "page"
    assert json.loads(row["state_json"])["model_called"] is False
    assert row["response_text"] == "Đã nhắc hôm qua."
