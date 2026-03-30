import sys
import os
import json
import logging
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.l5_inbox_mas_runner import process_single_thread
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

# In memory DB fixture
@pytest.fixture
def mock_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test_frankensqlite.db"
    def mock_get_db(*args, **kwargs):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        from fb_pipeline.persistence.l4_sqlite_store import setup_database
        setup_database(conn)
        return conn
    monkeypatch.setattr("tools.l5_telegram_hitl.get_db_connection", mock_get_db)
    monkeypatch.setattr("tools.l5_inbox_mas_runner.get_db_connection", mock_get_db)
    monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.get_db_connection", mock_get_db)
    monkeypatch.setattr("fb_pipeline.persistence.l4_sqlite_store.get_db_connection", mock_get_db)
    return mock_get_db

@pytest.fixture
def mock_tools(monkeypatch):
    monkeypatch.setattr("tools.l5_inbox_mas_runner.run_adk_pipeline", lambda *args, **kwargs: {
        "classification": "question", "reply_text": "Mocked reply"
    })
    monkeypatch.setattr("adk_agents.tools.seeker_tools.lookup_seeker", lambda *args, **kwargs: {"id": "s1", "name": "Test User"})
    monkeypatch.setattr("adk_agents.tools.seeker_tools.get_thread_messages", lambda *args, **kwargs: {"status": "success", "count": 1, "messages": [{"content": "hello", "sender": "user"}]})
    monkeypatch.setattr("adk_agents.tools.l5_stage_tools.evaluate_stage_gate", lambda *args, **kwargs: {"promoted": False})
    
    nav_mock = MagicMock(return_value=True)
    send_mock = MagicMock(return_value=True)
    commit_mock = MagicMock(return_value=True)
    clear_mock = MagicMock(return_value=True)
    
    monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.navigate_to_thread", nav_mock)
    monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.send_reply_via_cdp", send_mock)
    monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.commit_reply_via_cdp", commit_mock)
    monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.clear_composer_via_cdp", clear_mock)
    monkeypatch.setattr("adk_agents.tools.l5_facebook_tools.log_auto_reply", MagicMock())
    
    return {
        "nav": nav_mock, "send": send_mock, "commit": commit_mock, "clear": clear_mock
    }

def test_hitl_approval_loop(mock_db, mock_tools, monkeypatch):
    monkeypatch.setattr("tools.l5_telegram_hitl.get_telegram_credentials", lambda: ("t", "c"))
    
    sleep_calls = 0
    def fake_sleep(secs):
        nonlocal sleep_calls
        sleep_calls += 1
        conn = mock_db()
        if sleep_calls == 1:
            conn.execute("UPDATE telegram_hitl_queue SET status='rejected', feedback_text='rewrite this' WHERE status='pending'")
            conn.commit()
        elif sleep_calls == 2:
            conn.execute("UPDATE telegram_hitl_queue SET status='approved' WHERE status='pending'")
            conn.commit()
        conn.close()

    monkeypatch.setattr("tools.l5_inbox_mas_runner.time.sleep", fake_sleep)
    
    with patch("tools.l5_telegram_hitl.requests.post") as mock_post:
        mock_response_1 = MagicMock()
        mock_response_1.json.return_value = {"ok": True, "result": {"message_id": 100}}
        mock_response_2 = MagicMock()
        mock_response_2.json.return_value = {"ok": True, "result": {"message_id": 101}}
        mock_post.side_effect = [mock_response_1, mock_response_2]
        
        monkeypatch.setattr("tools.l5_telegram_hitl.poll_telegram_updates", lambda: None)
        
        page_mock = MagicMock()
        res = process_single_thread(page_mock, "mock_page", "mock_thread", "Test User", dry_run=True)
        
        assert res["status"] == "drafted"
        assert mock_tools["clear"].call_count == 1
        assert mock_tools["commit"].call_count == 1
        assert mock_tools["send"].call_count == 2
