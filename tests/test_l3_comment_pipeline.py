import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.comments.l1_helpers import detect_city, extract_user_info
from fb_pipeline.comments.l3_pipeline import build_post_record, enrich_post_record, persist_post_record
from fb_pipeline.persistence.l4_sqlite_store import setup_comment_database


class TestCommentContracts(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.conn = sqlite3.connect(":memory:")
        setup_comment_database(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_build_post_record_parses_visible_post(self):
        post = build_post_record("page123", {
            "domIndex": 4,
            "name": "Post Title",
            "text": "Post Title\nPreview line\nToday",
        })
        self.assertEqual(post.page_id, "page123")
        self.assertEqual(post.post_name, "Post Title")
        self.assertEqual(post.preview_text, "Preview line Today")
        self.assertEqual(post.dom_index, 4)

    def test_enrich_post_record_builds_normalized_comments(self):
        post = build_post_record("page123", {
            "domIndex": 0,
            "name": "Post Title",
            "text": "Post Title\nPreview",
        })
        enriched = enrich_post_record(post, [
            {"commenter_name": "Nguyễn Văn A", "comment_text": "Cho mình đăng ký ở Hà Nội, SĐT 0912345678", "timestamp": "2d", "profile_url": "https://facebook.com/nguyenvana", "fb_user_id": "nguyenvana", "is_reply": False},
            {"commenter_name": "", "comment_text": "   ", "timestamp": "1d", "profile_url": "", "fb_user_id": "", "is_reply": False},
        ], extract_user_info, detect_city, post_url="post-456")
        self.assertEqual(enriched.post_url, "post-456")
        self.assertEqual(enriched.city, "Hà Nội")
        self.assertEqual(enriched.user_info["phone"], "0912345678")
        self.assertEqual(len(enriched.comments), 1)
        self.assertEqual(enriched.comments[0].comment_timestamp, "2d")
        self.assertEqual(enriched.comments[0].is_reply, 0)

    def test_persist_post_record_writes_all_boundaries(self):
        post = enrich_post_record(
            build_post_record("page123", {"domIndex": 0, "name": "Post Title", "text": "Post Title\nPreview"}),
            [
                {"commenter_name": "Nguyễn Văn A", "comment_text": "Mình ở Đà Nẵng, email a@test.com", "timestamp": "Today", "profile_url": "https://facebook.com/nguyenvana", "fb_user_id": "nguyenvana", "is_reply": False},
                {"commenter_name": "Page", "comment_text": "Chào bạn", "timestamp": "Today", "profile_url": "", "fb_user_id": "", "is_reply": False},
            ],
            extract_user_info,
            detect_city,
            post_url="post-789",
        )
        result = persist_post_record(self.conn, post)
        self.assertEqual(result["comments_added"], 2)
        self.assertTrue(result["is_new_post"])

        post_row = self.conn.execute("SELECT page_id, post_name, post_url FROM posts WHERE id = ?", (post.post_id,)).fetchone()
        self.assertEqual(post_row[0], "page123")
        self.assertEqual(post_row[1], "Post Title")
        self.assertEqual(post_row[2], "post-789")

        comment_count = self.conn.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post.post_id,)).fetchone()[0]
        self.assertEqual(comment_count, 2)

        user_row = self.conn.execute("SELECT commenter_name, email, city, last_synced_at FROM comment_users WHERE post_id = ?", (post.post_id,)).fetchone()
        self.assertEqual(user_row[0], "Nguyễn Văn A")
        self.assertEqual(user_row[1], "a@test.com")
        self.assertEqual(user_row[2], "Đà Nẵng")
        self.assertIsNotNone(user_row[3])

    def test_persist_post_record_only_refreshes_last_synced_at_without_new_comment(self):
        post = enrich_post_record(
            build_post_record("page123", {"domIndex": 0, "name": "Post Title", "text": "Post Title\nPreview"}),
            [
                {"commenter_name": "Nguyễn Văn A", "comment_text": "Mình ở Đà Nẵng, email a@test.com", "timestamp": "Today", "profile_url": "https://facebook.com/nguyenvana", "fb_user_id": "nguyenvana", "is_reply": False},
            ],
            extract_user_info,
            detect_city,
            post_url="post-789",
        )
        persist_post_record(self.conn, post)

        stale_interaction = "2000-01-01 00:00:00"
        stale_synced = "2000-01-01 00:00:00"
        self.conn.execute(
            "UPDATE comment_users SET last_interaction = ?, last_synced_at = ? WHERE post_id = ? AND commenter_name = ?",
            (stale_interaction, stale_synced, post.post_id, "Nguyễn Văn A"),
        )
        self.conn.commit()

        resynced_post = enrich_post_record(
            build_post_record("page123", {"domIndex": 0, "name": "Post Title", "text": "Post Title\nPreview"}),
            [
                {"commenter_name": "Nguyễn Văn A", "comment_text": "Mình ở Đà Nẵng, email a@test.com", "timestamp": "Today", "profile_url": "https://facebook.com/nguyenvana", "fb_user_id": "nguyenvana", "is_reply": False},
            ],
            extract_user_info,
            detect_city,
            post_url="post-789",
        )
        persist_post_record(self.conn, resynced_post)

        user_row = self.conn.execute(
            "SELECT last_interaction, last_synced_at FROM comment_users WHERE post_id = ? AND commenter_name = ?",
            (post.post_id, "Nguyễn Văn A"),
        ).fetchone()
        self.assertEqual(user_row[0], stale_interaction)
        self.assertNotEqual(user_row[1], stale_synced)


if __name__ == '__main__':
    unittest.main()
