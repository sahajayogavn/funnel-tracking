import unittest
import logging
from unittest.mock import MagicMock

# Assuming the path allows importing from fb_pipeline
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.browser.inbox.thread_detail_parser import verify_thread_switch

class MockThreadRecord:
    def __init__(self, fb_url=None, selected_item_id=None):
        if fb_url is not None:
            self.fb_url = fb_url
        if selected_item_id is not None:
            self.selected_item_id = selected_item_id

class MockPage:
    def __init__(self, url="https://business.facebook.com/latest/inbox/all"):
        self.url = url
        self.timeout_called = 0

    def evaluate(self, script):
        return "Inbox"  # Fake header for first thread

    def wait_for_timeout(self, ms):
        self.timeout_called += 1

class TestFirstThreadUrlRegression(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_logger")
        self.logger.setLevel(logging.DEBUG)

    def test_fallback_to_target_item_id_when_fb_url_missing(self):
        # Scenario: pre-parsed fb_url is missing, but target_item_id (from a[href]) exists
        thread_record = MockThreadRecord(fb_url="", selected_item_id="123456789")
        page = MockPage(url="https://business.facebook.com/latest/inbox/all")
        
        # When _poll >= 3 and candidate is empty, it returns safely
        fb_url, is_valid = verify_thread_switch(
            page=page,
            logger=self.logger,
            name="Viet Nguyen",
            prev_fb_url="previous_123",
            pre_click_fingerprint="old_fingerprint",
            is_first_thread=True,
            thread_record=thread_record
        )
        
        # Should retain the target_item_id as the fb_url
        self.assertEqual(fb_url, "123456789")
        self.assertTrue(is_valid)

    def test_fallback_to_candidate_url_missing_target_id(self):
        # Scenario: thread_record has absolutely no fb_url and no target_item_id, 
        # but the React Router finally updates the URL with selected_item_id
        thread_record = MockThreadRecord(fb_url="", selected_item_id="")
        page = MockPage(url="https://business.facebook.com/latest/inbox/all?selected_item_id=987654321")
        
        fb_url, is_valid = verify_thread_switch(
            page=page,
            logger=self.logger,
            name="Viet Nguyen",
            prev_fb_url="previous_123",
            pre_click_fingerprint="old_fingerprint",
            is_first_thread=True,
            thread_record=thread_record
        )
        
        # Should fallback to the candidate extracted from the URL query param
        self.assertEqual(fb_url, "987654321")
        self.assertTrue(is_valid)

    def test_fallback_candidate_when_url_does_not_change(self):
        # Scenario: candidate URL is exactly the same as prev_fb_url (Facebook UI anomaly)
        # and thread_record is completely empty. The system should NOT lose the fb_url.
        thread_record = MockThreadRecord(fb_url="", selected_item_id="")
        page = MockPage(url="https://business.facebook.com/latest/inbox/all?selected_item_id=previous_123")
        
        fb_url, is_valid = verify_thread_switch(
            page=page,
            logger=self.logger,
            name="Viet Nguyen",
            prev_fb_url="previous_123",
            pre_click_fingerprint="old_fingerprint",
            is_first_thread=True,
            thread_record=thread_record
        )
        
        self.assertEqual(fb_url, "previous_123")
        self.assertTrue(is_valid)

if __name__ == "__main__":
    unittest.main()
