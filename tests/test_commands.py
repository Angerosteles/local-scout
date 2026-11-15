"""Command behavior and fail-closed origin checks, without a running Hermes."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin import register
from plugin.scout import menu
from plugin.scout.commands import HELP, LOCAL_ONLY, NAMES, cli_handler, slash_handler
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout


def session_source(platform):
    """Shape of gateway.session.SessionSource: .platform is an Enum whose .value is the platform name."""
    from types import SimpleNamespace
    return SimpleNamespace(platform=SimpleNamespace(value=platform))


class CommandContext(LocalContext):
    """Registration contract double; not evidence of a real Hermes API signature."""
    def __init__(self, home):
        super().__init__(home)
        self.commands, self.cli_commands = {}, {}

    def register_command(self, name, handler, *, description=''):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=''):
        # Signature of hermes_cli PluginContext.register_cli_command in Hermes 0.23.1.
        self.cli_commands[name] = (handler_fn, setup_fn)


class Commands(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'profile'
        self.scout = Scout(LocalContext(self.home))
        self.handle = slash_handler(self.scout)
        self.local = dict(source='cli', platform='cli')

    def configure(self):
        self.scout.configure_local('report', grants={scope: ['profile'] for scope in menu.SCOPES},
                                   name_masking='basic', address_masking='on', other_languages='off')

    def test_fresh_status_is_actual_state_without_creating_files(self):
        text = self.handle('', **self.local)
        self.assertTrue(text.startswith('Local Scout: masking on\nMasks: e-mail, PESEL'))
        self.assertIn('57–100% detected · 0–2.4 false masks per 100 texts', text)
        self.assertIn('Names: off (optional; only in your prompts)', text)
        self.assertIn('insights off · reply note on', text)
        self.assertIn('/scout details', text)
        self.assertEqual(len(text.splitlines()), 6)
        self.assertTrue(all(len(line) <= 80 for line in text.splitlines()))
        self.assertEqual(self.handle('status', **self.local), text)
        self.assertFalse(self.home.exists())
        self.handle('masking off', **self.local)
        off = self.handle('', **self.local)
        self.assertTrue(off.startswith('Local Scout: masking off — nothing is masked'))
        self.assertNotIn('detected', off)

    def test_details_from_any_session_without_writing(self):
        for context in (self.local, dict(platform='discord'), {}):
            text = self.handle('details', **context)
            self.assertIn('Local Scout · measurements', text)
            self.assertIn('deep PL           112/191   59%  247/410   60%  48.7', text)
            self.assertNotIn(LOCAL_ONLY, text)
        self.assertFalse(self.home.exists())

    def test_each_setting_and_value(self):
        for name, option in NAMES.items():
            for value in menu.OPTIONS['names'] if option == 'names' else ('on', 'off'):
                with self.subTest(name=name, value=value):
                    self.configure()
                    text = self.handle(name + ' ' + value, **self.local)
                    config = self.scout.profile()['config']
                    key = {'names': 'name_masking', 'addresses': 'address_masking',
                           'languages': 'other_languages'}.get(option)
                    if key:
                        self.assertEqual(config[key], value)
                    elif option == 'mode':
                        self.assertEqual(config['mode'], 'report' if value == 'on' else 'background')
                    else:
                        scope = 'mask' if option == 'masking' else 'insights'
                        self.assertEqual('profile' in config['grants'].get(scope, []), value == 'on')
                    self.assertTrue(text == 'No changes.' or text.startswith('Saved: '), text)
                    if text.startswith('Saved: '):
                        self.assertIn('Active from your next message — no restart needed.', ' '.join(text.split()))
        self.configure()
        self.assertTrue(self.handle('note off', **self.local).startswith('Saved: reply note on → off.'))

    def test_note_keeps_triage_mode(self):
        self.scout.configure_local('on_demand', grants={scope: ['profile'] for scope in ('observe', 'report', 'mask')})
        self.assertIn('reply note off (triage)', ' '.join(self.handle('', **self.local).split()))
        self.assertEqual(self.handle('note off', **self.local), 'No changes.')
        self.assertEqual(self.scout.profile()['config']['mode'], 'on_demand')
        self.assertTrue(self.handle('note on', **self.local).startswith('Saved: reply note off (triage) → on.'))
        self.assertEqual(self.scout.profile()['config']['mode'], 'report')

    def test_exact_summary_invalid_help_and_no_rewrite(self):
        self.configure()
        self.assertEqual(self.handle('names deep', **self.local),
                         'Saved: names basic → deep. Active from your next message — no restart needed.\n'
                         'Deep is aggressive; noisy on Polish text: ≈49 false names per 100 PL texts.')
        path = self.scout.root() / 'settings.json'
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        for args in ('names zzz', 'names deep extra', 'masking yes', 'mode off', 'note maybe', 'unknown', 'status extra', 'help extra'):
            self.assertIn('/scout', self.handle(args, **self.local))
        self.assertEqual(self.handle('names zzz', **self.local), 'Usage: /scout names off|basic|deep')
        self.assertIn(HELP, self.handle('help', **self.local))
        self.assertEqual(self.handle('names deep', **self.local), 'No changes.\n' + menu.DEEP_WARNING)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_unknown_missing_and_gateway_context_cannot_write(self):
        self.configure()
        path = self.scout.root() / 'settings.json'
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        contexts = ({}, dict(source='gateway'), dict(platform='discord'),
                    dict(source='gateway', platform='cli'), dict(source='cli', platform='telegram'),
                    dict(source='unknown', platform='tui'), dict(source=object(), platform='cli'),
                    dict(source='', platform=''), dict(source='CLI', platform=None), dict(source='cli'),
                    dict(source=session_source('discord'), platform='local'), dict(platform='api_server'),
                    dict(source=session_source('local'), platform='discord'))
        for context in contexts:
            for args in ('names deep', 'masking off', 'note off', '', 'status', 'help'):
                with self.subTest(context=context, args=args):
                    text = self.handle(args, **context)
                    self.assertIn('Local Scout:', text)
                    self.assertIn(LOCAL_ONLY, text)
                    self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_explicit_local_origins(self):
        # Hermes 0.23.1: classic CLI (platform='cli', source=None) and clients attached to the local gateway
        # (platform='local', SessionSource with Platform.LOCAL).
        for context in (dict(platform='cli'), dict(source=None, platform='cli'), dict(source='tui', platform='cli'),
                        dict(source='tui', platform='tui'), dict(source=session_source('local'), platform='local')):
            with self.subTest(context=context):
                self.configure()
                self.assertTrue(self.handle('names deep', **context).startswith('Saved: '))

    def test_fresh_refusal_does_not_create_state(self):
        for context in ({}, dict(source='gateway', platform='discord')):
            self.assertIn(LOCAL_ONLY, self.handle('masking on', **context))
        self.assertFalse(self.home.exists())

    def test_single_option_preserves_grants_and_unrelated_configuration(self):
        grants = dict(observe=['support'], report=['support'], mask=['profile', 'support'],
                      insights=['other'], answer=['support'], train=['dataset'])
        self.scout.configure_local('report', grants=grants, retention_days=7, synthetic_pii=True,
                                   guard_mode='open', answer_task='triage', answer_marker='Ticket:')
        for args in ('names deep', 'addresses off', 'languages on', 'note off'):
            self.handle(args, **self.local)
            self.assertEqual(self.scout.profile()['config']['grants'], grants)
        config = self.scout.profile()['config']
        self.assertEqual((config['retention_days'], config['synthetic_pii'], config['guard_mode'],
                          config['answer_task'], config['answer_marker']), (7, True, 'open', 'triage', 'Ticket:'))
        self.handle('insights on', **self.local)
        self.assertEqual(self.scout.profile()['config']['grants']['insights'], ['other', 'profile'])
        self.handle('masking off', **self.local)
        config = self.scout.profile()['config']
        self.assertEqual(config['mode'], 'off')
        self.assertEqual(config['grants']['mask'], ['support'])
        self.assertEqual(config['grants']['insights'], ['other', 'profile'])
        self.assertEqual(config['grants']['answer'], ['support'])

    def test_fresh_option_edit_preserves_active_defaults(self):
        self.handle('names deep', **self.local)
        config = self.scout.profile()['config']
        self.assertEqual(config['mode'], 'report')
        grants = {scope: ['profile'] for scope in ('observe', 'report', 'mask')}
        grants['insights'] = []
        self.assertEqual(config['grants'], grants)
        self.handle('note off', **self.local)
        self.assertEqual(self.scout.profile()['config']['mode'], 'background')
        self.assertEqual(self.scout.profile()['config']['grants'], grants)
        text = self.handle('masking on', **self.local)
        self.assertEqual(text, 'No changes.')
        self.assertEqual(self.scout.profile()['config']['mode'], 'background')

    def test_existing_guard_reloads_slash_changes_without_restart(self):
        from plugin.scout.prompt_guard import PromptGuard
        self.configure()
        guard = PromptGuard(Scout(LocalContext(self.home)))
        with patch('plugin.scout.prompt_guard.privacy.mask_local_details', return_value=('masked', [])) as mask:
            guard.scan('synthetic text', names=True)
            self.assertEqual(mask.call_args.kwargs['name_masking'], 'basic')
            for args in ('names deep', 'addresses off', 'languages on'):
                self.handle(args, **self.local)
            guard.scan('synthetic text', names=True)
            self.assertEqual(mask.call_count, 2)
            self.assertEqual({k: mask.call_args.kwargs[k] for k in
                              ('name_masking', 'address_masking', 'other_languages')},
                             dict(name_masking='deep', address_masking='off', other_languages='on'))

    def test_registration_is_optional_and_handlers_are_not_tools(self):
        old = LocalContext(self.home)
        register(old)
        self.assertFalse(self.home.exists())
        ctx = CommandContext(self.home)
        register(ctx)
        self.assertIn('scout', ctx.commands)
        self.assertIn('scout', ctx.cli_commands)
        self.assertEqual(set(ctx.tools), set(old.tools))
        self.assertNotIn(ctx.commands['scout'], [entry['handler'] for entry in ctx.tools.values()])
        self.assertFalse(self.home.exists())

    def test_cli_no_args_and_setup_use_existing_menu_with_same_home(self):
        ctx = CommandContext(self.home)
        register(ctx)
        handler, setup = ctx.cli_commands['scout']
        parser = argparse.ArgumentParser()
        setup(parser)
        for tail in ([], ['setup'], ['menu'], ['configure']):
            with self.subTest(tail=tail), patch('sys.stdin.isatty', return_value=True), \
                 patch('sys.stdout.isatty', return_value=True), patch.object(menu, 'run') as run:
                handler(parser.parse_args(tail))
                scout, home = run.call_args.args
                self.assertEqual(home, self.home)
                self.assertEqual(scout.ctx.home, self.home)

    def test_registration_apis_are_independently_optional(self):
        for missing, expected in (('register_command', 'cli_commands'),
                                  ('register_cli_command', 'commands')):
            ctx = CommandContext(self.home)
            setattr(ctx, missing, None)
            register(ctx)
            self.assertIn('scout', getattr(ctx, expected))
        self.assertFalse(self.home.exists())

    def test_cli_forwards_configure_and_status_to_existing_parser(self):
        parser = argparse.ArgumentParser()
        from plugin.scout.commands import setup_parser
        setup_parser(parser)
        handler = cli_handler(self.scout)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            handler(parser.parse_args(['configure', '--mode', 'report', '--name-masking', 'deep',
                                       '--approve', 'mask@profile']))
        self.assertEqual(json.loads(output.getvalue())['status'], 'CONFIGURED')
        self.assertEqual(self.scout.profile()['config']['name_masking'], 'deep')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            handler(parser.parse_args(['status']))
        self.assertIn('report', output.getvalue())

    def test_cli_uses_active_hermes_profile_at_invocation(self):
        import sys
        from types import SimpleNamespace
        scout = Scout(object())
        handler = cli_handler(scout)
        for home in (self.home, self.home / 'second'):
            with patch.dict(sys.modules, {'hermes_constants': SimpleNamespace(get_hermes_home=lambda: home)}), \
                 patch('plugin.cli.main') as main:
                handler(argparse.Namespace(scout_args=['status']))
                main.assert_called_once_with(['--home', str(home), 'status'])

    def test_write_error_has_plain_response(self):
        with patch.object(self.scout, 'configure_local', side_effect=OSError('private path')):
            text = self.handle('names deep', **self.local)
        self.assertIn('could not be saved', text)
        self.assertNotIn('private path', text)
