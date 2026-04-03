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

from tools.l5_inbox_mas_thread import process_single_thread
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
    monkeypatch.setattr("tools.l5_inbox_mas_thread.run_adk_pipeline", lambda *args, **kwargs: {
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

def test_async_inbox_proposal_queueing(mock_db, mock_tools, monkeypatch):
    """# Gate 6: code:test-validation-001:hitl-to-l2"""
    monkeypatch.setattr("tools.l5_telegram_hitl.get_telegram_credentials", lambda: ("t", "c"))
    
    with patch("tools.l5_telegram_hitl.requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 100}}
        mock_post.return_value = mock_response
        
        page_mock = MagicMock()
        res = process_single_thread(page_mock, "mock_page", "mock_thread", "Test User", dry_run=True)
        
        assert res["status"] == "drafted"
        
        # In the async rewrite, it only drafts once visually via UI and does not commit
        assert mock_tools["clear"].call_count == 0
        assert mock_tools["commit"].call_count == 0
        assert mock_tools["send"].call_count == 1
        
        conn = mock_db()
        row = conn.execute("SELECT * FROM telegram_hitl_queue WHERE telegram_message_id = 100").fetchone()
        assert row is not None
        assert row["status"] == "pending"
        
        import json
        payload = json.loads(row["payload_json"])
        assert "proposals" in payload
        assert "msg_messages_json" in payload
        assert "seeker_dict" in payload
        conn.close()
