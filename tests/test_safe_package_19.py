"""Synthetic boundary regressions for the final 1.9 structural package."""
import unittest
from plugin.scout import privacy, pii_model


class SafePackage19(unittest.TestCase):
    def spans(self, text):
        return privacy.local_spans(text, names=False, address_masking='on')

    def full(self, text, value, category):
        start = text.index(value)
        found = self.spans(text)
        self.assertTrue(all(any(a <= i < b and c == category for a, b, c in found)
                            for i in range(start, start + len(value)) if text[i].isalnum()), text)

    def absent(self, text, category):
        self.assertFalse(any(c == category for _, _, c in self.spans(text)), text)

    def test_iban_lengths_and_checksum(self):
        for country in ('SK', 'AD', 'BG', 'CY', 'EE', 'GI', 'HR', 'IS', 'LI', 'LT', 'LV', 'MC', 'MT', 'SI', 'SM', 'VA', 'UA'):
            body = '1' * (privacy.IBAN_LENGTHS[country] - 4)
            numeric = body + ''.join(str(ord(c) - 55) for c in country) + '00'
            value = country + str(98 - int(numeric) % 97).zfill(2) + body
            with self.subTest(country=country):
                self.full('IBAN: ' + value, value, 'iban')
                self.absent('IBAN: ' + value[:2] + '00' + value[4:], 'iban')
                self.absent('IBAN: ' + value + '1', 'iban')

    def test_ssn_separators_and_zip(self):
        for value in ('241-62-7183', '241 62 7183', '241.62.7183', '241627183'):
            self.full('SSN: ' + value, value, 'ssn')
        for value in ('24162-7183', '241-627183', '241 62-7183'):
            self.absent('Postal ZIP: ' + value, 'ssn')
        for label in ('REGON', 'NIP', 'KRS'):
            self.absent(label + ': 241627183', 'ssn')
        self.full('REGON record; SSN: 241627183', '241627183', 'ssn')
        self.absent('SSN: 000-62-7183', 'ssn')

    def test_ssn_rows_arrows_and_field(self):
        for text in ('SSNs:\n- Member A: 241-62-7183\n- Member B: 352617294',
                     'TIN/SSN update: old -> 241627183, new -> 352617294'):
            self.full(text, '352617294', 'ssn')
        self.full('{"customer_ssn":"241-62-7183"}', '241-62-7183', 'ssn')

    def test_secret_boundaries(self):
        value = 'AIza' + 'Q7mx84Hd29Xt63Pa' * 2 + 'abcde'
        self.full(value, value, 'secret')
        value = 'Q7mx84=Hd29Xt'
        self.full('Temporary password is ' + value, value, 'secret')
        self.full('(OTP login code: 735291).', '735291', 'code')
        for prefix in ('API key: ', 'API key, for instance, '):
            self.full(prefix + 'Q7mx84Hd29Xt63Pa', 'Q7mx84Hd29Xt63Pa', 'secret')
        self.full('API Key: Q7mx84Hd29Xt63Pa, R8ny95Je30Yu74Qb', 'R8ny95Je30Yu74Qb', 'secret')
        self.full('OTP login code: R7XZ-42QT', 'R7XZ-42QT', 'code')
        self.absent('OTP login code: AB12_TEMPLATE', 'secret')

    def test_email_label_precedes_remote_login(self):
        for label in ('Email', 'E-mail', 'Mailbox', 'Contact', 'Kontakt'):
            for value in ('support@private.example', 'user@host.internal', 'banner@2x.png'):
                self.full(label + ': ssh ' + value, value, 'email')

    def test_address_forms(self):
        for value in ('42 Harbor Park Road West', 'ul. Św. Jasna 12, lok. 4\n00-123 Warszawa',
                      'Skrytka pocztowa 17\n00-123 Warszawa', 'skr. poczt. 17\n00-123 Warszawa',
                      '42 Cedar Road\nSuite 7\nPort City, NSW 2000'):
            self.full('Address: ' + value, value, 'address')

    def test_address_does_not_consume_next_field(self):
        value = '42 Cedar Road\nPort City, WX1 2YZ'
        for label in ('Phone', 'Order', 'Subject'):
            text = 'Address: ' + value + '\n' + label + ': 321654987'
            self.full(text, value, 'address')
            end = text.index('\n' + label)
            self.assertFalse(any(b > end for a, b, c in self.spans(text) if c == 'address'), text)
        # A place-like token following a comma must not bridge into a labelled line.
        text = 'Address: 42 Cedar Road, Port City\nSubject: Review'
        self.assertFalse(any(b > text.index('\nSubject') for a, b, c in self.spans(text) if c == 'address'))
