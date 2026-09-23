from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fb_pipeline.browser.inbox.thread_detail_parser import verify_thread_switch
from fb_pipeline.contracts.l1_fetch_integrity import check_snapshot, compare_snapshots, compare_stored
from fb_pipeline.contracts.l1_message_time import resolve_message_at


def message(**updates):
    return dict(dict(source_id="m1", sender="Customer", sender_confidence="explicit",
                     sender_evidence="aria-label=Lan sent a message", text="Dạ",
                     timestamp="Sep 10, 2026 9:00 AM", day_context="2026-09-10",
                     time_precision="date_time"), **updates)


@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("page_id,recipient,headings,expected", [
    ("123", "456", ["Lan"], True),
    ("999", "456", ["Lan"], False),
    ("123", "999", ["Lan"], False),
    ("123", "456", ["Inbox"], False),
    ("123", "456", ["Lan Anh"], False),
])
def test_identity_requires_same_poll_exact_ids_and_heading(first, page_id, recipient, headings, expected):
    page = Mock()
    page.evaluate.return_value = {"url": f"https://business.facebook.com/latest/inbox/all?asset_id={page_id}&selected_item_id={recipient}", "headings": headings}
    result = verify_thread_switch(page, Mock(), "Lan", "old", "changed", first,
                                  SimpleNamespace(page_id="123", selected_item_id="456"))
    assert result == ("456", True) if expected else result == ("", False)


def test_identity_accepts_exact_url_and_selected_sidebar_card_when_panel_heading_is_missing():
    page = Mock()
    page.evaluate.return_value = {
        "url": "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=456",
        "headings": ["Inbox"],
        "selectedCards": [{"text": "Lan\nYou: Xin chào", "selected": True}],
    }
    record = SimpleNamespace(page_id="123", selected_item_id="456")
    assert verify_thread_switch(page, Mock(), "Lan", "old", "changed", False, record) == ("456", True)


def test_identity_rejects_selected_sidebar_card_without_exact_url_identity():
    page = Mock()
    page.evaluate.return_value = {
        "url": "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=999",
        "headings": ["Inbox"],
        "selectedCards": [{"text": "Lan", "selected": True}],
    }
    record = SimpleNamespace(page_id="123", selected_item_id="456")
    assert verify_thread_switch(page, Mock(), "Lan", "old", "changed", False, record) == ("", False)


@pytest.mark.parametrize("unique,query,heading,accepted", [
    (True, "asset_id=123", "Lan", True),
    (False, "asset_id=123", "Lan", False),
    (True, "asset_id=123", "Lan Anh", False),
    (True, "asset_id=999", "Lan", False),
    (True, "asset_id=123&selected_item_id=999", "Lan", False),
    (True, "asset_id=123&selected_item_id=", "Lan", False),
])
def test_unique_heading_fallback(unique, query, heading, accepted):
    page = Mock()
    page.evaluate.return_value = {
        "url": "https://business.facebook.com/latest/inbox/all?" + query,
        "headings": [heading], "selectedCards": [],
    }
    record = SimpleNamespace(page_id="123", selected_item_id="456", heading_identity_unique=unique)
    assert verify_thread_switch(page, Mock(), "Lan", "", "", False, record) == (
        ("456", True) if accepted else ("", False))
    if accepted:
        assert page.evaluate.call_count == 2


def test_heading_uniqueness_checks_all_page_identities():
    import sqlite3
    from fb_pipeline.browser.inbox.thread_worker import _heading_identity_unique
    from fb_pipeline.inbox.l3_pipeline import canonical_thread_id
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE threads (id TEXT, page_id TEXT, thread_name TEXT)")
    record = SimpleNamespace(page_id="123", selected_item_id="456", thread_name="Lan")
    assert not _heading_identity_unique(conn, record)
    conn.execute("INSERT INTO threads VALUES (?, ?, ?)", (canonical_thread_id("123", "456"), "123", "Lan"))
    assert _heading_identity_unique(conn, record)
    conn.execute("INSERT INTO threads VALUES ('other', '999', 'Lan')")
    assert _heading_identity_unique(conn, record)
    conn.execute("INSERT INTO threads VALUES ('duplicate', '123', ' LAN  ')")
    assert not _heading_identity_unique(conn, record)
    conn.close()


