import unittest
from unittest.mock import patch

from plugin.scout import language, name_model as nm, name_model_f as f, name_model_g as g, paragraph_context as pc, privacy


class SingletonEvidence(unittest.TestCase):
    def filtered(self, text, words):
        names = []
        cursor = 0
        for word in words:
            start = text.index(word, cursor)
            cursor = start+len(word)
            names.append((start, start+len(word), 'name'))
        return [text[s:e] for s, e, _ in nm._singletons_with_evidence(text, names)]

    def test_requested_examples(self):
        for text, word, expected in (
            ('Hi Anna,', 'Anna', True), ('Thanks, Tom', 'Tom', True),
            ('Grace period applies', 'Grace', False), ('Faith in CI', 'Faith', False),
            ('Kafka processed the batch', 'Kafka', True), ('Best,\nAnna', 'Anna', True),
            ('Maya: can you review it?', 'Maya', True), ('\tAnna\t\r\n', 'Anna', True),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.filtered(text, [word]), [word] if expected else [])

    def test_oewn_irregular_verbs_and_no_suffix_guessing(self):
        self.assertEqual(self.filtered('Kafka said the batch was ready.', ['Kafka']), ['Kafka'])
        self.assertEqual(self.filtered('Kafka xqzved the batch.', ['Kafka']), [])
        self.assertEqual(self.filtered('Kafka in the report.', ['Kafka']), [])

    def test_full_name_and_component_in_same_text(self):
        text = 'Please ask John Doe. A note about John remains.'
        self.assertEqual(self.filtered(text, ['John Doe', 'John']), ['John Doe', 'John'])

    def test_component_without_other_evidence(self):
        text = 'John Doe called. We discussed Doe afterwards.'
        self.assertEqual(self.filtered(text, ['John Doe', 'Doe']), ['John Doe', 'Doe'])


