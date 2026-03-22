import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info, parse_ad_ids, parse_page_id


class TestParsePageId(unittest.TestCase):
    def test_parse_page_id_raw(self):
        self.assertEqual(parse_page_id("12345"), "12345")

    def test_parse_page_id_url(self):
        url = "https://business.facebook.com/latest/inbox/all/?nav_ref=manage_page_ap_plus_inbox_message_button&asset_id=1548373332058326&business_id="
        self.assertEqual(parse_page_id(url), "1548373332058326")

    def test_parse_page_id_fallback(self):
        self.assertEqual(parse_page_id("not-a-url-or-number"), "not-a-url-or-number")


class TestParseAdIds(unittest.TestCase):
    """Tests for ad_id pattern extraction."""

    def test_single_ad_id(self):
        text = "∞ ad_id.6930299765389"
        self.assertEqual(parse_ad_ids(text), ["6930299765389"])

    def test_multiple_ad_ids(self):
        text = "Intake ad_id.6930299765389 ad_id.6892367141614 messenger_ads"
        result = parse_ad_ids(text)
        self.assertEqual(len(result), 2)
        self.assertIn("6930299765389", result)
        self.assertIn("6892367141614", result)

    def test_no_ad_ids(self):
        text = "Intake messenger_ads some other label"
        self.assertEqual(parse_ad_ids(text), [])

    def test_partial_match_ignored(self):
        text = "ad_id.123"  # too short (< 5 digits)
        self.assertEqual(parse_ad_ids(text), [])

    def test_ad_id_without_dot(self):
        text = "ad_id6930299765389"
        self.assertEqual(parse_ad_ids(text), ["6930299765389"])

    def test_ad_id_from_log_line(self):
        text = "Intake \u200b ad_id.6892367141614"
        self.assertEqual(parse_ad_ids(text), ["6892367141614"])


class TestExtractUserInfo(unittest.TestCase):
    """Tests for phone/email extraction from messages."""

    def test_extract_phone_from_customer(self):
        messages = [
            {"sender": "Page", "content": "Bạn vui lòng gửi SĐT"},
            {"sender": "Customer", "content": "0935539464"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertEqual(info["phone"], "0935539464")

    def test_extract_email_from_customer(self):
        messages = [
            {"sender": "Customer", "content": "ni8745@gmail.com"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertEqual(info["email"], "ni8745@gmail.com")

    def test_extract_both(self):
        messages = [
            {"sender": "Customer", "content": "Trần thị hoài thương"},
            {"sender": "Customer", "content": "ni8745@gmail.com"},
            {"sender": "Customer", "content": "0935539464"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertEqual(info["phone"], "0935539464")
        self.assertEqual(info["email"], "ni8745@gmail.com")

    def test_no_info_found(self):
        messages = [
            {"sender": "Customer", "content": "Xin chào"},
            {"sender": "Page", "content": "Chào bạn!"},
        ]
        info = extract_user_info(messages, "Test User")
        self.assertIsNone(info["phone"])
        self.assertIsNone(info["email"])


class TestDetectCity(unittest.TestCase):
    """Tests for city detection from ad content."""

    def test_detect_hanoi(self):
        ad = "Lớp thiền miễn phí tại số 40 Vương Thừa Vũ, Hà Nội"
        self.assertEqual(detect_city(ad, []), "Hà Nội")

    def test_detect_hcm(self):
        ad = "Thiền Sahaja Yoga tại TP.HCM"
        self.assertEqual(detect_city(ad, []), "TP. Hồ Chí Minh")

    def test_detect_danang(self):
        ad = "Lớp thiền tại Đà Nẵng"
        self.assertEqual(detect_city(ad, []), "Đà Nẵng")

    def test_detect_nghean(self):
        ad = "Lớp tại Nghệ An"
        self.assertEqual(detect_city(ad, []), "Nghệ An")

    def test_detect_haiphong(self):
        ad = "Thiền tại Hải Phòng"
        self.assertEqual(detect_city(ad, []), "Hải Phòng")

    def test_detect_from_page_message(self):
        msgs = [{"sender": "Page", "content": "Địa chỉ: 02 Xô Viết Nghệ Tĩnh, Bình Thạnh"}]
        self.assertEqual(detect_city("", msgs), "TP. Hồ Chí Minh")

    def test_detect_unknown(self):
        self.assertEqual(detect_city("Lớp thiền", []), "Unknown")


if __name__ == '__main__':
    unittest.main()