def test_messenger_user_skips_before_browser_or_database_access():
    from fb_pipeline.browser.inbox.thread_worker import process_thread_task
    from fb_pipeline.browser.inbox.thread_list_parser import is_ignored_inbox_name
    task = SimpleNamespace(ordinal=0, record=SimpleNamespace(
        thread_name=" Messenger  USER ", thread_id="old"))
    page, conn = Mock(), Mock()
    result = process_thread_task(page, conn, task, Mock(), Mock())
    assert result.status == "skipped"
    assert not page.mock_calls and not conn.mock_calls
    assert not is_ignored_inbox_name("Messenger User Nguyen")


def test_snapshot_requires_actor_identity_and_calendar_evidence():
    assert check_snapshot([message()]) == []
    for updates in ({"source_id": None}, {"sender": "Unknown"}, {"sender_evidence": None},
                    {"day_context": "Today"}, {"time_precision": "time_only"},
                    {"day_context": "2026-09-11"}):
        assert check_snapshot([message(**updates)])
    assert check_snapshot([message(), message()])
    assert check_snapshot([message(), message(source_id="m2", timestamp="Sep 9, 2026 9:00 AM", day_context="2026-09-09")])


def test_snapshot_crosscheck_preserves_order_and_detects_each_field():
    first = [message(), message(source_id="m2")]
    assert compare_snapshots(first, first) == []
    assert compare_snapshots(first, first[::-1])
    assert compare_snapshots(first, first[:1])
    for field in ("sender", "timestamp", "text", "source_id"):
        assert compare_snapshots([message()], [message(**{field: "changed"})])[0]["field"] == field


@pytest.mark.parametrize("updates,field", [
    ({"thread_id": "other"}, "recipient"),
    ({"sender": "Page"}, "sender"),
    ({"message_at": "2026-09-11 09:00:00"}, "datetime"),
])
def test_stored_evidence_conflicts(updates, field):
    row = dict(source_id="m1", thread_id="t1", sender="Customer", sender_confidence="explicit",
               message_at="2026-09-10 09:00:00", time_precision="date_time")
    assert compare_stored([message()], [row], "t1") == []
    row.update(updates)
    assert compare_stored([message()], [row], "t1")[0]["field"] == field


def test_absolute_future_date_is_not_silently_changed_to_yesterday():
    stamp, approximate = resolve_message_at("Sep 22, 2026 9:00 AM", datetime(2026, 9, 21, 10))
    assert stamp is None and approximate


@pytest.mark.parametrize("label", ["9/22/2026, 9:00 AM", "9/10/2026, 13:00 PM", "9/10/2026, 0:00 AM"])
def test_invalid_or_future_absolute_clock_is_unresolved(label):
    assert resolve_message_at(label, datetime(2026, 9, 21, 10)) == (None, True)


def test_integrity_failure_never_requeues_full_scan():
    from fb_pipeline.inbox.l3_parallel_fetch import _requeue_failed
    queue = Mock()
    assert not _requeue_failed(Mock(), SimpleNamespace(status="error", error='{"code": "fetch_integrity_failed"}'), queue, "worker:1", Mock())
    queue.put.assert_not_called()


@pytest.mark.parametrize("failure", ["sender", "datetime", "unstable", "recipient", "integrity", "stored_recipient"])
def test_worker_conflict_does_not_extract_contact_or_write(monkeypatch, failure):
    import fb_pipeline.browser.inbox.thread_worker as worker
    import fb_pipeline.inbox.l3_pipeline as pipeline
    from fb_pipeline.contracts.l1_inbox import ThreadRecord
    from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask
    page, conn, logger = Mock(), Mock(), Mock()
    monkeypatch.setattr(worker, "save_integrity_report", Mock(return_value="/tmp/test-report.json"))
    task = ThreadTask(0, ThreadRecord("123", "provisional", "Lan", "", [], 0, selected_item_id="456"), 0, "", True)
    monkeypatch.setattr(worker, "locate_thread_in_sidebar", Mock(return_value=SimpleNamespace(clicked=True, method="sidebar", prev_fb_url="", pre_click_fingerprint="")))
    monkeypatch.setattr(worker, "verify_thread_switch", Mock(side_effect=[("456", True), ("999", True)] if failure == "recipient" else None, return_value=("456", True)))
    monkeypatch.setattr(worker, "scroll_up_message_panel", Mock())
    monkeypatch.setattr(worker, "extract_ad_context", Mock(return_value=""))
    monkeypatch.setattr(worker, "validate_thread_integrity", Mock(return_value=failure != "integrity"))
    observed = message()
    if failure == "sender":
        observed["sender"] = "Unknown"
    if failure == "datetime":
        observed["day_context"] = "Yesterday"
    monkeypatch.setattr(worker, "extract_thread_messages", Mock(side_effect=[[observed], [message(text="Changed")]] if failure == "unstable" else None, return_value=[observed]))
    conn.execute.return_value.fetchall.return_value = ([dict(source_id="m1", thread_id="other")] if failure == "stored_recipient" else [])
    persist = Mock()
    monkeypatch.setattr(pipeline, "persist_thread_record", persist)
    contact = Mock()
    result = worker.process_thread_task(page, conn, task, worker.ThreadWorkerDeps(Mock(return_value=[]), contact, Mock()), logger)
    unresolved = failure in {"sender", "datetime"}
    assert result.status == ("needs_review" if unresolved else "error")
    assert ('"code": "fetch_evidence_needs_review"' if unresolved else '"code": "fetch_integrity_failed"') in result.error
    if failure == "stored_recipient":
        assert 'stored_evidence_conflict' in result.error
    persist.assert_not_called()
    contact.assert_not_called()
    logger.error.assert_called_once()
    if unresolved:
        conn.commit.assert_called_once()
    else:
        conn.commit.assert_not_called()