class PromptRouting(unittest.TestCase):
    def test_real_english_path(self):
        for text, expected in (
            ('Hi Anna,', 'Hi [NAME],'), ('Thanks, Tom', 'Thanks, [NAME]'),
            ('Faith in CI', 'Faith in CI'),
            ('Kafka processed the batch', '[NAME] processed the batch'),
            ('Maya: can you review the report?', '[NAME]: can you review the report?'),
        ):
            with self.subTest(text=text):
                self.assertGreaterEqual(pc.classify(text)['lang']['EN'], nm.EN_CONFIDENCE)
                self.assertEqual(privacy.mask_local_details(text, names=True, name_masking='deep')[0], expected)

    def test_real_uncertain_short_phrase_uses_english_path(self):
        # Uncertain text: basic (default) keeps full names only, deep keeps the previous detector.
        text = 'Grace period applies'
        self.assertLess(pc.classify(text)['lang']['EN'], nm.EN_CONFIDENCE)
        self.assertEqual(nm.prompt_spans(text, mode='basic'), [])
        self.assertEqual(nm.prompt_spans(text, mode='deep'), nm.spans(text))

    def test_real_polish_and_mixed_unchanged(self):
        for text in ('Proszę poprosić Annę Kowalską o raport.', 'Hi Anna, proszę o raport.',
                     'Please talk with Panem Kowalskim.'):
            with self.subTest(text=text):
                self.assertEqual(nm.prompt_spans(text), nm.spans(text))

    def test_mixed_document_offsets_and_only_en_replaced(self):
        text = 'Proszę poprosić Annę Kowalską o raport.\r\n\r\nFaith in CI\n\nThanks, Tom'
        parts = list(language.paragraphs(text))
        result = nm.prompt_spans(text)
        baseline = nm.spans(text)
        for offset, paragraph in parts:
            local = [(s, e, c) for s, e, c in result if offset <= s < offset+len(paragraph)]
            if offset == 0:
                self.assertEqual(local, [x for x in baseline if x[0] < len(paragraph)])
            elif paragraph == 'Faith in CI':
                self.assertEqual(local, [])
            else:
                self.assertEqual(local, [(offset+8, offset+11, 'name')])
        masked, _ = privacy.mask_local_details(text, names=True, name_masking='deep')
        self.assertIn('Faith in CI', masked)
        self.assertIn('Thanks, [NAME]', masked)

    def test_exact_confidence_boundary_and_class_winner(self):
        text = 'Faith in CI'
        baseline = nm.spans(text)
        for probs, changed in (({'EN':.8,'PL':.1,'mixed':.1}, True),
                               ({'EN':.799,'PL':.1,'mixed':.101}, False),
                               ({'EN':.01,'PL':.01,'mixed':.98}, False)):
            with self.subTest(probs=probs), patch.object(pc, 'classify', return_value={'lang':probs}):
                self.assertEqual(nm.prompt_spans(text), [] if changed else baseline)

    def test_classification_exception_preserves_baseline_document(self):
        text = 'Thanks, Tom\n\nFaith in CI'
        original = pc.classify
        def classify(part):
            if part == 'Thanks, Tom':
                raise RuntimeError('unavailable routing')
            return original(part)
        with patch.object(pc, 'classify', side_effect=classify):
            self.assertEqual(nm.prompt_spans(text), nm.spans(text))

    def test_model_or_rule_failure_retains_baseline_mask(self):
        text = 'Hi Anna,'
        backend = g.backend('g_full_r1')
        for target, method in ((backend, 'filter_spans'), (nm, '_singletons_with_evidence')):
            with self.subTest(method=method), patch.object(target, method, side_effect=RuntimeError('unavailable')) as failed:
                self.assertEqual(nm.prompt_spans(text), nm.spans(text))
                self.assertEqual(privacy.mask_local_details(text, names=True, name_masking='deep')[0], 'Hi [NAME],')
                self.assertTrue(failed.called)

    def test_original_failure_is_not_silently_unmasked(self):
        with patch.object(nm, 'spans', side_effect=RuntimeError('baseline unavailable')):
            with self.assertRaises(RuntimeError):
                privacy.mask_local_details('Hi Anna,', names=True, name_masking='deep')

    def test_length_guard_before_classifier_and_g(self):
        text = 'Thanks, Tom ' * (nm.EN_MAX_TEXT//12+1)
        baseline = nm.spans(text)
        with patch.object(pc, 'classify', side_effect=AssertionError('too long')), \
             patch.object(g, 'backend', side_effect=AssertionError('too long')):
            self.assertEqual(nm.prompt_spans(text), baseline)
        for length, expected in ((nm.EN_MAX_TEXT, 1), (nm.EN_MAX_TEXT+1, 0)):
            with patch.object(pc, 'classify', return_value={'lang':{'PL':1.}}) as classify:
                nm.prompt_spans('x' * length)
                self.assertEqual(classify.call_count, expected)

    def test_unicode_normalization_field_and_other_category_union(self):
        text = 'Hi Ａnna, e-mail anna@example.org'
        masked, cats = privacy.mask_local_details(text, names=True, field='message', name_masking='deep')
        self.assertNotIn('Ａnna', masked)
        self.assertNotIn('anna@example.org', masked)
        self.assertIn('name', cats)
        self.assertIn('email', cats)

    def test_structural_non_person_context_survives_c_addition(self):
        for text in ('Please read "Zelvar Morven" before tomorrow.',
                     'The company Zelvar Holdings requested the report yesterday.', 'Owner: SRE On-Call'):
            with self.subTest(text=text):
                self.assertEqual(nm.prompt_spans(text), [])
                self.assertEqual(privacy.mask_local_details(text, names=True, name_masking='deep')[0], text)

    def test_confirmed_propagation_across_english_paragraphs(self):
        text = 'Please ask John Doe to review the report.\n\nThe report is ready and John Doe reviewed it.'
        seed = [(text.index('John Doe'), text.index('John Doe')+8, 'name')]
        with patch.object(g.backend('g_full_r1'), 'score', return_value=1.), \
             patch.object(nm, '_base_spans', return_value=seed), \
             patch.object(f.c_backend(), 'filter_spans', return_value=[]), \
             patch.object(pc, 'classify', return_value={'lang':{'EN':1.}}):
            names = nm.prompt_spans(text)
        self.assertEqual([text[s:e] for s, e, _ in names], ['John Doe', 'John Doe'])

    def test_removed_dispatches_are_rejected(self):
        for backend in ('etap_a', 'a2_lr', 'b_B2_K1_r3', 'c_span', 'f_full2_r1', 'g_veto_r1'):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                nm.spans('John Doe called.', backend=backend)


import contextlib
import io
import json
import tempfile
from pathlib import Path

from plugin.cli import main
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout.prompt_guard import PromptGuard

DE = 'Die Deutsche Bahn und die Stadt planen das Projekt Neue Schule mit weiteren Partnern.'
FR = 'Le bureau est situé au 12 Main Street, Boston, MA 02110 et les clients contactent info@example.org ou +1 617 555 0123.'

class Languages18(unittest.TestCase):
    def test_languages_and_fallback(self):
        for text in (DE, FR, 'El equipo de los clientes es una parte del proyecto para la ciudad.',
                     'Il progetto della scuola è una parte delle attività per le persone.'):
            self.assertEqual(language.paragraph_language(text), 'other')
        self.assertNotEqual(language.paragraph_language('Die Deutsche Bahn'), 'other')
        self.assertNotEqual(language.paragraph_language('The team and the project are in the city.'), 'other')

    def test_examples(self):
        en = 'Ship to 12 Main St, Springfield, IL 62704 for Tom Brown'
        out, cats = privacy.mask_local_details(en, names=True)
        self.assertIn('name', cats); self.assertIn('address', cats)
        pl = 'Pan Zelvarowi, ul. Długa 5, 00-001 Warszawa'
        out, cats = privacy.mask_local_details(pl, names=True, name_masking='deep')
        self.assertIn('name', cats); self.assertIn('address', cats)
        self.assertEqual(privacy.mask_local_details('Cześć Anno', names=True)[0], 'Cześć Anno')
        self.assertNotIn('Anno', privacy.mask_local_details('Cześć Anno', names=True, name_masking='deep')[0])
        self.assertEqual(privacy.mask_local_details(DE, names=True, name_masking='deep')[0], DE)
        self.assertIn('[NAME]', privacy.mask_local_details(DE, names=True, other_languages='on')[0])
        german = 'Die Mitarbeiterin Anna Müller spricht mit Anna über das Projekt und die Stadt.'
        self.assertEqual(privacy.mask_local_details(german, names=True)[0], german)
        self.assertEqual(privacy.mask_local_details(german, names=True, name_masking='deep', other_languages='on')[0].count('[NAME]'), 2)
        repeat = 'Proszę zapytać Pana Zelvarowi o raport, potem Pana Zelvarowi o odpowiedź.'
        self.assertEqual(privacy.mask_local_details(repeat, names=True, name_masking='deep')[0].count('[NAME]'), 2)
        out, cats = privacy.mask_local_details(FR)
        self.assertIn('12 Main Street', out); self.assertIn('email', cats); self.assertIn('phone', cats)
        self.assertIn('address', privacy.mask_local_details(FR, other_languages='on')[1])
        mixed = en + '\n\n' + FR
        out, cats = privacy.mask_local_details(mixed, names=True)
        self.assertIn('[ADDRESS]', out); self.assertIn('12 Main Street', out); self.assertIn('[EMAIL]', out)

    def test_profile_cache_and_tool(self):
        with tempfile.TemporaryDirectory() as d:
            a = Scout(LocalContext(Path(d)))
            a.configure_local('report', grants={'mask': ['profile']})
            status = io.StringIO()
            with contextlib.redirect_stdout(status): main(['--home', d, 'status'])
            self.assertEqual(json.loads(status.getvalue())['other_languages'], 'off')
            guard = PromptGuard(a)
            self.assertIn('12 Main Street', guard.scan(FR)[0])
            self.assertIn('12 Main Street', a.transform_tool_result(result=FR, session_id='s', tool_call_id='1'))
            a.configure_local('report', other_languages='on')
            self.assertIn('[ADDRESS]', guard.scan(FR)[0])
            self.assertIn('[ADDRESS]', a.transform_tool_result(result=FR, session_id='s', tool_call_id='2'))
            with self.assertRaises(ValueError): a.configure_local('report', other_languages='bad')
            with self.assertRaises(ValueError): privacy.mask_local_details('x', other_languages='bad')
            out = io.StringIO()
            with contextlib.redirect_stdout(out): main(['--home', d, 'status'])
            self.assertEqual(json.loads(out.getvalue())['other_languages'], 'on')
            with contextlib.redirect_stdout(io.StringIO()): main(['--home', d, 'configure', '--mode', 'report', '--other-languages', 'off'])
            self.assertEqual(Scout(LocalContext(Path(d))).profile()['config']['other_languages'], 'off')



from plugin.scout import name_model, privacy, menu


class MaskingOptions(unittest.TestCase):
    def test_real_english_and_fallback(self):
        text = 'Hi, I am John Carter. Please contact John.'
        basic = privacy.mask_local_details(text, names=True, name_masking='basic')[0]
        self.assertEqual(basic, 'Hi, I am [NAME]. Please contact [NAME].')
        self.assertEqual(privacy.mask_local_details('Hi Anna,', names=True, name_masking='basic')[0], 'Hi Anna,')
        self.assertEqual(privacy.mask_local_details('Hi Anna,', names=True, name_masking='deep')[0], 'Hi [NAME],')
        pl = 'Proszę poprosić Annę Kowalską o raport.'
        self.assertEqual(name_model.prompt_spans(pl, mode='basic'), name_model.spans(pl))

    def test_off_and_validation(self):
        with patch.object(name_model, 'prompt_spans', side_effect=AssertionError('name path called')):
            out, cats = privacy.mask_local_details('Alice alice@example.org', names=True, name_masking='off')
            self.assertIn('[EMAIL]', out)
            privacy.mask_local_details('Alice', names=False, name_masking='deep')
        for kwargs in ({'name_masking': []}, {'address_masking': 'bad'}):
            with self.assertRaises(ValueError): privacy.mask_local_details('x', **kwargs)
        out, cats = privacy.mask_local_details('123 Main Street, Boston, MA 02110', address_masking='off')
        self.assertNotIn('address', cats)
        self.assertNotIn('[ADDRESS]', out)

    def test_cache_and_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            a = Scout(LocalContext(Path(d)/'a'))
            b = Scout(LocalContext(Path(d)/'b'))
            a.configure_local('report', name_masking='off', address_masking='off')
            guard = PromptGuard(a)
            text = 'Hi Anna,'
            self.assertEqual(guard.scan(text, names=True)[0], text)
            a.configure_local('report', name_masking='basic')
            self.assertEqual(guard.scan(text, names=True)[0], text)
            a.configure_local('report', name_masking='deep')
            self.assertEqual(guard.scan(text, names=True)[0], 'Hi [NAME],')
            a.configure_local('report', name_masking='off')
            self.assertEqual(guard.scan(text, names=True)[0], text)
            self.assertNotIn('name_masking', b.profile()['config'])
            self.assertEqual(Scout(LocalContext(Path(d)/'a')).profile()['config']['address_masking'], 'off')
            address = '123 Main Street, Boston, MA 02110'
            self.assertNotIn('[ADDRESS]', guard.scan(address)[0])
            a.configure_local('report', address_masking='on')
            self.assertIn('[ADDRESS]', guard.scan(address)[0])
            a.configure_local('report', grants={'mask': ['profile']}, address_masking='off')
            tool_result = a.transform_tool_result(result=address, session_id='s', tool_call_id='1')
            self.assertNotIn('[ADDRESS]', tool_result or '')
            a.configure_local('report', address_masking='on')
            tool_result = a.transform_tool_result(result=address, session_id='s', tool_call_id='2')
            self.assertIn('[ADDRESS]', tool_result)
            before = (a.root()/'settings.json').read_text()
            with self.assertRaises(ValueError): a.configure_local('report', name_masking='deep', address_masking='invalid')
            self.assertEqual((a.root()/'settings.json').read_text(), before)
            broken = json.loads(before)
            broken['name_masking'] = 'corrupt'
            (a.root()/'settings.json').write_text(json.dumps(broken))
            with self.assertRaises(ValueError):
                PromptGuard(Scout(LocalContext(Path(d)/'a'))).scan('Hi Anna,', names=True)

    def test_cli_migration_and_menu(self):
        with tempfile.TemporaryDirectory() as d:
            def cli(*args):
                out = io.StringIO()
                with contextlib.redirect_stdout(out): main(['--home', d, *args])
                return json.loads(out.getvalue())
            old = Path(d)/'plugin-data'/'local-scout'
            old.mkdir(parents=True)
            (old/'settings.json').write_text(json.dumps({'mode': 'report', 'grants': {'mask': ['profile']}, 'sources': {}}))
            self.assertEqual((cli('status')['name_masking'], cli('status')['address_masking']), ('off', 'on'))
            cli('configure', '--mode', 'report', '--name-masking', 'deep', '--address-masking', 'off')
            self.assertEqual((cli('status')['name_masking'], cli('status')['address_masking']), ('deep', 'off'))
            cli('configure', '--mode', 'report')
            self.assertEqual(cli('status')['name_masking'], 'deep')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['--home', d, 'configure', '--mode', 'report', '--name-masking', 'bad'])
            scout = Scout(LocalContext(d))
            answers = iter(['y', '1', 'on'])
            output = []
            menu.masking_on(scout, lambda _: next(answers), output.append)
            self.assertEqual(scout.profile()['config']['name_masking'], 'basic')
            self.assertTrue(any('--address-masking on' in line for line in output))


import math

from plugin.scout.paragraph_context import classify, features


class ParagraphContextTests(unittest.TestCase):
    def test_distributions_and_entropy(self):
        for text in ('', 'Cześć, proszę o odpowiedź.', 'ERROR: timeout /var/log/app', 'Hi, proszę sprawdź log.'):
            result = classify(text)
            self.assertEqual(set(result['type']), {'technical', 'email_chat', 'form_list', 'prose', 'mixed'})
            self.assertEqual(set(result['lang']), {'PL', 'EN', 'mixed'})
            for task in ('type', 'lang'):
                self.assertTrue(math.isclose(sum(result[task].values()), 1.0))
                self.assertTrue(all(0 <= p <= 1 for p in result[task].values()))
                self.assertTrue(0 <= result['uncertainty'][task] <= 1 + 1e-12)
            self.assertEqual(len(features(text)), 32)

    def test_non_string_rejected(self):
        with self.assertRaises(TypeError):
            classify(None)


if __name__ == '__main__':
    unittest.main()
