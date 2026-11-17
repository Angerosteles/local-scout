"""Synthetic regressions for technical syntax mistaken for personal data."""
import unittest
from plugin.scout import privacy, pii_model


class FalseMasks19(unittest.TestCase):
    def assert_absent(self, text, category):
        self.assertFalse(any(c == category for _, _, c in privacy.local_spans(text)), text)

    def test_addresses_inside_technical_tokens(self):
        for text in ('05:42 Weather station reports clear skies.',
                     'Document I-7 SSN fields need checking.',
                     'Install on Mac OS X 12.8 before rebooting.'):
            self.assert_absent(text, 'address')
        for text in ('Address: 42 Cedar Street', 'ul. Jasna 28', 'os Zielone 17', 'Now 613-28 Cedar Street'):
            self.assertTrue(any(c == 'address' for _, _, c in privacy.local_spans(text)), text)

    def test_register_dumps(self):
        number = '4242424242424242'
        self.assertTrue(pii_model.luhn(number))
        self.assert_absent('FPR23 ' + number + ' (f: 0.000, d: 8.64e+02)', 'card')
        self.assertTrue(any(c == 'card' for _, _, c in privacy.local_spans('Card: ' + number)))

    def test_business_number_labels(self):
        for label in ('EAN', 'UPC:', 'IMEI number:', 'REGON:'):
            self.assert_absent(label + ' 4242424242424242', 'card')
        for label in ('SKU ', 'Part number: ', 'Manifest #', 'Waybill ID: '):
            self.assert_absent(label + '628-475-1936', 'phone')
        self.assertTrue(any(c == 'phone' for _, _, c in privacy.local_spans('Phone: 628-475-1936')))

    def test_card_candidates_do_not_join_calendar_dates(self):
        self.assert_absent('Release dates: 08/21/2031 09/12/2032', 'card')
        value = '4242/4242/4242/4242'
        self.assertTrue(any(c == 'card' for _, _, c in privacy.local_spans(value)))

    def test_asset_filenames(self):
        self.assert_absent('Image assets include ribbon@3x.webp and banner@2x.png.', 'email')
        self.assertTrue(any(c == 'email' for _, _, c in privacy.local_spans('Email: ribbon@3x.webp')))
        self.assertTrue(any(c == 'email' for _, _, c in privacy.local_spans('Contact: ivy@cedar.example')))

    def test_technical_secret_shapes_keep_explicit_passwords(self):
        for text in ('CSS hex colour code #A7B3E9 for the legend.',
                     'Display panel calibrated at 2560x1440@165Hz.',
                     'Login failures in Runtime*.log',
                     'Remove passwords from the deploy*.properties files.'):
            self.assert_absent(text, 'secret')
        for value in ('#A7B3E9', '2560x1440@165Hz', 'Runtime*.log'):
            self.assertTrue(any(c == 'secret' for _, _, c in privacy.local_spans('password: ' + value)))

    def test_empty_secret_assignments(self):
        self.assert_absent('API_KEY=\n  NEXT_OPTION=<demo-value>', 'secret')
        self.assertTrue(any(c == 'secret' for _, _, c in privacy.local_spans('token:\nv8Rn4Qs7Lp2Xa9Bc')))

    def test_identifier_labels_are_not_person_names(self):
        from plugin.scout import name_model
        for mode in ('basic', 'deep'):
            for label in ('Numer IBAN', 'Polish SSN', 'Number PESEL', 'Developer Edition 8.6'):
                self.assertFalse(name_model.prompt_spans(label, mode=mode), (mode,label))
            self.assertTrue(name_model.prompt_spans('Mr. Adrian Vale', mode=mode))
