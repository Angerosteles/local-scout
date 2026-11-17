"""Synthetic structural-mask regressions, independent of development holdouts."""
import unittest

from plugin.scout import pii_model, privacy


def iban(country, bban):
    tail = ''.join(str(ord(c) - 55) for c in country) + '00'
    numeric = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in bban)
    return country + f'{98 - int(numeric + tail) % 97:02d}' + bban


def card(prefix):
    return next(prefix + str(i) for i in range(10) if pii_model.luhn(prefix + str(i)))


class Tightening19(unittest.TestCase):
    def covered(self, text, value, category):
        start = text.index(value)
        masked, index = privacy._normalise_for_detection(text)
        found = privacy.local_spans(masked, names=False)
        if index is not None:
            found = [(index[a], index.ends[b - 1], c) for a, b, c in found]
        self.assertTrue(any(a <= start and start + len(value) <= b and c == category
                            for a, b, c in found), (category, found))

    def test_iban_neighbours_and_alphanumeric_bban(self):
        first = iban('DE', '408502991234567890')
        second = iban('FR', '2406810300ABCDEF2345678')
        for join in (' and ', ' to ', '; ', '\n'):
            text = first + join + second
            self.covered(text, first, 'iban')
            self.covered(text, second, 'iban')

    def test_iban_rejects_checksum_and_overlong_token(self):
        value = iban('DE', '408502991234567890')
        self.assertFalse(any(c == 'iban' for _, _, c in privacy.spans(value + '9')))
        damaged = value[:2] + '00' + value[4:]
        self.assertFalse(any(c == 'iban' for _, _, c in privacy.spans(damaged)))

    def test_explicit_secret_fields_and_url_password(self):
        value = 'q7Vn4pX9rM6dL2wZ8aK5'
        for label in ('api_key', 'otp_secret_key', 'backup_api_key', 'service_token',
                      'DEPLOY_SECRET', 'SIGNING_KEY', 'pwd'):
            for text in (f'"{label}": "{value}"', f'{label}: {value}', f'{label}={value}'):
                self.covered(text, value, 'secret')
        self.covered('postgres://reader:violet!42@db.example.org/app', 'violet!42', 'secret')
        self.covered('postgres://reader:violet%40blue@db.example.org/app', 'violet%40blue', 'secret')
        self.covered('"backup_api_key": "violet\\\"blue42"', 'violet\\\"blue42', 'secret')
        self.covered('"backup_api_key": "' + 'v' * 5000 + '"', 'v' * 5000, 'secret')
        for label in ('backup_api_key', 'service_token'):
            self.covered(f'"{label}": "violetmoon"', 'violetmoon', 'secret')

    def test_new_secret_fields_exclude_templates_and_ordinary_keys(self):
        for text in ('api_key: "${SERVICE_KEY}"', 'service_token: YOUR_SERVICE_TOKEN',
                     'sort_key: alphabetical', 'primary_key: 1234567890123456',
                     'pwd: false', 'api_key: "<api_key>"'):
            self.assertFalse(any(c == 'secret' for _, _, c in privacy.spans(text)), text)

    def test_phone_formats_and_complete_extensions(self):
        for value in ('(234)567-8910', '234.567.8910', '06 24 38 52 76'):
            for extension in ('', ' ext 417', 'x861', ' wew. 23'):
                whole = value + extension
                self.covered('**Phone:** ' + whole, whole, 'phone')
        self.covered('**Phone:** 2345678910', '2345678910', 'phone')
        self.covered('06 24 38 52 76', '06 24 38 52 76', 'phone')
        self.covered('(234 567-8910', '(234 567-8910', 'phone')

    def test_phone_rejects_ip_and_labelled_order(self):
        for text in ('IP: 10.24.38.52', 'Order: 06 24 38 52 76', 'Invoice: 2345678910'):
            self.assertFalse(any(c == 'phone' for _, _, c in privacy.spans(text)), text)

    def test_amex_and_numbered_card_lists(self):
        amex = card('37428163092517')
        visa = card('453218760943821')
        for value in (amex, amex[:4] + '-' + amex[4:10] + '-' + amex[10:],
                      amex[:4] + ' ' + amex[4:10] + ' ' + amex[10:], visa):
            for marker in ('', '2) ', '3. '):
                self.covered(marker + value, value, 'card')

    def test_list_does_not_bypass_luhn(self):
        value = card('453218760943821')
        damaged = value[:-1] + str((int(value[-1]) + 1) % 10)
        self.assertFalse(any(c == 'card' for _, _, c in privacy.spans('2. ' + damaged)))
        self.assertFalse(any(c == 'card' for _, _, c in privacy.spans('2. ' + '0' * 16)))

    def test_ssn_list_context_is_section_scoped(self):
        text = 'SSN list:\nAvery: 427183956\n1) Morgan: 518294067'
        self.covered(text, '427183956', 'ssn')
        self.covered(text, '518294067', 'ssn')
        long_list = 'SSN list:\n' + ''.join(f'Person {i}: 4281839{i:02d}\n' for i in range(14))
        self.covered(long_list + 'Morgan: 518294067', '518294067', 'ssn')
        for other in ('SSN list:\nEnd of section.\nOrder: 427183956',
                      'SSN list:\nInvoice: 427183956', 'Build IDs:\nAvery: 427183956'):
            self.assertFalse(pii_model._ssn_list_context(other, other.index('427183956'), len(other)))

    def test_ssn_list_keeps_validity_gate(self):
        text = 'SSNs:\nAvery: 927183956\nMorgan: 427003956'
        self.assertFalse(any(c == 'ssn' for _, _, c in privacy.spans(text)))

    def test_encoded_offsets_cover_complete_source_sequences(self):
        for value in ('avery&#64;example.org', 'avery%40example%2Eorg',
                      'avery@example.or&#103;', 'avery@example.or%67'):
            self.covered('prefix ' + value + ' suffix', value, 'email')
            self.assertEqual(privacy.mask_local_details('prefix ' + value + ' suffix')[0],
                             'prefix [EMAIL] suffix')
        value = card('453218760943821')
        for encoded in (''.join(f'&#{ord(c)};' for c in value),
                        ''.join(f'%{ord(c):02X}' for c in value)):
            self.covered('Card: ' + encoded, encoded, 'card')

    def test_checksum_values_with_alternative_separators(self):
        pan = card('453218760943821')
        account = iban('DE', '408502991234567890')
        head = '0422296183'
        pesel = head + str(-sum(int(d)*w for d, w in zip(head, (1,3,7,9,1,3,7,9,1,3))) % 10)
        for value, category in ((pan, 'card'), (account, 'iban'), (pesel, 'pesel')):
            for sep in ('\n', '.', '/'):
                variant = value[:len(value)//2] + sep + value[len(value)//2:]
                self.covered(variant, variant, category)

    def test_homoglyphs_only_in_mixed_alphabet_tokens(self):
        self.covered('Contact: avеry@example.org', 'avеry@example.org', 'email')
        text = 'Русское слово секрет остаётся текстом.'
        self.assertEqual(privacy._normalise_for_detection(text), (text, None))
        self.assertEqual(privacy._normalise_for_detection('m%FFail')[0], 'm%FFail')

    def test_encoded_utf8_and_nfkc_intervals(self):
        clean, index = privacy._normalise_for_detection('%C4%85&#xFB03;')
        self.assertEqual(clean, 'ąffi')
        self.assertEqual(index, [0, 6, 6, 6])
        self.assertEqual(index.ends, [6, 14, 14, 14])

    def test_alternative_separators_do_not_skip_validation(self):
        pan = card('453218760943821')
        damaged = pan[:-1] + str((int(pan[-1]) + 1) % 10)
        value = damaged[:8] + '\n' + damaged[8:]
        self.assertFalse(any(c == 'card' for _, _, c in privacy.spans(value)))
        self.assertFalse(any(c == 'pesel' for _, _, c in privacy.spans('920714/15327')))
        head = '0422296183'
        valid_check = -sum(int(d)*w for d, w in zip(head, (1,3,7,9,1,3,7,9,1,3))) % 10
        invalid = head[:6] + '/' + head[6:] + str((valid_check + 1) % 10)
        self.assertFalse(any(c == 'pesel' for _, _, c in privacy.spans(invalid)))
        self.assertFalse(any(c == 'card' for _, _, c in privacy.spans('Content-Length: 45321876/09438210')))



class LonePercent(unittest.TestCase):
    def test_a_lone_percent_is_kept_and_the_password_stays_masked(self):
        from plugin.scout import privacy
        self.assertEqual(privacy._normalise_for_detection('hasło Rzeka%Glacier147')[0], 'hasło Rzeka%Glacier147')
        self.assertEqual(privacy.mask_local_details('tymczasowe hasło do konta to Rzeka%Glacier147;')[0],
                         'tymczasowe hasło do konta to [SECRET];')


class ChecksumShape(unittest.TestCase):
    def test_versions_timestamps_floats_and_hex_dumps_are_not_cards(self):
        from plugin.scout import privacy
        for text in ('type=AVC msg=audit(1389964628.602): denied', 'register value (f: 1279256.000000)',
                     'zip im.properties 1.0.0.200911091101',
                     'V5.0.7.0 - 5.0.7.2 5.0.8.0', '8: 00000000/009B176C   9: 00000000/00000080\n         085'):
            self.assertNotIn('card', privacy.categories(text)); self.assertNotIn('pesel', privacy.categories(text))

    def test_real_grouping_still_bridges_lines_and_separators(self):
        from plugin.scout import privacy
        for text in ('card 4539.1488.0343.6467', 'card 4539 1488\n0343 6467', 'Amex 3746/572918/40259'):
            self.assertIn('card', privacy.categories(text), text)

    def test_the_bare_word_password_is_not_a_secret_value(self):
        from plugin.scout import privacy
        self.assertNotIn('secret', privacy.categories('KRZ_CONN_PASSWORD=password'))


class VersionTables(unittest.TestCase):
    def test_software_version_tables_are_not_phones(self):
        from plugin.scout import privacy
        text = ('9.0.0.4 (Windows, Linux, AIX) 8.0.50.52 7.1 SR 3 FP 10 6.3.1.12 (6.4.0.4 Linux) '
                '8.0.0.4 (Solaris, HP) 8.0.50.52')
        self.assertNotIn('phone', privacy.categories(text))

    def test_dotted_phones_stay_masked(self):
        from plugin.scout import privacy
        for text in ('call me at 1.800.555.0199', 'phone: 415.555.0132'):
            self.assertIn('phone', privacy.categories(text), text)
