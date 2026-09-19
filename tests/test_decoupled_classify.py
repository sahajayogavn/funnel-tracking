import pytest
import sqlite3
import os
import json
from datetime import datetime, timedelta
from fb_pipeline.contracts.l1_inbox import EnrichedThreadRecord, SeekerInfo, MasHandoff, InboxMessage
from fb_pipeline.inbox.l3_pipeline import persist_thread_record
from tools.l5_fetch_fb_city_classify import _post_scrape_llm_city_classify

# code:test-decoupled-001:classify

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # minimal setup
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    import logging
    setup_database(conn, logging.getLogger("test"))
    yield conn
    conn.close()

def _make_thread_record(thread_id, name, messages):
    js_msgs = []
    for i, m in enumerate(messages):
        js_msgs.append(InboxMessage(
            sender=m.get("sender"),
            content=m.get("text", ""),
            message_timestamp=m.get("timestamp", ""),
            seq=i
        ))
    return EnrichedThreadRecord(
        page_id="123",
        thread_id=thread_id,
        thread_name=name,
        preview_text="preview",
        thread_lines=[],
        dom_index=0,
        sidebar_time_text="Just now",
        sidebar_time_kind="today",
        sidebar_identity_key="",
        selected_item_id="",
        fb_url="",
        ad_context="",
        ad_ids=[],
        user_info={},
        city=None,
        program_code=None,
        messages=js_msgs,
        mas_handoff=None
    )


def test_C_01_fetch_no_city(temp_db):
    """C-01: Fetch không ghi city, user mới -> city IS NULL"""
    record = _make_thread_record("t1", "User 1", [{"sender": "Customer", "text": "Hello"}])
    persist_thread_record(temp_db, record)
    
    cur = temp_db.execute("SELECT city, program_code, classification_verified_at FROM users WHERE thread_id='t1'")
    row = cur.fetchone()
    assert row["city"] is None
    assert row["program_code"] is None
    assert row["classification_verified_at"] is None


def test_C_02_fetch_no_overwrite_city(temp_db):
    """C-02: Fetch không xoá city cũ khi update"""
    record1 = _make_thread_record("t2", "User 2", [{"sender": "Customer", "text": "Hi"}])
    persist_thread_record(temp_db, record1)
    
    # manual set city and verify (in UTC because DB's datetime('now') is UTC)
    past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    temp_db.execute("UPDATE users SET city='Hà Nội', classification_verified_at=? WHERE thread_id='t2'", (past,))
    temp_db.commit()
    
    # new message
    record2 = _make_thread_record("t2", "User 2", [
        {"sender": "Customer", "text": "Hi"},
        {"sender": "Customer", "text": "How are you"}
    ])
    persist_thread_record(temp_db, record2)
    
    cur = temp_db.execute("SELECT city, last_interaction, classification_verified_at FROM users WHERE thread_id='t2'")
    row = cur.fetchone()
    assert row["city"] == "Hà Nội"
    # last_interaction should be newer than verified_at
    assert row["last_interaction"] > row["classification_verified_at"]

def test_C_03_stale_predicate(temp_db):
    """C-03: Predicate stale ba trạng thái"""
    now = datetime.utcnow()
    past1 = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    past2 = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    
    # User 1: NULL verified
    temp_db.execute("INSERT INTO users (thread_id, thread_name, last_interaction) VALUES ('t1', 'U1', ?)", (past2,))
    # User 2: verified < last_interaction
    temp_db.execute("INSERT INTO users (thread_id, thread_name, last_interaction, classification_verified_at, contact_extracted_at) VALUES ('t2', 'U2', ?, ?, ?)", (past2, past1, past1))
    # User 3: verified >= last_interaction
    temp_db.execute("INSERT INTO users (thread_id, thread_name, last_interaction, classification_verified_at, contact_extracted_at) VALUES ('t3', 'U3', ?, ?, ?)", (past1, past2, past2))
    
    temp_db.commit()
    
    from tools.l5_fetch_fb_city_classify import STALE_CLASSIFICATION_SQL
    cur = temp_db.execute(f"SELECT COUNT(*) FROM users u WHERE {STALE_CLASSIFICATION_SQL}")
    count = cur.fetchone()[0]
    assert count == 2

def test_C_05_unknown_is_conclusion(temp_db, monkeypatch):
    """C-05: Mock LLM trả Unknown cho 1 user"""
    record = _make_thread_record("t1", "User 1", [{"sender": "Customer", "text": "Hello"}])
    persist_thread_record(temp_db, record)
    
    def mock_detect(*args, **kwargs):
        return {"city": "Unknown", "program_code": "SY_VN", "proof": "No city signal", "confidence": "low"}

    def mock_config():
        return {"api_base": "fake", "api_key": "fake", "model": "fake"}
    
    monkeypatch.setattr("tools.l5_fetch_fb_city_classify._get_llm_config_safe", mock_config)
    monkeypatch.setattr("tools.l5_fetch_fb_city_classify.detect_city_llm", mock_detect)
    
    res = _post_scrape_llm_city_classify(temp_db, "123")
    assert res["updated"] == 1
    
    cur = temp_db.execute("SELECT city, program_code, classification_verified_at FROM users WHERE thread_id='t1'")
    row = cur.fetchone()
    assert row["city"] == "Unknown"
    assert row["program_code"] == "SY_VN"
    assert row["classification_verified_at"] is not None
