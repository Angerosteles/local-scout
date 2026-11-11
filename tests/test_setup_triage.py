import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin.cli import main
from plugin.scout import learning
from plugin.scout.setup_triage import prepare
from plugin.scout.prompt_guard import PromptGuard


def examples(with_split=True):
    result = []
    for label, word in [('billing', 'invoice'), ('technical', 'device')]:
        for i in range(60):
            row = dict(text=f'{word} help request topic {chr(97+i//26)}{chr(97+i%26)}', label=label)
            if with_split:
                row['split'] = 'train' if i < 45 else 'validation'
            result.append(row)
    return result


class SetupTriage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'history.jsonl'
        self.home = self.root / 'home'
        self.write(examples())

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main(['--home', str(self.home), 'setup-triage', '--history', str(self.path), *args])
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_happy_and_max_error(self):
        code, output, err = self.invoke('--yes', '--json', '--max-error', '0')
        self.assertEqual(code, 0, err)
        result = json.loads(output)
        self.assertEqual(result['threshold'], .3)
        default_meta, _ = learning.train(prepare(self.path)[0], 'tfidf_logreg')
        self.assertEqual(default_meta['threshold'], .5)
        self.assertEqual(result['threshold_selection']['source'], 'user_max_error')
        self.assertEqual(result['split_counts']['test'], 0)
        self.assertTrue(all(p['accepted'] <= 30 for p in result['validation_curve']))
        from plugin.scout.context import LocalContext
        from plugin.scout.core import Scout
        s = Scout(LocalContext(self.home))
        from plugin.scout.local_answers import LocalAnswers
        import time
        import numpy as np
        with patch('plugin.scout.learning.raw_scores', return_value=(np.array([0]), np.array([.4]))), patch('plugin.scout.learning.predict', wraps=learning.predict) as predict:
            handled = s.call('scout_handle', dict(task='triage', text='invoice help request'))
            self.assertTrue(handled['ok'], handled)
            self.assertEqual(handled['data']['status'], 'handled_locally')
            self.assertEqual(predict.call_args.args[0]['threshold'], .3)
            request = {'messages':[{'role':'user','content':'invoice help request'}]}
            with patch('plugin.scout.local_answers.MAX_COLD_SECONDS', 60):
                LocalAnswers(s)._answer(request, request, 'chat_completions', None, time.monotonic())
            self.assertEqual(predict.call_args.args[0]['threshold'], .3)

    def test_automatic_split(self):
        self.write(examples(False))
        code, output, err = self.invoke('--yes', '--json')
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(output)['automatic_split'])
        self.assertIn('deterministic', err)

    def test_consent_and_errors(self):
        with patch('sys.stdin.isatty', return_value=False):
            code, _, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn('Confirmation required', err)
        self.assertFalse(self.home.exists())
        self.write([{'text':'example'}])
        self.assertIn('label', self.invoke('--yes')[2])
        rows = examples(); rows[0]['label'] = 'rare'; self.write(rows)
        self.assertIn('two examples per class', self.invoke('--yes')[2])
        rows = examples(); rows[0]['split'] = 'test'; self.write(rows)
        self.assertIn('test data is not allowed', self.invoke('--yes')[2])

    def test_validation_only(self):
        rows, _, _, _ = prepare(self.path)
        baseline, _ = learning.train(rows, 'tfidf_logreg')
        for i in range(10):
            rows.append(dict(id=f'test-{i}', group=f'test-{i}', kind='triage', text='wrong test example', label='billing', split='test'))
        second, _ = learning.train(rows, 'tfidf_logreg')
        self.assertEqual(baseline['validation_curve'], second['validation_curve'])
        self.assertEqual(baseline['threshold'], second['threshold'])

    def test_auxiliary_has_no_alert(self):
        class Fake:
            def profile(self):
                return {'config': {'mode':'on_demand', 'grants':{'mask':['profile']}}}
        messages = [{'role':'user', 'content':'contact person@example.com'}]
        PromptGuard(Fake()).auxiliary(messages)
        self.assertIn('[EMAIL]', messages[0]['content'])
        self.assertNotIn('LOCAL_SCOUT_ALERT', messages[0]['content'])


