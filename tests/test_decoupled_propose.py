import pytest
import sqlite3
import json
import os
from datetime import datetime, timedelta

from adk_agents.tools.seeker_tools import find_unreplied_threads

# Use an in-memory DB or a tmp file for tests
@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_db.sqlite"
    def override_get_db_connection():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    import adk_agents.tools.l5_seeker_tools
    monkeypatch.setattr(adk_agents.tools.l5_seeker_tools, "get_db_connection", override_get_db_connection)
    import tools.l5_inbox_mas_runner
    monkeypatch.setattr(tools.l5_inbox_mas_runner, "get_db_connection", override_get_db_connection)

    # Setup schema
    conn = override_get_db_connection()
    conn.executescript('''
        CREATE TABLE threads (id TEXT PRIMARY KEY, page_id TEXT, thread_name TEXT, inbox_sort_index INTEGER);
        CREATE TABLE messages (seq INTEGER, thread_id TEXT, sender TEXT, timestamp INTEGER, message_timestamp INTEGER, content TEXT);
        CREATE TABLE telegram_hitl_queue (id INTEGER PRIMARY KEY, route TEXT, thread_id TEXT, payload_json TEXT, status TEXT);
        CREATE TABLE fetch_log (id INTEGER PRIMARY KEY, page_id TEXT, fetched_at TEXT, qa_status TEXT);
        CREATE TABLE auto_replies (thread_id TEXT, customer_message_timestamp INTEGER);
        CREATE TABLE users (thread_id TEXT, thread_name TEXT, phone TEXT, email TEXT, fb_url TEXT, city TEXT, lead_stage TEXT, first_seen INTEGER, last_interaction INTEGER);
    ''')
    conn.close()
    
    return override_get_db_connection

def test_p01_predicate_selects_correct_threads(test_db):
    conn = test_db()
    conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T1', 'P1', 'T1', 1)")
    conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp) VALUES (1, 'T1', 'Customer', 100)")
    conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T2', 'P1', 'T2', 2)")
    conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp) VALUES (1, 'T2', 'Customer', 100)")
    conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp) VALUES (2, 'T2', 'Page', 101)")
    conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T3', 'P1', 'T3', 3)")
    conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp) VALUES (5, 'T3', 'Customer', 100)")
    conn.execute("INSERT INTO telegram_hitl_queue (route, thread_id, payload_json) VALUES ('inbox', 'T3', '{\"last_message_seq\": 5}')")
    conn.execute("INSERT INTO threads (id, page_id, thread_name, inbox_sort_index) VALUES ('T4', 'P1', 'T4', 4)")
    conn.execute("INSERT INTO messages (seq, thread_id, sender, timestamp) VALUES (7, 'T4', 'Customer', 102)")
    conn.execute("INSERT INTO telegram_hitl_queue (route, thread_id, payload_json) VALUES ('inbox', 'T4', '{\"last_message_seq\": 5}')")
    conn.commit()
    conn.close()

    result = find_unreplied_threads('P1', limit=10)
    assert result['status'] == 'success'
    threads = [t['thread_id'] for t in result['threads']]
    assert 'T1' in threads
    assert 'T4' in threads
    assert 'T2' not in threads
    assert 'T3' not in threads

def test_p04_freshness_gate_failed(test_db):
    conn = test_db()
    conn.execute("INSERT INTO fetch_log (page_id, fetched_at, qa_status) VALUES ('P1', datetime('now'), 'failed')")
    conn.commit()
    conn.close()
    from tools.l5_inbox_mas_runner import run_inbox_cycle
    result = run_inbox_cycle('P1')
    assert result['status'] == 'skipped'
    assert result['reason'] == 'fetch_untrusted'

def test_p05_freshness_gate_too_old(test_db):
    conn = test_db()
    conn.execute("INSERT INTO fetch_log (page_id, fetched_at, qa_status) VALUES ('P1', datetime('now', '-7 hours'), 'passed')")
    conn.commit()
    conn.close()
    from tools.l5_inbox_mas_runner import run_inbox_cycle
    result = run_inbox_cycle('P1')
    assert result['status'] == 'skipped'
    assert result['reason'] == 'fetch_untrusted'

def test_p06_freshness_gate_old_no_qa(test_db):
    conn = test_db()
    conn.execute("INSERT INTO fetch_log (page_id, fetched_at, qa_status) VALUES ('P1', datetime('now', '-1 hours'), NULL)")
    conn.commit()
    conn.close()
    from tools.l5_inbox_mas_runner import run_inbox_cycle
    result = run_inbox_cycle('P1')
    # Because there are no unreplied threads, it will return no_unreplied instead of skipped
    assert result['status'] != 'skipped'
