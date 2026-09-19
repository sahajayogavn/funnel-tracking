import os
import sqlite3
import pytest
import json
from unittest.mock import patch, MagicMock

from tools.l5_telegram_hitl import (
    send_proposal_to_telegram,
    format_inbox_proposal,
    get_seeker_detail_url,
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


def test_format_inbox_proposal_is_compact_indented_and_links_to_seeker(monkeypatch):
    monkeypatch.setattr("tools.env_manager.load_credentials", lambda: {
        "SEEKER_WEB_BASE_URL": "https://ops.example.org/"
    })
    post_text = "A" * 240 + "CALL_TO_ACTION"
    text = format_inbox_proposal(
        thread_id="thread / 42",
        seeker_name="Quỳnh Như",
        messages=[
            {"timestamp": "2026-09-11 08:45", "sender": "Auto_Page", "content": post_text},
            {"timestamp": "2026-09-11 08:46", "sender": "Customer", "content": "Mình ở Sài Gòn"},
        ],
        reply_text="Dạ CLB sẽ liên hệ lại bạn ạ.",
    )

    assert "🔗 Hồ sơ seeker\n  https://ops.example.org/seekers/thread%20%2F%2042" in text
    assert "  [2026-09-11 08:45 | Auto_Page]\n    " in text
    assert "…" in text
    assert "CALL_TO_ACTION" in text
    assert post_text not in text
    assert "🤖 Đề xuất trả lời (MAS)\n  Dạ CLB sẽ liên hệ lại bạn ạ." in text


def test_seeker_detail_url_uses_the_canonical_local_dashboard(monkeypatch):
    monkeypatch.setattr("tools.env_manager.load_credentials", lambda: {})
    monkeypatch.delenv("SEEKER_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_APP_URL", raising=False)
    assert get_seeker_detail_url("5691") == "http://localhost:9995/seekers/5691"


def test_unconfigured_telegram_retains_escalation_in_local_hitl_queue(monkeypatch, mock_db):
    monkeypatch.setattr("tools.env_manager.load_credentials", lambda: {})
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    proposal_id = send_proposal_to_telegram(
        route="inbox", thread_id="t-local", proposed_text="Cần người xem",
        payload={"status": "escalated"}, escalation_reason="knowledge_gap",
        escalation_note="Không có lịch lớp phù hợp.",
    )

    assert proposal_id.startswith("local:")
    row = mock_db.execute(
        "SELECT escalation_reason, escalation_note, payload_json FROM telegram_hitl_queue WHERE telegram_message_id=?",
        (proposal_id,),
    ).fetchone()
    assert row["escalation_reason"] == "knowledge_gap"
    assert row["escalation_note"] == "Không có lịch lớp phù hợp."
    assert json.loads(row["payload_json"])["delivery_state"] == "telegram_unconfigured"


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


def test_missing_telegram_message_id_is_never_auto_approved(mock_env, mock_db):
    status, feedback = check_hitl_status("")
    assert status == "pending"
    assert feedback == "missing_telegram_message_id"