class SetupRealDataShapeTests(unittest.TestCase):
    """real histories have out-of-scope `unknown` rows and incidental PII."""
    def test_unknown_allowed_and_pii_masked(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'h.jsonl'
            rows = []
            for i in range(12):
                rows.append(dict(text=f'please cancel my order number {i}', label='cancel', split='train' if i < 9 else 'validation'))
                rows.append(dict(text=f'where is my refund for purchase {i}', label='refund', split='train' if i < 9 else 'validation'))
            rows.append(dict(text='what is the best modem', label='unknown', split='train'))
            rows.append(dict(text='call me at (212) 555-0147 to cancel', label='cancel', split='train'))
            path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            prepared, automatic, classes, masked = prepare(path)
            self.assertEqual(classes, 2)
            self.assertEqual(masked, 1)
            self.assertFalse(any('555-0147' in r['text'] for r in prepared))
            self.assertTrue(any(r['label'] == 'unknown' for r in prepared))
            # IDs must not contain digit runs the PII detector could flag (UNSUPPORTED_SOURCE on real data)
            self.assertFalse(any(ch.isdigit() for r in prepared for ch in r['id'] + r['group']))


class SetupTransactionTests(unittest.TestCase):
    """failure restores the previous configuration; success keeps earlier grants such as mask."""
    def _history(self, tmp, labels=('cancel', 'refund')):
        path = Path(tmp) / 'h.jsonl'
        rows = []
        for i in range(12):
            rows.append(dict(text=f'please cancel my order number {chr(97+i)}', label=labels[0], split='train' if i < 9 else 'validation'))
            rows.append(dict(text=f'where is my refund for purchase {chr(97+i)}', label=labels[1], split='train' if i < 9 else 'validation'))
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return path

    def test_failure_restores_settings_and_success_keeps_mask(self):
        from plugin.scout.core import Scout
        from plugin.scout.context import LocalContext
        from plugin.scout import setup_triage
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'p'
            scout = Scout(LocalContext(home))
            scout.configure_local('report', grants={'mask': ['profile'], 'observe': ['profile']})
            before = (scout.root() / 'settings.json').read_bytes()
            args = type('A', (), dict(history=self._history(tmp), dataset='support', marker=None, mask_prompts=False,
                                      recipe='tfidf_logreg', max_error=None, yes=True, json=False))()
            with patch.object(scout, 'scout_train', side_effect=RuntimeError('boom')), contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(Exception):
                    setup_triage.run(scout, args)
            self.assertEqual((scout.root() / 'settings.json').read_bytes(), before)
            self.assertFalse((scout.root() / 'setup-triage' / 'support.jsonl').exists())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                setup_triage.run(scout, args)
            grants = scout.profile()['config']['grants']
            self.assertEqual(grants.get('mask'), ['profile'])
            self.assertIn('support', grants['answer'])

    def test_mask_prompts_makes_prompt_masks_visible_in_insights(self):
        from plugin.scout.core import Scout
        from plugin.scout.context import LocalContext
        from plugin.scout import setup_triage, insights
        with tempfile.TemporaryDirectory() as tmp:
            scout = Scout(LocalContext(Path(tmp) / 'p'))
            args = type('A', (), dict(history=self._history(tmp), dataset='support', marker=None, mask_prompts=True,
                                      recipe='tfidf_logreg', max_error=None, yes=True, json=False))()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                setup_triage.run(scout, args)
            grants = scout.profile()['config']['grants']
            self.assertIn('profile', grants['mask'])
            self.assertIn('profile', grants['insights'])
            out = PromptGuard(scout)(request={'messages': [{'role': 'user', 'content': 'mail me at demo@example.invalid'}]},
                                     api_mode='chat_completions')
            self.assertNotIn('demo@example.invalid', json.dumps(out['request']))
            summary = insights.summarize(scout.root())
            self.assertEqual(summary['pii_masks'].get('email'), 1)
            self.assertNotIn('demo@example.invalid', json.dumps(summary))

