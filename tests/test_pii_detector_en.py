"""Regression tests for English PII formats and validated numeric identifiers."""
import unittest

from plugin.scout import privacy


class EnglishPiiDetector(unittest.TestCase):
    def test_us_ssn_formats_and_invalid_groups(self):
        for ssn in ("123-45-6789", "123 45 6789"):
            self.assertEqual(privacy.spans("SSN: " + ssn), [(5, 16 if "-" in ssn else 16, "ssn")])
        for ssn in ("000-12-3456", "666-12-3456", "900-12-3456", "123-00-3456", "123-45-0000"):
            self.assertNotIn("ssn", privacy.categories("SSN: " + ssn))

    def test_payment_cards_require_network_and_luhn(self):
        for number in ("4111 1111 1111 1111", "5555-5555-5555-4444", "378282246310005"):
            self.assertIn("card", privacy.categories("Card " + number))
        self.assertNotIn("card", privacy.categories("Card 4111 1111 1111 1112"))
        self.assertNotIn("card", privacy.categories("Reference 1234 5678 9012 3456"))

    def test_nanp_e164_and_international_phones_need_format_or_context(self):
        for number in ("(415) 555-2671", "+44 20 7946 0958", "phone: 4155552671", "415-555-2671"):
            self.assertIn("phone", privacy.categories(number))
        self.assertNotIn("phone", privacy.categories("version 202611060 and size 123456789"))

    def test_us_street_address_and_existing_polish_address(self):
        us = "Ship to 123 Main Street, Springfield, IL 62704"
        self.assertIn("address", privacy.categories(us))
        masked, _ = privacy.mask_local(us)
        self.assertIn("[ADDRESS]", masked)
        self.assertIn("address", privacy.categories("ul. Kwiatowa 12, 00-001 Warszawa"))

    def test_existing_categories_and_public_return_shapes(self):
        text = "email a@example.com, PESEL 44051401458, IBAN PL61109010140000071219812874"
        found = privacy.spans(text)
        self.assertTrue({"email", "pesel", "iban"} <= {kind for _, _, kind in found})
        self.assertIsInstance(privacy.categories(text), list)
        masked, count = privacy.mask_local("email a@example.com")
        self.assertEqual((masked, count), ("email [EMAIL]", 1))


if __name__ == "__main__":
    unittest.main()
