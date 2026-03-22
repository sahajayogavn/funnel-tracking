import ast
import os
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestWrapperBootstrapImports(unittest.TestCase):
    def _parse_imports(self, file_path):
        with open(file_path, 'r') as f:
            tree = ast.parse(f.read(), filename=file_path)

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        return imports

    def test_canonical_l5_wrappers_import_shared_pipeline(self):
        expected = {
            os.path.join(PROJECT_ROOT, 'tools', 'l5_fetch_fb_messages.py'): 'fb_pipeline',
            os.path.join(PROJECT_ROOT, 'tools', 'l5_fetch_comments.py'): 'fb_pipeline',
            os.path.join(PROJECT_ROOT, 'tools', 'l5_inbox_mas_runner.py'): 'fb_pipeline',
            os.path.join(PROJECT_ROOT, 'tools', 'l5_fb_browser_bootstrap.py'): 'fb_pipeline',
            os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'l5_facebook_tools.py'): 'fb_pipeline',
            os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'l5_seeker_tools.py'): 'fb_pipeline',
        }

        for file_path, prefix in expected.items():
            with self.subTest(file_path=file_path):
                imports = self._parse_imports(file_path)
                self.assertTrue(any(name.startswith(prefix) for name in imports), imports)

    def test_legacy_wrapper_shims_still_exist(self):
        expected = [
            os.path.join(PROJECT_ROOT, 'tools', 'fetch_fb_messages.py'),
            os.path.join(PROJECT_ROOT, 'tools', 'fetch_comments.py'),
            os.path.join(PROJECT_ROOT, 'tools', 'inbox_mas_runner.py'),
            os.path.join(PROJECT_ROOT, 'tools', 'fb_browser_bootstrap.py'),
            os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'facebook_tools.py'),
            os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'seeker_tools.py'),
        ]
        for file_path in expected:
            with self.subTest(file_path=file_path):
                self.assertTrue(os.path.exists(file_path), file_path)

    def test_canonical_fetch_comments_imports_bootstrap_from_l2_layer(self):
        file_path = os.path.join(PROJECT_ROOT, 'tools', 'l5_fetch_comments.py')
        imports = self._parse_imports(file_path)
        self.assertIn('fb_pipeline.session.l2_bootstrap', imports)
        self.assertNotIn('tools.l5_fb_browser_bootstrap', imports)

    def test_legacy_fetch_comments_is_a_canonical_wrapper_shim(self):
        file_path = os.path.join(PROJECT_ROOT, 'tools', 'fetch_comments.py')
        imports = self._parse_imports(file_path)
        self.assertIn('tools.l5_fetch_comments', imports)

    def test_legacy_bootstrap_wrapper_points_to_canonical_bootstrap_wrapper(self):
        file_path = os.path.join(PROJECT_ROOT, 'tools', 'fb_browser_bootstrap.py')
        imports = self._parse_imports(file_path)
        self.assertIn('tools.l5_fb_browser_bootstrap', imports)

    def test_wrapper_compat_tests_still_target_tool_shim(self):
        expected_test_files = [
            os.path.join(PROJECT_ROOT, 'tests', 'test_l2_fb_browser_bootstrap.py'),
            os.path.join(PROJECT_ROOT, 'tests', 'test_l5_fetch_fb_messages.py'),
            os.path.join(PROJECT_ROOT, 'tests', 'test_l5_fetch_comments.py'),
        ]
        for file_path in expected_test_files:
            with self.subTest(file_path=file_path):
                imports = self._parse_imports(file_path)
                self.assertIn('tools.fb_browser_bootstrap', imports)


if __name__ == '__main__':
    unittest.main()
