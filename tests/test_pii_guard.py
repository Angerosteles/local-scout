"""Contract tests for opt-in tool-result redaction and metadata-only logging."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin.demo.__main__ import LocalContext
from plugin import register
from plugin.scout.core import Scout
from plugin.scout import privacy



class PiiGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.scout = Scout(LocalContext(Path(self.temp.name)))
        self.scout.configure_local('report', grants={
            'mask': ['profile'], 'observe': ['profile'], 'report': ['profile']})

    def tearDown(self):
        self.temp.cleanup()

    def test_email_and_pesel_mask_alert_and_safe_journal(self):
        for value, category in [('kontakt a.test@example.com', 'EMAIL'), ('PESEL 44051401458', 'PESEL')]:
            result = self.scout.transform_tool_result(tool_name='read_file', result=value,
                                                      session_id='session-a', tool_call_id='call-' + category)
            self.assertIn(f'[{category}]', result)
            self.assertIn('LOCAL_SCOUT_ALERT v1;', result)
            self.assertIn('action=masked', result)
            self.assertIn('source=tool_result', result)
            self.assertNotIn(value, result)

        saved = json.dumps(self.scout.profile()['events'], ensure_ascii=False)
        persisted = (self.scout.root() / 'aggregates.json').read_text(encoding='utf-8')
        report = self.scout.scout_report('profile')
        report_text = json.dumps(report, ensure_ascii=False)
        for secret in ('a.test@example.com', '44051401458'):
            self.assertNotIn(secret, saved + persisted + report_text)
        self.assertGreaterEqual({item['category'] for item in report['tool_events']}, {'email', 'pesel'})

    def test_valid_iban_is_masked_by_checksum(self):
        value = 'PL61109010140000071219812874'
        result = self.scout.transform_tool_result(result='rachunek ' + value,
                                                  session_id='iban-session', tool_call_id='iban-call')
        self.assertIn('[IBAN]', result)
        self.assertIn('categories=iban;', result)
        self.assertNotIn(value, result)

    def test_iban_does_not_consume_following_prose_and_phone_needs_context_or_format(self):
        value='PL61109010140000071219812874'
        masked,_=privacy.mask_local('Numer: '+value+' został opłacony.')
        self.assertIn('[IBAN] został opłacony.',masked)
        plain,_=privacy.mask_local('seed 202611060; size 123456789; token=None; token: int')
        self.assertEqual(plain,'seed 202611060; size 123456789; token=None; token: int')
        for phone in ('telefon: 123456789','+48 123 456 789','123-456-789'):
            self.assertIn('[PHONE]',privacy.mask_local(phone)[0])

    def test_json_object_alert_is_top_level_field(self):
        raw=json.dumps({'email':'jan@example.invalid','status':'ok'})
        parsed=json.loads(self.scout.transform_tool_result(result=raw,session_id='s',tool_call_id='json-top'))
        self.assertEqual(parsed['status'],'ok')
        self.assertEqual(parsed['email'],'[EMAIL]')
        self.assertTrue(parsed['_local_scout_alert'].startswith('LOCAL_SCOUT_ALERT v1;'))

    def test_journal_io_failure_keeps_computed_mask(self):
        with patch('plugin.scout.core.storage.write_json',side_effect=OSError('disk full')):
            result=self.scout.transform_tool_result(result='mail jan@example.invalid',session_id='s',tool_call_id='io')
        self.assertIn('[EMAIL]',result)
        self.assertIn('LOCAL_SCOUT_ALERT v1;',result)

    def test_clean_text_returns_none(self):
        self.assertIsNone(self.scout.transform_tool_result(result='bez danych osobowych',
                                                           session_id='s', tool_call_id='c'))
        self.assertEqual(self.scout.profile()['events'], [])

    def test_plugin_registers_both_result_transform_hooks(self):
        ctx = LocalContext(Path(self.temp.name) / 'registered')
        register(ctx)
        self.assertEqual(len(ctx.hooks['transform_tool_result']), 1)
        self.assertEqual(len(ctx.hooks['transform_terminal_output']), 1)
        active = ctx.tools['scout_inspect']['handler'].scout
        active.configure_local('report', grants={'mask': ['profile']})
        masked = ctx.hooks['transform_tool_result'][0](tool_name='read_file', args={}, result='x@example.com',
                                                       session_id='s', tool_call_id='registered')
        self.assertIn('[EMAIL]', masked)

    def test_json_is_valid_and_only_text_leaves_are_masked(self):
        raw = json.dumps({'text': 'mail x@example.com', 'count': 7, 'nested': ['czysto']})
        replacement = self.scout.transform_tool_result(result=raw, session_id='s', tool_call_id='json')
        parsed = json.loads(replacement)
        self.assertTrue(parsed['text'].startswith('mail [EMAIL]'))
        self.assertTrue(parsed['_local_scout_alert'].startswith('LOCAL_SCOUT_ALERT v1;'))
        self.assertEqual(parsed['count'], 7)
        self.assertEqual(parsed['nested'], ['czysto'])
        self.assertNotIn('x@example.com', replacement)

    def test_off_and_missing_mask_consent_return_none(self):
        off = Scout(LocalContext(Path(self.temp.name) / 'off'))
        off.configure_local('off', grants={'mask': ['profile']})
        no_consent = Scout(LocalContext(Path(self.temp.name) / 'no-consent'))
        no_consent.configure_local('report', grants={'observe': ['profile'], 'report': ['profile']})
        self.assertIsNone(off.transform_tool_result(result='x@example.com', session_id='s', tool_call_id='c'))
        self.assertIsNone(no_consent.transform_tool_result(result='x@example.com', session_id='s', tool_call_id='c'))

    def test_deduplicates_by_session_call_and_category(self):
        first = self.scout.transform_tool_result(result='x@example.com', session_id='s', tool_call_id='c')
        second = self.scout.transform_tool_result(result='x@example.com', session_id='s', tool_call_id='c')
        self.assertIn('[EMAIL]', first)
        self.assertIn('[EMAIL]', second)
        self.assertEqual(len([e for e in self.scout.profile()['events'] if e.get('category') == 'email']), 1)

    def test_detector_exception_is_fail_closed_and_partial_is_recorded(self):
        # Fail-closed with mask consent: a scan error withholds content instead of passing it through.
        with patch.object(privacy, 'spans', side_effect=RuntimeError('detector broke')):
            withheld = self.scout.transform_tool_result(result='x@example.com', session_id='s', tool_call_id='c')
            self.assertIn('action=withheld', withheld)
            self.assertNotIn('x@example.com', withheld)
        self.assertTrue(any(e.get('category') == 'coverage:partial' for e in self.scout.profile()['events']))
        self.assertNotIn('x@example.com', (self.scout.root() / 'aggregates.json').read_text(encoding='utf-8'))

    def test_non_string_result_is_fail_closed_and_plain_number_passes(self):
        withheld = self.scout.transform_tool_result(result={'binary': b'private'}, session_id='s', tool_call_id='object')
        self.assertIn('action=withheld', withheld)
        self.assertNotIn('private', withheld)
        self.assertIsNone(self.scout.transform_tool_result(result='42', session_id='s', tool_call_id='number'))
        self.assertIsNone(self.scout.transform_tool_result(result='{"total_lines": 3, "file_size": 99, "truncated": false}',
                                                           session_id='s', tool_call_id='readfile'))
        self.assertTrue(all(e.get('category') == 'coverage:partial' for e in self.scout.profile()['events']))

    def test_large_result_is_bounded_and_terminal_source_is_recorded(self):
        raw = ('a' * 40000) + ' x@example.com'
        replacement = self.scout.transform_tool_result(result=raw, session_id='s', tool_call_id='large')
        self.assertIn('[EMAIL]', replacement)
        self.assertNotIn('x@example.com', replacement)
        terminal = self.scout.transform_terminal_output(output='terminal x@example.com', task_id='s', tool_call_id='term')
        self.assertIn('source=terminal', terminal)
        self.assertNotIn('x@example.com', terminal)
        too_large = 'x@example.com ' + ('z' * (256 * 1024))
        withheld = self.scout.transform_tool_result(result=too_large, session_id='s', tool_call_id='oversize')
        self.assertIn('action=withheld', withheld)
        self.assertNotIn('x@example.com', withheld)
        self.assertTrue(any(e.get('category') == 'coverage:partial' and e.get('source') == 'tool_result'
                            for e in self.scout.profile()['events']))


    def test_failure_outside_the_scan_withholds_instead_of_passing_raw(self):
        raw = 'contact a.test@example.com'
        with patch('plugin.scout.core.secrets.token_hex', side_effect=RuntimeError('boom')):
            result = self.scout.transform_tool_result(tool_name='read_file', result=raw, session_id='s', tool_call_id='c')
        self.assertIsNotNone(result)
        self.assertIn('action=withheld', result)
        self.assertIn('reason=scout_state_unavailable', result)
        self.assertNotIn('a.test@example.com', result)
        with patch.object(Scout, 'profile', side_effect=RuntimeError('unreadable state')):
            result = self.scout.transform_terminal_output(command='cat x', output=raw)
        self.assertIn('action=withheld', result)
        self.assertNotIn('a.test@example.com', result)

    def test_mask_off_still_leaves_results_unchanged(self):
        self.scout.configure_local('report', grants={'observe': ['profile'], 'report': ['profile']})
        self.assertIsNone(self.scout.transform_tool_result(tool_name='read_file', result='contact a.test@example.com'))
        self.scout.configure_local('off', grants={})
        self.assertIsNone(self.scout.transform_terminal_output(command='cat x', output='contact a.test@example.com'))

if __name__ == '__main__':
    unittest.main()
