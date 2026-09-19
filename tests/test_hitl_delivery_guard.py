"""code:hitl-delivery-guard-001:tests — offline DB/browser delivery contract."""
import copy
import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from tools import l5_delivery_guard as guard
from tools import l5_hitl_execution as executor
from tools import l5_action_queue as queue
from fb_pipeline.persistence import l4_sqlite_store as store

PAGE = "1548373332058326"
THREAD = PAGE + "_100001005716854"
MESSAGES = [{"sender": "Customer", "text": "Xin chào", "source_id": "m1", "timestamp": "Sep 19, 2026"}]


def item():
    return {"id": 1, "page_id": PAGE, "target_id": THREAD, "target_name": "Test Seeker",
            "status": "executing", "approved_at": "2026-09-19", "queue_type": "proactive_message",
            "action_text": "Chào bạn\nHẹn gặp lại", "payload": {"conversation_snapshot": guard.conversation_snapshot(MESSAGES)}}


@pytest.fixture
def database(monkeypatch, tmp_path):
    def connect():
        db = sqlite3.connect(tmp_path / "test.db")
        db.row_factory = sqlite3.Row
        return db
    db = connect()
    store.setup_database(db)
    db.close()
    monkeypatch.setattr(store, "get_db_connection", connect)
    monkeypatch.setattr(queue, "get_db_connection", connect)
    return connect


@pytest.fixture
def browser(monkeypatch):
    page = MagicMock()
    box = MagicMock()
    content = [""]
    box.count.return_value = 1
    box.inner_text.side_effect = lambda: content[0]
    box.fill.side_effect = lambda text: content.__setitem__(0, text)
    page.locator.return_value = box
    monkeypatch.setattr(guard, "assert_recipient", lambda *_: None)
    monkeypatch.setattr(guard, "read_live_messages", lambda *_: copy.deepcopy(MESSAGES))
    return page, box, content


def test_default_drafts_multiline_without_enter(browser):
    page, box, content = browser
    assert guard.prepare_draft(page, item()) == "drafted"
    assert content[0] == item()["action_text"]
    box.press.assert_not_called()
    page.bring_to_front.assert_called_once()


@pytest.mark.parametrize("flags,expected", [([], False), (["--draft-only"], False), (["--auto-send"], True)])
def test_cli_default_and_explicit_modes(monkeypatch, flags, expected):
    import sys
    calls = []
    monkeypatch.setattr(sys, "argv", ["hitl", "--page-id", PAGE, "--live", "--once", *flags])
    monkeypatch.setattr(executor, "run_cycle", lambda *args: calls.append(args))
    executor.main()
    assert calls == [(PAGE, False, expected)]


def test_conflicting_cli_flags_fail(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["hitl", "--page-id", PAGE, "--draft-only", "--auto-send"])
    with pytest.raises(SystemExit) as exc: executor.main()
    assert exc.value.code == 2


def test_execution_boundary_uses_guard_and_keeps_tab(browser, monkeypatch):
    page, _, _ = browser
    fake_session(monkeypatch, page)
    assert executor._execute_approved_action(item(), PAGE) == "drafted"
    missing = item()
    missing["payload"] = {}
    with pytest.raises(guard.OutdatedAction): executor._execute_approved_action(missing, PAGE)
    assert executor._execute_approved_action({}, PAGE, dry_run=True) == "preview"


def test_delivery_does_not_scroll_up_full_history(browser, monkeypatch):
    page, _, _ = browser
    fake_session(monkeypatch, page)
    scroll = MagicMock()
    from fb_pipeline.browser.inbox import thread_detail_parser
    monkeypatch.setattr(thread_detail_parser, "scroll_up_message_panel", scroll)
    assert executor._execute_approved_action(item(), PAGE) == "drafted"
    scroll.assert_not_called()


def test_legacy_missing_context_is_rejected_by_worker_and_requests_refetch(database, monkeypatch):
    action = enqueue()
    queue.approve_action(action, "webui")
    with database() as db: db.execute("UPDATE action_queue SET payload_json='{}' WHERE id=?", (action,))
    @contextmanager
    def lock(*a): yield True
    monkeypatch.setattr(executor, "scheduler_browser_cycle", lock)
    monkeypatch.setattr(guard, "reconcile_drafts", lambda *_: None)
    refresh = MagicMock()
    monkeypatch.setattr(guard, "refresh_outdated_actions", refresh)
    executor.hitl_execution_job(PAGE, dry_run=False)
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (action,)).fetchone()
    assert row["status"] == "rejected"
    assert row["error_text"].startswith("Out-date:")
    refresh.assert_called_once_with(PAGE)


