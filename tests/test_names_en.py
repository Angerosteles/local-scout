import unittest
from unittest.mock import patch

from plugin.scout import name_model as nm, name_model_f as f, name_model_c as c, name_model_g as g

TEXT = 'Kofi Boateng patched it.\n\nBoateng closed it.'
FULL = [(0, 12, 'name')]


def backend(bias, **options):
    b = g.Backend(dict(weights={}, bias=bias, nbits=16, channel="pron"), **options)
    b.freq, b.english = {}, lambda paragraph: True
    return b


class SlotBackend(unittest.TestCase):
    def test_contract(self):
        self.assertEqual(nm.spans('', backend='f_veto'), [])
        self.assertEqual(nm.spans('A' * (nm.MAX_TEXT + 1), backend='f_veto'), [])
        with self.assertRaises(ValueError):
            nm.spans('Ask Avery.', backend='f_unknown')
        with self.assertRaises(ValueError):
            f.Backend(dict(weights={'1': float('nan')}, bias=0., nbits=16))

    def test_veto_needs_explicit_context(self):
        b = backend(-9., threshold=.5)
        self.assertEqual(b.apply(TEXT, FULL), [])
        # Greeting followed by punctuation: no evidence, so even a hostile model keeps the name.
        self.assertEqual(b.apply('Hi Zyx,\nthanks.', [(3, 6, 'name')]), [(3, 6, 'name')])

    def test_veto_never_adds_or_moves(self):
        self.assertEqual(backend(9., threshold=.5).apply(TEXT, FULL), FULL)

    def test_other_languages_untouched(self):
        b = f.Backend(dict(weights={}, bias=-9., nbits=16), threshold=.5)
        self.assertFalse(b.english('Proszę zadzwonić do pana Kowalskiego, dziękuję.'))
        self.assertEqual(b.apply('Proszę zadzwonić, Kowalski czeka na raport.', [(18, 26, 'name')]), [(18, 26, 'name')])

    def test_propagation_across_paragraphs(self):
        b = backend(9., threshold=.5, propagate='confirmed')
        self.assertEqual(b.apply(TEXT, FULL, evidence={(0, 12)}), FULL + [(26, 33, 'name')])
        # An ordinary word inside a confirmed name is not repeated.
        b.freq = {'will': 1000}
        self.assertEqual(b.apply('Will Boateng patched it.\n\nWill the deploy finish?', FULL, evidence={(0, 12)}), FULL)

    def test_slot_ignores_spelling(self):
        seen = []
        for text in ('Xqzv patched it.', 'Wmrk patched it.'):
            ctx = f.context(text, {})
            ctx['prob'] = {}
            seen.append(f.slot(text, ctx, 0, 0))
        self.assertEqual(*seen)
        self.assertFalse(any('xqzv' in x.lower() for x in seen[0]))



