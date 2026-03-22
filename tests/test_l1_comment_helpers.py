import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.comments.l1_helpers import detect_city, extract_user_info, parse_page_id, parse_post_id


class TestParsePageId(unittest.TestCase):
    def test_parse_page_id_raw(self):
        self.assertEqual(parse_page_id("12345"), "12345")

    def test_parse_page_id_url(self):
        url = "https://business.facebook.com/latest/inbox/facebook?asset_id=1548373332058326&mailbox_id=&selected_item_id=4490615344500762&thread_type=FB_PAGE_POST"
        self.assertEqual(parse_page_id(url), "1548373332058326")

    def test_parse_page_id_fallback(self):
        self.assertEqual(parse_page_id("not-a-url-or-number"), "not-a-url-or-number")


class TestParsePostId(unittest.TestCase):
    def test_parse_post_id_from_url(self):
        url = "https://business.facebook.com/latest/inbox/facebook?asset_id=1548373332058326&selected_item_id=4490615344500762&thread_type=FB_PAGE_POST"
        self.assertEqual(parse_post_id(url), "4490615344500762")

    def test_parse_post_id_raw(self):
        self.assertEqual(parse_post_id("4490615344500762"), "4490615344500762")


class TestExtractUserInfoComments(unittest.TestCase):
    """Tests for phone/email extraction from comments."""

    def test_extract_phone(self):
        comments = [{"comment_text": "Tôi muốn tham gia, SĐT 0935539464"}]
        info = extract_user_info(comments)
        self.assertEqual(info["phone"], "0935539464")

    def test_extract_email(self):
        comments = [{"comment_text": "Email tôi là ni8745@gmail.com"}]
        info = extract_user_info(comments)
        self.assertEqual(info["email"], "ni8745@gmail.com")

    def test_extract_both(self):
        comments = [
            {"comment_text": "Liên hệ 0912345678"},
            {"comment_text": "hoặc email abc@test.com"},
        ]
        info = extract_user_info(comments)
        self.assertEqual(info["phone"], "0912345678")
        self.assertEqual(info["email"], "abc@test.com")

    def test_no_info_found(self):
        comments = [{"comment_text": "Xin chào"}, {"comment_text": "Tôi muốn tham gia"}]
        info = extract_user_info(comments)
        self.assertIsNone(info["phone"])
        self.assertIsNone(info["email"])


class TestDetectCityComments(unittest.TestCase):
    """Tests for city detection from comment text."""

    def test_detect_hanoi(self):
        self.assertEqual(detect_city("Lớp thiền tại Hà Nội"), "Hà Nội")

    def test_detect_hcm(self):
        self.assertEqual(detect_city("Tôi ở TP.HCM"), "TP. Hồ Chí Minh")

    def test_detect_danang(self):
        self.assertEqual(detect_city("Đà Nẵng có lớp không"), "Đà Nẵng")

    def test_detect_unknown(self):
        self.assertEqual(detect_city("Tôi muốn tham gia"), "Unknown")


if __name__ == '__main__':
    unittest.main()
