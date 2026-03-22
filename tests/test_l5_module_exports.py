import importlib
import os
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

CANONICAL_FILES = [
    'fb_pipeline/contracts/l1_session.py',
    'fb_pipeline/contracts/l1_inbox.py',
    'fb_pipeline/contracts/l1_comments.py',
    'fb_pipeline/comments/l1_helpers.py',
    'fb_pipeline/session/l2_bootstrap.py',
    'fb_pipeline/browser/l2_actions.py',
    'fb_pipeline/inbox/l3_pipeline.py',
    'fb_pipeline/comments/l3_pipeline.py',
    'fb_pipeline/browser/l3_inbox.py',
    'fb_pipeline/browser/l3_comments.py',
    'fb_pipeline/persistence/l4_sqlite_store.py',
    'tools/l5_fetch_fb_messages.py',
    'tools/l5_fetch_comments.py',
    'tools/l5_inbox_mas_runner.py',
    'tools/l5_fb_browser_bootstrap.py',
    'adk_agents/tools/l5_facebook_tools.py',
    'adk_agents/tools/l5_seeker_tools.py',
]

MODULE_EXPORTS = {
    'fb_pipeline.contracts.l1_session': ['AuthorizedSession', 'CDPConnectionError'],
    'fb_pipeline.contracts.session': ['AuthorizedSession', 'CDPConnectionError'],
    'fb_pipeline.contracts.l1_inbox': ['ThreadRecord', 'parse_page_id'],
    'fb_pipeline.contracts.inbox': ['ThreadRecord', 'parse_page_id'],
    'fb_pipeline.contracts.l1_comments': ['PostRecord', 'CommentRecord'],
    'fb_pipeline.contracts.comments': ['PostRecord', 'CommentRecord'],
    'fb_pipeline.comments.l1_helpers': ['parse_page_id', 'parse_post_id', 'extract_user_info', 'detect_city'],
    'fb_pipeline.comments.helpers': ['parse_page_id', 'parse_post_id', 'extract_user_info', 'detect_city'],
    'fb_pipeline.session.l2_bootstrap': ['attach_to_authorized_session', 'sanitize_storage_state_file'],
    'fb_pipeline.session.bootstrap': ['attach_to_authorized_session', 'sanitize_storage_state_file'],
    'fb_pipeline.browser.l2_actions': ['navigate_to_thread', 'send_reply_via_cdp'],
    'fb_pipeline.browser.actions': ['navigate_to_thread', 'send_reply_via_cdp'],
    'fb_pipeline.inbox.l3_pipeline': ['build_thread_record', 'enrich_thread_record', 'persist_thread_record', 'scrape_inbox'],
    'fb_pipeline.inbox.pipeline': ['build_thread_record', 'enrich_thread_record', 'persist_thread_record', 'scrape_inbox'],
    'fb_pipeline.comments.l3_pipeline': ['build_post_record', 'enrich_post_record', 'persist_post_record'],
    'fb_pipeline.comments.pipeline': ['build_post_record', 'enrich_post_record', 'persist_post_record'],
    'fb_pipeline.browser.l3_inbox': ['scrape_inbox', 'scrape_inbox_ui', 'extract_ad_id_labels'],
    'fb_pipeline.browser.inbox': ['scrape_inbox', 'scrape_inbox_ui', 'extract_ad_id_labels'],
    'fb_pipeline.browser.l3_comments': ['scrape_comments', 'scrape_comments_ui'],
    'fb_pipeline.browser.comments': ['scrape_comments', 'scrape_comments_ui'],
    'fb_pipeline.persistence.l4_sqlite_store': ['get_db_connection', 'get_comment_db_connection', 'should_fetch', 'should_fetch_comments'],
    'fb_pipeline.persistence.sqlite_store': ['get_db_connection', 'get_comment_db_connection', 'should_fetch', 'should_fetch_comments'],
    'tools.l5_fetch_fb_messages': ['main', 'fetch_messages'],
    'tools.fetch_fb_messages': ['main', 'fetch_messages'],
    'tools.l5_fetch_comments': ['main', 'fetch_comments'],
    'tools.fetch_comments': ['main', 'fetch_comments'],
    'tools.l5_inbox_mas_runner': ['main', 'run_inbox_cycle'],
    'tools.inbox_mas_runner': ['main', 'run_inbox_cycle'],
    'tools.l5_fb_browser_bootstrap': ['AuthorizedSession', 'attach_to_authorized_session'],
    'tools.fb_browser_bootstrap': ['AuthorizedSession', 'attach_to_authorized_session'],
    'adk_agents.tools.l5_facebook_tools': ['navigate_to_thread', 'send_reply_via_cdp', 'log_auto_reply'],
    'adk_agents.tools.facebook_tools': ['navigate_to_thread', 'send_reply_via_cdp', 'log_auto_reply'],
    'adk_agents.tools.l5_seeker_tools': ['lookup_seeker', 'get_thread_messages', 'find_unreplied_threads'],
    'adk_agents.tools.seeker_tools': ['lookup_seeker', 'get_thread_messages', 'find_unreplied_threads'],
}


class TestLayerPrefixedModules(unittest.TestCase):
    def test_canonical_prefixed_files_exist(self):
        for relative_path in CANONICAL_FILES:
            with self.subTest(path=relative_path):
                self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, relative_path)), relative_path)

    def test_canonical_and_legacy_modules_import_and_export_symbols(self):
        for module_name, expected_symbols in MODULE_EXPORTS.items():
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                for symbol in expected_symbols:
                    self.assertTrue(hasattr(module, symbol), f'{module_name} missing {symbol}')

    def test_legacy_and_canonical_bootstrap_share_session_type(self):
        legacy = importlib.import_module('tools.fb_browser_bootstrap')
        canonical = importlib.import_module('tools.l5_fb_browser_bootstrap')
        self.assertIs(legacy.AuthorizedSession, canonical.AuthorizedSession)


if __name__ == '__main__':
    unittest.main()