class ConfirmedPropagation(unittest.TestCase):
    def repeat(self, text, seed, confirmed=True):
        b = backend(9., threshold=.02, propagate='confirmed')
        start = text.index(seed)
        span = (start, start + len(seed), 'name')
        result = b.apply(text, [span], evidence={span[:2]} if confirmed else ())
        return [text[s:e] for s, e, _ in result], result

    def test_full_repeat_and_isolated_components(self):
        text = 'Please ask John Doe to call.\n\nJohn Doe reviewed it. John agreed; Doe replied.'
        words, spans = self.repeat(text, 'John Doe')
        self.assertEqual(words, ['John Doe', 'John Doe', 'John', 'Doe'])
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])))

    def test_shared_surname_is_not_a_match_inside_another_person(self):
        words, _ = self.repeat('Please ask Jane Doe to call. John Doe reviewed it.', 'Jane Doe')
        self.assertEqual(words, ['Jane Doe'])

    def test_places_and_companies_are_not_extended(self):
        for text, seed in [
            ('Palm Beach is listed. West Palm Beach is listed.', 'Palm Beach'),
            ('Hartford Premier Insurance is listed. Hartford Premier Insurance Co replied.', 'Hartford Premier Insurance'),
            ('Sarah Susan Ayala called. Ayala Holdings replied.', 'Sarah Susan Ayala'),
            ('Sarah Susan Ayala called. Ayala’s Artisanal Bakery replied.', 'Sarah Susan Ayala'),
            ("Sarah Susan Ayala called. Ayala's Artisanal Bakery replied.", 'Sarah Susan Ayala'),
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.repeat(text, seed)[0], [seed])

    def test_slot_score_alone_is_not_person_evidence(self):
        self.assertEqual(self.repeat('John Doe called. John Doe replied.', 'John Doe', confirmed=False)[0], ['John Doe'])
        # C's existing seed threshold, not the low add threshold, supplies independent evidence.
        text = 'Palm Beach is listed. Palm Beach is listed.'
        cb = f.c_backend()
        with patch.object(c, 'english', return_value=True), patch.object(cb.model, 'probability', return_value=.8):
            evidence = set()
            cb.filter_spans(text, evidence=evidence)
        self.assertEqual(evidence, set())

    def test_target_veto_and_language_still_apply(self):
        text = 'John Doe called. John replied.'
        b = backend(9., threshold=.02, propagate='confirmed')
        b.score = lambda text, ctx, i, j: 1. if j > i else 0.
        self.assertEqual(b.apply(text, [(0, 8, 'name')], evidence={(0, 8)}), [(0, 8, 'name')])
        b.english = lambda text: False
        self.assertEqual(b.apply(text, [(0, 8, 'name')], evidence={(0, 8)}), [(0, 8, 'name')])


class ReleasedOperatingPoint(unittest.TestCase):
    POINTS = ['g_full_r1']
    TEXTS = ('Please ask John Doe to call me tomorrow.\n\nJohn Doe reviewed the report.',
             'Jane Doe called. John Doe replied.', 'Owner: Priya Raman\nStatus: Active',
             'Dr. Sarah K. Lewis called. Sarah replied.', 'Palm Beach and West Palm Beach.',
             'Hartford Premier Insurance Co. Ayala Holdings.', 'no capitals', '')

    def test_cross_full_repeat_through_public_api(self):
        self.assertEqual(nm.spans(self.TEXTS[0], backend='g_full_r1'), [(11, 19, 'name'), (42, 50, 'name')])

    def test_public_contract_all_points(self):
        for point in self.POINTS:
            for text in self.TEXTS:
                with self.subTest(point=point, text=text):
                    spans = nm.spans(text, backend=point)
                    self.assertEqual(spans, sorted(set(spans)))
                    self.assertTrue(all(isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text)
                                        and kind == 'name' for s, e, kind in spans))
                    self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])))
            self.assertEqual(nm.spans('A' * (nm.MAX_TEXT + 1), backend=point), [])
        for bad in ('f_full2', 'f_full2_r0', 'f_full2_r2'):
            with self.assertRaises(ValueError):
                nm.spans('John Doe called.', backend=bad)

    def test_add_optimization_parity(self):
        original = c.Backend(f.c_backend().data, threshold=.25)
        optimized = f.c_backend()
        for text in self.TEXTS:
            multi = lambda spans: [x for x in spans if len(text[x[0]:x[1]].split()) >= 2]
            self.assertEqual(multi(original.filter_spans(text)), multi(optimized.filter_spans(text)))
            for point in self.POINTS:
                expected = nm.spans(text, backend=point)
                with patch.object(f, 'c_backend', return_value=original):
                    self.assertEqual(nm.spans(text, backend=point), expected)


import json

from plugin.scout import name_model as nm, name_model_f as f, name_model_g as g


def features(text, word, channel='pron'):
    ctx=f.context(text,{})
    ctx['prob']={}
    s=text.index(word)
    return set(g.extra(text,ctx,ctx['starts'][s],ctx['ends'][s+len(word)],channel))