def test_auto_send_requires_new_page_message_evidence(browser, monkeypatch):
    page, box, content = browser
    sent = {"sender": "Page", "text": item()["action_text"], "source_id": "sent"}
    def press(key):
        assert key == "Enter"
        content[0] = ""
        monkeypatch.setattr(guard, "read_live_messages", lambda *_: MESSAGES + [sent])
    box.press.side_effect = press
    assert guard.prepare_draft(page, item(), auto_send=True) == "sent"
    box.press.assert_called_once_with("Enter")


def test_enter_without_delivery_confirmation_is_failure(browser):
    page, box, _ = browser
    with pytest.raises(RuntimeError, match="send_unconfirmed"):
        guard.prepare_draft(page, item(), auto_send=True)
    box.press.assert_called_once_with("Enter")


@pytest.mark.parametrize("change", ["append", "edit", "sender", "quote", "identity", "delete"])
def test_changed_context_is_outdated(change):
    changed = copy.deepcopy(MESSAGES)
    if change == "append": changed.append({"sender": "Page", "text": "Một tin mới"})
    elif change == "delete": changed.clear()
    elif change == "edit": changed[0]["text"] = "Đừng nhắn nữa"
    elif change == "sender": changed[0]["sender"] = "Page"
    elif change == "quote": changed[0]["quoted_text"] = "Đổi lịch"
    else: changed[0]["source_id"] = "new-id"
    with pytest.raises(guard.OutdatedAction):
        guard.assert_context_current(item()["payload"]["conversation_snapshot"], changed)


def test_missing_snapshot_fails_closed():
    with pytest.raises(guard.OutdatedAction, match="missing_context_snapshot"):
        guard.assert_context_current(None, MESSAGES)


def test_latest_message_without_stable_id_fails_closed():
    snapshot = guard.conversation_snapshot([{"sender": "Customer", "text": "Dạ"}])
    with pytest.raises(guard.OutdatedAction):
        guard.assert_context_current(snapshot, [{"sender": "Customer", "text": "Dạ"}])


def test_virtualized_history_matches_only_latest_fetched_message():
    history = [
        {"sender": "Customer", "text": f"Lịch sử {index}", "source_id": f"m{index}"}
        for index in range(28)
    ]
    snapshot = guard.conversation_snapshot(history)
    assert snapshot == {
        "version": 2,
        "messages": [{"body": "Lịch sử 27", "sender": "Customer", "source_id": "m27",
                      "raw_timestamp": "", "day_context": "", "quoted_text": "", "reply_to_message_id": ""}],
        "complete": False,
        "latest_count": 1,
    }
    guard.assert_context_current(snapshot, history[-8:])


def test_legacy_full_snapshot_uses_latest_stable_message_not_count():
    history = [
        {"sender": "Customer", "text": f"Lịch sử {index}", "source_id": f"m{index}"}
        for index in range(28)
    ]
    legacy_snapshot = guard.conversation_snapshot(history, complete=True, latest_count=None)
    legacy_snapshot["version"] = 1
    legacy_snapshot.pop("latest_count")
    guard.assert_context_current(legacy_snapshot, history[-8:])


def test_new_message_during_fill_clears_only_own_text(browser, monkeypatch):
    page, box, content = browser
    calls = iter([MESSAGES, MESSAGES + [{"sender": "Customer", "text": "Khoan"}]])
    monkeypatch.setattr(guard, "read_live_messages", lambda *_: next(calls))
    with pytest.raises(guard.OutdatedAction):
        guard.prepare_draft(page, item(), auto_send=True)
    assert content[0] == ""
    box.press.assert_not_called()


def test_existing_operator_draft_is_not_overwritten(browser):
    page, box, content = browser
    content[0] = "Operator đang soạn"
    with pytest.raises(RuntimeError, match="operator draft"):
        guard.prepare_draft(page, item())
    box.fill.assert_not_called()


@pytest.mark.parametrize("page_id,psid", [(PAGE, "wrong"), ("other", "100001005716854")])
def test_wrong_recipient_rejected(page_id, psid):
    page = MagicMock()
    page.url = f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}&selected_item_id={psid}"
    with pytest.raises(RuntimeError, match="recipient_identity_unverified"):
        guard.assert_recipient(page, item())


def test_exact_page_scoped_psid_is_sufficient_when_name_changes():
    page = MagicMock()
    page.url = f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE}&selected_item_id=100001005716854"
    guard.assert_recipient(page, item())


def enqueue(target=THREAD, page=PAGE):
    return queue.enqueue_action(queue_type="proactive_message", page_id=page, target_type="thread",
        target_id=target, target_name="Test Seeker", action_text="Chào bạn",
        payload=item()["payload"])


