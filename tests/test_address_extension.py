"""Address span extension: whole address masked, ordinary text untouched."""
import unittest
from plugin.scout import privacy


class AddressExtensionTests(unittest.TestCase):
    def masked(self, text):
        return privacy.mask_local_details(text)[0]

    def test_full_address_segments_masked(self):
        for text, leak in (('Ship to 49103 Bernard Inlet, S8P 3K5, Martinezville please', 'Martinezville'),
                           ('Address: 4121 Andrew Lights, Apt. 625', '625'),
                           ('Send it to 9818 Lee Track, 73503, Kleinbury, KS today', 'Kleinbury')):
            out = self.masked(text)
            self.assertIn('[ADDRESS]', out)
            self.assertNotIn(leak, out)

    def test_extension_stops_at_sentence_end(self):
        out = self.masked('Visit 89887 Dalton Island, J3X 1C4, Lake Robertchester. Michelle will help you.')
        self.assertIn('Michelle will help you.', out)

    def test_ordinary_text_untouched(self):
        for text in ('Meet me at noon, Monday, in the lobby.', 'Version 2.3, released in May, fixes bugs.'):
            self.assertEqual(self.masked(text), text)


if __name__ == '__main__':
    unittest.main()


class SpanUnionTests(unittest.TestCase):
    """overlapping spans are united — no partial leak of an e-mail, card or secret."""
    def test_name_inside_email_masks_whole_email(self):
        out, cats = privacy.mask_local_details('My name is John Carter@example.invalid', names=True)
        self.assertNotIn('example.invalid', out)
        self.assertIn('email', cats)

    def test_address_next_to_secret_or_card_leaks_nothing(self):
        for text, secret in (('Ship to 4121 Andrew Lights, Apt. 625, Secret sk-test-ABCDEF1234567890abcdef', 'ABCDEF1234567890'),
                             ('9818 Lee Track, 73503, Kleinbury, card 4111 1111 1111 1111', '1111 1111'),
                             ('Address: 12 Oak Road, Token api_key=sk-live-1234567890abcdefghij', '1234567890abcdef')):
            out, _ = privacy.mask_local_details(text, names=True)
            self.assertNotIn(secret, out)
