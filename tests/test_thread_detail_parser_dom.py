"""Browser-executed regressions for structured Inbox notation.

These fixtures execute the exact JavaScript passed to ``page.evaluate`` in a
local headless Chrome.  They intentionally do not mock parser return rows: the
failure modes here are DOM-boundary errors which Python-only tests cannot see.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from fb_pipeline.browser.inbox.thread_detail_parser import extract_thread_messages


_MACOS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture(scope="module")
def dom_page():
    executable = os.environ.get("PARSER_TEST_CHROME", _MACOS_CHROME)
    if not Path(executable).exists():
        pytest.skip("DOM parser regressions require local Chrome; set PARSER_TEST_CHROME")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True, executable_path=executable
            )
        except PlaywrightError as exc:  # pragma: no cover - host dependency
            pytest.skip(f"unable to launch local Chrome for DOM parser test: {exc}")
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def _extract(dom_page, html: str):
    dom_page.set_content(html)
    return extract_thread_messages(
        dom_page, observed_at="2026-09-19T10:15:30+00:00"
    )


def test_dom_parser_recognizes_explicit_customer_label_without_heuristics(dom_page):
    messages = _extract(
        dom_page,
        '''<div role="region" aria-label="message history">
             <div class="x1fqp7bg" data-message-id="cluster-lan">
               <div class="x1y1aw1k" data-message-id="lan-1"
                    aria-label="Lan sent a message">Dạ</div>
             </div>
             <div class="x1fqp7bg">
               <div class="x1y1aw1k">Không có nhãn sender</div>
             </div>
           </div>''',
    )

    assert [(message["sender"], message["sender_confidence"]) for message in messages] == [
        ("Customer", "explicit"),
        ("Unknown", "unknown"),
    ]
    assert "Lan sent a message" in messages[0]["sender_evidence"]


def test_dom_parser_excludes_nested_quote_from_page_body(dom_page):
    messages = _extract(
        dom_page,
        '''<div role="region" aria-label="message history">
             <div class="x1fqp7bg" data-message-id="page-cluster"
                  aria-label="You sent a message">
               <div class="x1y1aw1k" aria-label="You sent a message">
                 <div class="x1y1aw1k" data-quoted-sender="Lan"
                      aria-label="Quoted reply from Lan">Không nhắn nữa</div>
                 Dạ
               </div>
             </div>
           </div>''',
    )

    assert len(messages) == 1
    assert messages[0]["sender"] == "Page"
    assert messages[0]["body"] == "Dạ"
    assert "Không nhắn nữa" not in messages[0]["body"]
    assert messages[0]["quoted_text"] == "Không nhắn nữa"
    assert messages[0]["quoted_sender"] == "Lan"


def test_dom_parser_never_copies_cluster_source_id_to_sibling_bodies(dom_page):
    messages = _extract(
        dom_page,
        '''<div role="region" aria-label="message history">
             <div class="x1fqp7bg" data-message-id="cluster-only">
               <div class="x1y1aw1k">Dòng một</div>
               <div class="x1y1aw1k">Dòng hai</div>
             </div>
           </div>''',
    )

    assert [message["body"] for message in messages] == ["Dòng một", "Dòng hai"]
    assert [message["source_id"] for message in messages] == [None, None]


def test_dom_parser_does_not_copy_page_actor_from_shared_cluster(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history">
        <div class="x1fqp7bg" aria-label="You sent a message">
          <div class="x1y1aw1k" data-message-id="greeting" aria-label="You sent a message">Chào Châu</div>
          <div class="x1y1aw1k" data-message-id="quickreply">Hỏi chi tiết</div>
        </div></div>''')
    assert [(m["sender"], m["body"]) for m in messages] == [
        ("Page", "Chào Châu"), ("Unknown", "Hỏi chi tiết")]


def test_dom_parser_conflicting_actor_labels_remain_unknown(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history">
        <div class="x1fqp7bg" aria-label="You sent a message">
          <div class="x1y1aw1k" data-message-id="m1" aria-label="Lan sent a message">Dạ</div>
        </div></div>''')
    assert messages[0]["sender"] == "Unknown"


def test_dom_parser_keeps_reaction_control_id_distinct_from_target_and_uses_observed_time(dom_page):
    messages = _extract(
        dom_page,
        '''<div role="region" aria-label="message history">
             <div class="x1fqp7bg" data-message-id="target-message">
               <div class="x1y1aw1k" data-message-id="target-message"
                    aria-label="You sent a message">Xin chào</div>
               <span data-message-id="reaction-control"
                     aria-label="You reacted Love to this message"><img alt="Love"></span>
             </div>
           </div>''',
    )

    reaction = messages[0]["reactions"][0]
    assert reaction["source_id"] == "reaction-control"
    assert reaction["target_type"] == "message"
    assert reaction["target_message_id"] == "target-message"
    assert reaction["target_message_id"] != reaction["source_id"]
    assert reaction["observed_at"] == "2026-09-19T10:15:30+00:00"


def test_dom_to_sqlite_to_mas_gate_preserves_explicit_customer_evidence(
    dom_page, tmp_path, monkeypatch,
):
    """Exercise the real DOM extractor through persistence and MAS's gate.

    This guards against a parser fix that is subsequently discarded by the
    intermediate Inbox object or SQLite columns.  It intentionally calls no
    model and no worker.
    """
    from adk_agents.tools import l5_seeker_tools
    from fb_pipeline.contracts.l1_conversation_state import ACTION_REPLY, compute_conversation_state
    from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
    from fb_pipeline.inbox.l3_pipeline import build_thread_record, enrich_thread_record, persist_thread_record
    from fb_pipeline.persistence.l4_sqlite_store import setup_database

    extracted = _extract(
        dom_page,
        '''<div role="region" aria-label="message history">
             <div class="x1fqp7bg" data-message-id="customer-1">
               <div class="x1y1aw1k" data-message-id="customer-1"
                    aria-label="Lan sent a message">Mình muốn hỏi lịch lớp</div>
             </div>
           </div>''',
    )
    db_path = tmp_path / "dom-history.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    record = enrich_thread_record(
        build_thread_record("page-1", {"name": "Lan", "text": "Lan"}),
        extracted, extract_user_info, detect_city,
    )
    persist_thread_record(conn, record, detect_city)
    conn.commit()
    conn.close()

    def open_db():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(l5_seeker_tools, "get_db_connection", open_db)
    history = l5_seeker_tools.get_thread_messages(record.thread_id)

    assert history["status"] == "success"
    assert history["messages"][0]["sender"] == "Customer"
    assert "Lan sent a message" in history["messages"][0]["sender_evidence"]
    assert compute_conversation_state(history["messages"]).action == ACTION_REPLY
