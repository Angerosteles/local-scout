import ast
import inspect
import re
import tempfile
import unittest
from pathlib import Path

from plugin import MASKING_PROMPT, register
from plugin.scout import credentials, name_model, pii_model, privacy
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout


def literal_tuple_items(function, node_type, index):
    tree = ast.parse(inspect.getsource(function))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, node_type) and isinstance(node.value, ast.Tuple):
            item = node.value.elts[index]
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                result.add(item.value)
    return result


class MaskingGuidanceTests(unittest.TestCase):
    def test_tags_follow_detectors(self):
        categories = set(privacy.PATTERNS) | set(pii_model.KINDS)
        categories |= literal_tuple_items(credentials._code, ast.Return, 0)
        categories |= literal_tuple_items(credentials.candidates, ast.Yield, -1)
        # Extract labels from name-span tuples written to the result list.
        tree = ast.parse(inspect.getsource(name_model.spans))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'result'
                    and node.func.attr == 'append' and node.args and isinstance(node.args[0], ast.Tuple)):
                item = node.args[0].elts[-1]
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    categories.add(item.value)
        self.assertEqual({c.upper() for c in categories}, set(re.findall(r'\[([A-Z]+)\]', MASKING_PROMPT)))

    def test_registration_and_profile_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = LocalContext(tmp)
            sections = {}
            ctx.register_system_prompt_section = lambda name, content, **kwargs: sections.update({name: (content, kwargs)})
            register(ctx)
            self.assertTrue(ctx.skills['masking']['path'].is_file())
            self.assertIn('decide', ctx.skills)
            render, options = sections['local-scout.masking']
            self.assertEqual(options, {'position': 'after_memory', 'max_chars': 900})
            self.assertLessEqual(len(MASKING_PROMPT), options['max_chars'])
            self.assertEqual(render({}), MASKING_PROMPT)
            self.assertFalse((Path(tmp) / 'plugin-data').exists())
            scout = Scout(ctx)
            scout.configure_local('report', grants={'mask': ['profile']})
            self.assertEqual(render({}), MASKING_PROMPT)
            scout.configure_local('report', grants={})
            self.assertEqual(render({}), '')
            scout.configure_local('off', grants={'mask': ['profile']})
            self.assertEqual(render({}), '')

    def test_package_data_contains_skill(self):
        import tomllib
        project = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())
        self.assertIn('skills/masking/SKILL.md', project['tool']['setuptools']['package-data']['hermes_local_scout'])
