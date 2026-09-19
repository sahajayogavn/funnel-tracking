import pytest
import asyncio
from fb_pipeline.persistence.l4_llm_trace import span, start_call, end_call, get_trace_context, _trace_id
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

@pytest.fixture(autouse=True)
def setup_test_db():
    # Use in-memory DB for tests
    import fb_pipeline.persistence.l4_sqlite_store as store
    import os
    
    import sqlite3
    
    shared_conn = sqlite3.connect(':memory:', check_same_thread=False)
    shared_conn.row_factory = sqlite3.Row
    store.setup_database(shared_conn)
    
    # Store original
    orig_get_db_connection = store.get_db_connection
    
    # Override
    def mock_get_db_connection(memory_dir=None, logger=None):
        class DummyConn:
            def __init__(self, c):
                self._c = c
            def execute(self, *args, **kwargs):
                return self._c.execute(*args, **kwargs)
            def commit(self):
                self._c.commit()
            def close(self):
                pass
        return DummyConn(shared_conn)
        
    test_conn = mock_get_db_connection()
    
    # Patch the llm_trace get_db_connection
    import fb_pipeline.persistence.l4_llm_trace as trace
    orig_trace_db = trace.get_db_connection
    trace.get_db_connection = mock_get_db_connection
    
    yield shared_conn
    
    trace.get_db_connection = orig_trace_db


def test_span_contextvars_isolation(setup_test_db):
    _trace_id.set(None)
    conn = setup_test_db
    async def worker(trigger: str, route: str, delay: float):
        with span(trigger=trigger, route=route, page_id="123", dry_run=True):
            await asyncio.sleep(delay)
            # Make sure we didn't leak context from the other worker
            t_id = _trace_id.get()
            call_id = start_call("test_agent", "model", "sys", "msg", "{}")
            return call_id, t_id

    async def main():
        task1 = asyncio.create_task(worker("cli", "propose", 0.2))
        task2 = asyncio.create_task(worker("web", "warmup", 0.1))
        
        res1, res2 = await asyncio.gather(task1, task2)
        call_id1, tid1 = res1
        call_id2, tid2 = res2
        
        assert tid1 != tid2
        assert call_id1 is not None
        assert call_id2 is not None
        return call_id1, call_id2
        
    call_id1, call_id2 = asyncio.run(main())
    
    call1 = conn.execute("SELECT trigger, route FROM llm_calls WHERE id = ?", (call_id1,)).fetchone()
    call2 = conn.execute("SELECT trigger, route FROM llm_calls WHERE id = ?", (call_id2,)).fetchone()
    
    assert call1["trigger"] == "cli"
    assert call1["route"] == "propose"
    
    assert call2["trigger"] == "web"
    assert call2["route"] == "warmup"


def test_llm_calls_insertion_and_end(setup_test_db):
    conn = setup_test_db
    with span(trigger="scheduler", route="event", page_id="456", dry_run=False, subject=("user", "u1", "Steve")):
        call_id = start_call(
            agent_name="event_adv",
            model="gpt-5",
            system_prompt="system",
            messages_json="[]",
            state_json="{}"
        )
        assert call_id is not None
        
        end_call(call_id, response_text="Hello", tokens_in=10, tokens_out=5)
        
    row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    assert row is not None
    assert row["trigger"] == "scheduler"
    assert row["route"] == "event"
    assert row["page_id"] == "456"
    assert row["subject_type"] == "user"
    assert row["subject_id"] == "u1"
    assert row["subject_label"] == "Steve"
    assert row["response_text"] == "Hello"
    assert row["status"] == "ok"
    assert row["tokens_in"] == 10
    assert row["tokens_out"] == 5
    assert row["duration_ms"] is not None


