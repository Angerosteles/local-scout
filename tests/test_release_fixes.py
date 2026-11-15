"""Final release regressions: consent, mixed content, name rules and disclosures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin.scout import insights, menu, name_model, privacy, storage, tool_guard
from plugin.scout.commands import slash_handler, status
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout.prompt_guard import PromptGuard, WITHHELD


class ReleaseFixes(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scout = Scout(LocalContext(Path(temporary.name)/'profile'))
        self.guard = PromptGuard(self.scout)

    def activity(self):
        self.guard(request={'messages': [{'role': 'user', 'content': 'a@example.com'}]})
        self.scout.observe(api_request_id='r', request_messages=[{'role': 'user', 'content': 'b@example.com'}])
        self.scout.transform_tool_result(result='c@example.com')
        self.scout.transform_terminal_output(output='d@example.com')
        self.scout.post_tool_call(tool_name='read_file', status='ok', duration_ms=1)
        insights.record_tool(self.scout, 'scout_report', {'ok': True}, 1, 100, session_id='s')
        insights.observe_usage_hook(self.scout)
        self.scout.usage_hook(api_request_id='u', session_id='s', usage={'prompt_tokens': 10, 'completion_tokens': 2})
        insights.record_local_answer(self.scout, False, 'task')

    def test_report_tool_on_pristine_profile_never_creates_files(self):
        self.activity()
        self.assertTrue(self.scout.call('scout_report', {'dataset_id': 'profile'})['ok'])
        self.assertFalse(self.scout.root().exists())

    def test_mask_only_grants_cannot_enable_any_persistence(self):
        self.scout.configure_local('report', grants={'mask': ['profile']})
        self.assertFalse(insights.collection_enabled(self.scout.profile()['config']))
        self.activity()
        self.assertEqual([p.name for p in self.scout.root().iterdir()], ['settings.json'])
        self.assertTrue(self.scout.profile()['events'])

    def test_grants_and_opt_out_both_required(self):
        for grants in ({}, {'mask': ['profile']}, {'observe': []}, {'insights': []},
                       {'observe': ['profile']}, {'insights': ['profile']}):
            for persist in (True, False):
                with self.subTest(grants=grants, persist=persist):
                    config = dict(grants=grants, persist_observation=persist)
                    self.assertEqual(insights.collection_enabled(config),
                                     persist and bool(grants.get('observe') or grants.get('insights')))

    def test_legacy_status_and_off_disable_all_writes_across_restart(self):
        saved = dict(mode='report', grants={'observe': ['profile'], 'report': ['profile'],
                                          'mask': ['profile'], 'insights': []}, sources={})
        storage.write_json(self.scout.root()/'settings.json', saved)
        self.assertTrue(menu.state_for(self.scout)['insights'])
        self.assertIn('insights on', status(self.scout))
        slash_handler(self.scout)('insights off', platform='cli')
        self.assertIs(self.scout.profile()['config']['persist_observation'], False)
        self.scout = Scout(self.scout.ctx)
        self.guard = PromptGuard(self.scout)
        self.activity()
        self.scout.call('scout_report', {'dataset_id': 'profile'})
        self.assertFalse(menu.state_for(self.scout)['insights'])
        self.assertEqual([p.name for p in self.scout.root().iterdir()], ['settings.json'])

    def test_off_always_saves_explicit_opt_out_and_preserves_existing_counters(self):
        handle = slash_handler(self.scout)
        self.assertIn('Saved:', handle('insights off', platform='cli'))
        self.assertIs(self.scout.profile()['config']['persist_observation'], False)
        handle('insights on', platform='cli')
        self.activity()
        files = {p.name: p.read_bytes() for p in self.scout.root().iterdir() if p.name != 'settings.json'}
        self.assertIn('aggregates.json', files)
        self.assertIn(insights.JOURNAL, files)
        handle('insights off', platform='cli')
        self.activity()
        self.scout.call('scout_report', {'dataset_id': 'profile'})
        self.assertEqual(files, {p.name: p.read_bytes() for p in self.scout.root().iterdir() if p.name != 'settings.json'})

    def test_mixed_blocks_all_roles_and_native_responses_leave_payloads_untouched(self):
        for role in ('user', 'tool', 'assistant', 'system'):
            for field in ('messages', 'input'):
                with self.subTest(role=role, field=field):
                    blocks = [{'type': 'input_text', 'text': 'a@example.com'},
                              {'type': 'input_image', 'image_url': 'opaque@example.com'},
                              {'type': 'input_audio', 'data': 'opaque@example.com'},
                              {'type': 'input_file', 'file_data': 'opaque@example.com'},
                              {'type': 'future_block', 'payload': 'opaque@example.com'}]
                    request = {field: [{'role': role, 'content': blocks}]}
                    before = copy.deepcopy(request)
                    output = self.guard(request=request)['request'][field][0]['content']
                    self.assertEqual(request, before)
                    self.assertIn('[EMAIL]', output[0]['text'])
                    self.assertEqual(output[1:5], blocks[1:])
                    if role in ('user', 'tool'):
                        self.assertIn('unscanned=audio,file,image,non_text', output[-1]['text'])

    def test_image_only_and_nested_tool_results_pass_with_annotation(self):
        image = {'type': 'image', 'source': {'data': 'opaque@example.com'}}
        for content in ([image], [{'type': 'tool_result', 'content': [
                {'type': 'text', 'text': 'a@example.com'}, image]}]):
            output = self.guard(request={'messages': [{'role': 'user', 'content': content}]})
            self.assertIn('unscanned=image', str(output))
            self.assertIn('opaque@example.com', str(output))
            self.assertNotIn('a@example.com', str(output))
            self.assertNotIn('withheld', str(output))
        messages = [{'role': 'user', 'content': [image]}]
        self.guard.auxiliary(request_messages=messages)
        self.assertEqual(messages[0]['content'][0], image)

    def test_tool_mcp_native_and_json_blocks_preserve_nontext(self):
        blocks = [{'type': 'text', 'text': 'a@example.com'},
                  {'type': 'image', 'data': 'opaque@example.com'}]
        for value in (blocks, {'content': blocks}, json.dumps({'content': blocks})):
            before = copy.deepcopy(value)
            output = self.scout.transform_tool_result(result=value)
            self.assertEqual(value, before)
            self.assertIn('unscanned=image', str(output))
            self.assertIn('[EMAIL]', str(output))
            self.assertNotIn('a@example.com', str(output))
            parsed = json.loads(output) if isinstance(output, str) else output
            content = parsed['content'] if isinstance(parsed, dict) else parsed
            self.assertEqual(content[1], blocks[1])
        binary = [{'type': 'audio', 'data': b'opaque'}]
        output = self.scout.transform_tool_result(result=binary)
        self.assertEqual(output[0], binary[0])
        self.assertIn('unscanned=audio', output[-1]['text'])

    def test_text_errors_still_closed_with_nontext_and_open_is_preserved(self):
        blocks = [{'type': 'text', 'text': 'a@example.com'}, {'type': 'image', 'data': 'opaque'}]
        request = {'messages': [{'role': 'user', 'content': blocks}]}
        with patch.object(privacy, 'mask_local_details', side_effect=RuntimeError('failed')):
            self.assertEqual(self.guard(request=request)['request']['messages'][0]['content'], WITHHELD)
            self.assertIn('action=withheld', self.scout.transform_tool_result(result=blocks))
            self.scout.configure_local('report', guard_mode='open')
            self.assertIsNone(self.guard(request=request))
            self.assertIsNone(self.scout.transform_tool_result(result=blocks))

    def test_polish_honorific_works_without_lexical_name_boost(self):
        for title in ('Pani', 'Pan'):
            text = title + ' Zelvarowi dziękuję za raport.'
            with patch.object(name_model.LegacyScore, 'score', return_value=({0: 0., 1: 0.}, .45, .02)):
                result = name_model.spans(text)
            self.assertEqual([text[s:e] for s, e, _ in result], ['Zelvarowi'])
        self.assertFalse(hasattr(name_model, '_GIVEN'))
        self.assertFalse(hasattr(name_model, '_PL_FORMS'))

    def test_docs_disclose_current_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root/'README.md').read_text()
        security = (root/'docs/SECURITY.md').read_text()
        evidence = (root/'docs/EVIDENCE.md').read_text()
        for text in (readme, security):
            self.assertIn('other non-text content are passed through unscanned', text)
            self.assertIn('counters only with Insights on', text)
            self.assertNotIn('English, capitalised ASCII names only', text)
        self.assertIn('| Data | User prompt | Tool / MCP result |', readme)
        self.assertIn('changes the mode to `on_demand`', readme)
        self.assertIn('unmasked candidate lines', readme)
        self.assertIn('≈49 false names per 100 PL texts', readme)
        self.assertNotIn('near-exact', readme)
        self.assertNotIn('[HOLDOUT_LOCATION]', evidence)
        self.assertIn('link in the release notes', evidence)
        for sha in ('3068644', '3fb45e5', '6ec6afe', 'c07b215'):
            self.assertNotIn(sha, readme + evidence + security)

    def test_insights_off_with_other_dataset_grants_survives_unrelated_changes(self):
        self.scout.configure_local('report', grants={'mask': ['profile'], 'observe': ['profile'],
                                                    'insights': ['support']})
        handle = slash_handler(self.scout)
        handle('insights off', platform='cli')
        self.assertIs(self.scout.profile()['config']['persist_observation'], False)
        for command in ('names deep', 'addresses off', 'languages on', 'mode background'):
            handle(command, platform='cli')
            self.assertIs(self.scout.profile()['config']['persist_observation'], False)
        self.activity()
        self.assertEqual([p.name for p in self.scout.root().iterdir()], ['settings.json'])
        self.assertEqual(self.scout.profile()['config']['grants']['insights'], ['support'])

    def test_nontext_annotation_is_idempotent_and_unknown_tool_blocks_are_opaque(self):
        block = {'type': 'future_block', 'data': b'opaque'}
        request = {'messages': [{'role': 'user', 'content': [block]}]}
        first = self.guard(request=request)['request']
        self.assertIsNone(self.guard(request=first))
        output = self.scout.transform_tool_result(result={'content': [block]})
        self.assertEqual(output['content'][0], block)
        self.assertIn('unscanned=non_text', output['content'][-1]['text'])

    def test_handle_counts_respect_insights_off_without_training(self):
        self.scout.configure_local('report', grants={scope: ['demo'] for scope in ('observe', 'train', 'report')},
                                   persist_observation=False)
        self.scout.profile()['active_dataset'] = 'demo'
        storage.write_json(self.scout.root()/'choices.json', {'fields': {
            'dataset_id': 'demo', 'recipe_id': 'regex', 'decision_id': 'd'}})
        storage.write_json(self.scout.root()/'decisions.json', [dict(
            dataset_id='demo', candidate_id='fields', decision_id='d')])
        from plugin.scout.operator import digest
        calibration = dict(dataset_id='demo', input_digest=digest([]), parameters={'tokens_per_task': 10})
        storage.write_json(self.scout.root()/'calibration.json', calibration)
        with patch('plugin.scout.calibration_output.read_details', return_value=calibration), \
             patch.object(self.scout, 'decision_rows', return_value=[]):
            result = self.scout.scout_handle('fields', 'a@example.com')
        self.assertEqual(result['status'], 'handled_locally')
        self.assertFalse((self.scout.root()/'handle-telemetry.json').exists())

    def test_removed_given_name_boost_no_longer_forces_detection(self):
        for text in ('Jan Kowalski', 'Janem Nowakiem', 'Kasiu, proszę o kontakt.'):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text, names=True, name_masking='deep')[0], text)

    def test_menu_equivalent_command_keeps_other_grants_and_explicit_off(self):
        import contextlib
        import io
        import shlex
        from plugin.cli import main
        self.scout.configure_local('report', grants={'mask': ['profile'], 'observe': ['profile'],
                                                    'insights': ['support']})
        previous = menu.state_for(self.scout)
        state = dict(previous, insights=False)
        _, command = menu.save_state(self.scout, state, previous, changed='insights')
        other_home = self.scout.ctx.home / 'equivalent'
        with contextlib.redirect_stdout(io.StringIO()):
            main(['--home', str(other_home), *shlex.split(command)[1:]])
        other = Scout(LocalContext(other_home))
        self.assertEqual(other.profile()['config'], self.scout.profile()['config'])

    def test_large_image_payload_is_unscanned_but_large_text_is_withheld(self):
        image = {'type': 'image', 'data': 'A' * (tool_guard.MAX_INPUT_CHARS + 1)}
        for value in ([image], json.dumps({'content': [image]})):
            output = self.scout.transform_tool_result(result=value)
            parsed = json.loads(output) if isinstance(output, str) else output
            content = parsed['content'] if isinstance(parsed, dict) else parsed
            self.assertEqual(content[0], image)
            self.assertIn('unscanned=image', str(output)[-300:])
        value = json.dumps({'content': [{'type': 'text', 'text': 'x' * (tool_guard.MAX_INPUT_CHARS + 1)}, image]})
        self.assertIn('action=withheld', self.scout.transform_tool_result(result=value))

    def test_cli_status_reports_effective_legacy_insights(self):
        import contextlib
        import io
        from plugin.cli import main
        storage.write_json(self.scout.root()/'settings.json', dict(
            mode='report', grants={'observe': ['support'], 'insights': []}, sources={}))
        for expected in (True, False):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(['--home', str(self.scout.ctx.home), 'status'])
            self.assertIs(json.loads(output.getvalue())['insights'], expected)
            slash_handler(self.scout)('insights off', platform='cli')
