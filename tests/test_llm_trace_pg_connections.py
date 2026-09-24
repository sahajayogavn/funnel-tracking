"""Regression: LLM trace helpers must never leak pooled PostgreSQL connections.

code:test-validation-001:persistence
"""
from datetime import datetime, timezone

from fb_pipeline.persistence import l4_llm_trace


class _FakeConn:
    def __init__(self, started_at):
        self.started_at = started_at
        self.closed = 0
        self.updates = []

    def execute(self, sql, params=()):
        if sql.startswith("UPDATE"):
            self.updates.append(params)
        return self

    def fetchone(self):
        return (self.started_at,)

    def commit(self):
        pass

    def close(self):
        self.closed += 1


def test_end_call_handles_naive_pg_timestamp_and_releases_connections(monkeypatch):
    # PostgreSQL returns ``timestamp without time zone`` as a naive string.
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    opened = []

    def fake_connection():
        conn = _FakeConn(naive)
        opened.append(conn)
        return conn

    monkeypatch.setattr(l4_llm_trace, "get_db_connection", fake_connection)
    l4_llm_trace.end_call(42, response_text="{}")

    assert opened and all(conn.closed for conn in opened)
    update_values = opened[-1].updates[0]
    assert any(isinstance(v, int) and 0 <= v < 60_000 for v in update_values)  # duration_ms


def test_trace_helper_releases_connection_when_query_raises(monkeypatch):
    class Boom(_FakeConn):
        def execute(self, sql, params=()):
            raise RuntimeError("db down")

    opened = []
    monkeypatch.setattr(l4_llm_trace, "get_db_connection", lambda: opened.append(Boom(None)) or opened[-1])
    l4_llm_trace._safe_update(7, {"status": "ok"})
    assert opened[0].closed == 1
