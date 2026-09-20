"""Focused contract tests for code:postgres-cutover-001:python-db-boundary."""

import os
from datetime import datetime

import pytest

from fb_pipeline.persistence import db


def test_sqlite_is_default_when_database_url_is_absent(monkeypatch, tmp_path):
    # An empty explicit value must win over a developer's runtime .env.
    monkeypatch.setenv("DATABASE_URL", "")
    conn = db.connect(str(tmp_path))
    try:
        assert conn.__class__.__module__ == "sqlite3"
        assert conn.execute("SELECT 1 AS value").fetchone()["value"] == 1
    finally:
        conn.close()


def test_translate_sql_preserves_literal_question_marks_and_maps_common_dialect():
    sql = """INSERT OR IGNORE INTO action_queue(payload_json, created_at)
             VALUES ('{\"question\": \"?\"}', datetime('now'))
             WHERE json_extract(payload_json, '$.type') = ?"""
    translated = db.translate_sql(sql)
    assert "?\"}'" in translated
    assert "CURRENT_TIMESTAMP" in translated
    assert "::jsonb ->> 'type'" in translated
    assert translated.count("%s") == 1
    assert translated.rstrip().endswith("ON CONFLICT DO NOTHING")


def test_translate_sql_supports_time_windows_localtime_and_nested_json_paths():
    translated = db.translate_sql(
        "SELECT datetime(fetched_at), datetime('now', '-6 hours'), "
        "datetime('now', 'localtime'), json_extract(payload_json, '$.session.class_key') "
        "FROM action_queue WHERE date('now', '-1 day') < datetime('now')"
    )
    assert "fetched_at::timestamp" in translated
    assert "INTERVAL '-6 hours'" in translated
    assert "Asia/Ho_Chi_Minh" in translated
    assert "#>> '{session,class_key}'" in translated
    assert "INTERVAL '-1 day'" in translated
    assert "datetime('now', %s)" not in db.translate_sql("SELECT datetime('now', ?)")
    assert "%s::interval" in db.translate_sql("SELECT datetime('now', ?)")


def test_translate_sql_types_standalone_optional_text_filter_parameter():
    translated = db.translate_sql(
        "SELECT * FROM action_queue WHERE (? IS NULL OR page_id=?) AND queue_type=?"
    )
    assert "(%s::text IS NULL OR page_id=%s)" in translated
    assert translated.endswith("queue_type=%s")


def test_pg_cursor_maps_begin_immediate_without_a_real_connection():
    class RawCursor:
        def execute(self, sql, params):
            self.sql, self.params = sql, params

    raw = RawCursor()
    cursor = db.PgCursor(raw, object())
    cursor.execute("BEGIN IMMEDIATE")
    assert raw.sql == "BEGIN"


def test_pg_connection_close_rolls_back_before_returning_to_pool():
    events = []

    class RawConnection:
        def rollback(self):
            events.append("rollback")

    class Pool:
        def putconn(self, connection):
            assert connection is raw
            events.append("putconn")

    raw = RawConnection()
    connection = db.PgConnection(raw, Pool())
    connection.close()
    connection.close()
    assert events == ["rollback", "putconn"]


def test_pg_row_supports_legacy_index_and_name_access():
    row = db.PgRow((7, "Lan"), ("id", "name"))
    assert row[0] == 7
    assert row["name"] == "Lan"
    assert tuple(row) == (7, "Lan")
    assert row.keys() == ("id", "name")


def test_pg_row_factory_preserves_legacy_json_and_timestamp_strings():
    class Column:
        def __init__(self, name):
            self.name = name

    class Cursor:
        description = (Column("payload_json"), Column("created_at"))

    row = db._pg_row_factory(Cursor())(({"kind": "test"}, datetime(2026, 9, 20, 9, 30)))
    assert row["payload_json"] == '{"kind": "test"}'
    assert row["created_at"] == "2026-09-20 09:30:00"