def test_bootstrap_requires_actual_click_and_stable_matching_panel():
    record = SimpleNamespace(page_id="123", selected_item_id="", identity_discovery_clicked=False)
    good = {"url": "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=456", "headings": ["Lan"]}
    page = Mock()
    page.evaluate.return_value = good
    assert verify_thread_switch(page, Mock(), "Lan", "", "", True, record) == ("", False)
    page.evaluate.assert_not_called()
    record.identity_discovery_clicked = True
    page.evaluate.side_effect = [{**good, "headings": ["Other"]}, good, good]
    assert verify_thread_switch(page, Mock(), "Lan", "old", "", True, record) == ("456", True)
    assert record.selected_item_id == "456"
    assert page.evaluate.call_count == 3


def test_bootstrap_never_combines_heading_from_one_poll_with_id_from_another():
    record = SimpleNamespace(page_id="123", selected_item_id="", identity_discovery_clicked=True)
    page = Mock()
    good = {"url": "https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=456", "headings": ["Lan"]}
    page.evaluate.side_effect = [good, {**good, "headings": ["Other"]}] * 10
    assert verify_thread_switch(page, Mock(), "Lan", "", "", True, record) == ("", False)
    assert record.selected_item_id == ""


def test_unresolved_observations_are_idempotent_and_never_become_messages():
    import sqlite3
    import json
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    from fb_pipeline.persistence.l4_inbox_observations import save_unresolved_observation
    conn = sqlite3.connect(":memory:")
    setup_database(conn)
    from fb_pipeline.inbox.l3_pipeline import canonical_thread_id
    record = SimpleNamespace(page_id="123", thread_id=canonical_thread_id("123", "456"), thread_name="Lan")
    try:
        one = save_unresolved_observation(conn, record, "456", [message(sender="Unknown")], [])
        two = save_unresolved_observation(conn, record, "456", [message(sender="Unknown")], [])
        assert one == two
        assert conn.execute("SELECT count(*) FROM inbox_fetch_observations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        # Same base tables/join as getAllSeekers: identity-only seekers must
        # be visible and have a numeric users.id for the detail link.
        seeker = conn.execute("SELECT u.id,t.thread_name,u.fb_url,u.last_interaction,t.fetched_at "
                              "FROM threads t JOIN users u ON u.thread_id=t.id").fetchone()
        assert seeker[0] > 0
        assert seeker[1:] == ("Lan", "456", None, None)
        conn.execute("UPDATE users SET phone='0900000000',city='Hà Nội',lead_stage='Yogi'")
        conn.commit()
        save_unresolved_observation(conn, record, "456", [message(sender="Unknown")], [])
        assert conn.execute("SELECT phone,city,lead_stage FROM users").fetchone() == ("0900000000", "Hà Nội", "Yogi")
        assert json.loads(conn.execute("SELECT payload_json FROM inbox_fetch_observations").fetchone()[0])["messages"][0]["sender"] == "Unknown"
        with pytest.raises(ValueError):
            save_unresolved_observation(conn, record, "", [], [])
    finally:
        conn.close()


def test_needs_review_is_reported_but_does_not_retire_or_retry_worker():
    from fb_pipeline.inbox.l3_parallel_fetch import _WorkerHealth, _requeue_failed, _aggregate_stats
    from fb_pipeline.contracts.l1_inbox_tasks import ThreadResult
    result = ThreadResult(ordinal=0, thread_id="123_abc", status="needs_review", error="unverified_actor")
    assert _WorkerHealth().record(result.status) is None
    assert not _requeue_failed(Mock(), result, Mock(), "worker:1", Mock())
    stats = _aggregate_stats({}, [result], 1, 2, 0, 0)
    assert stats["threads_needs_review"] == 1
    assert stats["seekers_saved"] == 1
    assert stats["failed_threads"][0]["status"] == "needs_review"


def test_unresolved_identity_conflict_rolls_back_without_overwriting_seeker():
    import sqlite3
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    from fb_pipeline.persistence.l4_inbox_observations import save_unresolved_observation
    from fb_pipeline.inbox.l3_pipeline import canonical_thread_id
    conn = sqlite3.connect(":memory:")
    setup_database(conn)
    record = SimpleNamespace(page_id="123", thread_id=canonical_thread_id("123", "456"), thread_name="Lan")
    try:
        conn.execute("INSERT INTO users(thread_id,thread_name,fb_url) VALUES (?,?,?)", (record.thread_id, "Existing", "999"))
        conn.commit()
        with pytest.raises(ValueError, match="stored_seeker_recipient_conflict"):
            save_unresolved_observation(conn, record, "456", [], [])
        assert conn.execute("SELECT thread_name,fb_url FROM users").fetchone() == ("Existing", "999")
        assert conn.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM inbox_fetch_observations").fetchone()[0] == 0
        record.thread_id = "wrong"
        with pytest.raises(ValueError, match="observation_thread_identity_mismatch"):
            save_unresolved_observation(conn, record, "456", [], [])
    finally:
        conn.close()


def test_unresolved_observation_cannot_rebind_existing_thread_to_another_page():
    import sqlite3
    from fb_pipeline.persistence.l4_sqlite_store import setup_database
    from fb_pipeline.persistence.l4_inbox_observations import save_unresolved_observation
    from fb_pipeline.inbox.l3_pipeline import canonical_thread_id
    conn = sqlite3.connect(":memory:")
    setup_database(conn)
    record = SimpleNamespace(page_id="123", thread_id=canonical_thread_id("123", "456"), thread_name="Lan")
    try:
        conn.execute("INSERT INTO threads(id,page_id,thread_name) VALUES (?,?,?)", (record.thread_id, "999", "Existing"))
        conn.commit()
        with pytest.raises(ValueError, match="stored_thread_page_conflict"):
            save_unresolved_observation(conn, record, "456", [], [])
        assert conn.execute("SELECT page_id,thread_name FROM threads").fetchone() == ("999", "Existing")
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    finally:
        conn.close()


def test_failed_observation_report_is_private_and_replayable(tmp_path):
    import json
    import os
    from fb_pipeline.browser.inbox.thread_worker import save_integrity_report
    report = save_integrity_report({"code": "fetch_integrity_failed"}, [message()], None, tmp_path)
    with open(report) as handle:
        assert json.load(handle)["observed"] == [message()]
    assert os.stat(report).st_mode & 0o777 == 0o600


def test_cli_allows_verification_failure_to_retry(monkeypatch, capsys):
    import sys
    from tools import l5_fetch_fb_messages as cli
    monkeypatch.setattr(sys, "argv", ["fetch", "--pageId", "123", "--headless"])
    monkeypatch.setattr(cli, "fetch_messages", Mock(return_value={
        "success": False, "error": "fetch_verification_failed", "data": {"stats": {"failed_threads": [{}]}}}))
    cli.main()
    assert "fetch_verification_failed" in capsys.readouterr().out


@pytest.mark.parametrize("reason", ["content_mismatch", "sender_mismatch", "last_message_time_mismatch"])
def test_cli_stops_only_for_hard_message_history_or_sender_qa(monkeypatch, reason):
    import sys
    from tools import l5_fetch_fb_messages as cli
    monkeypatch.setattr(sys, "argv", ["fetch", "--pageId", "123", "--headless"])
    monkeypatch.setattr(cli, "fetch_messages", Mock(return_value={
        "success": False,
        "error": "fetch_qa_message_history_or_sender_mismatch",
        "data": {"stats": {"qa": {"qa2": [{"verdict": "hard", "reason": reason}]}}},
    }))
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 76


def test_fetch_stop_error_ignores_non_history_qa_and_internal_diagnostics():
    from tools.l5_fetch_fb_messages import _fetch_stop_error
    assert _fetch_stop_error({"qa": {"qa_status": "failed", "qa1": [{"verdict": "hard"}]},
                              "failed_threads": [{"status": "error"}], "fetch_complete": False}) is None
    assert _fetch_stop_error({"qa": {"qa2": [{"verdict": "hard", "reason": "sender_mismatch"}]}}) == \
        "fetch_qa_message_history_or_sender_mismatch"
