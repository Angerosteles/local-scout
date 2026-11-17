"""Synthetic pattern regressions for phone and explicit credential fields."""
import unittest
from plugin.scout import privacy


class Docisk19(unittest.TestCase):
    def covered(self, text, value, category):
        clean, offsets = privacy._normalise_for_detection(text)
        found = privacy.local_spans(clean)
        if offsets is not None:
            found = [(offsets[a], offsets.ends[b - 1], c) for a, b, c in found]
        start = text.index(value)
        self.assertTrue(any(a <= start and start + len(value) <= b and c == category
                            for a, b, c in found), (text, found))

    def test_national_trunk_shapes(self):
        for value in ('021 4832 7596', '03-4286-9571', '07381 246857', '0412 583 9671', '0314 285963'):
            self.covered('Reception desk: ' + value, value, 'phone')

    def test_outer_parenthesis_and_local_extensions(self):
        for value in ('674-285-913 ext. 412', '674 285 913 wew 412'):
            self.covered('Telefon: (' + value + ')', value, 'phone')
        for suffix in ('Durchwahl 84', 'poste 731', 'int. 64'):
            value = '+49 30 2847596 ' + suffix
            self.covered('Direct: ' + value, value, 'phone')

    def test_contextual_phone_formats(self):
        for prefix in ('Contact Number: ', 'Contact our help team at ', 'phoneNumber="'):
            self.covered(prefix + '23864759', '23864759', 'phone')
        self.covered('Tel. 24.68.35.79', '24.68.35.79', 'phone')
        self.covered('Numer telefonu bez spacji: 48674285913', '48674285913', 'phone')
        self.covered('Desk: 1-628-475-1936', '1-628-475-1936', 'phone')

    def test_structural_secret_fields(self):
        value = 'v7Kr8Za2Nu4Qe6Ls'
        for text in ('Detail: WEBHOOK_SECRET=' + value, 'Trace: service_token: ' + value,
                     'Entry: "client_secret": "' + value + '"',
                     '"resetToken": "' + value + '"', 'smtp_pass: "' + value + '"',
                     'SEKRET_BRAMKI=' + value, 'haslo_operatora: "' + value + '"',
                     'CRED_D="' + value + '"'):
            self.covered(text, value, 'secret')
        self.covered('secondary_credential: "7284"', '7284', 'secret')

    def test_forms_and_ocr_labels(self):
        value = 'v7Kr8Za2Nu4Qe6Ls'
        for label in ('password', 'Hasto', 'hasło', 'API_T0KEN'):
            for join in ('\n', ':\n', ': '):
                self.covered(label + join + value, value, 'secret')
        text = 'API_T0KEN:\n' + 'ｖ７Ｋｒ８Ｚａ２Ｎｕ４Ｑｅ６Ｌｓ'
        self.covered(text, text.split('\n')[1], 'secret')

    def test_api_prose_and_lists(self):
        first, second = 'a3D7f9B2e8C6d4F1', 'hf-R6s2P8m4K9n3V7x5'
        for prefix in ('API key ', 'API key is ', 'API key for instance, ', 'klucz API dostawcy to '):
            self.covered(prefix + '`' + first + '`', first, 'secret')
        for join in (', ', ' and ', ', and '):
            text = 'API keys ' + first + join + second
            self.covered(text, second, 'secret')
        value = 'SG.' + 'a3B7c9D2e8F6g4H1j5K0mQ' + '.' + 'x7Y2z8W4v6U9t3S5r1P0n8M2k4J6h9G3f5D7s1A0q2E'
        self.covered('Credential: ' + value, value, 'secret')

    def test_overlapping_detectors_do_not_duplicate_identical_spans(self):
        text = 'password: q7Wr!8Te#9Yu'
        found = privacy.local_spans(text)
        self.assertEqual(len(found), len(set(found)))
        self.covered(text, 'q7Wr!8Te#9Yu', 'secret')

    def test_false_positive_boundaries(self):
        for text in ('GITHUB_TOKEN=\nREQUEST_TIMEOUT=37', 'password\nretry_count=16',
                     'API_T0KEN: ${DEPLOY_KEY}', 'Hasto: <password>',
                     'public_key: abc123abc123abc123', 'hashed_password: a3d7f9b2e8c6d4f1',
                     'api: v2', 'API key rotateCredentials()', 'API key YOUR_SERVICE_TOKEN'):
            self.assertFalse(any(c == 'secret' for _, _, c in privacy.local_spans(text)), text)
        for text in ('Order: 021 4832 7596', 'Phone: 192.168.2.4', 'version: 10.2.3.4'):
            self.assertFalse(any(c == 'phone' for _, _, c in privacy.local_spans(text)), text)