def test_draft_not_sent_not_reclaimed_and_other_seeker_can_progress(database):
    first = enqueue()
    second = enqueue(THREAD + "2")
    queue.approve_action(first, "webui")
    queue.approve_action(second, "webui")
    claimed = queue.claim_next_action("proactive_message", page_id=PAGE)
    guard.set_delivery_result(claimed, "drafted")
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (first,)).fetchone()
    assert row["executed_at"] is None
    assert row["status"] == "executing"
    assert queue.claim_next_action("proactive_message", page_id=PAGE)["id"] == second
    assert queue.claim_next_action("proactive_message", page_id=PAGE) is None


def test_outdated_rejects_and_persists_fetch_request(database):
    action = enqueue()
    queue.approve_action(action, "webui")
    guard.set_delivery_result(queue.claim_next_action("proactive_message", page_id=PAGE), "outdated", "Out-date: new message")
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (action,)).fetchone()
    assert row["status"] == "rejected" and row["executed_at"] is None
    assert json.loads(row["payload_json"])["fetch_request"] == "pending"


def test_unconfirmed_delivery_failure_does_not_record_sent_timestamp(database):
    action = enqueue()
    queue.approve_action(action, "webui")
    queue.claim_next_action("proactive_message", PAGE)
    queue.finish_action(action, "send_unconfirmed")
    with database() as db:
        row = db.execute("SELECT status,executed_at FROM action_queue WHERE id=?", (action,)).fetchone()
    assert row["status"] == "failed" and row["executed_at"] is None


def test_claim_page_scope_and_deleted_head(database):
    other = enqueue("other", "other-page")
    deleted = enqueue("deleted")
    action = enqueue()
    with database() as db: db.execute("UPDATE action_queue SET status='deleted' WHERE id=?", (deleted,))
    queue.approve_action(other, "webui")
    queue.approve_action(action, "webui")
    assert queue.peek_next_approved("proactive_message", page_id=PAGE)["id"] == action
    assert queue.claim_next_action("proactive_message", page_id=PAGE)["id"] == action


def test_web_approval_does_not_wait_for_unapproved_draft(database):
    enqueue("pending-other-seeker")
    action = enqueue()
    queue.approve_action(action, "webui")
    assert queue.claim_next_action("proactive_message", PAGE)["id"] == action


def test_same_recipient_cannot_get_second_draft_from_other_queue(database):
    action = enqueue()
    queue.approve_action(action, "webui")
    guard.set_delivery_result(queue.claim_next_action("proactive_message", PAGE), "drafted")
    reply = queue.enqueue_action(queue_type="reply_message", page_id=PAGE, target_type="thread",
        target_id=THREAD, target_name="Test Seeker", action_text="Another draft")
    queue.approve_action(reply, "webui")
    assert queue.claim_next_action("reply_message", PAGE) is None


def test_web_execution_precedes_telegram_and_survives_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "hitl_execution_job", lambda *a, **k: calls.append("web"))
    def telegram():
        calls.append("telegram")
        raise RuntimeError("offline")
    monkeypatch.setattr(executor, "telegram_poller_job", telegram)
    executor.run_cycle(PAGE, False)
    assert calls == ["web", "telegram"]
    calls.clear()
    executor.run_cycle(PAGE, True)
    assert calls == ["web"]


def test_executor_web_approved_draft_end_to_end(database, monkeypatch):
    action = enqueue()
    queue.approve_action(action, "webui")
    @contextmanager
    def lock(*a): yield True
    monkeypatch.setattr(executor, "scheduler_browser_cycle", lock)
    monkeypatch.setattr(guard, "reconcile_drafts", lambda *_: None)
    monkeypatch.setattr(guard, "refresh_outdated_actions", lambda *_: None)
    captured = []
    def execute(item, page, **kwargs):
        captured.append((item, kwargs))
        return "drafted"
    monkeypatch.setattr(executor, "_execute_approved_action", execute)
    executor.hitl_execution_job(PAGE, dry_run=False)
    assert captured[0][0]["approval_source"] == "webui"
    assert captured[0][1]["auto_send"] is False
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (action,)).fetchone()
    assert json.loads(row["payload_json"])["delivery_status"] == "drafted"


def fake_session(monkeypatch, page):
    from fb_pipeline.session import l2_bootstrap as bootstrap
    from fb_pipeline.browser import l2_actions
    from fb_pipeline.browser.inbox import thread_detail_parser
    import playwright.sync_api
    session = MagicMock(page=page, tab_role="test")
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", MagicMock())
    monkeypatch.setattr(bootstrap, "attach_to_authorized_session", lambda *a, **k: session)
    monkeypatch.setattr(bootstrap, "stamp_tab_role", lambda *a: None)
    monkeypatch.setattr(l2_actions, "navigate_to_thread", lambda *a: True)
    monkeypatch.setattr(thread_detail_parser, "scroll_up_message_panel", lambda *a: None)
    return bootstrap, session


