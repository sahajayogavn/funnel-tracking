import sqlite3

from fb_pipeline.persistence.l4_sqlite_store import setup_database
from tools import l5_action_queue as queue


def test_queue_needs_approval_and_preserves_fifo(monkeypatch, tmp_path):
    db_path = tmp_path / "queue.db"

    def get_test_db():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        return conn

    monkeypatch.setattr(queue, "get_db_connection", get_test_db)
    first = queue.enqueue_action(queue_type="reply_message", page_id="p", target_type="thread", target_id="1", target_name="A", action_text="one")
    second = queue.enqueue_action(queue_type="reply_message", page_id="p", target_type="thread", target_id="2", target_name="B", action_text="two")

    assert queue.claim_next_action("reply_message") is None
    assert queue.approve_action(second, "webui")
    assert queue.claim_next_action("reply_message") is None  # first is still undecided
    assert queue.approve_action(first, "telegram:👍")
    claimed = queue.claim_next_action("reply_message")
    assert claimed and claimed["id"] == first
    queue.finish_action(first)
    claimed = queue.claim_next_action("reply_message")
    assert claimed and claimed["id"] == second


def test_an_approved_action_is_claimed_only_once(monkeypatch, tmp_path):
    db_path = tmp_path / "single-claim.db"

    def get_test_db():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        return conn

    monkeypatch.setattr(queue, "get_db_connection", get_test_db)
    action_id = queue.enqueue_action(
        queue_type="reply_message", page_id="p", target_type="thread",
        target_id="1", target_name="A", action_text="only once",
    )
    assert queue.approve_action(action_id, "webui")
    first_claim = queue.claim_next_action("reply_message")
    second_claim = queue.claim_next_action("reply_message")
    assert first_claim and first_claim["id"] == action_id
    assert second_claim is None