class GrammarFeatures(unittest.TestCase):
    def test_ordinary_lexicon_and_irregular_senses(self):
        words=g.lexicon()
        self.assertTrue(all(w.islower() and not any(c.isupper() for c in w) for w in words))
        for w in ('said','wrote','sent'):
            self.assertIn('v',g.classes(w)[0])
        self.assertIn('verb.communication',g.classes('said')[1])
        self.assertIn('noun.person',g.classes('manager')[1])
        model=json.loads((f.ROOT/'name_g_slot.json').read_text())
        import hashlib
        self.assertEqual(model['meta']['lexicon_sha256'],hashlib.sha256((f.ROOT/'name_g_lexicon.json').read_bytes()).hexdigest())

    def test_imperative_article_and_cues(self):
        self.assertIn('g:imperative:0',features('Grant access to the service.','Grant'))
        # Access is not an article/pronoun; the learned verb/noun combination supplies that signal.
        self.assertIn('g:imperative:1',features('Mark the endpoint.','Mark'))
        self.assertIn('g:article:1',features('The Crystal was clear.','Crystal'))
        for cue in ('by','from','cc','thanks','hi'):
            self.assertIn('g:cue:'+cue,features('Please reply '+cue+' Xqzv.','Xqzv'))

    def test_channels_have_no_raw_unknown_name(self):
        for channel in g.CHANNELS:
            a=features('Xqzv said it was ready.','Xqzv',channel)
            b=features('Wmrk said it was ready.','Wmrk',channel)
            self.assertEqual(a,b)
            self.assertFalse(any('xqzv' in x.lower() for x in a))
        a=features('Xqzv said it was ready.','Xqzv','morph')
        b=features('Xqzv said it was ready.','Xqzv','pos')
        c=features('Xqzv said it was ready.','Xqzv','sem')
        d=features('Xqzv said it was ready.','Xqzv','pron')
        self.assertLess(a,b)
        self.assertLess(b,c)
        self.assertLess(c,d)
        self.assertIn('g:p:r1:v',b)
        self.assertIn('g:s:r1:verb.communication',c)

    def test_compound_role_relative_and_possessive(self):
        self.assertIn('g:p:r1:n',features('Grace period applies.','Grace'))
        self.assertIn('g:role:2',features('Xqzv, our manager, called.','Xqzv'))
        self.assertIn('g:apposition:1',features('Xqzv, our manager, called.','Xqzv'))
        for apostrophe in ("'",'’'):
            self.assertIn('g:possessive:1',features('Xqzv'+apostrophe+'s manager replied.','Xqzv'))
        for relative in ('who','which'):
            self.assertIn('g:relative:'+relative,features('Xqzv, '+relative+' replied.','Xqzv'))

    def test_sentence_and_paragraph_boundaries(self):
        self.assertNotIn('g:p:r1:v',features('Xqzv. Said it was ready.','Xqzv'))
        self.assertIn('g:pron:she',features('Xqzv called. She agreed. It failed.','Xqzv'))
        self.assertNotIn('g:pron:it',features('Xqzv called. She agreed. It failed.','Xqzv'))
        self.assertNotIn('g:pron:she',features('Xqzv called.\nShe agreed.','Xqzv'))
        self.assertNotIn('g:pron:he',features('He called. Xqzv agreed.','Xqzv'))


