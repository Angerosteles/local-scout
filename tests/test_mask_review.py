import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from plugin.scout import privacy, language

# These values are synthetic regression fixtures.
PROBES = [
    ('password = your_real_pass_123', 'your_real_pass_123', 'secret', None),
    ('api_key = Your_Secret_Key_99', 'Your_Secret_Key_99', 'secret', None),
    ('Authorization: Bearer your_secret_token_123456', 'your_secret_token_123456', 'secret', None),
    ('Use sk-your_real_key_99', 'sk-your_real_key_99', 'secret', None),
    ('pwd = your_real_pass_123', 'your_real_pass_123', 'secret', None),
    ('password =\nYour_Secret_99', 'Your_Secret_99', 'secret', None),
    ('your_secret_99', 'your_secret_99', 'secret', 'password'),
    ('secret = "your_secret_99"', 'your_secret_99', 'secret', None),
    ('api_key = <A7K9M2P4Q8>', '<A7K9M2P4Q8>', 'secret', None),
    ('token = <SECRET-9921-AB>', '<SECRET-9921-AB>', 'secret', None),
    ('password = ${S3cr3t_P4ss}', '${S3cr3t_P4ss}', 'secret', None),
    ('password = {{S3cr3t_P4ss}}', '{{S3cr3t_P4ss}}', 'secret', None),
    ('Telefon: 501 234 567 PLN', '501 234 567', 'phone', None),
    ('Zadzwoń pod +48 501 234 567 zł', '+48 501 234 567', 'phone', None),
    ('Phone: (415) 555-2671 USD', '(415) 555-2671', 'phone', None),
    ('Tel: 501-234-567 EUR', '501-234-567', 'phone', None),
    ('My SSN is 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('NINO: AB123456C GBP', 'AB123456C', 'ssn', None),
    ('I do! Located at: 10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Proszę: Shipping to: 10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Residence: 10 Calle Mayor, León', '10 Calle Mayor, León', 'address', None),
    ('I do: ping me at 731-4826.', '731-4826', 'phone', None),
    ('Proszę: ping me at 731-4826.', '731-4826', 'phone', None),
    ('Pan-European support: ping me at 731-4826.', '731-4826', 'phone', None),
    ('Moj numer stacjonarny to (22) 123 45 67 (contact)', '(22) 123 45 67', 'phone', None),
    ('This is my number: 501-234-567.', '501-234-567', 'phone', None),
    ('This is my number: (22) 123 45 67.', '(22) 123 45 67', 'phone', None),
    ('Please talk with Panem Kowalskim.', 'Kowalskim', 'name', None),
    ('Numer do zamówienia: +48 501 234 567', '+48 501 234 567', 'phone', None),
    ('Telefon ws. zamówienia: 501-234-567', '501-234-567', 'phone', None),
    ('Kontakt ws. faktury: 501-234-567', '501-234-567', 'phone', None),
    ('For invoice 9912, call me at 501 234 567', '501 234 567', 'phone', None),
    ('My order failed, ping me at 731-4826', '731-4826', 'phone', None),
    ('Call phone-415-555-2671', '415-555-2671', 'phone', None),
    ('Kontakt tel-501-234-567', '501-234-567', 'phone', None),
    ('Proszę o odpowiedź.\r\n\r\nPing me at 731-4826.', '731-4826', 'phone', None),
    ('Address:\n\n10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Address:\r\n\r\n10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Authorization: Bearer <MyRealToken1234567890!>', '<MyRealToken1234567890!>', 'secret', None),
    ('api_key=sk-REDACTED', 'sk-REDACTED', 'secret', None),
    ('Telefon ws. faktury: 501 234 567', '501 234 567', 'phone', None),
    ('Telefon ws. faktury: 501-234-567', '501-234-567', 'phone', None),
    ('al. Jana Pawła II 15, 00-175 Warszawa', 'al. Jana Pawła II 15, 00-175 Warszawa', 'address', None),
    ('ul. Adama Mickiewicza 10, 30-059 Kraków', 'ul. Adama Mickiewicza 10, 30-059 Kraków', 'address', None),
    ('123 Oak Road John Smith', '123 Oak Road', 'address', None),
    ('Address: 100 Anna Kowalska, Warsaw', '100 Anna Kowalska, Warsaw', 'address', None),
    ('Your_Secret_Key_99', 'Your_Secret_Key_99', 'secret', 'api_key'),
    ('Telefon ws. faktury: 0048 501 234 567 EUR', '0048 501 234 567', 'phone', None),
    ('Address: Unit 52 409 Oliver Service Way, South Alexandra', 'Unit 52 409 Oliver Service Way, South Alexandra', 'address', None),
    ('Address: Flat 80 96 Carlton River', 'Flat 80 96 Carlton River', 'address', None),
    ('Sales Order dated 1990-10-10 for Customer at 4874 Leslie Falls, South Jaredstad', '4874 Leslie Falls, South Jaredstad', 'address', None),
    ('Delivered to 39 Christine Glade. Contact: Oliver.', '39 Christine Glade', 'address', None),
    ('Address: H.No. 08 Dutta Zila, Apt. H.No. 73', 'H.No. 08 Dutta Zila, Apt. H.No. 73', 'address', None),
    ('Address: H.No. 079 Singhal Circle, Apt. 55/76', 'H.No. 079 Singhal Circle, Apt. 55/76', 'address', None),
    ('Address: 123 5th Avenue', '123 5th Avenue', 'address', None),
    ('Lord Byron, poet.', 'Byron', 'name', None),
    ('First Lady Melania Trump renovated the room.', 'Melania Trump', 'name', None),
]

R3C_PROBES = [
    ('Address:10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Shipping to:39 Christine Glade', '39 Christine Glade', 'address', None),
    ('Located at:10 Calle Mayor, Madrid', '10 Calle Mayor, Madrid', 'address', None),
    ('Address-10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Address/Billing:10 Downing North, London', '10 Downing North, London', 'address', None),
    ('Address: 10 Ave. Victor Hugo, Paris', '10 Ave. Victor Hugo, Paris', 'address', None),
    ('Address: 123 St. Martin North, London', '123 St. Martin North, London', 'address', None),
    ('Address: 45 Dr. Martin North, London', '45 Dr. Martin North, London', 'address', None),
    ('Address: 55 Mt. Vernon North, Boston', '55 Mt. Vernon North, Boston', 'address', None),
    ('Address: 100 N. Main North, London', '100 N. Main North, London', 'address', None),
    ('Address: 100 5 Avenue', '100 5 Avenue', 'address', None),
    ('I live at 100 5 Avenue', '100 5 Avenue', 'address', None),
    ('Meet me at 456 21 Street', '456 21 Street', 'address', None),
    ('I live at 123 5th Avenue, New York', '123 5th Avenue, New York', 'address', None),
    ('I live at 450 2nd Street', '450 2nd Street', 'address', None),
    ('Office at 100 5th Avenue', '100 5th Avenue', 'address', None),
    ('Phone for order: 415-555-2671', '415-555-2671', 'phone', None),
    ('Call regarding invoice: 415-555-2671', '415-555-2671', 'phone', None),
    ('Call for tracking: 415-555-2671', '415-555-2671', 'phone', None),
    ('Telefon w sprawie zamówienia: 501-234-567', '501-234-567', 'phone', None),
    ('Telefon ws. przesyłki: 501-234-567', '501-234-567', 'phone', None),
    ('Telefon do faktury: 501-234-567', '501-234-567', 'phone', None),
    ('My phone is 415-555-2671 USD', '415-555-2671', 'phone', None),
    ('Mój numer to 501 234 567 PLN', '501 234 567', 'phone', None),
    ('Employee SSN - 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('SSN/PESEL: 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('Taxpayer ID: 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('Social Security #: 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('Nasz oddzial w Warszawie: 22 123 45 67 (contact)', '22 123 45 67', 'phone', None),
    ('Komorka do Marka (contact): 22 123 45 67', '22 123 45 67', 'phone', None),
    ('Kowalski (contact): 501234567', '501234567', 'phone', None),
    ('Please call our branch: 501234567', '501234567', 'phone', None),
    ('The Warsaw branch: 501234567', '501234567', 'phone', None),
    ('Please call our branch: 22 123 45 67', '22 123 45 67', 'phone', None),
    ('Spotkałem się przy Annie Kowalskiej', 'Annie Kowalskiej', 'name', None),
    ('Rozmawiałem przy Janie', 'Janie', 'name', None),
    ('Widzę w Annie potencjał', 'Annie', 'name', None),
    ('We Franciszku jest nadzieja', 'Franciszku', 'name', None),
    ('token = <token, abcd1234>', 'abcd1234', 'secret', None),
    ('mysecretpassword', 'mysecretpassword', 'secret', 'api__key'),
    ('ul. Jana\u200b Pawła II 15, 00-175 Warszawa', 'ul. Jana\u200b Pawła II 15, 00-175 Warszawa', 'address', None),
    ('100 John\uff21 Smith Road', '100 John\uff21 Smith Road', 'address', None),
    ('Parties: ZZ 57 31 99 T, customer@example.org', 'ZZ 57 31 99 T', 'ssn', None),
    ('Personal data like SSNs (ZZ 307539 T, 806 832 556)', '806 832 556', 'ssn', None),
]
NEGATIVE_PROBES = [
    'Order ID: AB123456C', 'Invoice: AB123456C',
    'token = <token, see [RFC7230], Section 3.2.6>',
    'Tracking: 501234567', 'Invoice: 22 123 45 67',
    'My phone order: 501234567', 'W Warszawie jest biuro.',
]
R3D_PROBES = [
    ('Address: 10 Oak Park North, London', '10 Oak Park North, London', 'address', None),
    ('Address: 100 Green Valley Industrial Estate', '100 Green Valley Industrial Estate', 'address', None),
    ('10 Oak Park North, London', '10 Oak Park North, London', 'address', 'address'),
    ('+1 415 555 2671 USD', '+1 415 555 2671', 'phone', None),
    ('Call me: +44 20 7946 0958 GBP', '+44 20 7946 0958', 'phone', None),
    ('Tel.: +44 20 7946 0958 GBP', '+44 20 7946 0958', 'phone', None),
    ('Call us at 415-555-2671 USD', '415-555-2671', 'phone', None),
    ('SSN number: 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('Social Security No.: 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('PESEL: 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('Taxpayer ID (TIN): 123-45-6789 PLN', '123-45-6789', 'ssn', None),
    ('Tel.: 501-234-567 PLN', '501-234-567', 'phone', None),
    ('Tel. ws. faktury: 501-234-567', '501-234-567', 'phone', None),
    ('Telefon kontaktowy ws. faktury: 501-234-567', '501-234-567', 'phone', None),
    ('Spotkałem się przy Adamie Kowalskim', 'Adamie Kowalskim', 'name', None),
    ('Przy Robercie Lewandowskim', 'Robercie Lewandowskim', 'name', None),
    ('Rozmawiałem przy Marku', 'Marku', 'name', None),
    ('W Zuzannie jest siła', 'Zuzannie', 'name', None),
    ("w Annie Hathaway's film", 'Annie Hathaway', 'name', None),
    ('Address: 10 Ft. Worth North, Texas', '10 Ft. Worth North, Texas', 'address', None),
    ('Address: 123 Prof. Januszewskiego North, London', '123 Prof. Januszewskiego North, London', 'address', None),
    ('Address: 10 Bldg. B, North Wing', '10 Bldg. B, North Wing', 'address', None),
    ('Address: 10 Downing North. London', '10 Downing North. London', 'address', None),
    ('Send SMS to Marek: 501234567 (contact)', '501234567', 'phone', None),
    ('Residence: 10 Łąkowa North, Kraków', '10 Łąkowa North, Kraków', 'address', None),
    ('Address: 10, 2nd North, London', '10, 2nd North, London', 'address', None),
    ('password = <password, abcd1234>', 'abcd1234', 'secret', None),
    ('token = <token, see [RFC7230], abcd1234>', 'abcd1234', 'secret', None),
    ('token = <token, see [RFC7230], password=MyRealPass123>', 'MyRealPass123', 'secret', None),
    ('+49 30 123456789 EUR', '+49 30 123456789', 'phone', None),
    ('+48 501 234 567 PLN', '+48 501 234 567', 'phone', None),
    ('0048 501 234 567 PLN', '0048 501 234 567', 'phone', None),
    ('Numer telefonu: 501-234-567 PLN', '501-234-567', 'phone', None),
    ('National Insurance Number: AB123456C GBP', 'AB123456C', 'ssn', None),
    ('450 2nd Street', '450 2nd Street', 'address', None),
    ('Address: 2 2nd Street', '2 2nd Street', 'address', None),
    ('Phone: 10 200 30 40 USD', '10 200 30 40', 'phone', None),
]
R3D_NEGATIVE_PROBES = [
    ('In 2024 2nd place went to Alice', 'address'),
    ('Race 1 2nd place finish', 'address'),
    ('Stage 2 1st run completed', 'address'),
    ('Round 2 1st pass', 'address'),
    ('Chapter 1 2nd view', 'address'),
    ('Track 1 2nd lane', 'address'),
    ('Score 10 1st point', 'address'),
    ('Version 2 2nd Street release', 'address'),
    ('Chapter 5 Avenue of the Stars', 'address'),
    ('Version 2nd Street release', 'address'),
    ('Dimensions: 10 200 30 40', 'phone'),
    ('Amounts: 10 200 30 40', 'phone'),
    ('The dimensions are: 10 200 30 40', 'phone'),
    ('Page numbers: 10 200 30 40', 'phone'),
    ('Address: 100 items in stock', 'address'),
    ('Shipping to: 5 countries in Europe', 'address'),
]
# Known optional gap, retained in the differential corpus and checked explicitly.
R3D_DEFERRED_PROBES = ['The branch is in Warsaw: 501234567']
PROBES += R3C_PROBES + R3D_PROBES


def load_baseline(path):
    """Load an isolated package without changing imports of the candidate."""
    path = Path(path).resolve() / 'scout'
    name = '_mask_baseline_' + path.parent.name.replace('.', '_')
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path / '__init__.py',
                                                     submodule_search_locations=[str(path)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return __import__(name + '.privacy', fromlist=['privacy'])


def full_coverage(detector, text, field=None):
    """Record spans from the actual full-mask call, then verify output reconstruction."""
    captured = []
    original = detector.local_spans if hasattr(detector, 'local_spans') else None
    if original:
        def record(work, names=False, **kwargs):
            found = original(work, names=names, **kwargs)
            captured.extend(found)
            return found
        context = patch.object(detector, 'local_spans', side_effect=record)
    else:
        # The released version calls the three detectors separately.
        from contextlib import ExitStack
        context = ExitStack()
        name_model = __import__(detector.__package__ + '.name_model', fromlist=['name_model'])
        for module in (detector, detector.credentials, name_model):
            method = module.spans
            def record(work, _method=method):
                found = _method(work)
                captured.extend(found)
                return found
            context.enter_context(patch.object(module, 'spans', side_effect=record))
    with context:
        masked, categories = detector.mask_local_details(text, names=True, field=field)
    clean, index = detector._normalise_for_detection(text)
    n = len(detector._field_probe(field))
    found = [(max(s,n)-n, e-n, c) for s,e,c in captured if e>n]
    if index is not None:
        found = [(index[s],(index.ends[e-1] if hasattr(index,'ends') else index[e-1]+1),c) for s,e,c in found if e>s]
    regions = []
    for s,e,c in sorted(found, key=lambda x:(x[0],-(x[1]-x[0]))):
        if regions and s < regions[-1][1]:
            regions[-1][1] = max(e,regions[-1][1])
            regions[-1][2].append((e-s,c))
        else:
            regions.append([s,e,[(e-s,c)]])
    out, stop = [], 0
    for s,e,members in regions:
        out.extend((text[stop:s], '['+max(members,key=lambda x:x[0])[1].upper()+']'))
        stop = e
    out.append(text[stop:])
    if ''.join(out) != masked:
        raise AssertionError('Full-mask reconstruction differs from actual output')
    covered = {i for s,e,_ in regions for i in range(s,e)}
    return covered, found, masked, categories


class ReviewRegression(unittest.TestCase):
    # The 3c/3d review probes measure detection reach, i.e. the deep mode (basic keeps full names only).
    def setUp(self):
        import functools
        from unittest import mock
        patcher = mock.patch.object(privacy, 'mask_local_details', functools.partial(privacy.mask_local_details, name_masking='deep'))
        patcher.start(); self.addCleanup(patcher.stop)

    def test_r3c_negative_controls(self):
        for text in NEGATIVE_PROBES:
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text, names=True)[0], text)
        self.assertNotIn('phone', privacy.categories('Personal data like SSNs (ZZ 307539 T, 806 832 556) and date of birth (1977-10-18)'))
        for text, value, cat, field in R3C_PROBES:
            if cat == 'name':
                with self.subTest(text=text):
                    self.assertEqual(privacy.mask_local_details(text, names=True)[0], text.replace(value, '[NAME]', 1))

    def test_r3d_negative_controls_and_language(self):
        from plugin.scout import pii_model
        for text, category in R3D_NEGATIVE_PROBES:
            with self.subTest(text=text):
                self.assertNotIn(category, privacy.mask_local_details(text, names=True)[1])
        for value in ('10 200 30 40', '22 123 45 67'):
            self.assertFalse(pii_model.accepted(value, 0, len(value), 'phone', 'en'))

    @unittest.expectedFailure
    def test_r3d_unlabelled_branch_known_gap(self):
        for text in R3D_DEFERRED_PROBES:
            self.assertIn('phone', privacy.mask_local_details(text, names=True)[1])

    def test_all_review_probes(self):
        for text,value,category,field in PROBES:
            with self.subTest(text=text, field=field):
                covered, spans, masked, cats = full_coverage(privacy,text,field)
                start = text.index(value)
                self.assertTrue(set(range(start,start+len(value))) <= covered, masked)
                self.assertIn(category,cats)

    def test_175_non_name_character_coverage_on_review_and_fixture(self):
        import hashlib
        import json
        fixture = Path(__file__).with_name('fixtures_mask_175.jsonl')
        for line in fixture.read_text().splitlines():
            row = json.loads(line)
            with self.subTest(id=row['id']):
                self.assertEqual(hashlib.sha256(row['text'].encode()).hexdigest(),row['sha256'])
                if row['allowed']:
                    self.assertTrue(row['exception_reasons'])
                new = full_coverage(privacy,row['text'],row.get('field'))[0]
                # English name acceptance now follows the separately tested evidence rule.
                old = {i for a,b,c in row['released_spans'] if c != 'name' for i in range(a,b)}
                self.assertFalse((old-new)-set(row['allowed']), 'New unjustified exposure')

    def test_template_filter_is_case_sensitive_and_bounded(self):
        for value in ('YOUR_REAL_PASS_123', '<SECRET-9921-AB>', '${mixedCase}', '{{S3cr3t_P4ss}}'):
            self.assertFalse(privacy._secret_placeholder(value))
        for text in ('REGON: 683-217-459', 'Use sk-REDACTED'):
            self.assertNotIn('secret',privacy.categories(text))

    def test_routing_crlf_and_ambiguous_words(self):
        for text in ('I do!', 'w z', 'León', 'Pan-European'):
            self.assertNotEqual(language.detect(text),'pl')
        text = 'Proszę o odpowiedź.\r\n\r\nPlease call me.'
        self.assertEqual([language.detect(p) for _,p in language.paragraphs(text)],['pl','en'])



from plugin.scout import name_model as nm, name_model_g as g, paragraph_context as pc, privacy


class EmailBounds(unittest.TestCase):
    def test_adversarial_runs_without_domain(self):
        for text in ('a.b-c_d' * 7000 + '@', 'a.' * 10000 + '@', 'a+' * 10000 + '@'):
            with self.subTest(length=len(text)):
                self.assertEqual(privacy.mask_local_details(text), (text, ()))
                self.assertFalse(pc.features(text)[14])

    def test_rfc_bounds_and_existing_punctuation(self):
        domain = 'a' * 63 + '.' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 63
        for value in ('a' * 64 + '@example.org', 'user@' + domain, 'user+tag_a.b-c@example.org'):
            with self.subTest(value=value):
                self.assertEqual(privacy.mask_local_details(value)[0], '[EMAIL]')
                self.assertTrue(pc.features(value)[14])
        for value in ('a' * 65 + '@example.org', 'user@' + domain + 'd'):
            with self.subTest(length=len(value)):
                self.assertNotIn('email', privacy.categories(value))
                self.assertFalse(pc.features(value)[14])
        self.assertEqual(privacy.mask_local_details('Mail: a@example.org.')[0], 'Mail: [EMAIL].')


class NameSpanBounds(unittest.TestCase):
    def test_long_title_run_does_not_erase_document(self):
        text = ('Alpha Beta Gamma Delta Epsilon Zeta Theta Kappa Lambda Sigma ' * 1700)[:100000]
        names = nm.spans(text)
        self.assertTrue(names)
        self.assertTrue(all(len(nm.tokens(text[s:e])) <= nm.MAX_NAME_TOKENS for s, e, _ in names))
        self.assertTrue(all(s > 0 or e < len(text) for s, e, _ in names))
        masked, _ = privacy.mask_local_details(text, names=True)
        self.assertIn('Alpha', masked)
        self.assertIn('Epsilon', masked)

    def test_low_probability_leaders_and_surnames(self):
        for text, expected in (
            ('Send Mary-Jane Watson to 123 Oak Road.', 'Send [NAME] to [ADDRESS].'),
            ('Call Anna Kowalska at the office.', 'Call [NAME] at the office.'),
            ('Spotkałem się przy Annie Kowalskiej', 'Spotkałem się przy [NAME]'),
            ('Contractor Eleni Papadopoulos completed the work.', 'Contractor [NAME] completed the work.'),
            ('Meet Jean-Luc Picard, Mary-Jane Watson and Anne-Marie Johnson.', 'Meet [NAME], [NAME] and [NAME].'),
        ):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text, names=True)[0], expected)

    def test_candidate_budget_precedes_expensive_models(self):
        text = ('John ' * 1639)[:8192]
        with patch.object(g, 'backend', side_effect=AssertionError('budget')), \
             patch.object(pc, 'classify', side_effect=AssertionError('budget')):
            self.assertEqual(nm.prompt_spans(text), nm.spans(text))

    def test_candidate_budget_boundary(self):
        for count, routed in ((nm.EN_MAX_CANDIDATES, True), (nm.EN_MAX_CANDIDATES + 1, False)):
            with self.subTest(count=count), patch.object(nm, 'spans', wraps=nm.spans) as spans:
                nm.prompt_spans('John ' * count)
                # the budget protects the expensive English models; the cheap language check may still run
                self.assertEqual(any(c.kwargs.get('backend') == 'g_full_r1' for c in spans.call_args_list), routed)

    def test_particle_extension_has_same_span_bound(self):
        text = 'John van der de da di von Smith'
        found = nm._english_boundaries(text, [(0, 4, 'name')])
        self.assertLessEqual(len(nm.tokens(text[found[0][0]:found[0][1]])), nm.MAX_NAME_TOKENS)


class EnglishRegressions(unittest.TestCase):
    def setUp(self):
        # Te regresje dotyczą trybu deep (pojedyncze imiona z dowodem); domyślny basic sprawdza osobny test.
        import functools
        from unittest import mock
        patcher = mock.patch.object(privacy, 'mask_local_details', functools.partial(privacy.mask_local_details, name_masking='deep'))
        patcher.start(); self.addCleanup(patcher.stop)

    def assert_names_covered(self, text, words):
        covered, _, masked, _ = full_coverage(privacy, text)
        for word in words:
            start = text.index(word)
            self.assertTrue(set(range(start, start + len(word))) <= covered, masked)

    def test_coordinated_names(self):
        self.assert_names_covered('Bob, Emily, Michael, David, Sarah and Jessica met Daniel yesterday.',
                                  ['Bob', 'Emily', 'Michael', 'David', 'Sarah', 'Jessica', 'Daniel'])
        self.assert_names_covered('Tom told Alice and Bob that Sarah, Emily, and Michael will join David later.',
                                  ['Tom', 'Alice', 'Bob', 'Sarah', 'Emily', 'Michael', 'David'])

    def test_reversed_names_in_headers_csv_and_bare(self):
        for text in ('From: Kowalska, Anna; Reviewer: Smith, John; Cc: Dr. Tom Brown.',
                     'Kowalska, Anna', 'Smith, John', '"Kowalska, Anna","value"'):
            words = [w for w in ('Kowalska', 'Anna', 'Smith', 'John') if w in text]
            with self.subTest(text=text):
                self.assert_names_covered(text, words)

    def test_line_endings_and_call_boundary(self):
        for newline in ('\n', '\r\n', '\r'):
            text = f'Hi Tom,{newline}Call Anna Kowalska at +48 600 700 800{newline}Email: tom@example.com{newline}'
            with self.subTest(newline=newline):
                self.assert_names_covered(text, ['Tom', 'Anna Kowalska'])
                self.assertIn('Call [NAME]', privacy.mask_local_details(text, names=True)[0])

    def test_oewn_preceding_verb(self):
        for text in ('Please contact Pani Zelvarowi about the report.', 'We discussed Anna in the report.',
                     'We introduced Emily yesterday.'):
            with self.subTest(text=text):
                self.assertIn('[NAME]', privacy.mask_local_details(text, names=True)[0])
        self.assertEqual(nm._singletons_with_evidence('We xqzved Anna today.', [(10, 14, 'name')]), [])

    def test_ansi_and_vertical_tab_person_role(self):
        text = '\x1b[31mError\x1b[0m: user Tom\x0bemail: tom@example.com\x0c'
        self.assert_names_covered(text, ['Tom'])
        self.assertEqual(privacy.mask_local_details(text, names=True)[0],
                         '\x1b[31mError\x1b[0m: user [NAME]\x0bemail: [EMAIL]\x0c')

    def test_non_person_lists_remain_unchanged(self):
        for text in (
            'Our stack integrates Apple, Microsoft, Google, Oracle, Amazon, Cisco, Dell, Nvidia, Stripe, Cloudflare, Datadog.',
            'Latency between Warsaw, Berlin, Paris, London, Prague, Austin, Seattle, Dallas, Florence, Victoria, and Sofia is under 40ms.',
            'Release scheduled for April, May, June, or August; freeze starts on Friday and ends Sunday.',
            'Lehman Partners requested help. We discussed Partners afterwards.',
            'force Hermes shallow-copy fallback',
        ):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text, names=True)[0], text)

    def test_no_partial_identifier_coordination(self):
        self.assertEqual(nm._coordinated_names('Kx7#Rz2Qp and Wq8#Lm3Zx here', []), [])

    def test_coordination_preserves_structural_book_ranges(self):
        text = 'Pan Zygmunt to tytuł i Anna'
        start = text.index('Anna')
        seed = [(start, start + 4, 'name')]
        self.assertEqual(nm._coordinated_names(text, seed), seed)

    def test_structural_evidence_failure_retains_baseline(self):
        text = 'Hi Anna,'
        with patch.object(nm, '_coordinated_names', side_effect=RuntimeError('dictionary unavailable')):
            self.assertEqual(nm.prompt_spans(text), nm.spans(text))


