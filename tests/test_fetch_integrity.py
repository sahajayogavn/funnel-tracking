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
    assert result.status == "error"
    assert '"code": "fetch_integrity_failed"' in result.error
    persist.assert_not_called()
    contact.assert_not_called()
    logger.error.assert_called_once()
    conn.commit.assert_not_called()


def test_failed_observation_report_is_private_and_replayable(tmp_path):
    import json
    import os
    from fb_pipeline.browser.inbox.thread_worker import save_integrity_report
    report = save_integrity_report({"code": "fetch_integrity_failed"}, [message()], None, tmp_path)
    with open(report) as handle:
        assert json.load(handle)["observed"] == [message()]
    assert os.stat(report).st_mode & 0o777 == 0o600


def test_cli_reports_verification_failure_as_nonretryable_exit(monkeypatch, capsys):
    import sys
    from tools import l5_fetch_fb_messages as cli
    monkeypatch.setattr(sys, "argv", ["fetch", "--pageId", "123", "--headless"])
    monkeypatch.setattr(cli, "fetch_messages", Mock(return_value={
        "success": False, "error": "fetch_verification_failed", "data": {"stats": {"failed_threads": [{}]}}}))
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 76
    assert "fetch_verification_failed" in capsys.readouterr().out
