"""Unit tests for tools/dedup_users.py"""

import sqlite3
import unittest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools.dedup_users import score_user, find_duplicate_groups, merge_group, run_dedup


def _create_test_db():
    """Create an in-memory DB with the production schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT UNIQUE,
            thread_name TEXT,
            phone TEXT,
            email TEXT,
            fb_url TEXT,
            city TEXT DEFAULT 'Unknown',
            lead_stage TEXT DEFAULT 'Intake',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            page_id TEXT,
            thread_name TEXT,
            last_synced_time TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            sender TEXT,
            content TEXT,
            message_timestamp TEXT,
            seq INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, sender, content, message_timestamp, seq)
        )
    """)
    conn.execute("""
        CREATE TABLE user_ad_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            ad_id TEXT,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(thread_id, ad_id)
        )
    """)
    return conn


class TestScoreUser(unittest.TestCase):
    def test_empty_record_scores_zero(self):
        score_key = score_user({"phone": "", "email": "", "city": "Unknown", "lead_stage": "Intake"})
        self.assertEqual(score_key[0], 0)

    def test_phone_adds_one(self):
        score_key = score_user({"phone": "0123456789", "email": "", "city": "Unknown", "lead_stage": "Intake"})
        self.assertEqual(score_key[0], -1)

    def test_all_fields_score_four(self):
        score_key = score_user({
            "phone": "0123456789",
            "email": "a@b.com",
            "city": "Hanoi",
            "lead_stage": "Engaged",
        })
        self.assertEqual(score_key[0], -4)

    def test_tie_break_uses_earliest_first_seen_then_thread_id(self):
        earlier = score_user({
            "phone": "0123456789",
            "first_seen": "2026-01-01 00:00:00",
            "thread_id": "b-thread",
        })
        later = score_user({
            "phone": "0123456789",
            "first_seen": "2026-01-02 00:00:00",
            "thread_id": "a-thread",
        })
        same_time_a = score_user({
            "phone": "0123456789",
            "first_seen": "2026-01-01 00:00:00",
            "thread_id": "a-thread",
        })
        same_time_b = score_user({
            "phone": "0123456789",
            "first_seen": "2026-01-01 00:00:00",
            "thread_id": "b-thread",
        })

        self.assertLess(earlier, later)
        self.assertLess(same_time_a, same_time_b)


class TestFindDuplicateGroups(unittest.TestCase):
    def test_no_duplicates(self):
        conn = _create_test_db()
        conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('t1', 'A', '111')")
        conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('t2', 'B', '222')")
        conn.commit()
        groups = find_duplicate_groups(conn)
        self.assertEqual(len(groups), 0)

    def test_finds_pair(self):
        conn = _create_test_db()
        conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('t1', 'A', '111')")
        conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('t2', 'B', '111')")
        conn.commit()
        groups = find_duplicate_groups(conn)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_ignores_empty_fb_url(self):
        conn = _create_test_db()
        conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('t1', 'A', '')")
        conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('t2', 'B', '')")
        conn.commit()
        groups = find_duplicate_groups(conn)
        self.assertEqual(len(groups), 0)


