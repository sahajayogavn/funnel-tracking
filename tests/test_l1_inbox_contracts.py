import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info, parse_ad_ids, parse_page_id, detect_city_smart


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
    """Tests for phone/email extraction from messages.
    # Gate 2: code:test-validation-001:l3-to-l1
    """

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


class TestDetectCitySmart(unittest.TestCase):
    """Tests for the detect_city_smart integration layer.
    code:test-validation-001:l1-city-smart
    """

    from unittest.mock import patch

    @patch("fb_pipeline.contracts.l1_city_llm.detect_city_llm")
    @patch.dict("os.environ", {"OPENAI_API_BASE": "mock", "OPENAI_API_KEY": "mock"})
    def test_detect_smart_returns_llm_result(self, mock_detect_llm):
        """When LLM returns a valid city, detect_city_smart should return it without fallback."""
        mock_detect_llm.return_value = {
            "city": "Đà Nẵng",
            "confidence": "high",
            "reasoning": "mock reasoning"
        }
        
        result = detect_city_smart(
            ad_context="Lớp thiền Hà Nội",
            page_messages=[{"sender": "Page", "content": "Địa chỉ Hà Nội"}],
            thread_name="Test Seeker",
            customer_messages=[{"sender": "Customer", "content": "Mình đang ở Đà Nẵng, có lớp không?"}]
        )
        
        self.assertEqual(result, "Đà Nẵng")
        mock_detect_llm.assert_called_once()

    @patch("fb_pipeline.contracts.l1_city_llm.detect_city_llm")
    @patch.dict("os.environ", {"OPENAI_API_BASE": "mock", "OPENAI_API_KEY": "mock"})
    def test_detect_smart_falls_back_on_unknown(self, mock_detect_llm):
        """When LLM returns 'Unknown', the system should fallback to keyword-based detect_city."""
        mock_detect_llm.return_value = {
            "city": "Unknown",
            "confidence": "low",
            "reasoning": "Could not determine city"
        }
        
        # 'Thủ Đức' is a keyword for 'TP. Hồ Chí Minh'
        result = detect_city_smart(
            ad_context="Lớp thì ở Quận Thủ Đức",
            page_messages=[],
            thread_name="Test User",
            customer_messages=[]
        )
        
        # The LLM failed, so fallback will read "Thủ Đức" and map it to "TP. Hồ Chí Minh"
        self.assertEqual(result, "TP. Hồ Chí Minh")
        mock_detect_llm.assert_called_once()

    @patch("fb_pipeline.contracts.l1_city_llm.detect_city_llm")
    @patch.dict("os.environ", {"OPENAI_API_BASE": "mock", "OPENAI_API_KEY": "mock"})
    def test_detect_smart_falls_back_on_timeout(self, mock_detect_llm):
        """When LLM throws an exception (e.g., timeout), the system should safely catch it and fallback."""
        mock_detect_llm.side_effect = Exception("API Timeout")
        
        # 'Hà Nội' is present in ad context
        result = detect_city_smart(
            ad_context="Khóa học thiền miễn phí tại Hà Nội",
            page_messages=[],
            thread_name="Test User",
            customer_messages=[]
        )
        
        # Expected fallback logic
        self.assertEqual(result, "Hà Nội")
        mock_detect_llm.assert_called_once()


if __name__ == '__main__':
    unittest.main()
