# code:test-validation-001:bug-inbox-thread-name-001
"""Regression coverage for invalid Inbox labels and re-crawl name repair."""

import sqlite3

import pytest

from fb_pipeline.browser.inbox.thread_list_parser import is_conversation_name
from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
from fb_pipeline.inbox.l3_pipeline import (
    build_thread_record,
    enrich_thread_record,
    persist_thread_record,
)
from fb_pipeline.persistence.l4_sqlite_store import setup_database


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    yield conn
    conn.close()


def test_navigation_labels_are_not_conversation_names():
    assert not is_conversation_name("All messages")
    assert not is_conversation_name("Tất cả tin nhắn")
    assert not is_conversation_name("  unread  ")
    assert is_conversation_name("Everly Green")


def test_recrawl_replaces_navigation_label_with_live_name(db):
    record = enrich_thread_record(
        build_thread_record("page1", {
            "name": "Everly Green",
            "text": "Everly Green\nXin chào",
            "fbUrl": "61572045434146",
        }),
        [{"sender": "Customer", "text": "Xin chào", "timestamp": "8:20 AM"}],
        extract_user_info,
        detect_city,
    )
    db.execute(
        "INSERT INTO threads (id, page_id, thread_name) VALUES (?, ?, ?)",
        (record.thread_id, "page1", "All messages"),
    )
    db.execute(
        "INSERT INTO users (thread_id, thread_name) VALUES (?, ?)",
        (record.thread_id, "All messages"),
    )
    db.commit()

    persist_thread_record(db, record, detect_city)

    assert db.execute(
        "SELECT thread_name FROM users WHERE thread_id = ?", (record.thread_id,)
    ).fetchone()[0] == "Everly Green"
    assert db.execute(
        "SELECT thread_name FROM threads WHERE id = ?", (record.thread_id,)
    ).fetchone()[0] == "Everly Green"


def test_persist_rejects_navigation_label_as_a_name(db):
    bad_record = enrich_thread_record(
        build_thread_record("page1", {"name": "All messages", "text": "All messages"}),
        [],
        extract_user_info,
        detect_city,
    )

    with pytest.raises(ValueError, match="navigation label"):
        persist_thread_record(db, bad_record, detect_city)