class TestMergeGroup(unittest.TestCase):
    def _seed_pair(self, conn, keeper_phone="0912345678", dupe_phone=""):
        conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url, phone, first_seen) "
            "VALUES ('t_keep', 'Keeper', '111', ?, '2026-01-01 00:00:00')",
            (keeper_phone,),
        )
        conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url, phone, first_seen) "
            "VALUES ('t_dupe', 'Dupe', '111', ?, '2026-01-02 00:00:00')",
            (dupe_phone,),
        )
        conn.execute("INSERT INTO threads (id, thread_name) VALUES ('t_keep', 'Keeper')")
        conn.execute("INSERT INTO threads (id, thread_name) VALUES ('t_dupe', 'Dupe')")
        conn.execute(
            "INSERT INTO messages (thread_id, sender, content, seq) VALUES ('t_dupe', 'User', 'Hello', 0)"
        )
        conn.execute(
            "INSERT INTO messages (thread_id, sender, content, seq) VALUES ('t_keep', 'User', 'Hi', 0)"
        )
        conn.execute(
            "INSERT INTO user_ad_ids (thread_id, ad_id) VALUES ('t_dupe', 'ad_1')"
        )
        conn.commit()

    def test_dry_run_no_changes(self):
        conn = _create_test_db()
        self._seed_pair(conn)
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=True)
        # Both users should still exist
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 2)

    def test_execute_removes_dupe(self):
        conn = _create_test_db()
        self._seed_pair(conn)
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=False)
        remaining = conn.execute("SELECT * FROM users").fetchall()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0][1], "t_keep")  # thread_id

    def test_messages_repointed(self):
        conn = _create_test_db()
        self._seed_pair(conn)
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=False)
        # All messages should now belong to keeper
        msgs = conn.execute("SELECT thread_id FROM messages").fetchall()
        for m in msgs:
            self.assertEqual(m[0], "t_keep")

    def test_user_ad_ids_repointed(self):
        conn = _create_test_db()
        self._seed_pair(conn)
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=False)
        ads = conn.execute("SELECT thread_id FROM user_ad_ids").fetchall()
        for a in ads:
            self.assertEqual(a[0], "t_keep")

    def test_dupe_thread_deleted(self):
        conn = _create_test_db()
        self._seed_pair(conn)
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=False)
        threads = conn.execute("SELECT id FROM threads").fetchall()
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0][0], "t_keep")

    def test_backfill_phone_from_dupe(self):
        """When dupe has phone but original doesn't, dupe wins by score and keeps as keeper."""
        conn = _create_test_db()
        self._seed_pair(conn, keeper_phone="", dupe_phone="0999888777")
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=False)
        # t_dupe has more data (phone), so it becomes the keeper
        remaining = conn.execute("SELECT thread_id, phone FROM users").fetchone()
        self.assertEqual(remaining["thread_id"], "t_dupe")
        self.assertEqual(remaining["phone"], "0999888777")

    def test_keeper_phone_not_overwritten(self):
        """When both have phone (equal score), earlier first_seen wins."""
        conn = _create_test_db()
        self._seed_pair(conn, keeper_phone="0912345678", dupe_phone="0999888777")
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='111' ORDER BY first_seen").fetchall()]
        merge_group(conn, group, dry_run=False)
        # Both have equal score (1 each), but t_keep has earlier first_seen
        remaining = conn.execute("SELECT thread_id, phone FROM users").fetchone()
        self.assertIsNotNone(remaining)
        # Keeper's phone should be preserved (not overwritten by dupe's)
        self.assertEqual(remaining["phone"], "0912345678")

    def test_record_with_more_data_kept(self):
        """If dupe has more data, it becomes the keeper."""
        conn = _create_test_db()
        conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url, phone, email, city, first_seen) "
            "VALUES ('t_sparse', 'Sparse', '222', '', '', 'Unknown', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url, phone, email, city, first_seen) "
            "VALUES ('t_rich', 'Rich', '222', '0123', 'a@b.com', 'Hanoi', '2026-01-02')"
        )
        conn.execute("INSERT INTO threads (id, thread_name) VALUES ('t_sparse', 'Sparse')")
        conn.execute("INSERT INTO threads (id, thread_name) VALUES ('t_rich', 'Rich')")
        conn.commit()
        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='222' ORDER BY first_seen").fetchall()]
        summary = merge_group(conn, group, dry_run=False)
        self.assertEqual(summary["keeper_name"], "Rich")
        remaining = conn.execute("SELECT thread_name FROM users").fetchone()
        self.assertEqual(remaining[0], "Rich")

    def test_equal_score_and_timestamp_uses_thread_id_tiebreak(self):
        """Equal-score rows with identical first_seen pick a stable keeper by thread_id."""
        conn = _create_test_db()
        conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url, phone, first_seen) "
            "VALUES ('t_b', 'Later', '333', '0911111111', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url, phone, first_seen) "
            "VALUES ('t_a', 'EarlierById', '333', '0922222222', '2026-01-01 00:00:00')"
        )
        conn.execute("INSERT INTO threads (id, thread_name) VALUES ('t_b', 'Later')")
        conn.execute("INSERT INTO threads (id, thread_name) VALUES ('t_a', 'EarlierById')")
        conn.commit()

        group = [dict(r) for r in conn.execute("SELECT * FROM users WHERE fb_url='333' ORDER BY thread_id DESC").fetchall()]
        summary = merge_group(conn, group, dry_run=False)

        self.assertEqual(summary["keeper_name"], "EarlierById")
        remaining = conn.execute("SELECT thread_id, phone FROM users").fetchone()
        self.assertEqual(remaining["thread_id"], "t_a")
        self.assertEqual(remaining["phone"], "0922222222")


class TestRunDedup(unittest.TestCase):
    def test_full_run(self):
        """End-to-end: create DB with dupes, run dedup, verify clean."""
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT UNIQUE, thread_name TEXT,
                phone TEXT, email TEXT, fb_url TEXT,
                city TEXT DEFAULT 'Unknown', lead_stage TEXT DEFAULT 'Intake',
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_interaction DATETIME DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("""CREATE TABLE threads (
                id TEXT PRIMARY KEY, page_id TEXT, thread_name TEXT,
                last_synced_time TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("""CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT, sender TEXT, content TEXT,
                message_timestamp TEXT, seq INTEGER DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(thread_id, sender, content, message_timestamp, seq))""")
            conn.execute("""CREATE TABLE user_ad_ids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT, ad_id TEXT,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(thread_id, ad_id))""")
            conn.execute("INSERT INTO users (thread_id, thread_name, fb_url, phone) VALUES ('a', 'Alice', '111', '09')")
            conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES ('b', 'Bob', '111')")
            conn.execute("INSERT INTO threads (id, thread_name) VALUES ('a', 'Alice')")
            conn.execute("INSERT INTO threads (id, thread_name) VALUES ('b', 'Bob')")
            conn.commit()
            conn.close()

            stats = run_dedup(db_path=path, dry_run=False)
            self.assertEqual(stats["duplicate_groups"], 1)
            self.assertEqual(stats["records_removed"], 1)

            conn = sqlite3.connect(path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            conn.close()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