class GBackend(unittest.TestCase):
    def test_soft_features_and_preserved_veto_guard(self):
        text='Grace period applies. Xqzv said it.'
        spans=[(0,5,'name'),(22,26,'name')]
        for bias,expected in ((20.,spans),(-20.,[])):
            b=g.Backend(dict(weights={},bias=bias,nbits=16,channel='pron'),threshold=.5)
            b.english=lambda text:True
            self.assertEqual(b.apply(text,spans),expected)
        b=g.Backend(dict(weights={},bias=-20.,nbits=16,channel='pron'),threshold=.5)
        b.english=lambda text:True
        self.assertEqual(b.apply('Hi Xqzv,',[(3,7,'name')]),[(3,7,'name')])
        b.english=lambda text:False
        self.assertEqual(b.apply(text,spans),spans)

    def test_public_contract_and_subset_veto(self):
        for text in ('Grace period applies. Xqzv said it.', 'Hi John Doe. John replied.'):
            for point in ('g_full_r1',):
                spans=nm.spans(text,backend=point)
                self.assertEqual(spans,sorted(set(spans)))
                self.assertTrue(all(0<=s<e<=len(text) and c=='name' for s,e,c in spans))
                self.assertTrue(all(a[1]<=b[0] for a,b in zip(spans,spans[1:])))
        for bad in ('g_full','g_veto_r0','g_veto_r1','g_unknown'):
            with self.assertRaises(ValueError): nm.spans('John called.',backend=bad)

    def test_f2_mechanics_are_inherited(self):
        for method in ('filter_spans','judged','apply','repeat_confirmed','english'):
            self.assertIs(getattr(g.Backend,method),getattr(f.Backend,method))
        full=g.backend('g_full_r1')
        self.assertTrue(full.add)
        self.assertEqual(full.propagate,'confirmed')
        self.assertEqual(full.seed,.5)
        text='Please ask John Doe to call. John Doe reviewed it.'
        # A neutral high score must retain the existing confirmed propagation behavior.
        with patch.object(full,'score',return_value=1.),patch.object(full,'english',return_value=True):
            spans=full.apply(text,[(11,19,'name')],evidence={(11,19)})
        self.assertEqual([text[s:e] for s,e,_ in spans],['John Doe','John Doe'])





class EnglishNameBoundaries(unittest.TestCase):
    def test_examples_in_routed_english(self):
        examples = [
            ('Hey Tom, please reply to the message.', 'Tom'),
            ('Reach Liam on the phone today.', 'Liam'),
            ('Notify Nora Vance about the meeting.', 'Nora Vance'),
            ('Applicant Gabriel Santos submitted the application.', 'Gabriel Santos'),
            ('Tenant Javier Ortega signed the lease.', 'Javier Ortega'),
            ('Contractor Eleni Papadopoulos completed the work.', 'Eleni Papadopoulos'),
            ('Morning Mateo Alvarez, please reply.', 'Mateo Alvarez'),
            ('Could Priya confirm the meeting?', 'Priya'),
            ('CC Harish Menon on the email.', 'Harish Menon'),
            ('Hello Hendrik van der Berg, please reply.', 'Hendrik van der Berg'),
            ('Hello Hendrik al-Berg, please reply.', 'Hendrik al-Berg'),
            ("Hello Hendrik O'Connor, please reply.", "Hendrik O'Connor"),
            ('Hello Hendrik McDonald, please reply.', 'Hendrik McDonald'),
            ('Capt. Raymond Vance signed the report.', 'Raymond Vance'),
            ('Hi Ms. Sofia Mendoza-Cruz, please reply.', 'Sofia Mendoza-Cruz'),
            ('Will Smith reviewed the report.', 'Will Smith'),
        ]
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual([text[s:e] for s, e, _ in nm.prompt_spans(text)], [expected])

    def test_polish_name_boundary_helper_and_routing(self):
        text = 'Call Łukasz at the office today.'
        adjusted = nm._english_boundaries(text, [(0, 11, 'name')])
        self.assertEqual([text[s:e] for s, e, _ in adjusted], ['Łukasz'])
        self.assertEqual(nm.prompt_spans(text), nm.spans(text))

    def test_particles_and_punctuation(self):
        for particle in ('van', 'der', 'de', 'da', 'di', 'von', 'le', 'la', 'del', 'della', 'bin', 'ibn', 'al'):
            text = 'Hendrik ' + particle + ' Berg.'
            with self.subTest(particle=particle):
                spans = nm._english_boundaries(text, [(0, 7, 'name')])
                self.assertEqual(text[spans[0][0]:spans[0][1]], text[:-1])
        text = 'Hendrik van der berg.'
        self.assertEqual(nm._english_boundaries(text, [(0, 7, 'name')]), [(0, 7, 'name')])

    def test_initial_is_preserved(self):
        text = 'K. Asante-Quaye reviewed the report.'
        self.assertEqual(nm._english_boundaries(text, [(0, 15, 'name')]), [(0, 15, 'name')])

    def test_confirmed_word_is_preserved(self):
        text = 'Reviewed by Robin Smith. Robin Jones called.'
        first, second = text.index('Robin Smith'), text.index('Robin Jones')
        names = [(first, first + len('Robin Smith'), 'name'), (second, second + len('Robin Jones'), 'name')]
        self.assertEqual(nm._english_boundaries(text, names), names)

    def test_first_names_that_are_words_are_not_trimmed(self):
        # Dowódca 14.11: imię-słowo na początku zdania nie może zostać odcięte (wyciek); bez listy imion.
        for text in ('Tom Jones said the report is ready.', 'Grant Holloway approved the refund.',
                     'Mark Twain wrote it.', 'Dean Smith reviewed the patch.'):
            with self.subTest(text=text):
                end = text.index(' ', text.index(' ') + 1)
                self.assertEqual(nm._english_boundaries(text, [(0, end, 'name')]), [(0, end, 'name')])

    def test_role_needs_two_remaining_name_words(self):
        text = 'Tenant Ortega paid the deposit.'
        self.assertEqual(nm._english_boundaries(text, [(0, 13, 'name')]), [(0, 13, 'name')])