def test_adk_callback_persists_rendered_payload_without_internal_state(setup_test_db):
    from fb_pipeline.persistence.l4_llm_trace import adk_after_model, adk_before_model

    class State(dict):
        def to_dict(self):
            return dict(self)

    class Context:
        agent_name = "Responder"

        def __init__(self):
            self.state = State({
                "thread_messages": "[Customer] Xin chào",
                "_llm_trace_last_call_id": 41,
            })

    class Config:
        system_instruction = "Rendered prompt for Xin chào"

    class Request:
        config = Config()
        contents = [{"role": "user", "content": "Xin chào"}]
        model = "openai/test-model"
        tools_dict = None

    class Part:
        text = "Dạ chào bạn ạ 🙏"

    class Content:
        parts = [Part()]

    class Response:
        content = Content()
        usage_metadata = None
        function_calls = None
        error = None

    with span(trigger="web", route="propose", subject=("thread", "thread-1", "Lan")):
        context = Context()
        adk_before_model(context, Request())
        adk_after_model(context, Response())

    row = setup_test_db.execute("SELECT * FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["route"] == "propose"
    assert row["trigger"] == "web"
    assert row["subject_id"] == "thread-1"
    assert "Xin chào" in row["messages_json"]
    assert "_llm_trace_last_call_id" not in row["state_json"]
    assert row["response_text"] == "Dạ chào bạn ạ 🙏"
    assert row["status"] == "ok"


def test_adk_tool_call_turn_records_request_and_execution_without_fake_text(setup_test_db):
    from fb_pipeline.persistence.l4_llm_trace import (
        adk_after_model, adk_after_tool, adk_before_model, adk_before_tool,
    )

    class State(dict):
        def to_dict(self): return dict(self)

    class Context:
        agent_name = "InboxOrchestrator"
        def __init__(self): self.state = State()

    class Request:
        config = type("Config", (), {"system_instruction": "Use tools."})()
        contents = []
        model = "openai/test-model"
        tools_dict = {"get_seeker_profile": {}}

    class FunctionCall:
        name = "get_seeker_profile"
        args = {"thread_id": "thread-1"}

    class Part:
        text = None
        function_call = FunctionCall()

    class Response:
        content = type("Content", (), {"parts": [Part()]})()
        usage_metadata = None
        function_calls = [FunctionCall()]
        error = None

    class Tool:
        name = "get_seeker_profile"

    with span(trigger="scheduler", route="propose", subject=("thread", "thread-1", "Lan")):
        context = Context()
        adk_before_model(context, Request())
        adk_after_model(context, Response())
        adk_before_tool(Tool(), {"thread_id": "thread-1"}, context)
        adk_after_tool(Tool(), {"thread_id": "thread-1"}, context, {"status": "found", "city": "Hà Nội"})

    row = setup_test_db.execute("SELECT response_text, response_json, status FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    payload = __import__("json").loads(row["response_json"])
    assert row["response_text"] == ""
    assert row["status"] == "ok"
    assert [item["phase"] for item in payload["tool_calls"]] == ["requested", "started", "completed"]
    assert payload["tool_calls"][-1]["result"]["city"] == "Hà Nội"


def test_adk_after_tool_accepts_current_tool_response_keyword(setup_test_db):
    """A tracing callback must not interrupt the parent after an AgentTool returns."""
    from fb_pipeline.persistence.l4_llm_trace import adk_after_tool, adk_before_model

    class State(dict):
        def to_dict(self): return dict(self)

    class Context:
        agent_name = "InboxOrchestrator"
        def __init__(self): self.state = State()

    class Request:
        config = type("Config", (), {"system_instruction": "Use tools."})()
        contents = []
        model = "openai/test-model"
        tools_dict = None

    class Tool:
        name = "ConversationAnalyst"

    with span(trigger="scheduler", route="propose", subject=("thread", "thread-1", "Lan")):
        context = Context()
        adk_before_model(context, Request())
        # This is the keyword used by the installed Google ADK runtime.
        adk_after_tool(
            Tool(), {"request": "analyze"}, context,
            tool_response="intent: ask about next class",
        )

    row = setup_test_db.execute("SELECT response_json FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    payload = __import__("json").loads(row["response_json"])
    assert payload["tool_calls"][-1]["phase"] == "completed"
    assert payload["tool_calls"][-1]["result"] == "intent: ask about next class"


def test_thuy_do_message_trace_chains_four_pass_calls_and_optional_repair(setup_test_db):
    from fb_pipeline.persistence.l4_llm_trace import adk_before_model

    class State(dict):
        def to_dict(self):
            return dict(self)

    class Context:
        agent_name = "ConversationAnalyst"
        def __init__(self, state): self.state = State(state)

    class Request:
        config = type("Config", (), {"system_instruction": "Classify"})()
        contents = []
        model = "openai/test-model"
        tools_dict = None

    with span(trigger="scheduler", route="propose", subject=("thread", "4094", "Thuy Do")) as trace_id:
        action = get_trace_context()
    first = Context({"_llm_trace_context": action, "_llm_trace_seq": 0})
    adk_before_model(first, Request())
    contexts = [first]
    for agent_name in ("KnowledgeLibrarian", "ReplyComposer", "ReplyQAReviewer"):
        context = Context(dict(contexts[-1].state))
        context.agent_name = agent_name
        adk_before_model(context, Request())
        contexts.append(context)

    rows = setup_test_db.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
    assert [row["trace_id"] for row in rows] == [trace_id] * 4
    assert [row["seq_in_trace"] for row in rows] == [1, 2, 3, 4]
    assert [row["agent_name"] for row in rows] == ["ConversationAnalyst", "KnowledgeLibrarian", "ReplyComposer", "ReplyQAReviewer"]
    assert rows[0]["subject_label"] == "Thuy Do"
    assert [row["parent_call_id"] for row in rows[1:]] == [row["id"] for row in rows[:-1]]
    assert "_llm_trace_context" not in rows[0]["state_json"]

    repair = Context(dict(contexts[-1].state))
    repair.agent_name = "ReplyRepair"
    adk_before_model(repair, Request())
    repaired = setup_test_db.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
    assert [row["seq_in_trace"] for row in repaired] == [1, 2, 3, 4, 5]
    assert repaired[-1]["parent_call_id"] == repaired[-2]["id"]


def test_city_http_trace_persists_usage_metadata(setup_test_db, monkeypatch):
    import fb_pipeline.contracts.l1_city_llm as city_llm

    class Response:
        headers = {"Content-Type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"city":"Hà Nội"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }

        def close(self):
            return None

    monkeypatch.setattr(city_llm, "LLM_STREAM", False)
    monkeypatch.setattr(city_llm.requests, "post", lambda *args, **kwargs: Response())

    with span(trigger="scheduler", route="classify_detect", subject=("batch", "batch:1", "1 seeker")):
        text = city_llm.chat_completion_text(
            "https://llm.test/chat/completions",
            {"model": "test-model", "messages": [{"role": "system", "content": "Classify"}]},
            {},
            30,
        )

    row = setup_test_db.execute("SELECT * FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert text == '{"city":"Hà Nội"}'
    assert row["route"] == "classify_detect"
    assert row["route_group"] == "LLM"
    assert row["tokens_in"] == 12
    assert row["tokens_out"] == 4
    assert row["status"] == "ok"
