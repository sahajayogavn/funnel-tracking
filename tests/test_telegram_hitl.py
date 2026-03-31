import os
import sqlite3
import pytest
import json
from unittest.mock import patch, MagicMock

from tools.l5_telegram_hitl import (
    send_proposal_to_telegram,
    poll_telegram_updates,
    check_hitl_status,
    mark_hitl_executed,
)
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

@pytest.fixture
def mock_db(monkeypatch, tmp_path):
    # Set up a test DB 
    db_path = tmp_path / "test_frankensqlite.db"
    
    # Override get_db_connection to use test DB
    def mock_get_db(*args, **kwargs):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        from fb_pipeline.persistence.l4_sqlite_store import setup_database
        setup_database(conn)
        return conn
        
    monkeypatch.setattr("tools.l5_telegram_hitl.get_db_connection", mock_get_db)
    
    # Verify tables
    conn = mock_get_db()
    
    yield conn
    conn.close()


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_chat")
    # Also override env_manager
    monkeypatch.setattr("tools.env_manager.load_credentials", lambda: {
        "TELEGRAM_BOT_TOKEN": "test_token",
        "TELEGRAM_CHAT_ID": "test_chat"
    })


@patch("tools.l5_telegram_hitl.requests.post")
def test_send_proposal(mock_post, mock_env, mock_db):
    """# Gate 5: code:test-validation-001:l5-to-hitl"""
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "result": {"message_id": 999}}
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response
    
    msg_id = send_proposal_to_telegram(
        route="inbox",
        thread_id="test_thread",
        proposed_text="Hello tests",
        payload={"key": "val"}
    )
    
    assert msg_id == "999"
    
    # Verify DB
    row = mock_db.execute("SELECT * FROM telegram_hitl_queue WHERE telegram_message_id='999'").fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["route"] == "inbox"
    assert json.loads(row["payload_json"]) == {"key": "val"}


@patch("tools.l5_telegram_hitl.requests.get")
def test_poll_updates_like_reaction(mock_get, mock_env, mock_db):
    # Seed DB
    mock_db.execute(
        "INSERT INTO telegram_hitl_queue (route, thread_id, telegram_message_id, status) VALUES (?, ?, ?, ?)",
        ("inbox", "t1", "100", "pending")
    )
    mock_db.commit()
    
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 500,
                "message_reaction": {
                    "chat": {"id": 123},
                    "message_id": 100,
                    "date": 12345,
                    "old_reaction": [],
                    "new_reaction": [{"type": "emoji", "emoji": "👍"}]
                }
            }
        ]
    }
    mock_get.return_value = mock_response
    
    poll_telegram_updates()
    
    row = mock_db.execute("SELECT status FROM telegram_hitl_queue WHERE telegram_message_id='100'").fetchone()
    assert row["status"] == "approved"
    
    offset_row = mock_db.execute("SELECT last_update_id FROM telegram_offset WHERE id=1").fetchone()
    assert offset_row[0] == 501


@patch("tools.l5_telegram_hitl.requests.get")
def test_poll_updates_text_reply(mock_get, mock_env, mock_db):
    mock_db.execute(
        "INSERT INTO telegram_hitl_queue (route, thread_id, telegram_message_id, status) VALUES (?, ?, ?, ?)",
        ("warmup", "batch1", "200", "pending")
    )
    mock_db.commit()

    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 501,
                "message": {
                    "message_id": 201,
                    "reply_to_message": {
                        "message_id": 200
                    },
                    "text": "Please make it sound happier"
                }
            }
        ]
    }
    mock_get.return_value = mock_response
    
    poll_telegram_updates()
    
    row = mock_db.execute("SELECT status, feedback_text FROM telegram_hitl_queue WHERE telegram_message_id='200'").fetchone()
    assert row["status"] == "rejected"
    assert row["feedback_text"] == "Please make it sound happier"


def test_check_and_mark(mock_env, mock_db):
    mock_db.execute(
        "INSERT INTO telegram_hitl_queue (route, telegram_message_id, status, feedback_text) VALUES (?, ?, ?, ?)",
        ("event", "300", "rejected", "too short")
    )
    mock_db.commit()
    
    status, feedback = check_hitl_status("300")
    assert status == "rejected"
    assert feedback == "too short"
    
    mark_hitl_executed("300")
    
    status, _ = check_hitl_status("300")
    assert status == "executed"