import re

from plugin.scout import language, name_model as nm, name_model_f as f, privacy


class ContactPairs(unittest.TestCase):
    def test_full_mask_in_all_four_settings(self):
        for text, expected in (
            ('Please ask John Doe to call me tomorrow.', 'Please ask [NAME] to call me tomorrow.'),
            ('Call John Doe today.', 'Call [NAME] today.'),
            ('Email Kofi Boateng today.', 'Email [NAME] today.'),
            ('Tell John Doe about the change.', 'Tell [NAME] about the change.'),
            ('Contact John Doe first.', 'Contact [NAME] first.'),
            ('We asked Kofi Boateng about the change.', 'We asked [NAME] about the change.'),
        ):
            for mode in ('basic', 'deep'):
                for other in ('off', 'on'):
                    with self.subTest(text=text, mode=mode, other=other):
                        self.assertEqual(privacy.mask_local_details(text, names=True, name_masking=mode, other_languages=other), (expected, ('name',)))
                        start=text.index('John Doe') if 'John Doe' in text else text.index('Kofi Boateng')
                        end=start+len('John Doe' if 'John Doe' in text else 'Kofi Boateng')
                        self.assertIn((start,end,'name'), nm.prompt_spans(text, mode, other))

    def test_ordinary_words_and_identifiers_are_not_contacts(self):
        for text in ('Call Center', 'Email Marketing', 'Contact Form',
                     'Call Center Operations today.', 'Email Marketing Team today.',
                     'Contact Form Builder first.', 'Please ask Support Team today.',
                     'Please ask John Doe_123 today.', 'Please ask xJohn Doe today.',
                     'Call John.Doe today.'):
            for mode in ('basic', 'deep'):
                with self.subTest(text=text, mode=mode):
                    self.assertEqual(privacy.mask_local_details(text, names=True, name_masking=mode)[0], text)


