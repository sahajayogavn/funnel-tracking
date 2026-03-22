import ast
import os
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestImportBoundaries(unittest.TestCase):
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

    def test_fb_pipeline_does_not_import_tools_or_agents(self):
        fb_pipeline_root = os.path.join(PROJECT_ROOT, 'fb_pipeline')
        for root, _, files in os.walk(fb_pipeline_root):
            for name in files:
                if not name.endswith('.py'):
                    continue
                file_path = os.path.join(root, name)
                with self.subTest(file_path=file_path):
                    imports = self._parse_imports(file_path)
                    self.assertFalse(any(name.startswith('tools') for name in imports), imports)
                    self.assertFalse(any(name.startswith('adk_agents') for name in imports), imports)


if __name__ == '__main__':
    unittest.main()
