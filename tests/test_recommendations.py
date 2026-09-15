# code:test-validation-001:recommendations
import sqlite3
import pytest
from fb_pipeline.persistence.l4_sqlite_store import setup_database
from tools import l5_action_queue as queue
from tools import l5_recommendations as rec


@pytest.fixture
def test_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test_rec.db"

    def get_test_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        setup_database(conn)
        return conn

    monkeypatch.setattr(queue, "get_db_connection", get_test_conn)
    monkeypatch.setattr(rec, "get_db_connection", get_test_conn)

    conn = get_test_conn()
    # Insert sample thread and messages
    conn.execute(
        "INSERT INTO threads (id, page_id, thread_name, last_synced_time) VALUES (?, ?, ?, datetime('now'))",
        ("thread-101", "p1", "Nguyễn Văn A"),
    )
    conn.execute(
        "INSERT INTO messages (thread_id, sender, content, timestamp) VALUES (?, ?, ?, datetime('now'))",
        ("thread-101", "Customer", "Em muốn hỏi lớp thiền ở Hà Nội ạ"),
    )
    conn.execute(
        "INSERT INTO users (thread_id, thread_name, city, lead_stage, last_interaction) VALUES (?, ?, ?, ?, datetime('now'))",
        ("thread-101", "Nguyễn Văn A", "Hà Nội", "Intake"),
    )
    conn.commit()
    conn.close()
    return db_path


def test_generate_reply_recommendations_creates_pending_proposal(test_db):
    results = rec.generate_reply_recommendations(page_id="p1", limit=5)
    assert len(results) == 1
    item = results[0]
    assert item["queue_type"] == "reply_message"
    assert item["target_id"] == "thread-101"
    assert "Nguyễn Văn A" in item["target_name"]

    # Verify in DB that status is pending and action exists
    conn = rec.get_db_connection()
    row = conn.execute("SELECT * FROM action_queue WHERE id = ?", (item["action_id"],)).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert "Hà Nội" in row["action_text"]
    conn.close()

    # Second run should not create duplicate pending proposal
    dups = rec.generate_reply_recommendations(page_id="p1", limit=5)
    assert len(dups) == 0


def test_generate_warmup_recommendations_creates_pending_proposal(test_db):
    results = rec.generate_warmup_recommendations(page_id="p1", target_thread_id="thread-101", limit=5)
    assert len(results) == 1
    assert results[0]["queue_type"] == "proactive_message"
    conn = rec.get_db_connection()
    row = conn.execute("SELECT * FROM action_queue WHERE id = ?", (results[0]["action_id"],)).fetchone()
    assert row["status"] == "pending"
    assert "MIỄN PHÍ" in row["action_text"]
    conn.close()


def test_generate_event_recommendations_creates_pending_proposal(test_db):
    results = rec.generate_event_recommendations(page_id="p1", city="Hà Nội", limit=5)
    assert len(results) == 1
    assert results[0]["queue_type"] == "proactive_message"
    conn = rec.get_db_connection()
    row = conn.execute("SELECT * FROM action_queue WHERE id = ?", (results[0]["action_id"],)).fetchone()
    assert row["status"] == "pending"
    assert "thiền định" in row["action_text"].lower()
    conn.close()


def test_all_recommendations_never_auto_sends(test_db):
    # Generating recommendations must not change status to executed or approved
    rec.generate_all_recommendations(page_id="p1", limit_per_type=2)
    conn = rec.get_db_connection()
    rows = conn.execute("SELECT DISTINCT status FROM action_queue").fetchall()
    statuses = [r["status"] for r in rows]
    assert statuses == ["pending"]
    conn.close()