class OrdinaryLeaders(unittest.TestCase):
    def test_leader_is_preserved_and_single_name_is_deep_only(self):
        for leader in ('Today','Later','Maybe','However','Meanwhile','Suddenly','Recently'):
            text=leader+' Anna called the office.'
            with self.subTest(leader=leader):
                self.assertEqual(privacy.mask_local_details(text,names=True)[0],text)
                self.assertEqual(privacy.mask_local_details(text,names=True,name_masking='deep')[0],leader+' [NAME] called the office.')

    def test_polish_greetings(self):
        for leader in ('Cześć','Czesc','Witaj','Witam','Hej'):
            text=leader+' Anna, proszę o raport.'
            with self.subTest(leader=leader):
                self.assertEqual(privacy.mask_local_details(text,names=True)[0],text)
                self.assertEqual(privacy.mask_local_details(text,names=True,name_masking='deep')[0],leader+' [NAME], proszę o raport.')

    def test_discourse_never_propagates(self):
        for mode in ('basic','deep'):
            text='However Anna disagreed. However, the team continued.'
            expected=text if mode=='basic' else 'However [NAME] disagreed. However, the team continued.'
            self.assertEqual(privacy.mask_local_details(text,names=True,name_masking=mode)[0],expected)
            self.assertNotIn((24,31,'name'),nm.spans(text,backend='g_full_r1'))

    def test_full_name_and_its_repeat_keep_evidence(self):
        text='Today Anna Kowalska called the office. Anna replied.'
        for mode in ('basic','deep'):
            self.assertEqual(privacy.mask_local_details(text,names=True,name_masking=mode)[0], 'Today [NAME] called the office. [NAME] replied.')


class CacheAndRegex(unittest.TestCase):
    def test_call_cache_is_reset_on_success_and_failure(self):
        self.assertIsNone(language.CACHE.get())
        nm.prompt_spans('Call John Doe today.')
        self.assertIsNone(language.CACHE.get())
        with patch.object(nm,'_prompt_spans_existing',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                nm.prompt_spans('Call John Doe today.')
        self.assertIsNone(language.CACHE.get())
        cache={}
        token=language.CACHE.set(cache)
        try:
            privacy.mask_local_details('Call John Doe today.',names=True)
            self.assertIs(language.CACHE.get(),cache)
        finally:
            language.CACHE.reset(token)

    def test_sentence_regex_preserves_short_whitespace_contexts(self):
        old=re.compile(r'(?im)(?:^|[.!?]\s+)\s*(?P<word>May(?=\s+I\b)|Will(?=\s+(?:the|you|it)\b)|Mark(?=\s+the\b)|Bill(?=\s+the\b)|Rose(?=\s+from\s+the\b))')
        for space in (' ','\t','\n','\r\n','\v','\f','\u00a0','\n  \n\t'):
            for prefix in ('','x.','x!'):
                for phrase in ('May I','Will you','Mark the','Bill the','Rose from the'):
                    text=prefix+space+phrase
                    self.assertEqual([m.start('word') for m in nm._SENTENCE_COMMON.finditer(text)], [m.start('word') for m in old.finditer(text)],repr(text))

    def test_sentence_regex_adversarial_bounded_inputs(self):
        for text in ('\n    '*40000+'A','. '+' '*200000+'A'):
            self.assertEqual(list(nm._SENTENCE_COMMON.finditer(text)),[])



class BasicTitledNames(unittest.TestCase):
    """basic keeps a single surname after a title (Mr. Tate), like a full name; deep already did."""

    def test_titled_surname_masked_in_basic(self):
        for text, name in (('Please ask Mr. Tate to sign the form.', 'Tate'), ('Dr. Patel will review the scan.', 'Patel'),
                           ('Ms. Vogel sent the invoice.', 'Vogel')):
            for mode in ('basic', 'deep'):
                self.assertNotIn(name, privacy.mask_local_details(text, names=True, name_masking=mode)[0], (text, mode))

    def test_untitled_single_names_stay_basic_contract(self):
        self.assertIn('Grace', privacy.mask_local_details('Grace period applies to this invoice.', names=True)[0])
        self.assertIn('Mr. Clean', privacy.mask_local_details('Mr. Clean is a cleaning product.', names=True)[0])


if __name__ == '__main__':
    unittest.main()
