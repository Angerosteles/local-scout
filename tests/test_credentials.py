"""Password-like values without a known format: shape + cues, fixed exclusions, source thresholds, optional basal."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch

from plugin.scout import basal, credentials, privacy
from plugin.scout.prompt_guard import PromptGuard


def masked(text, source='text', confirm=None):
    token = credentials.CONTEXT.set((source, confirm))
    try:
        return privacy.mask_local_details(text)[0]
    finally:
        credentials.CONTEXT.reset(token)


class Detection(unittest.TestCase):
    def test_password_like_values_are_masked(self):
        for text, value in [('Hey unlock this for me, use IkXzYx33@$.', 'IkXzYx33@$'),
                            ('my pasword: hunter2025', 'hunter2025'),           # typo in the cue
                            ('passwrod is Xy7#kq2!', 'Xy7#kq2!'),
                            ('<Password>P8P9$xeg&&Cz2j</Password>', 'P8P9$xeg&&Cz2j'),
                            ('{"refresh_token": "j*Eac!5dPQ6$TQ!"}', 'j*Eac!5dPQ6$TQ!'),
                            ('**Passwords:** - W3!paDuJ#', 'W3!paDuJ#'),
                            ('PASSWORD=Tr0ub4dor&3', 'Tr0ub4dor&3'),
                            ('pwd #pt(1hy)hW_3BF+1N2', '#pt(1hy)hW_3BF+1N2')]:   # many symbols: not code
            with self.subTest(text=text):
                out = masked(text)
                self.assertNotIn(value, out)
                self.assertIn('[SECRET]', out)

    def test_codes_and_pins_by_their_norms(self):
        for text, value, label in [('Your verification code is 123456', '123456', '[CODE]'),
                                   ('{"otp_code": "543918"}', '543918', '[CODE]'),
                                   ('Your 2FA code: 48213907', '48213907', '[CODE]'),
                                   ('Your login code is 482913', '482913', '[CODE]'),
                                   ('Your PIN is 4821.', '4821', '[PIN]')]:
            with self.subTest(text=text):
                out = masked(text)
                self.assertNotIn(value, out)
                self.assertIn(label, out)
        for text in ['HS Code: 0713', 'Error code: 7223', 'Social Security Number: 144917 T', 'Access granted in 2023',
                     'zip code 90210', 'promo code 2024']:
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)
        # a bare "code" is uncertain: only the engine can confirm it
        self.assertEqual(masked('Use the code 4821 to open the door'), 'Use the code 4821 to open the door')
        self.assertIn('[CODE]', masked('Use the code 4821 to open the door', confirm=lambda line: 0.9))

    def test_password_rules_lower_confidence(self):
        short = [c[2] for c in credentials.candidates('cell Ab3$xy')]          # 6 characters
        long = [c[2] for c in credentials.candidates('cell Ab3$xyZ9q')]        # 10 characters
        self.assertTrue(short and long and short[0] < long[0], (short, long))

    def test_ordinary_tokens_stay(self):
        for text in ['Upgrade to Python3.11 and Windows11 on x86_64.',
                     'commit 3f2a9c1d4e merged; digest sha256:9b1c2d3e4f5a6b7c',
                     'see https://www.example.com/a?b=C1d2 and example.org/path',
                     'id 123e4567-e89b-12d3-a456-426614174000 at 2026-11-11T13:10:03',
                     'CVE-2015-2012 fixed in 1.1.5.x; ticket JIRA-1234',
                     'Mozilla/5.0 Read/Write SystemOut.log CreateBellState(q1) 127.0.0.1:9000',
                     'QR Code: ![QR Code](qr_code_url); Connection Type: L2TP/IPSec; TCP/IP; I/O',
                     'call foo(bar1), x[i], a->b, ns::f, {{user1}}, %s, $1, read%20write, &amp;',
                     'Tokenized to 025-99-XXX; device tempSensor_001',
                     'Password is incorrect, please try again.',
                     'Your password must be at least 12 characters long.',
                     'Error Code: 7223 failed; COVID-19; Diffie-Hellman; hashed_password',
                     'run with --memory=4g and $HOME, read%20write, 0x0002',
                     'Contact [EMAIL] about [SECRET]']:
            with self.subTest(text=text):
                self.assertEqual(masked(text), text)

    def test_long_values_are_left_to_the_key_detectors(self):
        self.assertEqual(masked('value a7Kq9!x2Lm4#Pz8@Wn5$Rt3'), 'value a7Kq9!x2Lm4#Pz8@Wn5$Rt3')

    def test_source_syntax_is_not_a_credential(self):
        for text in ['def load_key(key: Optional[Mapping[str, str]]) -> frozenset[str]:',
                     'sorted(_catalogs, key=lambda key: _catalogs[key][1])',
                     'for key in _message_keys()[1:]:',
                     'api_key = getattr(cli, "api_key", None))',
                     'max_tokens = groups[-1])',
                     'api_key = os.R_OK))',
                     'secret token is a (`key_cmd`)',
                     'secret credential format: ``fail``/``null``']:
            with self.subTest(text=text):
                self.assertEqual(credentials.spans(text), [])

    def test_business_identifiers_and_usernames_stay_readable(self):
        for text in ['password reset for users Jx-19347, O6758379',
                     'Login, MED59876572',
                     'Classification Level - Top Secret. Employee ID - Y1432214',
                     'Employee ID: Uj-7813, Customer ID: Bu34183-R',
                     'credentials: available; Medical Record Number: MED8154185',
                     'password expired with TSS7110E and RSU1412',
                     'Your password for the system thomas_6709',
                     'Login Enhancements: Submitted by black7149',
                     '{"client_token": "CTOK987654321", "customer_id": "CID123456789"}',
                     '{"password": "ordinary", "user_name": "sample_1234"}']:
            with self.subTest(text=text):
                self.assertEqual(credentials.spans(text), [])

    def test_credential_literals_survive_context_filters(self):
        for text, value in [('password = "Ab7$kLm9"', 'Ab7$kLm9'),
                            ('secret: QRS12345', 'QRS12345'),
                            ('{"auth_token": "QRS12345"}', 'QRS12345'),
                            ('password, ")tYQfJqh_93HEX_eK"', ')tYQfJqh_93HEX_eK'),
                            ('{"username": "r2wj_WXcy!266Tg$R1"}', 'r2wj_WXcy!266Tg$R1'),
                            ('password: ``Ab7$kLm9``.', 'Ab7$kLm9'),
                            ('{"refresh_token": "j*Eac!5dPQ6$TQ!"}', 'j*Eac!5dPQ6$TQ!')]:
            with self.subTest(text=text):
                spans = credentials.spans(text)
                self.assertIn(value, [text[a:b] for a, b, _ in spans])

    def test_json_fields_do_not_share_credential_cues(self):
        text = '{"password": "Ab7$kLm9", "status": "step_3", "customer_id": "CID123456789"}'
        self.assertEqual([text[a:b] for a, b, _ in credentials.spans(text)], ['Ab7$kLm9'])

    def test_terminal_needs_more_evidence_than_text(self):
        line = 'deploy Tr0ub4dor&3 now'
        self.assertIn('[SECRET]', masked(line, 'text'))
        self.assertEqual(masked(line, 'terminal'), line)
        self.assertIn('[SECRET]', masked('export DB_PASSWORD=Tr0ub4dor&3', 'terminal'))

    def test_scores_stay_in_range(self):
        for text in ['password: Aa1!Aa1!Aa1!', 'pin 0000', 'x' * 30, 'Tr0ub4dor&3']:
            for _, _, score, _, kind in credentials.candidates(text):
                self.assertIn(kind, ('secret', 'code', 'pin'))
                self.assertTrue(0.0 <= score <= 1.0)


class BasalSecondOpinion(unittest.TestCase):
    BAND = 'table cell Kx7#Rz2Qp here'      # shape-only candidate inside the text band
    PROMPT_BAND = 'table cell Kx7Rz2Qp9 here'  # inside the prompt band

    def test_band_is_uncertain(self):
        for text, source in ((self.BAND, 'text'), (self.PROMPT_BAND, 'prompt')):
            low, high = credentials.THRESHOLDS[source]
            scores = [round(c[2], 2) for c in credentials.candidates(text)]
            self.assertTrue(scores and all(low <= s < high for s in scores), (source, scores))
            self.assertEqual(masked(text, source), text)

    def test_confirmed_band_value_is_masked_and_refused_stays(self):
        self.assertIn('[SECRET]', masked(self.BAND, confirm=lambda line: 0.9))
        self.assertEqual(masked(self.BAND, confirm=lambda line: 0.1), self.BAND)

    def test_engine_failure_falls_back_after_one_call(self):
        calls = []

        def broken(line):
            calls.append(line)
            raise basal.Unavailable('down')
        text = '\n'.join(f'row {i} Kx7Rz2Qp{i}a' for i in range(5))
        self.assertEqual(masked(text, confirm=broken), text)
        self.assertEqual(len(calls), 1)

    def test_call_budget(self):
        calls = []
        text = '\n'.join(f'row {i} Kx7Rz2Qp{i}a' for i in range(20))
        masked(text, confirm=lambda line: calls.append(line) or 0.1)
        self.assertLessEqual(len(calls), credentials.MAX_CALLS)

    def test_neighbour_lines_are_asked_only_after_a_confirmation(self):
        text = 'vpn account below\ncell Kx7#Rz2Qp\nbackup Wq8Lm3Zx\nfar away line'   # line 3 is below the band
        asked = []

        def confirm(line):
            asked.append(line)
            return 0.9
        out = masked(text, confirm=confirm)
        self.assertNotIn('Kx7#Rz2Qp', out)
        self.assertIn('vpn account below', asked)           # neighbour above
        self.assertNotIn('Wq8Lm3Zx', out)                    # neighbour below: its best token is masked
        self.assertNotIn('far away line', asked)             # beyond NEIGHBOURS
        asked.clear()
        self.assertEqual(masked(text, confirm=lambda line: asked.append(line) or 0.1), text)
        self.assertEqual(asked, ['cell Kx7#Rz2Qp'])          # refused: no neighbour questions

    def test_engine_sees_the_candidate_in_a_long_line(self):
        seen = []
        text = 'x ' * 2000 + 'cell Kx7#Rz2Qp ' + 'y ' * 2000
        out = masked(text, confirm=lambda segment: seen.append(segment) or 0.9)
        self.assertNotIn('Kx7#Rz2Qp', out)
        self.assertIn('Kx7#Rz2Qp', seen[0])
        self.assertLessEqual(len(seen[0]), 2 * credentials.WINDOW + 20)

    def test_confirmed_neighbours_keep_identifier_exclusions(self):
        for neighbour in ['Customer ID: Bu34183-R', 'credentials: Optional[Mapping[str, str]]']:
            with self.subTest(neighbour=neighbour):
                text = self.BAND + '\n' + neighbour
                token = credentials.CONTEXT.set(('text', lambda line: 0.9))
                try:
                    self.assertEqual([text[a:b] for a, b, _ in credentials.spans(text)], ['Kx7#Rz2Qp'])
                finally:
                    credentials.CONTEXT.reset(token)

    def test_prompt_guard_uses_basal_only_for_user_prompts(self):
        class Fake:
            def profile(self):
                return {'config': {'mode': 'report', 'grants': {'mask': ['profile']}, 'basal_url': 'http://127.0.0.1:9'}}
        guard = PromptGuard(Fake())
        with patch.object(basal, 'credential_probability', return_value=0.95) as ask:
            self.assertIn('[SECRET]', guard.scan(self.PROMPT_BAND, names=True)[0])
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(guard.scan(self.BAND, names=False)[0], self.BAND)
            self.assertEqual(ask.call_count, 1)


class Engine(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        yes = 0.9 if 'unlock' in body['state'] else 0.05
        data = json.dumps({'answers': {'q': {'probabilities': {'yes': yes, 'no': 1 - yes}}}}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class CredentialClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Engine)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_probability_from_the_engine(self):
        self.assertAlmostEqual(basal.credential_probability(self.url, 'use Kx7Rz2Qp9 to unlock'), 0.9)
        self.assertAlmostEqual(basal.credential_probability(self.url, 'nothing here'), 0.05)

    def test_only_loopback(self):
        with self.assertRaises(ValueError):
            basal.credential_probability('http://198.51.100.7:8765', 'x')

    def test_unreachable_engine_is_unavailable(self):
        with self.assertRaises(basal.Unavailable):
            basal.credential_probability('http://127.0.0.1:9', 'x')

    def test_one_deadline_for_a_whole_tool_result(self):
        clock = [100.0]
        timeouts = []

        def slow(url, text, timeout=None):
            timeouts.append(round(timeout, 2))
            clock[0] += timeout            # the engine uses all the time it is given
            return 0.1
        with patch.object(basal.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(basal, 'credential_probability', side_effect=slow):
            check = basal.credential_checker({'basal_url': self.url}, budget=10.0)
            check('a'); check('b')
            with self.assertRaises(basal.Unavailable):
                check('c')
        self.assertEqual(timeouts, [8.0, 2.0])          # the second call only gets what is left

    def test_checker_only_with_a_registered_engine(self):
        self.assertIsNone(basal.credential_checker({}))
        self.assertAlmostEqual(basal.credential_checker({'basal_url': self.url})('please unlock Kx7Rz2Qp9'), 0.9)


if __name__ == '__main__':
    unittest.main()
