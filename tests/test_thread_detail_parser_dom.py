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
from tests.chrome_path import local_chrome




@pytest.fixture(scope="module")
def dom_page():
    executable = local_chrome()
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
    dom_page.goto('about:blank')
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


def test_empty_database_sidebar_click_discovers_recipient_and_persists(dom_page, monkeypatch):
    """Actual locator JS + verifier + parser + SQLite, without a cached PSID."""
    import logging
    from fb_pipeline.browser.inbox import thread_worker
    from fb_pipeline.contracts.l1_inbox import ThreadRecord, extract_user_info
    from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    html = '''<div role="main"><h1>Inbox</h1>
      <div class="_5_n1" onclick="history.pushState({}, '', '?asset_id=123&selected_item_id=456'); document.getElementById('panel-name').textContent='Lan'">Lan</div>
      <div id="panel-name" class="_4ik4 _4ik5">Old thread</div>
      <div role="region" aria-label="message history">
        <div class="x14vqqas">Sep 10, 2026</div><div class="x14vqqas">9:00 AM</div>
        <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="m1" aria-label="Lan sent a message">Dạ</div></div>
      </div></div>'''
    dom_page.route("https://business.facebook.com/**", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
    dom_page.goto("https://business.facebook.com/latest/inbox/all?asset_id=123")
    dom_page.evaluate('''() => {document.querySelector('[data-message-id="m1"]').__reactFiber$fixture = {
      memoizedProps: {message: {messageID:'m1', sender:{userID:'456'},
        textPayload:{text:'Dạ'}, timestamp:1789005600000},
        viewerID:'123', participants:[{userID:'123'},{userID:'456'}], isFromViewer:false}
    }}''')
    monkeypatch.setattr(thread_worker, "scroll_up_message_panel", lambda *_: 0)
    # No virtualized history in this one-event fixture.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    record = ThreadRecord("123", "provisional", "Lan", "", [], 0)
    try:
        result = thread_worker.process_thread_task(
            dom_page, conn, ThreadTask(0, record, 0, "", True),
            thread_worker.ThreadWorkerDeps(lambda _: [], extract_user_info, lambda *_: "Unknown"),
            logging.getLogger("cold-start-test"), is_first_thread=True,
        )
        assert result.status == "persisted", result.error
        assert record.selected_item_id == "456"
        row = conn.execute("SELECT sender, source_id, message_at FROM messages").fetchone()
        assert tuple(row) == ("Customer", "m1", "2026-09-10 09:00:00")
        assert conn.execute("SELECT count(*) FROM threads").fetchone()[0] == 1
    finally:
        conn.close()
        dom_page.unroute("https://business.facebook.com/**")


def test_bootstrap_does_not_click_ambiguous_names_or_verify_sidebar_as_panel(dom_page, monkeypatch):
    import logging
    from fb_pipeline.browser.inbox import thread_locator
    from fb_pipeline.browser.inbox.thread_detail_parser import verify_thread_switch
    from fb_pipeline.contracts.l1_inbox import ThreadRecord
    monkeypatch.setattr(dom_page, "wait_for_timeout", lambda _: None)
    monkeypatch.setattr(thread_locator, "MAX_THREAD_LOADING_WAIT_MS", 100)
    dom_page.set_content('''<div role="main"><h1>Inbox</h1>
      <div class="_5_n1" onclick="window.clicked=true"><div class="_4ik4 _4ik5">Lan</div></div>
      <div class="_5_n1" onclick="window.clicked=true"><div class="_4ik4 _4ik5">Lan</div></div>
      <div class="_4ik4 _4ik5">Other</div></div>''')
    record = ThreadRecord("123", "provisional", "Lan", "", [], 0)
    result = thread_locator.locate_thread_in_sidebar(dom_page, record, logging.getLogger("duplicate"))
    assert not result.clicked
    assert not dom_page.evaluate("() => Boolean(window.clicked)")
    # Even if a caller supplies click proof, the name must come from the
    # conversation panel, not either of the two sidebar cards.
    record.identity_discovery_clicked = True
    assert verify_thread_switch(dom_page, logging.getLogger("duplicate"), "Lan", "", "", True, record) == ("", False)


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


# code:inbox-sender-evidence-001:structural
_META_LIKE_THREAD = '''
<div aria-label="Message list container with messages, suggestions, and typing indicators">
  <div class="x14vqqas"><div class="x2b8uid">Sun 3:40 PM</div></div>
  <div class="x1fqp7bg" style="display:flex;flex-direction:row">
    <img alt="Hung Bui" src="data:," />
    <div class="x1y1aw1k" data-message-id="mid.c1" style="background:rgb(239,239,239)">Học phí ?</div>
  </div>
  <div style="display:flex;flex-direction:row-reverse">
    <div class="x1fqp7bg">
      <div class="x1y1aw1k" data-message-id="mid.p1" style="background:rgb(10,124,255)">Hoàn toàn miễn phí bạn à.</div>
    </div>
  </div>
  <div class="x1fqp7bg" style="display:flex;justify-content:center">
    <div class="x1y1aw1k">Hung Bui replied to an ad.</div>
  </div>
</div>
'''


def test_dom_parser_uses_layout_and_avatar_as_structural_actor_evidence(dom_page):
    dom_page.set_content(_META_LIKE_THREAD)
    # Observed on Monday 2026-09-21 03:00 UTC = 10:00 Asia/Ho_Chi_Minh.
    messages = extract_thread_messages(dom_page, observed_at="2026-09-21T03:00:00+00:00", thread_name="Hung Bui")
    by_text = {m["text"]: m for m in messages}
    customer = by_text["Học phí ?"]
    assert (customer["sender"], customer["sender_confidence"]) == ("Customer", "structural")
    assert '"side": "left"' in customer["sender_evidence"]
    page_msg = by_text["Hoàn toàn miễn phí bạn à."]
    assert (page_msg["sender"], page_msg["sender_confidence"]) == ("Page", "structural")
    assert '"side": "right"' in page_msg["sender_evidence"]
    banner = by_text["Hung Bui replied to an ad."]
    assert (banner["sender"], banner["kind"]) == ("System", "system_banner")


def test_dom_parser_resolves_weekday_clock_against_observed_moment(dom_page):
    from datetime import datetime
    dom_page.set_content(_META_LIKE_THREAD)
    observed = "2026-09-21T03:00:00+00:00"
    messages = extract_thread_messages(dom_page, observed_at=observed, thread_name="Hung Bui")
    m = next(x for x in messages if x["text"] == "Học phí ?")
    local = datetime.fromisoformat(observed).astimezone().replace(tzinfo=None)
    # The most recent Sunday strictly before the (local) observation day.
    expected_day = local.date()
    while expected_day.weekday() != 6 or expected_day >= local.date():
        expected_day = expected_day.fromordinal(expected_day.toordinal() - 1)
    assert m["day_context"] == expected_day.isoformat()
    assert m["time_precision"] == "date_time"
    assert m["raw_timestamp"] == "Sun 3:40 PM"
    assert m["timestamp"] == f"{expected_day.strftime('%b')} {expected_day.day}, {expected_day.year}, 3:40 PM"
    assert "resolved_against_observed_at" in m["time_evidence"]
    assert m["observed_at"] == observed


def test_meta_like_thread_passes_the_integrity_gate(dom_page):
    from fb_pipeline.contracts.l1_fetch_integrity import check_snapshot
    dom_page.set_content(_META_LIKE_THREAD)
    messages = extract_thread_messages(dom_page, observed_at="2026-09-21T03:00:00+00:00", thread_name="Hung Bui")
    issues = check_snapshot(messages)
    # The ad-reply banner is not a conversation turn and must not block the thread.
    assert issues == []


@pytest.mark.parametrize('page_color,customer_color', [('rgb(10,124,255)', 'rgb(239,239,239)'), ('rgb(80,60,90)', 'rgb(40,40,40)'), ('linear-gradient(blue, purple)', 'rgb(239,239,239)')])
def test_visual_sides_and_colors_without_aria_or_avatar(dom_page, page_color, customer_color):
    messages = _extract(dom_page, f'''
    <div role="region" aria-label="message history" style="width:800px">
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="c" style="width:200px;background:{customer_color}">Customer body</div></div>
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="p" style="width:200px;margin-left:auto;background:{page_color}">Page body
        <div class="x1y1aw1k" aria-label="Quoted reply from Customer" style="background:{customer_color}">Quoted body</div>
      </div></div>
      <div class="x1fqp7bg" style="text-align:center"><div class="x1y1aw1k">Lan replied to an ad.</div>
        <a href="https://www.facebook.com/123/posts/456">View post</a></div>
    </div>''')
    assert [m['sender'] for m in messages] == ['Customer', 'Page', 'System']
    assert messages[1]['body'] == 'Page body'
    assert messages[1]['quoted_text'] == 'Quoted body'
    assert messages[2]['source_links'][0]['url'] == 'https://www.facebook.com/123/posts/456'


def test_same_color_on_both_sides_fails_visual_crosscheck(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history" style="width:800px">
      <div class="x1fqp7bg"><div class="x1y1aw1k" style="width:200px;background:gray">Left</div></div>
      <div class="x1fqp7bg"><div class="x1y1aw1k" style="width:200px;margin-left:auto;background:gray">Right</div></div>
    </div>''')
    assert all(m['sender'] == 'Unknown' and '"conflict": true' in m['sender_evidence'] for m in messages)


def test_center_system_event_is_preserved_without_human_sender(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history" style="width:800px">
      <div class="x1fqp7bg" style="display:flex;justify-content:center"><div class="x1y1aw1k">Conversation closed</div></div>
    </div>''')
    assert messages[0]['sender'] == 'System'
    assert messages[0]['kind'] == 'system_banner'


def test_system_event_does_not_borrow_sibling_message_link(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history">
      <div class="x1fqp7bg">
        <div class="x1y1aw1k">Lan replied to an ad.</div>
        <div class="x1y1aw1k" aria-label="You sent a message">Read this <a href="https://www.facebook.com/123/posts/456">post</a></div>
      </div>
    </div>''')
    assert messages[0]['kind'] == 'system_banner'
    assert messages[0]['source_links'] == []


def test_banner_words_inside_colored_message_remain_a_human_turn(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history" style="width:800px">
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="c" style="width:200px;background:gray">Lan replied to an ad.</div></div>
    </div>''')
    assert (messages[0]['sender'], messages[0]['kind']) == ('Customer', 'message')


@pytest.mark.parametrize('text', [
    'Amber Lupo a répondu à une publication. Voir la publication',
    'Amber Lupo respondió a una publicación. Ver publicación',
])
def test_secondary_post_notice_is_language_independent(dom_page, text):
    from fb_pipeline.persistence.l4_inbox_events import source_target
    messages = _extract(dom_page, f'''<div role="region" aria-label="message history">
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="real"
        style="font-size:16px;color:black">Bonjour</div></div>
      <div class="x1fqp7bg" style="font-size:12px;color:gray">
        <div class="x1y1aw1k">{text}</div>
        <a href="https://www.facebook.com/story.php?story_fbid=pfbidAmber&amp;id=100069783204312">↗</a>
      </div></div>''')
    notice = next(m for m in messages if m['text'] == text)
    assert (notice['kind'], notice['sender']) == ('system_banner', 'System')
    assert '"secondary_text": true' in notice['sender_evidence']
    assert source_target(notice['source_links'][0]['url']) == ('post', 'pfbidAmber')


@pytest.mark.parametrize('attributes,link', [
    ('data-message-id="typed"', True),
    ('aria-label="You sent a message"', True),
    ('', False),
])
def test_secondary_text_alone_or_human_evidence_is_not_system(dom_page, attributes, link):
    anchor = '<a href="https://www.facebook.com/123/posts/456">Voir la publication</a>' if link else ''
    messages = _extract(dom_page, f'''<div role="region" aria-label="message history">
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="baseline"
        style="font-size:16px;color:black">Bonjour</div></div>
      <div class="x1fqp7bg" style="font-size:12px;color:gray">
        <div class="x1y1aw1k" {attributes}>Amber Lupo a répondu à une publication.</div>{anchor}
      </div></div>''')
    assert messages[-1]['kind'] == 'message'


def test_unpainted_identified_english_banner_text_is_message(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history">
      <div class="x1fqp7bg" style="text-align:center"><div class="x1y1aw1k"
        data-message-id="typed">Lan replied to an ad.</div></div></div>''')
    assert messages[0]['kind'] == 'message'


@pytest.mark.parametrize('style', ['font-size:16px;color:gray', 'font-size:12px;color:black'])
def test_post_link_requires_both_typography_signals(dom_page, style):
    messages = _extract(dom_page, f'''<div role="region" aria-label="message history">
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="baseline"
        style="font-size:16px;color:black">Bonjour</div></div>
      <div class="x1fqp7bg" style="{style}"><div class="x1y1aw1k">Voir la publication</div>
        <a href="https://www.facebook.com/123/posts/456">↗</a></div></div>''')
    assert messages[-1]['kind'] == 'message'


def test_secondary_notice_cannot_borrow_quote_link(dom_page):
    messages = _extract(dom_page, '''<div role="region" aria-label="message history">
      <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="baseline"
        style="font-size:16px;color:black">Bonjour</div></div>
      <div class="x1fqp7bg" style="font-size:12px;color:gray">
        <div class="x1y1aw1k">Amber Lupo a répondu à une publication.</div>
        <div class="x1y1aw1k" aria-label="Quoted reply from Customer">
          <a href="https://www.facebook.com/123/posts/456">Voir la publication</a>
        </div></div></div>''')
    assert messages[-1]['kind'] == 'message'
    assert messages[-1]['source_links'] == []
