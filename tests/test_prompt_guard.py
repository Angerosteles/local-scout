"""Provider request middleware regression tests without a Hermes installation."""
import copy
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from plugin import register
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout import insights
from plugin.scout.prompt_guard import PromptGuard, WITHHELD
from plugin.scout.tool_guard import MAX_INPUT_CHARS

from plugin.tests.hermes_source import SKIP_REASON, hermes_file

REF = hermes_file('hermes_cli/middleware.py')

class ShallowOnly:
    def __deepcopy__(self, memo):
        raise TypeError('force Hermes shallow-copy fallback')

class PromptGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scout = Scout(LocalContext(self.tmp.name))
        self.scout.configure_local('report', grants={'mask': ['profile'], 'observe': ['profile']})
        self.guard = PromptGuard(self.scout)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, request):
        # Real Hermes chain with a manager stub that mimics sequential delivery and shallow copies.
        plugin_module = types.ModuleType('hermes_cli.plugins')
        plugin_module.has_middleware = lambda kind: True
        def invoke(kind, _payload_key, **kwargs):
            value = self.guard(**kwargs)
            return [value] if value is not None else []
        plugin_module.invoke_middleware = invoke
        if REF is None:
            self.skipTest(SKIP_REASON)
        package = types.ModuleType('hermes_cli')
        spec = importlib.util.spec_from_file_location('hermes_cli.middleware', REF)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'hermes_cli': package, 'hermes_cli.plugins': plugin_module, 'hermes_cli.middleware': module}):
            spec.loader.exec_module(module)
            return module.apply_llm_request_middleware(request)

    def test_chat_history_parts_and_immutable(self):
        req = {'opaque': ShallowOnly(), 'messages': [{'role':'system','content':'system@example.com'}, {'role':'user','content':'first a@example.com'}, {'role':'assistant','content':'ok'}, {'role':'user','content':[{'type':'text','text':'second b@example.com'}]}]}
        before = {'opaque': req['opaque'], 'messages': copy.deepcopy(req['messages'])}
        result = self.apply(req)
        self.assertEqual(req, before)
        self.assertEqual(result.original_payload, before)
        # System prompts can carry user memory/profile data — PII there is masked too (no alert marker)
        self.assertEqual(result.payload['messages'][0], {'role': 'system', 'content': '[EMAIL]'})
        self.assertIn('[EMAIL]', result.payload['messages'][1]['content'])
        self.assertIn('[EMAIL]', result.payload['messages'][3]['content'][0]['text'])
        self.assertEqual(str(result.payload).count('source=user_prompt'), 2)
        self.assertFalse(self.apply(result.payload).changed)

    def test_responses_and_tool(self):
        req = {'input': [{'type':'message','role':'user','content':[{'type':'input_text','text':'a@example.com'}]}, {'type':'function_call_output','call_id':'x','output':'b@example.com'}]}
        out = self.apply(req).payload
        self.assertIn('[EMAIL]', str(out))
        self.assertNotIn('@example.com', str(out))
        self.assertFalse(self.apply(out).changed)

    def test_no_consent(self):
        self.scout.configure_local('report', grants={})
        self.assertFalse(self.apply({'messages':[{'role':'user','content':'a@example.com'}]}).changed)

    def test_error_unknown_size_and_open(self):
        req = {'messages':[{'role':'user','content':'a@example.com'}]}
        with patch('plugin.scout.privacy.mask_local_details', side_effect=ValueError):
            self.assertEqual(self.apply(req).payload['messages'][0]['content'], WITHHELD)
            with patch.dict(os.environ, {'LOCAL_SCOUT_GUARD_MODE':'open'}):
                self.assertFalse(self.apply(req).changed)
        for content in ([{'type':'image_url','image_url':'secret'}], 'a'*(MAX_INPUT_CHARS+1)):
            self.assertEqual(self.apply({'messages':[{'role':'user','content':content}]}).payload['messages'][0]['content'], WITHHELD)

    def test_registration_and_dedup_telemetry(self):
        ctx = LocalContext(self.tmp.name)
        ctx.middleware = {}
        ctx.register_middleware = lambda kind, callback: ctx.middleware.update({kind: callback})
        register(ctx)
        self.assertIn('llm_request', ctx.middleware)
        req = {'messages':[{'role':'user','content':'a@example.com'}]}
        self.apply(req); self.apply(req)
        events = self.scout.profile()['events']
        self.assertEqual(len([e for e in events if e.get('source') == 'user_prompt' and e.get('action') == 'masked']), 1)
        self.assertEqual(insights.summarize(self.scout.root())['pii_by_source']['user_prompt']['email'], 1)
        self.assertIn('masked in the main request', self.scout.notify_output(response_text='ok'))

if __name__ == '__main__':
    unittest.main()
