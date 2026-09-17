"""Stage 1 must recognise threads persisted by an earlier crawl.

# code:test-validation-001:stage1-identity
Retrospective [2026-09-17]: the Stage 1 DB lookup used a provisional id hashed
from the sidebar card, which never equals the PSID-derived id Stage 2 persists,
so ``skipped_threads`` was always 0 and every run re-crawled everything.
"""
import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fb_pipeline.browser.l3_inbox import discover_threads
from fb_pipeline.inbox.l3_pipeline import canonical_thread_id
from fb_pipeline.persistence.l4_sqlite_store import setup_database

PAGE = "1548373332058326"
PSID = "100056586964984"


class _Page:
    url = f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE}"

    def evaluate(self, *_a, **_k):
        return {"found": False}

    def wait_for_timeout(self, _ms):
        pass


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, m): self.lines.append(("info", m))
    def warning(self, m): self.lines.append(("warning", m))
    def error(self, m): self.lines.append(("error", m))
    def debug(self, m): self.lines.append(("debug", m))


def _card(name, preview, time_text="2h"):
    # A real sidebar card: no selected_item_id (href="#"), only a volatile identity key.
    return {
        "name": name, "text": f"{name}\n{preview}\n{time_text}", "previewText": preview,
        "sidebarTimeText": time_text, "sidebarIdentityKey": f"{name} || {preview} || {time_text}",
        "selectedItemId": "", "fbUrl": "", "domIndex": 0, "absoluteTop": 0,
    }


class TestStage1CanonicalIdentity(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        setup_database(self.conn)
        self.tid = canonical_thread_id(PAGE, PSID)
        self.conn.execute(
            "INSERT INTO threads (id, page_id, thread_name, last_synced_time) VALUES (?, ?, ?, datetime('now'))",
            (self.tid, PAGE, "Nguyễn Ngọc Giàu"),
        )
        self.conn.execute(
            "INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)",
            (self.tid, "Nguyễn Ngọc Giàu", PSID),
        )
        self.conn.execute(
            "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, 'Customer', 'Em muốn đăng ký lớp thiền', '', 0)",
            (self.tid,),
        )
        self.conn.commit()

    def _discover(self, cards, force_refresh=False):
        tasks = []
        with patch("fb_pipeline.browser.l3_inbox.wait_for_inbox_shell", return_value=""), \
             patch("fb_pipeline.browser.l3_inbox.wait_for_initial_threads",
                   return_value={"count": len(cards), "elapsed_ms": 1, "fingerprint": "fp"}), \
             patch("fb_pipeline.browser.l3_inbox.reset_sidebar_to_top", return_value={"found": False}), \
             patch("fb_pipeline.browser.l3_inbox.extract_visible_threads", side_effect=[cards, cards, []]), \
             patch("fb_pipeline.browser.l3_inbox.validate_quick_fetch_cache", return_value=False), \
             patch("fb_pipeline.browser.l3_inbox.scroll_sidebar_and_wait", return_value={"elapsed_ms": 0}):
            out = discover_threads(
                _Page(), PAGE, "7d", 50, self.conn, _Logger(), lambda *a, **k: None,
                skip_navigation=True, force_refresh=force_refresh, allow_early_exit=False,
                target_total_messages=None, on_task=tasks.append,
            )
        return out["stats"], tasks

    def test_known_thread_with_unchanged_preview_is_skipped(self):
        stats, tasks = self._discover([_card("Nguyễn Ngọc Giàu", "Em muốn đăng ký lớp thiền")])
        self.assertEqual(stats["threads_psid_resolved"], 1)
        self.assertEqual(stats["skipped_threads"], 1)
        self.assertEqual(stats["new_threads"], 0)
        self.assertEqual(tasks, [])

    def test_known_thread_with_new_preview_is_dispatched_with_canonical_id_and_psid(self):
        stats, tasks = self._discover([_card("Nguyễn Ngọc Giàu", "Cho em hỏi lịch học tuần sau")])
        self.assertEqual(stats["skipped_threads"], 0)
        self.assertEqual(stats["new_threads"], 0)  # existing row, not a new thread
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].record.thread_id, self.tid)
        self.assertEqual(tasks[0].psid_hint, PSID)
        self.assertEqual(tasks[0].record.selected_item_id, PSID)
        self.assertFalse(tasks[0].is_new)

    def test_preview_matches_a_recent_row_not_only_the_newest(self):
        # A late-extracted system banner sits at the end of the thread.
        self.conn.execute(
            "INSERT INTO messages (thread_id, sender, content, message_timestamp, seq) VALUES (?, 'Customer', 'Nguyễn Ngọc Giàu replied to an ad.', '', 1)",
            (self.tid,),
        )
        self.conn.commit()
        stats, tasks = self._discover([_card("Nguyễn Ngọc Giàu", "Em muốn đăng ký lớp thiền")])
        self.assertEqual(stats["skipped_threads"], 1)
        self.assertEqual(tasks, [])

    def test_refresh_still_dispatches_known_thread(self):
        stats, tasks = self._discover([_card("Nguyễn Ngọc Giàu", "Em muốn đăng ký lớp thiền")], force_refresh=True)
        self.assertEqual(stats["skipped_threads"], 1)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].psid_hint, PSID)

    def test_unknown_name_keeps_provisional_id_and_no_hint(self):
        stats, tasks = self._discover([_card("Người Lạ", "xin chào")])
        self.assertEqual(stats["threads_psid_resolved"], 0)
        self.assertEqual(stats["new_threads"], 1)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].psid_hint, "")
        self.assertNotEqual(tasks[0].record.thread_id, self.tid)

    def test_ambiguous_name_gets_no_hint(self):
        other = canonical_thread_id(PAGE, "999")
        self.conn.execute("INSERT INTO threads (id, page_id, thread_name) VALUES (?, ?, ?)", (other, PAGE, "Nguyễn Ngọc Giàu"))
        self.conn.execute("INSERT INTO users (thread_id, thread_name, fb_url) VALUES (?, ?, ?)", (other, "Nguyễn Ngọc Giàu", "999"))
        self.conn.commit()
        stats, tasks = self._discover([_card("Nguyễn Ngọc Giàu", "Em muốn đăng ký lớp thiền")])
        self.assertEqual(stats["threads_psid_resolved"], 0)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].psid_hint, "")


if __name__ == "__main__":
    unittest.main()