def test_postgres_cutover_guard_rejects_an_empty_or_sqlite_url(monkeypatch):
    monkeypatch.setenv("FUNNEL_REQUIRE_POSTGRES", "1")
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="Refusing unsafe SQLite fallback"):
        db.database_url()


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="requires TEST_POSTGRES_URL")
def test_postgres_connection_smoke_uses_mapping_rows(monkeypatch):
    """Optional integration test; never uses production DATABASE_URL implicitly."""
    pytest.importorskip("psycopg_pool")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    conn = db.connect()
    try:
        row = conn.execute("SELECT ? AS answer", (42,)).fetchone()
        assert row[0] == 42
        assert row["answer"] == 42
    finally:
        conn.close()


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="requires TEST_POSTGRES_URL")
def test_postgres_adapter_action_queue_dialect_on_dedicated_scratch_db(monkeypatch):
    """Exercise the bridge against an isolated DB, never the app DB.

    The CI/operator must provide a URL whose database name contains
    ``adapter_test``. This guard makes an accidental production integration
    run fail before it can execute DDL.
    """
    url = os.environ["TEST_POSTGRES_URL"]
    assert "adapter_test" in url, "TEST_POSTGRES_URL must point at a dedicated scratch database"
    pytest.importorskip("psycopg_pool")
    monkeypatch.setenv("DATABASE_URL", url)
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS adapter_queue_test")
        conn.execute(
            """CREATE TABLE adapter_queue_test (
                   id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                   source_id TEXT UNIQUE NOT NULL,
                   payload_json JSONB NOT NULL,
                   created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.commit()
        cursor = conn.execute(
            "INSERT INTO adapter_queue_test (source_id, payload_json) VALUES (?, ?::jsonb) RETURNING id",
            ("first", '{"session":{"class_key":"hn"}}'),
        )
        assert cursor.lastrowid == 1
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT json_extract(payload_json, '$.session.class_key') AS class_key, "
            "datetime('now', '-6 hours') AS window_start FROM adapter_queue_test WHERE source_id=?",
            ("first",),
        ).fetchone()
        assert row["class_key"] == "hn"
        assert row[0] == "hn"
        assert conn.execute("SELECT datetime('now', ?) AS since", ("-6 hours",)).fetchone()["since"] is not None
        conn.commit()
        ignored = conn.execute(
            "INSERT OR IGNORE INTO adapter_queue_test (source_id, payload_json) VALUES (?, ?::jsonb)",
            ("first", "{}"),
        )
        assert ignored.rowcount == 0
        conn.commit()
        # Run the real queue lifecycle against PostgreSQL: this covers the
        # writer transaction (BEGIN IMMEDIATE), JSON predicates, timestamps,
        # lastrowid and mapping rows used by the delivery path.
        conn.execute("DROP TABLE IF EXISTS action_queue")
        conn.execute(
            """CREATE TABLE action_queue (
                   id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                   queue_type TEXT NOT NULL, page_id TEXT, target_type TEXT NOT NULL,
                   target_id TEXT, target_name TEXT, action_text TEXT, reaction_type TEXT,
                   payload_json JSONB NOT NULL DEFAULT '{}'::jsonb, status TEXT NOT NULL DEFAULT 'pending',
                   approval_source TEXT, approved_at TIMESTAMP, claimed_at TIMESTAMP,
                   executed_at TIMESTAMP, error_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.commit()
        from tools import l5_action_queue as action_queue

        action_id = action_queue.enqueue_action(
            queue_type="reply_message", page_id="page", target_type="thread", target_id="thread-1",
            target_name="Test", action_text="Xin chào", payload={"type": "reactive"},
        )
        assert action_queue.active_proposal_status("thread-1", "reply_message", "reactive") == "pending"
        assert action_queue.approve_action(action_id, "test")
        claimed = action_queue.claim_next_action("reply_message", "page")
        assert claimed and claimed["id"] == action_id and claimed["payload"]["type"] == "reactive"
        action_queue.finish_action(action_id)

        # Comment ingestion uses INSERT OR IGNORE + timestamp UPSERTs; the
        # scheduler then updates the same PostgreSQL-backed user state.
        conn.execute("DROP TABLE IF EXISTS comments")
        conn.execute("DROP TABLE IF EXISTS comment_users")
        conn.execute("DROP TABLE IF EXISTS posts")
        conn.execute("DROP TABLE IF EXISTS users")
        conn.execute("CREATE TABLE posts (id TEXT PRIMARY KEY, page_id TEXT, post_name TEXT, post_url TEXT, last_synced_time TEXT)")
        conn.execute("CREATE TABLE comments (id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, post_id TEXT, commenter_name TEXT, comment_text TEXT, comment_timestamp TEXT, fb_profile_url TEXT, fb_user_id TEXT, is_reply BIGINT, comment_date TEXT, UNIQUE(post_id, commenter_name, comment_text, comment_timestamp))")
        conn.execute("CREATE TABLE comment_users (post_id TEXT, commenter_name TEXT, fb_user_id TEXT, fb_profile_url TEXT, phone TEXT, email TEXT, city TEXT, last_interaction TIMESTAMP, last_synced_at TIMESTAMP, UNIQUE(post_id, commenter_name))")
        conn.execute("CREATE TABLE users (thread_id TEXT PRIMARY KEY, temperature TEXT, last_warmup_at TIMESTAMP, warmup_count BIGINT DEFAULT 0, cool_step BIGINT)")
        conn.execute("INSERT INTO users (thread_id, temperature) VALUES (?, ?)", ("thread-1", "cold"))
        conn.commit()
        from fb_pipeline.contracts.l1_comments import CommentRecord, EnrichedPostRecord
        from fb_pipeline.comments.l3_pipeline import persist_post_record
        from tools.l5_scheduler_core import _update_user_decision_state

        comment_result = persist_post_record(conn, EnrichedPostRecord(
            page_id="page", post_id="post-1", post_name="Post", preview_text="", post_lines=[], dom_index=0,
            comments=[CommentRecord("Lan", "Quan tâm", "now", "", "fb-1", 0, "today")],
            user_info={"phone": None, "email": None}, city="Hà Nội",
        ))
        assert comment_result["comments_added"] == 1
        _update_user_decision_state("thread-1", "warm", warmup_sent=True, cool_step=1)
        user = conn.execute("SELECT temperature, warmup_count, cool_step FROM users WHERE thread_id=?", ("thread-1",)).fetchone()
        assert tuple(user) == ("warm", 1, 1)
        leaked = db.connect()
        leaked.execute("SELECT 1")  # SELECT opens a PostgreSQL transaction.
        leaked.close()
        fresh = db.connect()
        try:
            assert fresh.execute("SELECT 1 AS clean").fetchone()["clean"] == 1
        finally:
            fresh.close()
    finally:
        conn.execute("DROP TABLE IF EXISTS comments")
        conn.execute("DROP TABLE IF EXISTS comment_users")
        conn.execute("DROP TABLE IF EXISTS posts")
        conn.execute("DROP TABLE IF EXISTS users")
        conn.execute("DROP TABLE IF EXISTS action_queue")
        conn.execute("DROP TABLE IF EXISTS adapter_queue_test")
        conn.commit()
        conn.close()
