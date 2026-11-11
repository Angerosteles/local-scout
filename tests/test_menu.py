import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from plugin.cli import main
from plugin.scout import menu
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.tests.test_setup_triage import examples

PLUGIN = Path(__file__).resolve().parents[1]


class Script:
    """Answers menu questions in order; records everything the menu says."""
    def __init__(self, *answers):
        self.answers, self.said = list(answers), []

    def ask(self, question):
        self.said.append(question)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text=''):
        self.said.append(text)

    def text(self):
        return '\n'.join(self.said)


class Menu(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'
        self.scout = Scout(LocalContext(self.home))

    def tearDown(self):
        self.temp.cleanup()

    def drive(self, *answers):
        script = Script(*answers)
        menu.run(self.scout, self.home, script.ask, script.say)
        return script

    def config(self):
        return self.scout.profile()['config']

    def test_masking_on_needs_confirmation(self):
        script = self.drive('1', 'n', 'q')
        self.assertEqual(self.config().get('mode', 'off'), 'off')
        self.assertFalse((self.scout.root() / 'settings.json').exists())
        self.assertIn('masking: off', script.text())

    def test_masking_on_sets_report_and_profile_grants(self):
        script = self.drive('1', 'y', 'q')
        config = self.config()
        self.assertEqual(config['mode'], 'report')
        for scope in menu.MASK_SCOPES:
            self.assertEqual(config['grants'][scope], ['profile'])
        self.assertIn('masking: ON', script.text())
        self.assertIn('scout configure --mode report --dataset profile --approve observe,report,insights,mask', script.text())

    def test_masking_on_keeps_other_grants_and_mode(self):
        self.scout.configure_local('on_demand', grants={'answer': ['support'], 'observe': ['support']}, answer_task='triage')
        self.drive('1', 'y', 'q')
        config = self.config()
        self.assertEqual(config['mode'], 'on_demand')
        self.assertEqual(config['grants']['answer'], ['support'])
        self.assertEqual(config['grants']['observe'], ['support', 'profile'])
        self.assertIn('local triage: on', menu.describe(self.scout))

    def test_off_and_mode(self):
        self.drive('1', 'y', '3', '1', 'q')
        self.assertEqual(self.config()['mode'], 'background')
        self.drive('3', '9', 'q')
        self.assertEqual(self.config()['mode'], 'background')
        self.drive('2', 'n', 'q')
        self.assertEqual(self.config()['mode'], 'background')
        script = self.drive('2', 'y', 'q')
        self.assertEqual(self.config()['mode'], 'off')
        self.assertEqual(self.config()['grants']['mask'], ['profile'])
        self.assertIn('mode: off · masking: off', script.text())

    def test_quit_end_of_input_and_unknown_choice(self):
        self.drive()
        self.drive('7', 'x', '')
        script = Script('1')
        script.ask = lambda question: (_ for _ in ()).throw(KeyboardInterrupt)
        menu.run(self.scout, self.home, script.ask, script.say)
        self.assertEqual(self.config().get('mode', 'off'), 'off')

    def test_failed_step_hides_details(self):
        def broken(scout, ask, say):
            raise ValueError('jane.miller@example.com')
        with patch.object(menu, 'ACTIONS', [('Broken', broken)]):
            script = self.drive('1', 'q')
        self.assertIn('input details hidden', script.text())
        self.assertNotIn('jane.miller', script.text())

    def test_setup_triage_from_menu(self):
        history = Path(self.temp.name) / 'tickets.jsonl'
        history.write_text(''.join(json.dumps(r) + '\n' for r in examples()))
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='y'), \
                contextlib.redirect_stdout(io.StringIO()):
            script = self.drive('4', str(history), 'Customer message:', 'y', 'q')
        config = self.config()
        self.assertEqual(config['answer_task'], 'triage')
        self.assertEqual(config['answer_marker'], 'Customer message:')
        self.assertIn('profile', config['grants']['mask'])
        self.assertIn('Ready:', script.text())
        self.assertIn('local triage: on', script.text())

    def test_setup_triage_missing_file(self):
        script = self.drive('4', str(Path(self.temp.name) / 'missing.jsonl'), 'q')
        self.assertIn('No such file.', script.text())
        self.assertFalse((self.scout.root() / 'settings.json').exists())


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                main(['--home', str(self.home), *args])
                return 0, err.getvalue()
            except SystemExit as exc:
                return exc.code, err.getvalue()

    def test_scripts_still_need_mode(self):
        with patch('sys.stdin.isatty', return_value=False):
            code, err = self.invoke('configure', '--dataset', 'profile')
            self.assertEqual(code, 2)
            self.assertIn('--mode is required', err)
            code, err = self.invoke()
            self.assertEqual(code, 2)
            self.assertIn('menu needs a terminal', err)

    def test_terminal_opens_menu(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        for args in ((), ('menu',), ('configure',)):
            with patch('sys.stdin.isatty', return_value=True), patch.object(menu, 'run') as run, \
                    contextlib.redirect_stdout(Terminal()):
                main(['--home', str(self.home), *args])
            run.assert_called_once()


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
        self.assertEqual(self.launch().returncode, 2)  # no terminal: no menu

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
