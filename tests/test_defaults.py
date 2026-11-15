"""Fresh-profile privacy defaults, persistence and explicit opt-out regressions."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin import MASKING_PROMPT, register
from plugin.cli import main
from plugin.scout import insights, menu, storage
from plugin.scout.commands import slash_handler
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout.local_answers import LocalAnswers
from plugin.scout.prompt_guard import PromptGuard, WITHHELD
from plugin.scout.tool_guard import MAX_INPUT_CHARS


class DefaultMasking(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'profile'
        self.ctx = LocalContext(self.home)
        self.scout = Scout(self.ctx)
        self.guard = PromptGuard(self.scout)

    def request(self, text):
        return {'messages': [{'role': 'user', 'content': text}]}

    def test_prompt_and_tool_mask_card_email_without_files_or_name_masking(self):
        text = 'John Smith, jane@example.com, card 4111 1111 1111 1111'
        prompt = self.guard(request=self.request(text))['request']['messages'][0]['content']
        tool = self.scout.transform_tool_result(tool_name='mcp_mail', result=text)
        terminal = self.scout.transform_terminal_output(output=text)
        for result in (prompt, tool, terminal):
            self.assertIn('[EMAIL]', result)
            self.assertIn('[CARD]', result)
            self.assertIn('John Smith', result)
            self.assertNotIn('jane@example.com', result)
            self.assertNotIn('4111 1111 1111 1111', result)
        self.assertFalse(self.home.exists())

    def test_history_system_arguments_and_auxiliary_use_default_mask(self):
        request = {'system': 'sys@example.com', 'messages': [
            {'role': 'system', 'content': 'system@example.com'},
            {'role': 'assistant', 'content': 'earlier@example.com'},
            {'role': 'tool', 'content': 'tool@example.com'},
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call-a', 'type': 'function', 'function':
                 {'name': 'mail', 'arguments': '{"to":"arg@example.com"}'}}]},
            {'role': 'user', 'content': 'user@example.com'}]}
        result = self.guard(request=request)['request']
        self.assertNotIn('@example.com', json.dumps(result))
        self.assertEqual(result['system'], '[EMAIL]')
        self.assertEqual(json.loads(result['messages'][3]['tool_calls'][0]['function']['arguments']),
                         {'to': '[EMAIL]'})
        self.guard.auxiliary(request_messages=request['messages'])
        self.assertNotIn('@example.com', json.dumps(request['messages']))
        self.assertFalse(self.home.exists())

    def test_registration_default_prompt_and_hooks_without_writes(self):
        sections, middleware = {}, {}
        self.ctx.register_system_prompt_section = lambda name, callback, **opts: sections.update({name: callback})
        self.ctx.register_middleware = lambda name, callback: middleware.update({name: callback})
        register(self.ctx)
        self.assertEqual(sections['local-scout.masking']({}), MASKING_PROMPT)
        self.assertIn('[EMAIL]', str(middleware['llm_request'](request=self.request('a@example.com'))))
        self.assertIn('[EMAIL]', self.ctx.hooks['transform_tool_result'][0](result='b@example.com'))
        self.assertFalse(self.home.exists())

    def test_insights_all_automatic_paths_stay_in_memory(self):
        insights.observe_usage_hook(self.scout)
        self.guard(request=self.request('a@example.com'))
        self.scout.observe(api_request_id='r1', request_messages=self.request('a@example.com')['messages'])
        self.scout.transform_tool_result(result='b@example.com')
        self.scout.post_tool_call(tool_name='read_file', status='ok', duration_ms=1)
        insights.record_tool(self.scout, 'scout_report', {'ok': True}, 1, 100, session_id='s1')
        self.scout.usage_hook(api_request_id='u1', session_id='s1', usage={'prompt_tokens': 10, 'completion_tokens': 2})
        insights.record_local_answer(self.scout, False, 'task')
        forwarded = []
        LocalAnswers(self.scout)(request=self.request('a@example.com'),
                                 next_call=lambda request: forwarded.append(request))
        self.assertEqual(len(forwarded), 1)
        self.assertTrue(self.scout.profile()['events'])
        self.assertEqual(self.scout.profile()['usage']['input_tokens'], 10)
        self.assertFalse(self.home.exists())

    def test_report_only_after_actual_prompt_masking(self):
        self.assertIsNone(self.guard(request=self.request('Hello John Smith')))
        self.assertIsNone(self.scout.notify_output(response_text='ok'))
        self.guard(request=self.request('a@example.com'))
        self.assertIn('masked in the main request', self.scout.notify_output(response_text='ok'))
        self.assertIsNone(self.scout.notify_output(response_text='ok'))
        self.assertIsNone(self.guard(request=self.request('A normal next message')))
        self.assertIsNone(self.scout.notify_output(response_text='ok'))
        self.assertFalse(self.home.exists())

    def test_explicit_off_survives_new_instance_cli_slash_and_menu(self):
        for method in ('cli', 'slash', 'menu'):
            with self.subTest(method=method):
                home = Path(self.temp.name) / method
                scout = Scout(LocalContext(home))
                if method == 'cli':
                    with contextlib.redirect_stdout(io.StringIO()):
                        main(['--home', str(home), 'configure', '--mode', 'off'])
                elif method == 'slash':
                    self.assertIn('Saved:', slash_handler(scout)('masking off', platform='cli'))
                else:
                    menu.run_keys(scout, iter([' ', 's']), lambda screen: None, lambda message: None)
                self.assertTrue((scout.root() / 'settings.json').is_file())
                fresh = Scout(LocalContext(home))
                self.assertEqual(fresh.profile()['config']['mode'], 'off')
                self.assertIsNone(PromptGuard(fresh)(request=self.request('a@example.com')))
                self.assertIsNone(fresh.transform_tool_result(result='a@example.com'))

    def test_saved_mode_and_grants_win_missing_keys_get_defaults(self):
        path = self.scout.root() / 'settings.json'
        for saved in ({'mode': 'off', 'grants': {}, 'sources': {}},
                      {'mode': 'background', 'grants': {'observe': ['support']}, 'sources': {}},
                      {'mode': 'on_demand', 'grants': {'mask': ['profile']}, 'sources': {}}):
            with self.subTest(saved=saved):
                storage.write_json(path, saved)
                self.assertEqual(self.scout.profile()['config'], saved)
                self.assertEqual(json.loads(path.read_text()), saved)
        for saved in ({'mode': 'off'}, {'grants': {}}, {}):
            with self.subTest(missing=saved):
                storage.write_json(path, saved)
                config = self.scout.profile()['config']
                self.assertEqual(config['mode'], saved.get('mode', 'report'))
                self.assertEqual(config['grants'], saved.get('grants', {
                    'observe': ['profile'], 'report': ['profile'], 'mask': ['profile'], 'insights': []}))
                self.assertEqual(json.loads(path.read_text()), saved)

    def test_default_grants_do_not_leak_between_profiles(self):
        self.scout.profile()['config']['grants']['mask'].clear()
        other = Scout(LocalContext(Path(self.temp.name) / 'other'))
        self.assertEqual(other.profile()['config']['grants']['mask'], ['profile'])
        self.assertFalse(self.home.exists())

    def test_existing_saved_empty_insights_grant_keeps_legacy_observation(self):
        saved = dict(mode='report', grants={'observe': ['profile'], 'insights': []}, sources={})
        path = self.scout.root() / 'settings.json'
        storage.write_json(path, saved)
        self.assertEqual(self.scout.profile()['config'], saved)
        self.scout.post_tool_call(tool_name='read_file', status='ok', duration_ms=1)
        self.assertTrue((self.scout.root() / insights.JOURNAL).is_file())
        self.assertEqual(json.loads(path.read_text()), saved)

    def test_fresh_guard_closed_for_errors_oversize_and_unsupported(self):
        request = self.request('a@example.com')
        with patch('plugin.scout.privacy.mask_local_details', side_effect=ValueError('scan failed')):
            self.assertEqual(self.guard(request=request)['request']['messages'][0]['content'], WITHHELD)
            self.assertIn('action=withheld', self.scout.transform_tool_result(result='a@example.com'))
        for content in ('x' * (MAX_INPUT_CHARS + 1), [{'type': 'text', 'text': None}]):
            self.assertEqual(self.guard(request=self.request(content))['request']['messages'][0]['content'], WITHHELD)
        self.assertIn('action=withheld', self.scout.transform_tool_result(result='x' * (MAX_INPUT_CHARS + 1)))
        self.assertFalse(self.home.exists())

    def test_guard_open_is_explicit_persistent_and_profile_scoped(self):
        self.scout.configure_local('report', guard_mode='open')
        reopened = Scout(LocalContext(self.home))
        other = Scout(LocalContext(Path(self.temp.name) / 'closed'))
        with patch('plugin.scout.privacy.mask_local_details', side_effect=ValueError('scan failed')):
            self.assertIsNone(PromptGuard(reopened)(request=self.request('a@example.com')))
            self.assertIsNone(reopened.transform_tool_result(result='a@example.com'))
            self.assertEqual(PromptGuard(other)(request=self.request('a@example.com'))['request']['messages'][0]['content'], WITHHELD)
            self.assertIn('action=withheld', other.transform_tool_result(result='a@example.com'))
        self.assertFalse(other.root().exists())

    def test_individual_system_failure_retains_it_but_whole_step_failure_withholds(self):
        text = 'x' * (MAX_INPUT_CHARS + 1)
        request = {'messages': [{'role': 'system', 'content': text}, {'role': 'user', 'content': 'safe'}]}
        self.assertIsNone(self.guard(request=request))
        with patch.object(self.guard, 'transform', side_effect=ValueError('failed')):
            result = self.guard(request=request)['request']
        self.assertEqual(result['messages'], [{'role': 'user', 'content': WITHHELD}])
        self.assertFalse(self.home.exists())

    def test_saved_defaults_keep_insights_off_until_explicit_enable_and_disable(self):
        handle = slash_handler(self.scout)
        handle('names basic', platform='cli')
        path = self.scout.root() / 'settings.json'
        saved = path.read_bytes()
        self.guard(request=self.request('a@example.com'))
        self.scout.post_tool_call(tool_name='read_file', status='ok', duration_ms=1)
        self.assertEqual(list(self.scout.root().iterdir()), [path])
        self.assertEqual(path.read_bytes(), saved)
        handle('insights on', platform='cli')
        self.scout.post_tool_call(tool_name='read_file', status='ok', duration_ms=1)
        journal = self.scout.root() / insights.JOURNAL
        before = journal.read_bytes()
        handle('insights off', platform='cli')
        self.scout.post_tool_call(tool_name='read_file', status='ok', duration_ms=1)
        self.assertEqual(journal.read_bytes(), before)
