"""Optional basal engine: loopback-only client, masked text, threshold from validation, honest refusals."""
import contextlib
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from plugin.cli import main
from plugin.scout import basal, learning, storage
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout.local_answers import Forward, LocalAnswers


class FakeEngine(BaseHTTPRequestHandler):
    """Answers like basal-serve /v1/systemone: one probability per allowed option."""
    received = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', 'http://198.51.100.7/escaped')
            self.end_headers()
            return
        if self.path.startswith('/partial'):
            data = json.dumps({'answers': {'topic': {'probabilities': {'billing': 0.99}}}}).encode()
        elif self.path.startswith('/huge'):
            data = b' ' * (basal.MAX_RESPONSE_BYTES + 10)
        else:
            data = None
        if data is not None:
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeEngine.received.append(body)
        options = list(body['questions']['topic']['criteria'])
        text = body['state'].lower()
        winner = next((o for o in options if o != basal.OTHER and o in text), basal.OTHER)
        sure = 0.97 if 'clearly' in text else 0.6
        probabilities = {o: (sure if o == winner else (1 - sure) / (len(options) - 1)) for o in options}
        data = json.dumps({'answers': {'topic': {'choice': winner, 'confidence': sure, 'probabilities': probabilities}}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def examples():
    rows = []
    for label in ('billing', 'technical'):
        for i in range(30):
            sure = 'clearly ' if i % 3 else ''
            rows.append(dict(text=f'{sure}{label} question number {i}', label=label,
                             split='train' if i < 20 else 'validation'))
    return rows


class Basal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeEngine)
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        FakeEngine.received = []
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'
        self.history = Path(self.temp.name) / 'history.jsonl'
        self.history.write_text(''.join(json.dumps(r) + '\n' for r in examples()))

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main(['--home', str(self.home), *args])
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def setup_basal(self, *extra):
        code, _, err = self.cli('configure', '--mode', 'report', '--basal-url', self.url)
        self.assertEqual(code, 0, err)
        return self.cli('setup-triage', '--history', str(self.history), '--recipe', 'basal_zero_shot', '--yes', '--json', *extra)

    def test_configure_says_the_engine_also_checks_passwords(self):
        code, out, err = self.cli('configure', '--mode', 'report', '--basal-url', self.url)
        self.assertEqual(code, 0, err)
        self.assertIn('password-like', json.loads(out)['basal_notice'])
        code, out, err = self.cli('configure', '--mode', 'report')
        self.assertNotIn('basal_notice', json.loads(out))

    def test_only_loopback_ip_addresses_are_accepted(self):
        self.assertEqual(basal.check_url('http://127.0.0.1:8765/'), 'http://127.0.0.1:8765')
        self.assertEqual(basal.check_url('http://[::1]:8765'), 'http://[::1]:8765')
        for bad in ('http://localhost:8765', 'http://10.0.0.5:8765', 'https://127.0.0.1:8765', 'http://127.0.0.1',
                    'http://127.0.0.1:8765/v1', 'http://user:pw@127.0.0.1:8765', 'http://127.0.0.1:8765?x=1',
                    'http://example.com:80', '', None):
            with self.assertRaises(ValueError, msg=bad):
                basal.check_url(bad)

    def test_setup_triage_picks_the_threshold_on_validation_and_stores_no_address(self):
        code, output, err = self.setup_basal()
        self.assertEqual(code, 0, err)
        result = json.loads(output)
        # Every validation answer is correct, so the default gate takes the lowest point of its grid (0.5);
        # the curve still shows that 0.6-confidence answers drop out above 0.6.
        self.assertEqual(result['threshold'], 0.5)
        curve = {p['threshold']: p for p in result['validation_curve']}
        self.assertEqual(curve[0.5]['accepted'], 20)
        self.assertEqual(curve[0.7]['accepted'], 14)
        self.assertTrue(all(p['accepted_error'] in (0, None) for p in result['validation_curve']))
        self.assertEqual(len(FakeEngine.received), 20)  # validation only; nothing is fitted on training text
        scout = Scout(LocalContext(self.home))
        artifact = next((scout.root() / 'artifacts').glob('*.json'))
        meta, arrays = storage.load_artifact(artifact.parent, artifact.stem)
        storage.validate_model(meta, arrays)
        self.assertEqual(meta['engine'], 'basal_zero_shot')
        self.assertNotIn(self.url, artifact.read_text())
        self.assertNotIn('question number', artifact.read_text())

    def test_handle_masks_text_before_the_engine_and_refuses_other(self):
        code, _, err = self.setup_basal('--max-error', '0')
        self.assertEqual(code, 0, err)
        scout = Scout(LocalContext(self.home))
        FakeEngine.received = []
        handled = scout.call('scout_handle', dict(task='triage', text='clearly billing: refund to anna.test@example.com'))
        self.assertTrue(handled['ok'], handled)
        self.assertEqual(handled['data']['status'], 'handled_locally')
        self.assertEqual(handled['data']['label'], 'billing')
        sent = FakeEngine.received[-1]['state']
        self.assertNotIn('anna.test@example.com', sent)
        self.assertIn('[EMAIL]', sent)
        self.assertEqual(set(FakeEngine.received[-1]['questions']['topic']['criteria']), {'billing', 'technical', basal.OTHER})
        refused = scout.call('scout_handle', dict(task='triage', text='clearly something unrelated'))
        self.assertEqual(refused['data']['status'], 'abstained')
        self.assertIsNone(refused['data']['label'])

    def test_engine_down_is_a_clear_error_not_an_invalid_artifact(self):
        code, _, err = self.setup_basal()
        self.assertEqual(code, 0, err)
        scout = Scout(LocalContext(self.home))
        scout.configure_local('report', basal_url='http://127.0.0.1:9')  # discard port: nothing listens
        with patch.object(basal, 'TIMEOUT_SECONDS', 2):
            handled = scout.call('scout_handle', dict(task='triage', text='billing question'))
        self.assertFalse(handled['ok'])
        self.assertEqual(handled['error']['code'], 'IO_ERROR')
        self.assertIn('basal engine', handled['error']['message'])

    def test_without_an_engine_basal_is_unavailable(self):
        scout = Scout(LocalContext(self.home))
        scout.configure_local('report')
        self.assertIsNotNone(scout.extras()['basal_zero_shot'])
        code, _, err = self.cli('setup-triage', '--history', str(self.history), '--recipe', 'basal_zero_shot', '--yes')
        self.assertNotEqual(code, 0)
        self.assertEqual(FakeEngine.received, [])

    def test_proxy_settings_are_ignored(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:9', 'http_proxy': 'http://127.0.0.1:9', 'NO_PROXY': ''}):
            decided = basal.decide(self.url, ['clearly billing'], ['billing', 'technical'])
        self.assertEqual(decided[0][0], 0)

    def test_in_turn_local_answers_forward_instead_of_waiting_for_basal(self):
        code, _, err = self.setup_basal()
        self.assertEqual(code, 0, err)
        scout = Scout(LocalContext(self.home))
        scout.configure_local('report', grants=dict(scout.profile()['config']['grants'], answer=['support']),
                              answer_task='triage')
        request = {'messages': [{'role': 'user', 'content': 'clearly billing'}]}
        FakeEngine.received = []
        with self.assertRaises(Forward) as caught:
            LocalAnswers(scout)._answer(request, request, 'chat_completions', None, time.monotonic())
        self.assertEqual(caught.exception.args[0], 'slow_engine')
        self.assertEqual(FakeEngine.received, [])

    def test_descriptions_are_validated_and_masked(self):
        scout = Scout(LocalContext(self.home))
        scout.configure_local('report', basal_descriptions={'billing': 'Invoices; ask ops@corp.example for details'})
        self.assertEqual(scout.profile()['config']['basal_descriptions'], {'billing': 'Invoices; ask [EMAIL] for details'})
        for bad in ({'Bad Label': 'x'}, {'billing': ''}, {basal.OTHER: 'reserved'}, {'billing': 'x' * 201}):
            with self.assertRaises(ValueError):
                basal.check_descriptions(bad)


    def test_redirects_partial_and_oversized_answers_are_refused(self):
        # The client posts to <base>/v1/systemone; a base path prefix selects a misbehaving engine.
        for prefix, message in (('/redirect', 'redirect'), ('/partial', 'incomplete'), ('/huge', 'larger than 1 MB')):
            with patch.object(basal, 'check_url', return_value=self.url + prefix):
                with self.assertRaises(basal.Unavailable) as caught:
                    basal.decide('http://127.0.0.1:1', ['billing'], ['billing', 'technical'])
            self.assertIn(message, str(caught.exception))

    def test_a_user_class_named_other_keeps_its_meaning_and_the_reserved_id_is_rejected(self):
        self.assertEqual(basal.decide(self.url, ['clearly other question'], ['billing', 'other'])[0][0], 1)
        rows = [dict(text=f'clearly {basal.OTHER} {i}', label=basal.OTHER if i % 2 else 'billing',
                     split='train' if i < 20 else 'validation', group=f'g{i}') for i in range(30)]
        with self.assertRaises(basal.ReservedLabel):
            learning.train_basal(rows, basal_url=self.url)

    def test_the_recipe_id_is_not_an_artifact(self):
        scout = Scout(LocalContext(self.home))
        scout.configure_local('report')
        with self.assertRaises(Exception) as caught:
            scout._predict_approved('basal_zero_shot', 'profile', dict(id='q', text='billing'))
        self.assertEqual(caught.exception.args[0], 'PLAN_ONLY')
        self.assertIn('unavailable', caught.exception.args[1])

if __name__ == '__main__':
    unittest.main()
