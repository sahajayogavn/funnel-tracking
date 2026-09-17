import pytest
import asyncio
from fb_pipeline.persistence.l4_llm_trace import span, start_call, end_call, _trace_id
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
