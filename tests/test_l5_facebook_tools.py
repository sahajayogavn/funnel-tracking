import ast
import importlib.util
import os
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LEGACY_MODULE_PATH = os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'facebook_tools.py')
CANONICAL_MODULE_PATH = os.path.join(PROJECT_ROOT, 'adk_agents', 'tools', 'l5_facebook_tools.py')


def _parse_imports(file_path):
    with open(file_path, 'r') as f:
        tree = ast.parse(f.read(), filename=file_path)

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _load_module(module_path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFacebookToolWrappers(unittest.TestCase):
    def _exercise_navigate(self, module):
        page = MagicMock()
        page.url = 'https://business.facebook.com/latest/inbox/all?asset_id=123'
        thread_el = MagicMock()
        page.locator.return_value.filter.return_value.first = thread_el

        result = module.navigate_to_thread(page, '123', 'Thread Name')

        self.assertTrue(result)
        page.wait_for_selector.assert_called_once_with('div._5_n1', timeout=10000)
        page.locator.assert_called_once_with('div._5_n1')
        thread_el.click.assert_called_once_with(force=True, timeout=5000)

    def test_legacy_shim_imports_canonical_wrapper(self):
        imports = _parse_imports(LEGACY_MODULE_PATH)
        self.assertIn('adk_agents.tools.l5_facebook_tools', imports)

    def test_legacy_exact_path_works(self):
        module = _load_module(LEGACY_MODULE_PATH, 'facebook_tools_legacy_under_test')
        self._exercise_navigate(module)

    def test_canonical_exact_path_works(self):
        module = _load_module(CANONICAL_MODULE_PATH, 'facebook_tools_canonical_under_test')
        self._exercise_navigate(module)


if __name__ == '__main__':
    unittest.main()
