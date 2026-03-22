import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MODULE_PATH = os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'facebook_tools.py')

stub_tools = types.ModuleType('tools.fetch_fb_messages')
stub_tools.get_db_connection = MagicMock()
sys.modules.setdefault('tools.fetch_fb_messages', stub_tools)

spec = importlib.util.spec_from_file_location('facebook_tools_under_test', MODULE_PATH)
facebook_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facebook_tools)
navigate_to_thread = facebook_tools.navigate_to_thread


class TestNavigateToThread(unittest.TestCase):
    def test_navigate_uses_current_thread_selector(self):
        page = MagicMock()
        page.url = 'https://business.facebook.com/latest/inbox/all?asset_id=123'
        thread_el = MagicMock()
        page.locator.return_value.filter.return_value.first = thread_el

        result = navigate_to_thread(page, '123', 'Thread Name')

        self.assertTrue(result)
        page.wait_for_selector.assert_called_once_with('div._5_n1', timeout=10000)
        page.locator.assert_called_once_with('div._5_n1')
        thread_el.click.assert_called_once_with(force=True, timeout=5000)


if __name__ == '__main__':
    unittest.main()
