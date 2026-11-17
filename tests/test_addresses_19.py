"""International address shapes and boundaries using invented values."""
import unittest
from plugin.scout import privacy


class Addresses19(unittest.TestCase):
    def assert_address(self, value):
        text = 'Address: ' + value
        start = text.index(value)
        self.assertTrue(any(a <= start and start + len(value) <= b and c == 'address'
                            for a, b, c in privacy.local_spans(text, other_languages='on')), text)

    def test_continental_street_markers(self):
        for value in ('Rue des Nuages 17, 12345 Belleville', 'Birkenweg 12, 12345 Waldstadt',
                      'Via delle Nuvole 8\n12345 Borgonuovo', '17 rue des Nuages, 12345 Belleville',
                      'Keizersgracht 47, 1234 AB Havenstad'):
            with self.subTest(value=value):
                self.assert_address(value)

    def test_international_post_boxes(self):
        for value in ('Postfach 27, 12345 Waldstadt', 'Postbus 27, 1234 AB Havenstad',
                      'Boîte postale 27, 12345 Belleville', 'Apartado de Correos 27, 12345 Monteclaro',
                      'Casella postale 27, 12345 Borgonuovo'):
            with self.subTest(value=value):
                self.assert_address(value)

    def test_japanese_chome(self):
        self.assert_address('1-2-3 Aokawa, Midori-ku, Kiyoshi 123-4567')

    def test_ends_before_punctuation_or_next_field(self):
        for value in ('Rue des Nuages 17, 12345 Belleville', 'Postfach 27, 12345 Waldstadt',
                      '1-2-3 Aokawa, Midori-ku, Kiyoshi 123-4567'):
            for separator in ('. ', ', ', '\n'):
                for label in ('Phone', 'Order', 'Subject', 'Email'):
                    text = 'Address: ' + value + separator + label + ': Review'
                    end = len('Address: ' + value)
                    with self.subTest(text=text):
                        found = privacy.local_spans(text, other_languages='on')
                        self.assertTrue(any(a <= len('Address: ') and b >= end and c == 'address'
                                            for a, b, c in found), found)
                        self.assertFalse(any(b > end for a, b, c in found if c == 'address'), found)

    def test_no_generic_place_or_order_as_street(self):
        for text in ('Location: Belleville, 12345', 'Order Vector 17, 12345 Belleville',
                     'Product Vector 17, 12345 Belleville', 'Rue des Nuages, 12345 Belleville'):
            self.assertIsNone(privacy._CONTINENTAL_ADDRESS.search(text), text)

    def test_preserves_existing_direction_and_languages_switches(self):
        self.assert_address('42 Cedar Park Road West')
        text = 'Adresse: Birkenweg 12, 12345 Waldstadt'
        self.assertFalse(any(c == 'address' for _, _, c in privacy.local_spans(text, address_masking='off', other_languages='on')))
        self.assertTrue(any(c == 'address' for _, _, c in privacy.local_spans(text, other_languages='on')))