class MultilineKeyAndField(unittest.TestCase):
    def test_same_quote_two_line_keys(self):
        for quote in ('"', "'"):
            for newline in ('\n', '\r\n'):
                for head, tail in (('sk-proj-abc1234567890def', 'XYZ9876543210abcdef'),
                                   ('ghp_1234567890abcdef', '1234567890abcdef1234')):
                    text = f'KEY={quote}{head}{newline}{tail}{quote}'
                    with self.subTest(quote=quote, newline=newline, head=head):
                        self.assertEqual(privacy.mask_local_details(text)[0].count('[SECRET]'), 1)
                        self.assertNotIn(tail, privacy.mask_local_details(text)[0])

    def test_no_join_across_quotes_or_unquoted_prose(self):
        for text in ('GH="ghp_1234567890abcdef"\n"1234567890abcdef1234"',
                     'ghp_1234567890abcdef\n1234567890abcdef1234',
                     'GH="ghp_1234567890abcdef\nordinary prose"'):
            with self.subTest(text=text):
                self.assertEqual(privacy.mask_local_details(text)[0], text)

    def test_field_prefix_does_not_disable_supported_names(self):
        text = 'John Carter' + ' ' * (nm.MAX_TEXT - len('John Carter'))
        plain = privacy.mask_local_details(text, names=True)
        self.assertEqual(privacy.mask_local_details(text, names=True, field='message'), plain)
        self.assertTrue(plain[0].startswith('[NAME]'))