def test_refetch_failure_is_durable_then_success_persists_exact_thread(database, monkeypatch):
    from fb_pipeline.inbox import l3_pipeline
    action = enqueue()
    queue.approve_action(action, "webui")
    guard.set_delivery_result(queue.claim_next_action("proactive_message", PAGE), "outdated", "Out-date")
    page = MagicMock()
    fake_session(monkeypatch, page)
    monkeypatch.setattr(guard, "assert_recipient", lambda *a: None)
    def unavailable(*_): raise RuntimeError("Facebook temporarily unavailable")
    monkeypatch.setattr(guard, "read_live_messages", unavailable)
    guard.refresh_outdated_actions(PAGE)
    with database() as db:
        payload = json.loads(db.execute("SELECT payload_json FROM action_queue WHERE id=?", (action,)).fetchone()[0])
    assert payload["fetch_request"] == "pending"
    assert "unavailable" in payload["fetch_error"]
    captured = []
    monkeypatch.setattr(guard, "read_live_messages", lambda *a: MESSAGES)
    monkeypatch.setattr(l3_pipeline, "persist_thread_record", lambda conn, record: captured.append(record))
    guard.refresh_outdated_actions(PAGE)
    assert captured[0].thread_id == THREAD and captured[0].page_id == PAGE
    assert captured[0].messages[0].content == "Xin chào"
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (action,)).fetchone()
    assert row["status"] == "rejected"
    assert json.loads(row["payload_json"])["fetch_request"] == "completed"


def test_manual_enter_confirmed_on_next_poll(database, browser, monkeypatch):
    page, box, content = browser
    action = enqueue()
    queue.approve_action(action, "webui")
    claimed = queue.claim_next_action("proactive_message", PAGE)
    guard.set_delivery_result(claimed, "drafted")
    bootstrap, _ = fake_session(monkeypatch, page)
    browser_connection = MagicMock()
    browser_connection.contexts = [MagicMock(pages=[page])]
    page.evaluate.return_value = f"outbound:{action}"
    monkeypatch.setattr(bootstrap, "connect_to_cdp_browser", lambda *_: browser_connection)
    sent = {"sender": "Page", "text": claimed["action_text"], "source_id": "sent"}
    monkeypatch.setattr(guard, "read_live_messages", lambda *_: MESSAGES + [sent])
    guard.reconcile_drafts(PAGE)
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (action,)).fetchone()
    assert row["status"] == "executed" and row["executed_at"]
    box.press.assert_not_called()


def test_draft_new_message_on_next_tick_rejected_and_cleared(database, browser, monkeypatch):
    page, box, content = browser
    action = enqueue()
    queue.approve_action(action, "webui")
    claimed = queue.claim_next_action("proactive_message", PAGE)
    guard.set_delivery_result(claimed, "drafted")
    content[0] = claimed["action_text"]
    bootstrap, _ = fake_session(monkeypatch, page)
    connection = MagicMock()
    connection.contexts = [MagicMock(pages=[page])]
    page.evaluate.return_value = f"outbound:{action}"
    monkeypatch.setattr(bootstrap, "connect_to_cdp_browser", lambda *_: connection)
    monkeypatch.setattr(guard, "read_live_messages", lambda *_: MESSAGES + [{"sender": "Customer", "text": "Tin mới"}])
    guard.reconcile_drafts(PAGE)
    assert content[0] == ""
    with database() as db:
        row = db.execute("SELECT * FROM action_queue WHERE id=?", (action,)).fetchone()
    assert row["status"] == "rejected"
    assert json.loads(row["payload_json"])["fetch_request"] == "pending"


def test_real_dom_recipient_and_draft_no_enter():
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).exists(): pytest.skip("Local Chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=chrome)
        page = browser.new_page()
        # Route fulfills locally; no Facebook network request or account access.
        page.route("**/*", lambda route: route.fulfill(body='''<main><section>
          <h2>Test Seeker</h2><div role="region" aria-label="message history">
          <div class="x1fqp7bg"><div class="x1y1aw1k" data-message-id="m1"
          aria-label="Test Seeker sent a message">Xin chào</div></div></div>
          <div role="textbox" contenteditable="true"></div></section></main>
          <script>window.enters=0;document.addEventListener('keydown',e=>{if(e.key==='Enter')window.enters++})</script>''', content_type="text/html"))
        page.goto(f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE}&selected_item_id=100001005716854")
        approved = item()
        approved["payload"]["conversation_snapshot"] = guard.conversation_snapshot(guard.read_live_messages(page, approved))
        assert guard.prepare_draft(page, approved) == "drafted"
        assert page.locator(guard.COMPOSER).inner_text() == approved["action_text"]
        assert page.evaluate("window.enters") == 0
        # A renamed Facebook profile must not invalidate its stable PSID.
        page.locator("h2").evaluate("e => e.innerText = 'Renamed Facebook Profile'")
        guard.assert_recipient(page, approved)
        browser.close()
