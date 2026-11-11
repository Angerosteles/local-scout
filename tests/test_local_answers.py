"""Offline contract tests: real Hermes middleware chain, no running Hermes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from plugin import register
from plugin.cli import main
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout import insights, learning, storage
from plugin.scout.local_answers import LocalAnswers, MAX_TEXT

from plugin.tests.hermes_source import SKIP_REASON, hermes_file

REF = hermes_file('hermes_cli/middleware.py')


class LocalAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Only this stdlib-only reference file is imported, with a fake plugin manager.
        if REF is None:
            raise unittest.SkipTest(SKIP_REASON)
        spec = importlib.util.spec_from_file_location('_scout_execution_contract', REF)
        cls.chain = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.chain
        spec.loader.exec_module(cls.chain)
        cls.prior_modules = {key: sys.modules.get(key) for key in ('hermes_cli', 'hermes_cli.plugins')}
        package = ModuleType('hermes_cli')
        package.__path__ = []
        cls.plugins = ModuleType('hermes_cli.plugins')
        cls.plugins._delivery_manager = None
        sys.modules['hermes_cli'] = package
        sys.modules['hermes_cli.plugins'] = cls.plugins

    @classmethod
    def tearDownClass(cls):
        for key, previous in cls.prior_modules.items():
            if previous is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = previous
        sys.modules.pop('_scout_execution_contract', None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.scout = Scout(LocalContext(self.home))
        self.scout.configure_local('on_demand', grants={'answer': ['support']}, answer_task='triage')
        # Small deterministic, validated statistical model; no text examples in the artifact.
        self.meta = dict(engine='logreg', vocabulary=['billing', 'technical'],
                         classes=['billing', 'technical'], dataset_labels=['billing', 'technical'],
                         dataset_id='support', candidate_id='triage', threshold=.8,
                         threshold_source='validation')
        self.arrays = dict(idf=np.ones(2), coef=np.array([[-8., 8.]]), intercept=np.zeros(1))
        self.aid = storage.save_artifact(self.scout.root() / 'artifacts', self.meta, self.arrays)
        self.choices = {'triage': dict(dataset_id='support', recipe_id='logreg', decision_id='decision-a')}
        self.entries = [dict(decision_id='decision-a', dataset_id='support', candidate_id='triage',
                             recipe_id='logreg', outcome=dict(status='measured', artifact_id=self.aid))]
        storage.write_json(self.scout.root() / 'choices.json', self.choices)
        storage.write_json(self.scout.root() / 'decisions.json', self.entries)
        self.callback = LocalAnswers(self.scout)
        self.original = {'messages': [{'role': 'user', 'content': 'billing'}]}
        self.request = {'messages': [{'role': 'user', 'content': '[MASKED]'}]}
        self.terminal = Mock(return_value='provider-answer')
        # Timing is tested separately: these tests assert the logical contract independent of CPU load.
        self.budget = patch('plugin.scout.local_answers.MAX_LOCAL_SECONDS', 60)
        self.budget.start()

    def tearDown(self):
        self.budget.stop()
        self.temp.cleanup()

    def run_chain(self, **kwargs):
        manager = SimpleNamespace(_middleware={'llm_execution': [self.callback]}, _report_hook_failure=Mock())
        self.manager = manager
        context = dict(original_request=self.original, api_mode='chat_completions',
                       provider='openrouter', model='fixture-model', api_call_count=1, session_id='private-session')
        context.update(kwargs)
        with patch.object(self.plugins, '_delivery_manager', return_value=manager):
            return self.chain.run_llm_execution_middleware(self.request, self.terminal, **context)

    def assert_forwarded(self, **kwargs):
        self.assertEqual(self.run_chain(**kwargs), 'provider-answer')
        self.terminal.assert_called_once_with(self.request)
        self.assertIs(self.terminal.call_args.args[0], self.request)
        self.assertNotIn('original_request', self.terminal.call_args.kwargs)
        self.manager._report_hook_failure.assert_not_called()

    def test_chat_completed_shape_stream_and_nonstream(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                self.request['stream'] = streaming
                response = self.run_chain()
                choice = response.choices[0]
                self.assertEqual(choice.finish_reason, 'stop')
                self.assertIsNone(choice.message.tool_calls)
                self.assertIsNone(choice.message.function_call)
                self.assertIsNone(choice.message.refusal)
                self.assertEqual(choice.message.role, 'assistant')
                self.assertEqual(choice.message.content,
                                 '{"intent":"billing","answered_by":"local_scout","confidence":1.000}\n'
                                 'Answered locally by Scout; the model was not asked to answer.')
                self.assertEqual((response.usage.prompt_tokens, response.usage.completion_tokens,
                                  response.usage.total_tokens), (0, 0, 0))
                self.assertFalse(hasattr(response, '__iter__'))
                self.terminal.assert_not_called()

    def test_classifies_original_and_never_forwards_original(self):
        with patch.object(learning, 'predict', wraps=learning.predict) as predict:
            response = self.run_chain()
        self.assertIn('"intent":"billing"', response.choices[0].message.content)
        self.assertEqual(predict.call_args.args[2]['text'], 'billing')
        self.terminal.assert_not_called()
        self.scout.configure_local('on_demand', grants={})
        self.assert_forwarded()

    def test_no_answer_consent_even_with_all_other_grants(self):
        self.scout.configure_local('on_demand', grants={k: ['support'] for k in ('observe', 'insights', 'train', 'report', 'mask')})
        self.assert_forwarded()

    def test_answer_consent_must_match_artifact_dataset(self):
        self.scout.configure_local('on_demand', grants={'answer': ['other']})
        self.assert_forwarded()

    def test_no_task_and_off(self):
        for changes in ({'answer_task': None}, {'mode': 'off'}):
            with self.subTest(changes=changes):
                with patch.object(self.scout, 'profile', return_value={'config': {
                    'mode': 'report', 'answer_task': 'triage', 'grants': {'answer': ['support']}, **changes
                }}):
                    self.assert_forwarded()
                self.terminal.reset_mock()

    def test_codex_and_other_modes_always_forward(self):
        for mode in ('codex_responses', 'anthropic_messages', 'bedrock_converse', None):
            with self.subTest(mode=mode):
                self.assert_forwarded(api_mode=mode)
                self.terminal.reset_mock()

    def test_low_confidence_unknown_and_nan_forward(self):
        for result in (dict(decision='unknown', label=None, score=.9),
                       dict(decision='accepted', label='billing', score=.7),
                       dict(decision='accepted', label='unknown', score=1.),
                       dict(decision='accepted', label='untrained', score=1.),
                       dict(decision='accepted', label='billing', score=float('nan'))):
            with self.subTest(result=result), patch.object(learning, 'predict', return_value=result):
                self.assert_forwarded()
                self.terminal.reset_mock()

    def test_threshold_inclusive_and_three_decimal_places(self):
        with patch.object(learning, 'predict', return_value=dict(decision='accepted', label='billing', score=.8)):
            self.assertIn('"confidence":0.800}', self.run_chain().choices[0].message.content)
        self.terminal.assert_not_called()

    def test_tool_loop_suffix_in_either_payload_forwards(self):
        for key in ('original', 'request'):
            for suffix in ({'role': 'tool', 'content': 'tool-result'},
                           {'type': 'function_call_output', 'output': 'tool-result'},
                           {'role': 'assistant', 'tool_calls': [{'id': 'pending'}]}):
                with self.subTest(key=key, suffix=suffix):
                    original = getattr(self, key)
                    setattr(self, key, {'messages': original['messages'] + [suffix]})
                    self.assert_forwarded()
                    setattr(self, key, original)
                    self.terminal.reset_mock()

    def test_new_user_after_previous_completed_tool_turn_is_allowed(self):
        self.original['messages'] = [dict(role='user', content='previous'),
                                     dict(role='assistant', tool_calls=[dict(id='done')]),
                                     dict(role='tool', tool_call_id='done', content='done'),
                                     dict(role='assistant', content='done'),
                                     dict(role='user', content='billing')]
        self.assertIn('billing', self.run_chain().choices[0].message.content)

    def test_bad_or_missing_original_and_long_or_nontext_input_forward(self):
        for content in ('', 'x' * (MAX_TEXT + 1),
                        [{'type': 'text', 'text': 'x' * (MAX_TEXT + 1)}],
                        [{'type': 'image_url', 'image_url': 'unread'}], None):
            with self.subTest(content_type=type(content).__name__):
                self.original['messages'][-1]['content'] = content
                self.assert_forwarded()
                self.terminal.reset_mock()
        self.assert_forwarded(original_request=None)

    def test_marker_single_occurrence_and_text_blocks(self):
        self.scout.configure_local('on_demand', answer_marker='TASK:')
        self.original['messages'][-1]['content'] = [{'type': 'text', 'text': 'TASK: billing'}]
        with patch.object(learning, 'predict', wraps=learning.predict) as predict:
            self.assertIn('billing', self.run_chain().choices[0].message.content)
        self.assertEqual(predict.call_args.args[2]['text'], ' billing')
        self.original['messages'][-1]['content'] = 'TASK: technical TASK: billing'
        self.assert_forwarded()

    def test_exception_before_next_call_forwards_once(self):
        with patch.object(learning, 'predict', side_effect=RuntimeError('private exception details')):
            self.assert_forwarded()

    def test_downstream_exception_propagates_once(self):
        self.scout.configure_local('off')
        error = RuntimeError('provider failed')
        self.terminal.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.run_chain()
        self.assertIs(raised.exception, error)
        self.terminal.assert_called_once_with(self.request)
        self.manager._report_hook_failure.assert_not_called()

    def test_consent_revoked_during_prediction_forwards(self):
        def revoke(*args):
            self.scout.configure_local('on_demand', grants={})
            return dict(decision='accepted', label='billing', score=.99)
        with patch.object(learning, 'predict', side_effect=revoke):
            self.assert_forwarded()

    def test_cache_and_revocation_and_corruption(self):
        with patch.object(storage, 'load_artifact', wraps=storage.load_artifact) as load:
            self.run_chain()
            self.run_chain()
            self.assertEqual(load.call_count, 1)
            (self.scout.root() / 'artifacts' / (self.aid + '.npz')).write_bytes(b'corrupt')
            self.assert_forwarded()
        self.terminal.reset_mock()
        self.scout.configure_local('on_demand', grants={})
        self.assert_forwarded()

    def test_missing_artifact_and_active_decision_change_forward(self):
        self.run_chain()
        self.choices['triage']['decision_id'] = 'missing'
        storage.write_json(self.scout.root() / 'choices.json', self.choices)
        self.assert_forwarded()
        self.terminal.reset_mock()
        storage.write_json(self.scout.root() / 'choices.json', {'triage': {}})
        self.assert_forwarded()

    def test_unsupported_heavy_engine_forwards_without_loading(self):
        path = self.scout.root() / 'artifacts' / (self.aid + '.json')
        descriptor = storage.read_json(path)
        descriptor['metadata']['engine'] = 'own_cnn'
        storage.write_json(path, descriptor)
        with patch.object(storage, 'load_artifact') as load:
            self.assert_forwarded()
        load.assert_not_called()

    def test_budget_and_busy_cache_forward_without_waiting(self):
        with patch('plugin.scout.local_answers.MAX_LOCAL_SECONDS', -1), \
             patch('plugin.scout.local_answers.MAX_COLD_SECONDS', -1):
            self.assert_forwarded()
        self.terminal.reset_mock()
        self.callback._lock.acquire()
        try:
            self.assert_forwarded()
        finally:
            self.callback._lock.release()

    def test_cold_first_decision_gets_cold_budget_then_warm_budget_applies(self):
        # a one-shot `hermes -z` process is always cold; the first decision may use the cold budget.
        with patch('plugin.scout.local_answers.MAX_LOCAL_SECONDS', -1):
            response = self.run_chain()
            self.assertIn('Answered locally by Scout', response.choices[0].message.content)
            self.terminal.reset_mock()
            self.assert_forwarded()

    def test_local_answer_journal_is_opt_in_and_metadata_only(self):
        self.run_chain()
        self.assertFalse((self.scout.root() / insights.JOURNAL).exists())
        self.scout.configure_local('on_demand', grants={'answer': ['support'], 'insights': ['support']})
        for _ in range(5):
            self.run_chain()
        self.original['messages'][-1]['content'] = 'outside vocabulary'
        self.assert_forwarded()
        summary = insights.summarize(self.scout.root())
        self.assertEqual(summary['local_answers']['answered'], 5)
        self.assertEqual(summary['local_answers']['forwarded'], 1)
        self.assertEqual(summary['local_answers']['estimated_input_tokens_avoided'],
                         5 * len(json.dumps(self.request, separators=(',', ':'))) / 4)
        payload = insights.community_payload(summary)
        self.assertIn('local_answers', payload)
        # Journal is tiny test output; inspect it without retaining any prompt in telemetry.
        rows = [json.loads(line) for line in (self.scout.root() / insights.JOURNAL).read_text().splitlines()]
        self.assertEqual(set(rows[0]), {'ts', 'kind', 'answered', 'forwarded', 'reason',
                                       'estimated_input_tokens_avoided', 'estimate_basis'})
        encoded = json.dumps(rows) + json.dumps(payload)
        for forbidden in ('billing', 'support', 'MASKED', 'private-session', 'fixture-model', 'outside vocabulary'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(rows[-1]['reason'], 'confidence')
        self.assertIn('estimate:', rows[0]['estimate_basis'])

    def test_telemetry_failure_does_not_retry_provider(self):
        self.scout.configure_local('off')
        with patch.object(insights, 'record_local_answer', side_effect=OSError('disk')):
            self.assert_forwarded()

    def test_journal_rejects_free_prose_even_in_reason_or_estimate_basis(self):
        insights.append_event(self.scout.root(), dict(
            ts=1000, kind='local_answer', answered=0, forwarded=999, reason='PRIVATE-LABEL',
            estimated_input_tokens_avoided=999, estimate_basis='PRIVATE-TEXT', label='PRIVATE-LABEL'))
        row = insights._read_journal(self.scout.root(), 0)[0]
        self.assertEqual((row['answered'], row['forwarded'], row['reason']), (0, 1, 'error'))
        self.assertEqual(row['estimated_input_tokens_avoided'], 0)
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_report_hook_preserves_exact_local_reply(self):
        self.scout.configure_local('report', grants={'answer': ['support'], 'observe': ['support']})
        self.scout.profile()['notifications'] = [dict(dataset_id='support', prompt_categories=['email'])]
        response = self.run_chain().choices[0].message.content
        self.assertIsNone(self.scout.notify_output(response))
        self.assertEqual(len(self.scout.profile()['notifications']), 1)
        self.assertEqual(len(response.splitlines()), 2)

    def test_real_training_and_handle_use_same_acceptance_threshold(self):
        from plugin.demo.generate import demo_a
        from plugin.scout import operator
        rows = list(demo_a(seed=17))
        source = self.home / 'approved-synthetic.jsonl'
        source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.scout.configure_local('on_demand', grants={
            scope: ['support'] for scope in ('answer', 'observe', 'train', 'report')
        }, sources={'support': source})
        self.scout.scout_inspect('support')
        self.scout.scout_choose('triage', 'logreg', 'label_count>=2')
        trained = self.scout.scout_train('triage', 'logreg', 'support')
        meta, arrays = storage.load_artifact(self.scout.root() / 'artifacts', trained['artifact_id'])
        text = next(row['text'] for row in rows if row['kind'] == 'triage' and
                    learning.predict(meta, arrays, row)['decision'] == 'accepted')
        storage.write_json(self.scout.root() / 'calibration.json', dict(
            dataset_id='support', input_digest=operator.digest(self.scout.decision_rows('support')),
            parameters={'tokens_per_task': 1000}, events=[]))
        handled = self.scout.scout_handle('triage', text)
        self.original['messages'][-1]['content'] = text
        local = self.run_chain().choices[0].message.content
        payload = json.loads(local.splitlines()[0])
        self.assertEqual(handled['status'], 'handled_locally')
        self.assertEqual(payload['intent'], handled['label'])
        self.assertEqual(payload['confidence'], round(handled['confidence'], 3))
        self.assertEqual(meta['threshold'], trained['threshold'])
        self.terminal.assert_not_called()

    def test_cli_shared_interface_and_registration_compatibility(self):
        with contextlib.redirect_stdout(io.StringIO()):
            main(['--home', str(self.home), 'configure', '--mode', 'report', '--dataset', 'support',
                  '--approve', 'answer,insights', '--answer-task', 'triage', '--answer-marker', 'TASK:'])
        config = storage.read_json(self.scout.root() / 'settings.json')
        self.assertEqual((config['answer_task'], config['answer_marker']), ('triage', 'TASK:'))
        self.assertEqual(config['grants']['answer'], ['support'])
        old = LocalContext(self.home)
        register(old)
        new = LocalContext(self.home)
        new.register_middleware = Mock()
        register(new)
        name, callback = new.register_middleware.call_args.args
        self.assertEqual(name, 'llm_execution')
        self.assertIsInstance(callback, LocalAnswers)


if __name__ == '__main__':
    unittest.main()
