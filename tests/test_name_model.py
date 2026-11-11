"""Person-name masking: user prompts only by default; tool output and code untouched."""
import tempfile
import unittest
from pathlib import Path

from plugin.scout import name_model, privacy, tool_guard
from plugin.scout.core import Scout
from plugin.scout.context import LocalContext
from plugin.scout.prompt_guard import PromptGuard


class NameModelTests(unittest.TestCase):
    def test_chat_and_form_names_masked_when_requested(self):
        for text in ('Hi, I am John Carter, please help', 'Patient Name: Alec Stafford, DOB: 1942-08-10', 'Thanks, Maria!'):
            masked, cats = privacy.mask_local_details(text, names=True)
            self.assertIn('[NAME]', masked)
            self.assertIn('name', cats)
        self.assertNotIn('Carter', privacy.mask_local_details('Hi, I am John Carter', names=True)[0])

    def test_default_masking_unchanged_and_ordinary_capitals_kept(self):
        self.assertEqual(privacy.mask_local_details('Hi, I am John Carter')[0], 'Hi, I am John Carter')
        for text in ('Please restart the Apache server on Monday', 'Can I speak to Support about the refund?',
                     'def handle(self): return Path(x)'):
            self.assertEqual(privacy.mask_local_details(text, names=True)[0], text)

    def test_robust_inputs(self):
        self.assertEqual(name_model.spans(''), [])
        self.assertEqual(name_model.spans('all lowercase text'), [])
        self.assertEqual(name_model.spans(None), [])
        self.assertEqual(name_model.spans('A' * (name_model.MAX_TEXT + 1)), [])
        self.assertEqual(name_model.spans('Hello Maria Lopez'), name_model.spans('Hello Maria Lopez'))

    def test_meta_documents_measurement(self):
        meta = name_model.__dict__['_model']  # loaded lazily
        weights, bias, lowfreq, cues, mask, high, low = meta()
        self.assertEqual((high, low), (0.45, 0.02))


class NameMaskingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scout = Scout(LocalContext(Path(self.tmp.name)))
        self.scout.configure_local('report', grants={'mask': ['profile']})

    def tearDown(self):
        self.tmp.cleanup()

    def test_user_prompt_names_masked_tool_results_not(self):
        request = {'messages': [{'role': 'user', 'content': 'Hello, my name is Priya Raghunathan'},
                                {'role': 'tool', 'content': 'class Hermes: author Adam Smith'}]}
        out = PromptGuard(self.scout)(request=request, api_mode='chat_completions')['request']
        self.assertNotIn('Raghunathan', out['messages'][0]['content'])
        self.assertIn('[NAME]', out['messages'][0]['content'])
        self.assertIn('Adam Smith', out['messages'][1]['content'])
        self.assertEqual(request['messages'][0]['content'], 'Hello, my name is Priya Raghunathan')  # no mutation

    def test_auxiliary_user_names_masked(self):
        messages = [{'role': 'user', 'content': 'Title this: Dear Jane Roe, your refund is ready'}]
        PromptGuard(self.scout).auxiliary(request_messages=list(messages))
        self.assertNotIn('Jane Roe', messages[0]['content'])

    def test_tool_guard_keeps_names_by_default(self):
        body, cats, _ = tool_guard.transform('Signed by Adam Smith', 'read_file', 'e1')
        self.assertTrue(body is None or 'Adam Smith' in body)
        self.assertNotIn('name', cats)


if __name__ == '__main__':
    unittest.main()


class AuxiliaryTelemetryTests(unittest.TestCase):
    """auxiliary-channel masks are counted in Insights (metadata only)."""
    def test_aux_mask_recorded_with_observe(self):
        import tempfile as _t
        from plugin.scout import insights
        with _t.TemporaryDirectory() as tmp:
            scout = Scout(LocalContext(Path(tmp)))
            scout.configure_local('report', grants={'mask': ['profile'], 'observe': ['profile']})
            PromptGuard(scout).auxiliary(request_messages=[{'role': 'user', 'content': 'mail me at aux@example.invalid'}])
            summary = insights.summarize(scout.root())
            self.assertIn('auxiliary_call', summary['pii_by_source'])
            self.assertNotIn('aux@example.invalid', (scout.root() / 'aggregates.json').read_text())

