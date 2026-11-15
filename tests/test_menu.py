import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from plugin.cli import main
from plugin.scout import menu
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout

PLUGIN = Path(__file__).resolve().parents[1]


class Menu(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'
        self.scout = Scout(LocalContext(self.home))

    def tearDown(self):
        self.temp.cleanup()

    def drive(self, keys):
        screens, messages = [], []
        menu.run_keys(self.scout, iter(keys), screens.append, messages.append)
        return screens, messages

    def configured(self):
        self.scout.configure_local('report', grants={scope: ['profile'] for scope in menu.SCOPES},
                                   name_masking='basic', address_masking='on', other_languages='off')

    def test_enter_space_and_arrows_change_without_saving(self):
        # Names default to off: forward goes to basic, backward wraps to deep.
        for key, expected in (('\r', 'basic'), ('\n', 'basic'), (' ', 'basic'),
                              ('right', 'basic'), ('left', 'deep')):
            with self.subTest(key=key):
                state = menu.state_for(self.scout)
                self.assertEqual(menu.transition(state, 1, key), (1, None))
                self.assertEqual(state['names'], expected)
                screens, messages = self.drive(['down', key])
                self.assertIn(f'(•) {expected}', screens[-1])
                self.assertEqual(messages, ['Cancelled. Changes discarded.'])
                self.assertFalse((self.scout.root() / 'settings.json').exists())
        state = menu.state_for(self.scout)
        menu.transition(state, 0, '\n')
        self.assertFalse(state['masking'])
        menu.transition(state, 5, 'right')
        self.assertEqual(state['mode'], 'background')
        menu.transition(state, 5, 'left')
        self.assertEqual(state['mode'], 'report')
        menu.transition(state, 1, 'left')
        menu.transition(state, 1, 'left')
        self.assertEqual(state['names'], 'basic')

    def test_save_defaults(self):
        screens, messages = self.drive(['down'] * 7 + ['\n'])
        config = self.scout.profile()['config']
        self.assertEqual((config['mode'], config['name_masking'], config['address_masking'],
                          config['other_languages']), ('report', 'off', 'on', 'off'))
        for scope in ('observe', 'report', 'mask'):
            self.assertEqual(config['grants'][scope], ['profile'])
        self.assertEqual(config['grants']['insights'], [])
        self.assertIn('Masking          (•) on', screens[0])
        self.assertIn('Insights         ( ) on  (•) off', screens[0])
        self.assertIn('Saved: profile defaults.', messages[0])
        self.assertIn('next message — no restart needed', messages[0])
        self.assertFalse(any('system-prompt section' in m for m in messages))
        self.assertTrue(messages[1].startswith('Same as: scout configure'))
        other = Path(self.temp.name) / 'equivalent-defaults'
        with contextlib.redirect_stdout(io.StringIO()):
            main(['--home', str(other)] + shlex.split(messages[1].removeprefix('Same as: '))[1:])
        self.assertEqual(Scout(LocalContext(other)).profile()['config'], config)

    def test_save_summary_and_equivalent_command_preserve_grants(self):
        self.scout.configure_local('report', grants={'mask': ['profile', 'support'], 'answer': ['support']})
        _, messages = self.drive(['down', '\n', 'down', ' ', 'down', 'right', 's'])
        config = self.scout.profile()['config']
        self.assertEqual(config['grants']['mask'], ['support', 'profile'])
        self.assertEqual(config['grants']['answer'], ['support'])
        self.assertEqual((config['name_masking'], config['address_masking'], config['other_languages']),
                         ('basic', 'off', 'on'))
        self.assertTrue(all(len(line) <= 80 for line in messages[0].splitlines()))
        self.assertEqual(' '.join(messages[0].split()), 'Saved: names off → basic, addresses on → off, languages off → on. '
                         'Active from your next message — no restart needed.')
        self.assertFalse(any('new session' in message for message in messages))
        other = Path(self.temp.name) / 'equivalent'
        with contextlib.redirect_stdout(io.StringIO()):
            main(['--home', str(other)] + shlex.split(messages[1].removeprefix('Same as: '))[1:])
        self.assertEqual(Scout(LocalContext(other)).profile()['config'], config)

    def test_masking_off_keeps_other_grants_and_session_note(self):
        self.scout.configure_local('report', grants={'mask': ['profile', 'support'], 'answer': ['support']})
        _, messages = self.drive([' ', 's'])
        config = self.scout.profile()['config']
        self.assertEqual(config['mode'], 'off')
        self.assertEqual(config['grants']['mask'], ['support'])
        self.assertEqual(config['grants']['answer'], ['support'])
        self.assertIn('masking on → off', messages[0])
        self.assertIn('new session', messages[1])

    def test_quit_cancel_and_escape_prompt_save_or_discard(self):
        self.configured()
        original = (self.scout.root() / 'settings.json').read_bytes()
        for quit_keys in (['q'], ['\x1b'], ['up', 'up', '\n']):
            for answer in ('y', 'n'):
                with self.subTest(keys=quit_keys, answer=answer):
                    self.configured()
                    screens, messages = self.drive(['down', ' '] + quit_keys + [answer])
                    self.assertIn('Save changes? [y/n]', screens[-1])
                    if answer == 'y':
                        self.assertEqual(self.scout.profile()['config']['name_masking'], 'deep')
                        self.assertTrue(messages[0].startswith('Saved: names basic → deep.'))
                    else:
                        self.assertEqual((self.scout.root() / 'settings.json').read_bytes(), original)
                        self.assertEqual(messages, ['Cancelled. Changes discarded.'])

    def test_confirmation_ignores_enter_and_s_and_escape_resumes(self):
        self.configured()
        screens, messages = self.drive(['down', ' ', 'q', '\n', 's', '\x1b', 'left', 's'])
        self.assertTrue(all('Save changes? [y/n]' in s for s in screens[3:6]))
        self.assertNotIn('Save changes? [y/n]', screens[6])
        self.assertEqual(messages, ['No changes.'])

    def test_no_changes_does_not_write(self):
        self.configured()
        for keys in (['s'], ['q'], ['\x1b'], ['up', '\n'], ['down'] * 7 + ['\n'], ['down'] * 6 + ['\n', 'q', 'q']):
            with self.subTest(keys=keys), patch.object(self.scout, 'configure_local') as save:
                self.assertEqual(self.drive(keys)[1], ['No changes.'])
                save.assert_not_called()

    def test_fresh_profile_quit_reports_no_changes(self):
        for keys in (['q'], ['\x1b'], ['up', '\n'], []):
            screens, messages = self.drive(keys)
            self.assertNotIn('Save changes? [y/n]', screens[-1])
            self.assertEqual(messages, ['No changes.'])
            self.assertFalse(self.scout.root().exists())

    def test_interrupt_and_end_of_input_do_not_save(self):
        def interrupted():
            yield ' '
            raise KeyboardInterrupt
        for keys in ([' ', '\x03'], [' '], interrupted()):
            self.assertEqual(self.drive(keys)[1], ['Cancelled. Changes discarded.'])
            self.assertFalse((self.scout.root() / 'settings.json').exists())

    def test_non_tty_and_configure(self):
        for command in ([], ['menu'], ['setup']):
            out = io.StringIO()
            with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(out):
                main(['--home', str(self.home)] + command)
            self.assertEqual(out.getvalue().strip(), 'Interactive mode requires a terminal.')
            self.assertFalse((self.scout.root() / 'settings.json').exists())
        with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['--home', str(self.home), 'configure'])
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=False), \
             patch.object(menu, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            main(['--home', str(self.home), 'setup'])
            run.assert_not_called()

    def test_tty_cli_routes_to_menu(self):
        for command in ([], ['setup'], ['menu'], ['configure']):
            with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
                 patch.object(menu, 'run') as run:
                main(['--home', str(self.home)] + command)
                self.assertEqual(run.call_args.args[1], self.home)

    def test_screen_width_and_radio_choices(self):
        state = menu.state_for(self.scout)
        for width in (1, 12, 24, 40, 67, 68, 79, 80, 120):
            for names in menu.OPTIONS['names']:
                with self.subTest(width=width, names=names):
                    state['names'] = names
                    screen = menu.render(state, 1, width=width, confirming=True)
                    self.assertTrue(all(len(line) <= min(80, width) for line in screen.splitlines()))
                    details = menu.render_details(width)
                    self.assertTrue(all(len(line) <= min(80, width) for line in details.splitlines()))
                    if width >= 68:
                        self.assertIn(f'(•) {names}', screen)
                        self.assertIn('57–100% detected · 0–2.4 false masks per 100 texts', screen)
        state['names'] = 'off'
        screen = menu.render(state, 5)
        self.assertIn('Reply note       (•) on  ( ) off', screen)
        self.assertIn('Names: off (optional; only in your prompts)', screen)
        self.assertIn(menu.HELP, screen)
        self.assertIn('full names 59–80% detected · 3.7–6.0 false masks', menu.render(dict(state, names='basic')))
        self.assertIn('all names 60–64% detected · 8.4–48.7 false masks', menu.render(dict(state, names='deep')))
        without = menu.render(dict(state, addresses=False))
        self.assertNotIn('address', without.split('\n\n')[0])
        self.assertIn('68–100% detected', without)
        self.assertIn('Masking off: nothing is masked', menu.render(dict(state, masking=False)))
        table = menu.render_details()
        for row in ('e-mail            200/200  100%   2.4', 'address           114/200   57%   0.7',
                    'password/PIN/key  136/200   68%   0.9', 'basic PL          112/191   59%  176/410   43%  6.0',
                    'deep PL           112/191   59%  247/410   60%  48.7'):
            self.assertIn(row, table)
        self.assertLessEqual(len(table.splitlines()), 23)

    def test_measurements_match_evidence(self):
        evidence = (PLUGIN / 'docs' / 'EVIDENCE.md').read_text()
        rows = {line.split('|')[1].strip(): [c.strip() for c in line.split('|')[2:-1]]
                for line in evidence.splitlines() if line.startswith('| ') and line.count('|') > 4}
        key = {'e-mail': 'email', 'card number': 'card', 'password/PIN/key': 'secret'}
        false = dict(zip(('Name', 'E-mail', 'Secret', 'Address', 'Phone', 'Card'), rows['1.8.0 basic'][2:]))
        for name, masked, false_masks in menu.STRUCTURED:
            with self.subTest(name=name):
                self.assertTrue(rows[key.get(name, name.lower())][1].startswith(f'{masked}/200 '))
                column = {'e-mail': 'E-mail', 'password/PIN/key': 'Secret', 'card number': 'Card'}.get(name, name.title())
                expected = int(false.get(column, 0)) + (1 if name in ('SSN', 'password/PIN/key') else 0)
                self.assertEqual(false_masks, expected)
        self.assertIn('one SSN span and one PIN span', evidence)
        for mode, column in (('basic', 1), ('deep', 2)):
            for lang, (full, every, false_names) in menu.NAMES[mode].items():
                total_full, total_all, texts = menu.NAME_TOTALS[lang]
                self.assertEqual(rows[f'{lang} full'][column], f'{full}/{total_full}')
                self.assertEqual(rows[f'{lang} all names'][column], f'{every}/{total_all}')
                self.assertTrue(rows[f'{lang} visible false `[NAME]` ({texts} texts)'][column].startswith(f'{false_names} '))

    def test_details_view_opens_and_returns(self):
        self.configured()
        for back in ('\x1b', 'q', '\n', ' '):
            with self.subTest(back=back):
                screens, messages = self.drive(['down'] * 6 + ['\n', back, 's'])
                self.assertIn('Local Scout · measurements', screens[-2])
                self.assertIn(menu.DETAILS_HELP, screens[-2])
                self.assertIn('Local Scout setup', screens[-1])
                self.assertEqual(messages, ['No changes.'])
        with patch.dict('sys.modules', {'curses': None}), patch('builtins.input', side_effect=['7', '', 'q']), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            menu.run(self.scout, self.home)
        self.assertIn('\n  e-mail            200/200', out.getvalue())
        self.assertTrue(out.getvalue().endswith('No changes.\n'))

    def test_triage_mode_shows_note_off_and_switches_on(self):
        self.scout.configure_local('on_demand', grants={scope: ['profile'] for scope in ('observe', 'report', 'mask')})
        state = menu.state_for(self.scout)
        self.assertEqual(state['mode'], 'on_demand')
        self.assertIn('Reply note       ( ) on  (•) off  · triage mode', menu.render(state, 5))
        self.assertIn('Triage mode', menu.render(state, 5))
        menu.transition(state, 5, '\n')
        self.assertEqual(state['mode'], 'report')
        _, messages = self.drive(['down'] * 5 + ['\n', 's'])
        self.assertIn('reply note off (triage) → on', messages[0])
        self.assertEqual(self.scout.profile()['config']['mode'], 'report')

    def test_curses_keys_and_small_screens(self):
        import curses
        for height, width in ((24, 80), (12, 40), (8, 24), (3, 6)):
            with self.subTest(height=height, width=width):
                self.configured()
                initial = menu.state_for(self.scout)
                frames = []
                class Screen:
                    def __init__(self):
                        self.keys = iter([ord('j'), curses.KEY_ENTER, curses.KEY_LEFT, curses.KEY_RIGHT,
                                          curses.KEY_DOWN, ord(' '), 27, ord('y')])
                        self.lines = {}
                    def keypad(self, enabled): self.enabled = enabled
                    def clear(self): self.lines = {}
                    def getmaxyx(self): return height, width
                    def addnstr(self, y, x, line, count, attr):
                        self.lines[y] = line[:count]
                        self.assert_bounds(y, line, count)
                    def assert_bounds(self, y, line, count):
                        assert y < height and len(line) <= count
                    def refresh(self): frames.append(list(self.lines.values()))
                    def getch(self): return next(self.keys)
                with patch.object(curses, 'wrapper', side_effect=lambda fn: fn(Screen())), \
                     patch.object(curses, 'curs_set'), contextlib.redirect_stdout(io.StringIO()):
                    menu.run(self.scout, self.home)
                self.assertEqual(self.scout.profile()['config']['name_masking'], 'deep')
                self.assertEqual(self.scout.profile()['config']['address_masking'], 'off')
                if (height, width) == (24, 80):
                    self.assertEqual('\n'.join(frames[0]), menu.render(initial, width=79))
                if height >= 8:
                    self.assertTrue(any('Save changes? [y/n]' in '\n'.join(f) for f in frames))
                    self.assertTrue(all('q quit' in ' '.join(' '.join(f).split()) for f in frames))

    def test_fallback_numbers_and_confirmation(self):
        for inputs, saved in ((['2', '3', '8'], True), (['2', '9', 'y'], True),
                              (['2', 'q', 'n'], False), (['2', 'esc', 'y'], True)):
            with self.subTest(inputs=inputs):
                self.configured()
                with patch.dict('sys.modules', {'curses': None}), patch('builtins.input', side_effect=inputs), \
                     contextlib.redirect_stdout(io.StringIO()) as out:
                    menu.run(self.scout, self.home)
                self.assertIn(' 2. Names', out.getvalue())
                self.assertIn(' 8. Save', out.getvalue())
                self.assertIn(' 9. Cancel', out.getvalue())
                self.assertEqual(self.scout.profile()['config']['name_masking'], 'deep' if saved else 'basic')

    def test_fallback_enter_changes_and_eof_discards(self):
        with patch.dict('sys.modules', {'curses': None}), \
             patch('builtins.input', side_effect=['2', '', EOFError]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            menu.run(self.scout, self.home)
        self.assertIn('(•) off', out.getvalue())
        self.assertIn('Cancelled. Changes discarded.', out.getvalue())
        self.assertFalse((self.scout.root() / 'settings.json').exists())

    def test_existing_guard_sees_saved_options_without_restart(self):
        from plugin.scout.prompt_guard import PromptGuard
        self.configured()
        reader = Scout(LocalContext(self.home))
        guard = PromptGuard(reader)
        with patch('plugin.scout.prompt_guard.privacy.mask_local_details', return_value=('masked', [])) as mask:
            guard.scan('synthetic message', names=True)
            self.assertEqual(mask.call_args.kwargs['name_masking'], 'basic')
            self.drive(['down', '\n', 'down', ' ', 'down', ' ', 's'])
            guard.scan('synthetic message', names=True)
            self.assertEqual(mask.call_count, 2)  # Same guard and text; settings invalidate its cache.
            self.assertEqual({key: mask.call_args.kwargs[key] for key in
                              ('name_masking', 'address_masking', 'other_languages')},
                             dict(name_masking='deep', address_masking='off', other_languages='on'))

    def test_fallback_narrow_terminal_and_eof_without_changes(self):
        self.configured()
        with patch.dict('sys.modules', {'curses': None}), \
             patch('builtins.input', side_effect=EOFError), \
             patch.object(menu.shutil, 'get_terminal_size', return_value=os.terminal_size((24, 24))), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            menu.run(self.scout, self.home)
        self.assertTrue(all(len(line) <= 24 for line in out.getvalue().splitlines()))
        self.assertTrue(out.getvalue().endswith('No changes.\n'))


class Launcher(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home, self.cache = root / 'hermes', root / 'cache'
        self.env = dict(os.environ, HERMES_HOME=str(self.home), XDG_CACHE_HOME=str(self.cache))
        self.env.pop('LOCAL_SCOUT_PYTHON', None)

    def tearDown(self):
        self.temp.cleanup()

    def launch(self, *args, cwd=None):
        return subprocess.run(['bash', str(PLUGIN / 'scout.sh'), *args], env=self.env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=120, cwd=cwd)

    def venv(self, name, python):
        bin_dir = self.home / 'installs' / 'a' / 'environments' / name / 'venv' / 'bin'
        bin_dir.mkdir(parents=True)
        (bin_dir / 'python').write_text(f'#!/bin/sh\nexec "{python}" "$@"\n')
        (bin_dir / 'python').chmod(0o755)

    def test_syntax(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(PLUGIN / 'scout.sh')]).returncode, 0)

    def test_no_environment(self):
        result = self.launch('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('no Hermes plugin environment', result.stderr)

    def test_picks_environment_with_dependencies(self):
        self.venv('without', '/bin/false')
        self.venv('with', sys.executable)
        result = self.launch('configure', '--mode', 'off')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'CONFIGURED')
        self.assertEqual((self.cache / 'local-scout' / 'plugin').resolve(), PLUGIN)
        self.assertEqual(self.launch().returncode, 0)  # no terminal: status only

    def test_relative_paths_and_local_plugin_directory(self):
        self.venv('with', sys.executable)
        work = Path(self.temp.name) / 'work'
        (work / 'plugin').mkdir(parents=True)
        (work / 'plugin' / '__init__.py').write_text('')
        (work / 'plugin' / 'cli.py').write_text('print("decoy")\n')
        (work / 'tickets.jsonl').write_text('{"text": "invoice", "label": "billing"}\n')
        result = self.launch('configure', '--mode', 'off', '--dataset', 'support', '--source', 'tickets.jsonl', cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('decoy', result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'], 'CONFIGURED')

    def test_refuses_foreign_directory(self):
        self.venv('with', sys.executable)
        (self.cache / 'local-scout' / 'plugin').mkdir(parents=True)
        result = self.launch('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('is not a link', result.stderr)


if __name__ == '__main__':
    unittest.main()
