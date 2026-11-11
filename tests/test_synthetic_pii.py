"""Credential formats and card verification codes: high-precision prefixes, keyword-anchored CVV."""
import unittest
from plugin.scout import privacy


class CredentialAndCvvTests(unittest.TestCase):
    def masked(self, text):
        return privacy.mask_local_details(text)[0]

    def test_credential_formats(self):
        for value in ('sk_live_' + 'a1B2c3D4e5F6g7H8i9J0k1L2', 'ghp_' + 'A' * 36, 'github_pat_' + 'B' * 22 + '_' + 'C' * 59,
                      'AKIA' + 'ABCDEFGHIJKLMNOP', 'xoxb-12345678901-1234567890123-' + 'D' * 24, 'AIza' + 'E' * 35,
                      'eyJhbGciOiJIUzI1NiJ9abc.eyJzdWIiOiIxMjM0NTY3ODkwIn0.' + 'F' * 20):
            self.assertNotIn(value, self.masked('use ' + value + ' now'))
        self.assertNotIn('qwertyuiopasdfghjklzxcvb', self.masked('Authorization: Bearer qwertyuiopasdfghjklzxcvb'))
        self.assertNotIn('MIIBVgIBADAN', self.masked('-----BEGIN PRIVATE KEY-----\nMIIBVgIBADAN\n-----END PRIVATE KEY-----'))

    def test_cvv_keyword_anchored_digits_only(self):
        self.assertEqual(self.masked('CVV 402'), 'CVV [CVV]')
        self.assertEqual(self.masked('security code: 1234'), 'security code: [CVV]')
        for text in ('Room 123 is on the third floor.', 'Error code 404 again.', 'The CVV field is required.', 'Call 911.'):
            self.assertEqual(self.masked(text), text)

    def test_ordinary_code_and_prose_untouched(self):
        for text in ('    token = pii_model.budget.set(limit)', 'Use the sk-learn library.', 'Rotate your token every 90 days.',
                     'The password field must be at least 8 characters.'):
            self.assertEqual(self.masked(text), text)


if __name__ == '__main__':
    unittest.main()
