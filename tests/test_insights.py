import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin.scout import insights
from plugin.scout.presentation import insights_terminal, write_insights_html
from plugin.cli import main
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.tools import handler_for


class InsightsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_public_preview_is_allowlisted_and_does_not_leak_text_ids_or_pii(self):
        secret = 'UNIQUE-INPUT-SENTINEL jane.private@example.com 987-654-321'
        insights.append_event(self.root, {'ts': 1000, 'kind': 'tool', 'tool': 'scout_predict', 'ok': False,
                                          'code': 'INVALID_INPUT', 'response_bytes': 77,
                                          'input': secret, 'id': secret, 'path': secret})
        (self.root / 'choices.json').write_text(json.dumps({'private-dataset-name': {
            'dataset_id': 'private-dataset-name', 'recipe_id': 'regex', 'reason': secret}}), encoding='utf-8')
        (self.root / 'decisions.json').write_text(json.dumps([{'dataset_id': 'private-dataset-name', 'reason': secret,
            'outcome': {'predictions': [{'query_id': secret, 'decision': 'accepted', 'text': secret}]}}]), encoding='utf-8')
        payload = insights.community_payload(insights.summarize(self.root, now=1001))
        encoded = json.dumps(payload, ensure_ascii=False)
        for forbidden in ('UNIQUE-INPUT-SENTINEL', 'jane.private@example.com', '987-654-321', secret,
                          'private-dataset-name', 'query_id'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(payload['schema_version'], 1)
        self.assertEqual(set(payload), {'schema_version', 'period_days', 'state_metrics_scope', 'operations', 'pii_masks', 'pii_by_source',
                                        'predictions', 'methods', 'training', 'calibration_events', 'local_answers', 'forwarded_reasons'})

    def test_small_category_counts_are_suppressed(self):
        (self.root / 'aggregates.json').write_text(json.dumps({'events': [
            {'timestamp': 1000, 'category': 'email', 'source': 'tool_result'} for _ in range(4)]}), encoding='utf-8')
        payload = insights.community_payload(insights.summarize(self.root, now=1001))
        self.assertNotIn('email', payload['pii_masks'])

    def test_prompt_and_auxiliary_masks_are_counted_separately_from_injection_alerts(self):
        (self.root / 'aggregates.json').write_text(json.dumps({'events': [
            {'timestamp': 1000, 'category': 'email', 'source': 'user_prompt', 'action': 'masked'},
            {'timestamp': 1000, 'category': 'phone', 'source': 'auxiliary_call', 'action': 'masked'},
            {'timestamp': 1000, 'category': 'prompt_injection', 'source': 'user_prompt', 'action': 'detected'},
        ]}), encoding='utf-8')
        summary = insights.summarize(self.root, now=1001)
        self.assertEqual(summary['pii_masks'], {'email': 1, 'phone': 1})
        self.assertEqual(summary['pii_by_source'], {
            'auxiliary_call': {'phone': 1}, 'user_prompt': {'email': 1, 'prompt_injection': 1}})
        self.assertEqual(summary['prompt_injection_alerts'], 1)

    def test_local_answer_reasons_are_summarized_and_small_export_cells_are_omitted(self):
        for i, reason in enumerate(['budget', 'confidence', 'budget', 'budget']):
            insights.append_event(self.root, {'ts': 1000 + i, 'kind': 'local_answer', 'answered': 0,
                                               'forwarded': 1, 'reason': reason})
        summary = insights.summarize(self.root, now=1005)
        self.assertEqual(summary['forwarded_reasons'], {'budget': 3, 'confidence': 1})
        payload = insights.community_payload(summary)
        self.assertEqual(payload['forwarded_reasons'], {})
        for i in range(6):
            insights.append_event(self.root, {'ts': 1010 + i, 'kind': 'local_answer', 'answered': 0,
                                               'forwarded': 1, 'reason': 'budget'})
        payload = insights.community_payload(insights.summarize(self.root, now=1020))
        self.assertEqual(payload['forwarded_reasons'], {'budget': 10})

    def test_missing_usage_is_none_with_context_and_local_answers_feed_handling_totals(self):
        (self.root / 'settings.json').write_text(json.dumps({'mode': 'on_demand', 'grants': {'answer': ['triage']}}))
        insights.append_event(self.root, {'ts': 1000, 'kind': 'local_answer', 'answered': 1,
                                           'forwarded': 0, 'estimated_input_tokens_avoided': 140})
        summary = insights.summarize(self.root, now=1001)
        self.assertIsNone(summary['tokens_observed']['input'])
        self.assertIn('on_demand', summary['tokens_observed']['reason'])
        self.assertEqual(summary['handled_locally'], 1)
        self.assertEqual(summary['estimated_tokens_avoided'], 140)
        self.assertEqual(summary['handling_sources'], {'handle_telemetry': None, 'local_answers': 1})
        self.assertIn('not observed', insights.savings_summary(self.root, 140)['net_note'])

        from plugin.scout.core import Scout
        scout = Scout(LocalContext(self.root / 'on-demand-profile'))
        scout.configure_local('on_demand', grants={'observe': ['triage']})
        insights.observe_usage_hook(scout)
        scout.usage_hook({'dataset_id': 'triage', 'api_request_id': 'req-1',
                          'usage': {'input_tokens': 100, 'output_tokens': 20}})
        on_demand_summary = insights.summarize(scout.root())
        self.assertIsNone(on_demand_summary['tokens_observed']['input'])
        self.assertIn('on_demand', on_demand_summary['tokens_observed']['reason'])
        self.assertFalse((scout.root() / insights.JOURNAL).exists())

    def test_two_scout_instances_merge_aggregate_events_and_prune_on_each_write(self):
        from plugin.scout.core import Scout
        from plugin.scout.context import LocalContext
        home = self.root / 'home'
        first, second = Scout(LocalContext(home)), Scout(LocalContext(home))
        first.configure_local('background', grants={'mask': ['profile'], 'observe': ['profile']})
        # Load both per-instance caches before either event is persisted.
        first.profile(); second.profile()
        first._transform_tool_output('one@example.invalid', 'tool_result')
        second._transform_tool_output('212-555-1212', 'tool_result')
        aggregate_path = first.root() / 'aggregates.json'
        saved = json.loads(aggregate_path.read_text())['events']
        self.assertEqual({event['category'] for event in saved}, {'email', 'phone'})
        settings = json.loads((first.root() / 'settings.json').read_text())
        settings['retention_days'] = 1
        (first.root() / 'settings.json').write_text(json.dumps(settings))
        insights.append_event(first.root(), {'ts': 1000, 'kind': 'tool', 'tool': 'scout_report', 'ok': True})
        # Any aggregate save applies retention; old events must not survive.
        from plugin.scout import storage
        storage.write_json(aggregate_path, {'events': saved + [{'timestamp': 1, 'category': 'email', 'source': 'tool_result'}]})
        pruned = json.loads(aggregate_path.read_text())['events']
        self.assertTrue(all(event['timestamp'] >= __import__('time').time() - 86400 for event in pruned))

    def test_append_rotates_bounded_journal(self):
        insights.MAX_JOURNAL_BYTES = 350
        try:
            for i in range(20):
                insights.append_event(self.root, {'ts': 1000 + i, 'kind': 'tool', 'tool': 'scout_test',
                    'ok': True, 'duration_ms': i, 'response_bytes': 1})
            self.assertTrue((self.root / 'insights.jsonl.1').exists())
            self.assertLessEqual((self.root / 'insights.jsonl').stat().st_size, 350)
            self.assertLessEqual((self.root / 'insights.jsonl.1').stat().st_size, 350)
        finally:
            insights.MAX_JOURNAL_BYTES = 1_000_000

    def test_handler_records_only_metadata_and_usage_hook_records_accepted_tokens(self):
        secret = 'UNIQUE-ARGUMENT jane.private@example.com'
        class FakeScout:
            def root(self):
                return self_root
            def profile(self):
                return {'config': {'mode': 'report', 'grants': {'insights': ['profile']}}, 'usage': {}}
            def call(self, name, params):
                self.assertion = params
                return {'ok': True, 'data': secret}
            def usage_hook(self, payload=None, **kwargs):
                return None
        self_root = self.root
        fake = FakeScout()
        handler = handler_for(fake, 'scout_report')
        handler({'text': secret})
        journal = (self.root / 'insights.jsonl').read_text(encoding='utf-8')
        self.assertNotIn(secret, journal)
        self.assertIn('response_bytes', journal)

        scout = Scout(LocalContext(self.root / 'profile'))
        scout.configure_local('background', grants={'observe': ['demo_a']})
        insights.observe_usage_hook(scout)
        scout.usage_hook({'dataset_id': 'demo_a', 'api_request_id': secret,
                          'usage': {'prompt_tokens': 31, 'completion_tokens': 7}})
        events = [json.loads(line) for line in (scout.root() / 'insights.jsonl').read_text(encoding='utf-8').splitlines()]
        usage = next(event for event in events if event['kind'] == 'usage')
        self.assertEqual((usage['input_tokens'], usage['output_tokens']), (31, 7))
        self.assertNotIn(secret, json.dumps(events))

    def test_summary_and_terminal_html_views_render(self):
        insights.append_event(self.root, {'ts': 1000, 'kind': 'tool', 'tool': 'scout_report', 'ok': True,
                                          'duration_ms': 12, 'response_bytes': 48})
        summary = insights.summarize(self.root, now=1001)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            insights_terminal(summary)
        self.assertIn('scout_report: ok=1', output.getvalue())
        self.assertIn('not observed', output.getvalue())
        target = self.root / 'insights.html'
        write_insights_html(summary, target)
        self.assertIn('Scout Insights', target.read_text(encoding='utf-8'))

    def test_local_answer_telemetry_is_visible_in_terminal_and_html(self):
        (self.root / 'settings.json').write_text(json.dumps({'mode': 'on_demand', 'grants': {'answer': ['triage']}}))
        insights.append_event(self.root, {'ts': 1000, 'kind': 'local_answer', 'answered': 1, 'forwarded': 0,
                                           'estimated_input_tokens_avoided': 100})
        summary = insights.summarize(self.root, now=1001)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            insights_terminal(summary)
        self.assertIn('Local answers', output.getvalue())
        self.assertIn('answered=1', output.getvalue())
        target = self.root / 'answers.html'
        write_insights_html(summary, target)
        page = target.read_text(encoding='utf-8')
        self.assertIn('Local answers', page)
        self.assertIn('answered', page)

    def test_export_requires_explicit_insights_grant(self):
        class Scout:
            def __init__(self, grants):
                self.grants = grants
            def profile(self):
                return {'config': {'grants': self.grants}}
        self.assertFalse(insights.export_allowed(Scout({})))
        self.assertTrue(insights.export_allowed(Scout({'insights': ['demo_a']})))

    def test_cli_refuses_preview_without_consent_and_allows_configured_grant(self):
        home = self.root / 'profile'
        stderr = io.StringIO()
        with patch('sys.stderr', stderr), self.assertRaises(SystemExit) as raised:
            main(['--home', str(home), 'insights', '--preview'])
        self.assertEqual(raised.exception.code, 2)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main(['--home', str(home), 'configure', '--mode', 'report', '--dataset', 'demo_a', '--approve', 'insights'])
            main(['--home', str(home), 'insights', '--preview'])
        self.assertIn('"schema_version": 1', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()


class InsightsConsentTests(unittest.TestCase):
    def test_no_grant_means_no_journal(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            class FakeScout:
                def root(self):
                    return root
                def profile(self):
                    return {'config': {'mode': 'background', 'grants': {}}, 'usage': {}}
                def call(self, name, params):
                    return {'ok': False, 'error': {'code': 'CONSENT_REQUIRED'}}
                def usage_hook(self, payload=None, **kwargs):
                    return None
            handler_for(FakeScout(), 'scout_inspect')({'dataset_id': 'demo_a'})
            self.assertFalse((root / 'insights.jsonl').exists())



class ScopedGrantCliTests(unittest.TestCase):
    """mask@profile next to answer for the triage dataset in one configure call."""
    def test_scope_at_dataset(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'profile'
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(['--home', str(home), 'configure', '--mode', 'on_demand', '--dataset', 'savings',
                      '--approve', 'observe,train,answer,mask@profile,observe@profile', '--answer-task', 'triage'])
            grants = json.loads(out.getvalue().strip().splitlines()[-1])['effective_scope']
            self.assertEqual(grants['mask'], ['profile'])
            self.assertEqual(sorted(grants['observe']), ['profile', 'savings'])
            self.assertEqual(grants['answer'], ['savings'])

    def test_unknown_scope_still_rejected(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'profile'
            with patch('sys.stderr', io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                try:
                    main(['--home', str(home), 'configure', '--mode', 'on_demand', '--approve', 'nope@profile'])
                except SystemExit:
                    pass
            cfg = home / 'local-scout' / 'profile.json'
            self.assertFalse(cfg.exists() and 'nope' in cfg.read_text())