class BasicModeFullNames(unittest.TestCase):
    def test_reversed_pair_is_a_full_name_but_lists_of_first_names_are_not(self):
        out = privacy.mask_local_details('From: Kowalska, Anna; please review.', names=True)[0]
        self.assertNotIn('Kowalska', out); self.assertNotIn('Anna', out)
        out = privacy.mask_local_details('Bob, Emily, Michael and Sarah met yesterday.', names=True)[0]
        self.assertIn('Emily', out)


class PrefixedIdsStayReadable(unittest.TestCase):
    def test_object_ids_are_not_secrets_but_key_prefixes_are(self):
        for text in ('event_id=evt_z8QbyN4R9M0FhbcBgkNEzhd9 retried', 'order ord_ByFHgP06kuYKbbUW8W shipped',
                     'trace_KCEdhWe1kHizWKuw in the log'):
            self.assertEqual(privacy.mask_local_details(text)[0], text)
        for text in ('whsec_mYPUWS0CTqRER6ZVGsW2hXOI', 'npm_23ey9gwBZYo42hOorCQPVbiasHpV', 'token: pat_E8MkPLQCv6XSv6xRQ1n3pm'):
            self.assertIn('[SECRET]', privacy.mask_local_details(text)[0])


if __name__ == '__main__':
    unittest.main()
