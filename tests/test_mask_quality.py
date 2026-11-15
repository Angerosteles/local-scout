import unicodedata
import unittest

from plugin.scout import language, name_model, pii_model, privacy


class MaskQualityRegression(unittest.TestCase):
    def test_r3_address_is_one_complete_region(self):
        for value in ('os. Pogodne 26 m. 9, 35-001 Rzeszów',
                      'plac Targowy 11 lok. 3\n62-800 Kalisz',
                      'Bukowa 48/6, 25-001 Kielce',
                      'Stara Wieś 29, 07-100 Węgrów',
                      '67 Beech Road, London W1A 1HQ',
                      '28 Palm Street Apt 7\nMiami, FL 33101-2389'):
            with self.subTest(value=value):
                self.assertEqual(privacy.mask_local_details(value, names=True)[0], '[ADDRESS]')

    def test_r3_preposition_requires_street_evidence(self):
        text = 'Odbiór przy Modrzewiowej 38 m. 4.'
        start = text.index('Modrzewiowej')
        self.assertIn((start,len(text)-1,'address'),privacy.spans(text))
        for text in ('HTTP 502 Bad Gateway', 'Wall Street', 'Na Windows 11 działa.',
                     'Rozdział 38 opisuje przykład.', 'Kielce są miastem.'):
            self.assertNotIn('address', privacy.categories(text))

    def test_r3_village_without_postcode_requires_context(self):
        for prefix in ('Adres: ', 'Adres dostawy: ', 'Wieś: '):
            self.assertEqual(privacy.mask_local_details(prefix+'Podlesie 24 m. 8')[0],
                             prefix+'[ADDRESS]')
        for text in ('Wieś ma 24 mieszkańców.', 'Rozdział 24 opisuje przykład.'):
            self.assertNotIn('address',privacy.categories(text))

    def test_r3_newline_does_not_swallow_next_field(self):
        for tail in ('\nCountry of Origin: France', '\nShipment Date: 2020-05-17',
                     '\nDelivery Location: 87 Maple Road'):
            value = '29 Birch Street'
            masked = privacy.mask_local_details(value+tail)[0]
            self.assertTrue(masked.startswith('[ADDRESS]\n'),masked)

    def test_r3_polish_phone_formats_and_identifiers(self):
        for value in ('794-613-258', '683591274', '0048 796 281 435',
                      '(12) 614 72 83', '42-715-26-38', '+48 (91) 625-37-41'):
            self.assertEqual(privacy.mask_local_details('Zadzwoń pod '+value)[0],
                             'Zadzwoń pod [PHONE]')
        for text in ('NIP: 784-61-23-590', 'REGON: 683-217-459',
                     'PESEL: 92071415326', 'Faktura 683591274',
                     'Numer przesyłki: 794613258', 'Kwota 794 613 258 USD',
                     'SSN:** 683 217 459', '<biometricIdentifier>BIO-683217459</biometricIdentifier>'):
            self.assertNotIn('phone', privacy.categories(text))

    def test_r3_ping_context_for_local_phone(self):
        self.assertEqual(privacy.mask_local_details('Ping me at 731-4826.')[0],
                         'Ping me at [PHONE].')
        self.assertNotIn('phone', privacy.categories('Order 731-4826.'))

    def test_r3_placeholders_preserve_real_password_like_values(self):
        for value in ('<YOUR_KEY>', 'YOUR_ACCESS_KEY', 'REDACTED', 'XXXXXX',
                      '${SERVICE_KEY}', '{{SERVICE_KEY}}'):
            text = 'api_key = '+value
            self.assertEqual(privacy.mask_local_details(text)[0],text)
        for value in ('your-real-password-38', '<real89!>', 'redactedButReal58', 'XxX72!p'):
            text = 'password = '+value
            self.assertEqual(privacy.mask_local_details(text)[0],'[SECRET]')

    def test_r3_name_label_inside_address_preserves_union(self):
        from unittest.mock import patch
        text = '123 Oak Road'
        with patch.object(name_model, 'spans', return_value=[(4,7,'name')]):
            self.assertNotIn('name', [c for _,_,c in privacy.local_spans(text,names=True)])
            self.assertEqual(privacy.mask_local_details(text,names=True)[0],'[ADDRESS]')
        text += 'house'
        with patch.object(privacy, 'spans', return_value=[(0,12,'address')]), \
             patch.object(privacy.credentials, 'spans', return_value=[]), \
             patch.object(name_model, 'spans', return_value=[(4,len(text),'name')]):
            self.assertEqual(privacy.mask_local_details(text,names=True)[0],'[NAME]')

    def test_paragraph_language_and_safe_uncertainty(self):
        for text, expected in (
            ('Proszę o odpowiedź.', 'pl'), ('Please send the report.', 'en'),
            ('Please send the report, dziękuję.', 'both'), ('Jan Kowalski', 'both'),
            ('555-0199', 'both'), ('OK', 'both'),
        ):
            with self.subTest(text=text):
                self.assertEqual(language.detect(text), expected)
        text = 'Proszę o dokument.\n \nPlease call me.\n\nOK'
        self.assertEqual([language.detect(p) for _,p in language.paragraphs(text)], ['pl','en','both'])
        self.assertTrue(all(text[s:s+len(p)] == p for s,p in language.paragraphs(text)))

    def test_polish_vocatives_and_honorific_boundaries(self):
        # Bare forms have no dictionary boost; a title supplies structural evidence.
        for value in ('Kasiu', 'Krzysztofie', 'Joanno'):
            text = value + ', proszę o kontakt.'
            self.assertEqual(privacy.mask_local_details(text, names=True)[0], text)
            self.assertEqual(privacy.mask_local_details('Pani '+text, names=True)[0],
                             'Pani [NAME], proszę o kontakt.')
        for prefix, value in (('Panie ', 'Tomaszu'), ('Pani ', 'Magdaleno'), ('Panie ', 'Kowalski'),
                              ('Pani ', 'Wiśniewska'), ('Panie ', 'Janie Nowaku'),
                              ('Dziękuję ', 'Joannie Wiśniewskiej')):
            for form in (value, unicodedata.normalize('NFD', value)):
                with self.subTest(prefix=prefix, value=form):
                    self.assertEqual(privacy.mask_local_details(prefix+form+', proszę o kontakt.', names=True)[0],
                                     prefix+'[NAME], proszę o kontakt.')

    def test_sentence_capitalisation_needs_person_evidence(self):
        for text in ('May I ask a question?', 'Mark the document as read.',
                     'Will the service restart?', 'Bill the customer tomorrow.',
                     'Rose from the chair and left.', 'Wyniki są dostępne w biurze.',
                     'Pani Dyrektor, raport jest gotowy.', 'Panie Prezesie, dziękuję.'):
            with self.subTest(text=text):
                self.assertEqual(name_model.spans(text), [])
        self.assertIn((0,11,'name'), name_model.spans('Rose Miller will call tomorrow.'))

    def test_local_phone_and_currency(self):
        for text in ('555-0199', 'Call me at 555-0199.', 'You can reach me on 555-0199.'):
            with self.subTest(text=text):
                self.assertIn('phone', privacy.categories(text))
        for text in ('Ticket 555-0199 is closed.', 'Release 555-0199 is ready.',
                     '555-0199 Main Street', '501 234 567 zł', '222 333 444,50 PLN'):
            with self.subTest(text=text):
                self.assertNotIn('phone', privacy.categories(text))

    def test_explicit_pii_across_language_boundaries(self):
        for text, value, category in (
            ('Proszę wysłać do 123 Main Street.', '123 Main Street', 'address'),
            ('Please send to ul. Kwiatowa 7.', 'ul. Kwiatowa 7', 'address'),
            ('Please call Pani Zelvarowi tomorrow.', 'Zelvarowi', 'name'),
            ('Please call me.\n\nPani Magdaleno, dziękuję.', 'Magdaleno', 'name'),
        ):
            with self.subTest(text=text):
                start = text.index(value)
                spans = name_model.spans(text) if category == 'name' else privacy.spans(text)
                self.assertIn((start,start+len(value),category), spans)

    def test_paragraph_rules_preserve_classifier_context(self):
        text = 'Claimant: Barry J. Young\n\nContact Insurance Company for Further Assistance.'
        self.assertIn((10,24,'name'), name_model.spans(text))

    def test_candidate_budget_is_global_across_paragraphs(self):
        token = pii_model._CANDIDATE_BUDGET.set(1)
        try:
            with self.assertRaises(pii_model.CandidateBudgetExceeded):
                pii_model.spans('Phone: 555-0199\n\nPhone: 555-0188')
        finally:
            pii_model._CANDIDATE_BUDGET.reset(token)

    def test_honorific_in_explicit_book_title(self):
        self.assertEqual(name_model.spans('Pan Tadeusz to tytuł książki.'), [])
        self.assertEqual(name_model.spans('Pani Dorota to tytuł powieści.'), [])
        self.assertEqual(name_model.spans('Pan Tadeusz, proszę o odpowiedź.'), [(4,11,'name')])

    def test_currency_is_not_an_identifier_in_the_final_mask(self):
        for text in ('501 234 567 zł', '222 333 444,50 PLN', '123 456 789 złotych',
                     'Budżet: 501 234 567 zł.', 'Cost is 222 333 444,50 PLN.'):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text)[0], text)
        self.assertIn('ssn', privacy.categories('SSN: 501 234 567 PLN'))
        self.assertIn('phone', privacy.categories('Telefon: 501 234 567'))

    def test_all_false_address_examples_from_november_12(self):
        for text in (
            '2017 but were first discovered after a complaint',
            'According to a 2011 report in Fortune, this has resulted in a culture',
            'By 2013, Apple was using 100% renewable energy',
            'August 16, 2016, that Lens Technology, one of its major suppliers',
            'In March 2017, The Wall Street Journal reported that Apple',
            'Apple directly employs 10,000 workers across its retail and corporate divisions',
            'In May 1790, construction began on Government House',
            '2.2.1 Recommended header fields', '2.2.2 New header fields',
            '2.2.3 Omitted Header Fields',
            'Upgraded to IBM HTTP Server 7.0.0.41, is TLS 1.2 supported?',
            '9 zapasowe resety Codexa',
        ):
            with self.subTest(text=text):
                self.assertNotIn('address', privacy.categories(text))

    def test_street_addresses_in_prose_cover_the_entire_value(self):
        for value in ('1600 Pennsylvania Avenue NW', '221B Baker Street',
                      '190 High Street', '3 Cherry Street', '30 Market Street'):
            with self.subTest(value=value):
                self.assertEqual(privacy.mask_local_details('Visit ' + value + ' today.')[0],
                                 'Visit [ADDRESS] today.')

    def test_polish_street_postcode_and_city(self):
        for value in ('ul. Łąkowej 7/3', 'ul. Kwiatowa 12, 00-001 Warszawa',
                      'al. Jana Pawła II 15, 00-175 Warszawa',
                      'ul. 3 Maja 8A, 90-001 Łódź', '00-001 Warszawa'):
            with self.subTest(value=value):
                self.assertEqual(privacy.mask_local_details('Adres: ' + value + '.')[0],
                                 'Adres: [ADDRESS].')

    def test_full_name_expansion_and_possessive_boundary(self):
        for prefix, name in (('was ', 'Nicholas Meyer'), ('acquire ', 'Jimmy Iovine'),
                             ('President ', 'Theodore Roosevelt'), ('appointed ', 'Michael Spindler'),
                             ('by ', 'Cory Doctorow'), ('', 'Eddy Cue'),
                             ('', 'Craig Federighi'), ('', 'Jennifer Newstead'),
                             ('', 'Johny Srouji'), ('', 'Jean-Louis Gassée')):
            with self.subTest(name=name):
                text = prefix + name + "'s work"
                self.assertIn((len(prefix), len(prefix)+len(name), 'name'), name_model.spans(text))

    def test_initials_are_included_between_name_parts(self):
        for value in ('Roy T. Fielding', 'Julian F. Reschke'):
            with self.subTest(value=value):
                self.assertIn((0, len(value), 'name'), name_model.spans(value+' (editor)'))

    def test_structural_work_company_and_label_contexts_are_untouched(self):
        for text in ('Please read "Zelvar Morven" before tomorrow.',
                     'The company Zelvar Holdings requested the report yesterday.', 'Owner: SRE On-Call'):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text, names=True)[0], text)

    def test_polish_declension_and_unicode_boundaries(self):
        for name in ('Anną Kowalską', 'Anny Kowalskiej', 'Annie Kowalskiej',
                     'Annę Kowalską', 'Janem Nowakiem', 'Żanetą Wiśniewską',
                     'Łukasz Żółkiewski', 'Maria Nowak-Kowalska'):
            for form in (name, unicodedata.normalize('NFD', name)):
                with self.subTest(name=form):
                    self.assertEqual(privacy.mask_local_details('Pani '+form+'.', names=True)[0],
                                     'Pani [NAME].')
        for text in ('Pamięć podręczna jest pełna.', 'Ładowanie zakończone.',
                     'Środowisko działa poprawnie.', 'Żółte światło oznacza ostrzeżenie.'):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text, names=True)[0], text)
        self.assertEqual(privacy.mask_local_details('Spotkałem się z Anną Kowalską w Łodzi.', names=True)[0],
                         'Spotkałem się z [NAME] w Łodzi.')

    def test_polish_phones_and_grouped_numbers(self):
        for text in ('501 234 567', '123-456-789', 'Kontakt: 501-234-567',
                     'Telefon: 22 123 45 67', '+48 501 234 567'):
            with self.subTest(text=text):
                self.assertIn('phone', privacy.categories(text))
        for text in ('120 000 / 35 788', '120 000 000', 'Budżet wynosi 120 000 zł, a koszt 35 788 zł.'):
            with self.subTest(text=text):
                self.assertNotIn('phone', privacy.categories(text))

    def test_rfc_grammar_and_content_length_are_not_secrets_or_cards(self):
        for text in ('token         = <token, see [RFC7230], Section 3.2.6>',
                     'token = <token, see [RFC7230], Section 3.2.6>'):
            self.assertNotIn('secret', privacy.categories(text))
        self.assertNotIn('card', privacy.categories('Content-Length: 1234567890987'))

    def test_existing_protected_formats_remain_detected(self):
        for cat, text in (('email', 'test@example.invalid'), ('iban', 'PL61109010140000071219812874'),
                          ('iban', 'GB82 WEST 1234 5698 7654 32'),
                          ('card', 'Card 4111 1111 1111 1111'), ('card', 'Card 378282246310005'),
                          ('phone', 'Call (415) 555-2671'), ('phone', 'Phone: +44 20 7946 0958'),
                          ('secret', 'password=ExampleOnly123!'),
                          ('secret', 'token = „zażółć”'),
                          ('secret', 'Use sk-test-ABCDEF1234567890abcdef')):
            with self.subTest(category=cat):
                self.assertIn(cat, privacy.categories(text))


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


class StreetTypeTests(unittest.TestCase):
    def test_parade_quay_quadrant_and_apostrophes(self):
        for text in ("52 King's Parade, Cambridge CB2 1TN", "52 King’s Parade, Cambridge CB2 1TN",
                     "12 St Mary's Row, London SW1A 1AA", "4 Queen's Road, London SW1A 1AA",
                     "14 Regent Quay, Aberdeen AB11 5BE", "92B Highbury Quadrant, London N5 2TE"):
            for names in (False, True):
                with self.subTest(text=text, names=names):
                    self.assertEqual(privacy.mask_local_details(text, names=names)[0], '[ADDRESS]')


if __name__ == '__main__':
    unittest.main()
