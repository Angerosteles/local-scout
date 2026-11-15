import unittest
from unittest.mock import patch

from plugin.scout import name_context as nc, name_model as nm, paragraph_context as pc, privacy


def spans(text, *values):
    return [(text.index(value), text.index(value)+len(value), 'name') for value in values]


class ContextRules(unittest.TestCase):
    def filtered(self, text, values, mechanism):
        return nc.filter_spans(text, spans(text,*values), {mechanism})

    def test_labels_preserve_chat_and_values(self):
        for text, wrong, people in (
            ('Addr: 12 Main St', ['Addr'], []),
            ('Prev Addr: 12 Main St', ['Prev Addr'], []),
            ('Acked-by: Priya Raman', ['Acked-by'], ['Priya Raman']),
            ('Owner: Priya Raman', ['Owner'], ['Priya Raman']),
            ('Owner: SRE On-Call', ['Owner','SRE On-Call'], []),
            ('\tAddr: 12 Main St\r\nOwner: Priya Raman', ['Addr','Owner'], ['Priya Raman']),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.filtered(text,wrong+people,'labels'),spans(text,*people))
        for person in ('Maya','Grace','Mark','Robin'):
            text = person+': can you review the report?'
            self.assertEqual(self.filtered(text,[person],'labels'),spans(text,person))
        for person in ('Robin','Faith','Will','Bill','Grace','Hope'):
            text = 'Hi '+person+', please reply.'
            self.assertEqual(self.filtered(text,[person],'labels'),spans(text,person))
        text = 'Dear Finance, please reply.'
        self.assertEqual(self.filtered(text,['Finance'],'labels'),[])

    def test_company_suffixes_and_enumerations(self):
        for suffix in (
                'Corp','Inc','Ltd','LLC','GmbH','Partners','Group','Bank','Press','Labs','Systems','Holdings','& Co'):
            text = 'Lehman '+suffix+' requested the report.'
            with self.subTest(suffix=suffix):
                self.assertEqual(self.filtered(text,['Lehman'],'companies'),[])
        for text in (
            'The firms Lazard Frères, Clifford Chance and Smith & Smith requested help.',
            'We asked Lazard Frères, Clifford Chance and Smith & Co to call.',
        ):
            self.assertEqual(self.filtered(text,['Lazard Frères','Clifford Chance','Smith'],'companies'),[])
        text = 'Ms. Bank signed. Harper Lee wrote the report.'
        self.assertEqual(self.filtered(text,['Bank','Harper Lee'],'companies'),spans(text,'Bank','Harper Lee'))
        text = 'Lehman Partners requested help. Priya Raman wrote the report.'
        self.assertEqual(self.filtered(text,['Lehman Partners','Priya Raman'],'companies'),spans(text,'Priya Raman'))
        text = 'Lehman Partners, Ms. Bank and Clifford Chance requested help.'
        self.assertEqual(self.filtered(text,['Lehman Partners','Bank','Clifford Chance'],'companies'),spans(text,'Bank'))
        text = 'Lazard Frères, Mr. Bank and Clifford Chance signed the report.'
        self.assertEqual(self.filtered(text,['Lazard Frères','Bank','Clifford Chance'],'companies'),
                         spans(text,'Lazard Frères','Bank','Clifford Chance'))

    def test_work_context_is_required_with_typography(self):
        for left,right in [('"','"'),('“','”'),('‘','’'),("'","'"),('*','*'),('_','_'),('','')]:
            text = 'Please read '+left+'Tom Jones'+right+' before tomorrow.'
            self.assertEqual(self.filtered(text,['Tom Jones'],'works'),[])
            text = left+'Tom Jones'+right+' said the report is ready.'
            self.assertEqual(self.filtered(text,['Tom Jones'],'works'),spans(text,'Tom Jones'))
        for text in ('The book by Harper Lee is here.', 'The translations for Tom Jones said nothing new.',
                     'Please send the book to Harper Lee.', 'Please ask "Tom Jones" to reply.'):
            value = 'Harper Lee' if 'Harper Lee' in text else 'Tom Jones'
            self.assertEqual(self.filtered(text,[value],'works'),spans(text,value))
        text = 'The translations for Hamlet and Little Dorrit are ready.'
        self.assertEqual(self.filtered(text,['Hamlet','Little Dorrit'],'works'),[])

    def test_nationality_and_person_guard(self):
        for text,wrong in (('Your Polish PESEL is needed.','Your Polish PESEL'),
                           ('His Polish mobile is unavailable.','His Polish')):
            self.assertEqual(self.filtered(text,[wrong],'nationality'),[])
        for text,person in (('Polish said the report is ready.','Polish'),
                            ('Ms. Polish signed the report.','Polish'),
                            ('Polish JOHN said the report is ready.','Polish JOHN'),
                            ('Please ask Polish Lee to reply.','Polish Lee'),
                            ('The Polish engineer John Doe called.','John Doe')):
            self.assertEqual(self.filtered(text,[person],'nationality'),spans(text,person))
        text = 'Your Polish PESEL Priya Raman'
        self.assertEqual(self.filtered(text,[text],'nationality'),spans(text,'Priya Raman'))
        self.assertNotIn('technical',nc._nationalities())
        self.assertIn('polish',nc._nationalities())

    def test_no_cascading_or_cross_line_company_list(self):
        text = 'Lehman Partners\nPriya Raman said the report is ready.'
        self.assertEqual(self.filtered(text,['Lehman Partners','Priya Raman'],'companies'),spans(text,'Priya Raman'))
        text = 'Dear Finance, please reply.'
        original = spans(text,'Finance')
        self.assertEqual(nc.filter_spans(text,original,frozenset()),original)
        self.assertEqual(original,spans(text,'Finance'))


class PromptIntegration(unittest.TestCase):
    def test_person_guards_through_real_prompt_and_full_mask(self):
        for text,person in (('Harper Lee wrote the report.','Harper Lee'),
                            ('Ms. Bank signed the report.','Bank'),
                            ('Owner: Priya Raman','Priya Raman'),
                            ('Tom Jones said the report is ready.','Tom Jones'),
                            ('Maya: can you review the report?','Maya'),
                            ('Hi Robin, please review the report.','Robin')):
            with self.subTest(text=text):
                self.assertEqual(nm.prompt_spans(text),spans(text,person))
                self.assertEqual(privacy.mask_local_details(text,names=True, name_masking='deep')[0],text.replace(person,'[NAME]'))

    def test_removed_organization_cannot_support_a_singleton(self):
        text = 'Lehman Partners requested help. We discussed Partners afterwards.'
        fresh = spans(text,'Lehman Partners','Partners')
        # The second occurrence is otherwise kept solely as a full-name component.
        second = text.rindex('Partners')
        fresh[-1] = (second,second+8,'name')
        old_spans = nm.spans
        def detect(t,*,backend=None):
            return fresh if backend else old_spans(t)
        with patch.object(nm,'spans',side_effect=detect),patch.object(pc,'classify',return_value={'lang':{'EN':1.}}):
            self.assertEqual(nm.prompt_spans(text),[])

    def test_routing_boundaries_and_fail_closed(self):
        text = 'Dear Finance, please review the report.'
        baseline = nm.spans(text)
        with patch.object(pc,'classify',return_value={'lang':{'EN':.79,'PL':.21}}):
            self.assertEqual(nm.prompt_spans(text, mode='basic'),[])
        with patch.object(nc,'filter_spans',side_effect=RuntimeError('missing lexicon')):
            self.assertEqual(nm.prompt_spans(text),baseline)
        text = 'Proszę poprosić Annę Kowalską o raport.\n\nDear Finance, please reply.'
        result = nm.prompt_spans(text, mode='basic')
        self.assertEqual([x for x in nm.prompt_spans(text) if x[0]<text.index('\n')],
                         [x for x in nm.spans(text) if x[0]<text.index('\n')])
        self.assertFalse(any(text[s:e]=='Finance' for s,e,_ in result))



from plugin.scout import name_model as nm


NON_PERSON_LISTS = (
    'Our frontend suite mixes Mocha, Chai, Jasmine, and Karma',
    'Hamlet and Macbeth outranked Alice in Wonderland',
    'Holly and Olive themes ship in Chai',
    'Clay and Reed connectors in Redis',
    'the Julia and Ada benchmarks',
    'metrics from Watson and Alexa connectors',
    'regional blocs cover Georgia, Jordan, Kenya, and Chad',
    'teams split across Sydney, Santiago, Madison, and Sofia',
)
SIGNOFFS = ('Best, Tariq', 'Regards, Nina', 'Cheers, Chloe', 'Thanks, Anna')
PERSON_LISTS = (
    ('Bob, Emily, Michael, David, Sarah and Jessica met Daniel yesterday.',
     ('Bob', 'Emily', 'Michael', 'David', 'Sarah', 'Jessica', 'Daniel')),
    ('Tom told Alice and Bob that Sarah, Emily, and Michael will join David later.',
     ('Tom', 'Alice', 'Bob', 'Sarah', 'Emily', 'Michael', 'David')),
    ('Please invite Bob, Emily and Sarah.', ('Bob', 'Emily', 'Sarah')),
    ('Robin and Faith signed the report.', ('Robin', 'Faith')),
)
HEADERS = (
    ('From: Kowalska, Anna; Reviewer: Smith, John', ('Kowalska', 'Anna', 'Smith', 'John')),
    ('Kowalska, Anna', ('Kowalska', 'Anna')),
    ('Smith, John', ('Smith', 'John')),
    ('"Kowalska, Anna","value"', ('Kowalska', 'Anna')),
    ('"Smith, John","value"', ('Smith', 'John')),
)




class ListEvidence(unittest.TestCase):
    def test_commanders_negative_lists_through_both_modes(self):
        for text in NON_PERSON_LISTS:
            for mode in ('basic', 'deep'):
                with self.subTest(text=text, mode=mode):
                    self.assertEqual(nm.prompt_spans(text, mode), [])

    def test_existing_and_contact_action_positives(self):
        for text, people in PERSON_LISTS:
            with self.subTest(text=text):
                self.assertEqual(nm.prompt_spans(text, 'deep'), spans(text, *people))

    def test_person_verbs_and_modal_have_the_same_evidence(self):
        for action in ('met', 'told', 'will join', 'signed'):
            text = 'Robin and Faith ' + action + ' Daniel.'
            found = nm._coordinated_names(text, [])
            self.assertEqual(found, spans(text, 'Robin', 'Faith'))

    def test_confirmed_full_name_or_greeting_supports_list(self):
        for text, seed, people in (
            ('Robin Smith replied. Robin and Faith are here.', ('Robin Smith',), ('Robin', 'Faith')),
            ('Hi Robin and Faith, please reply.', (), ('Robin', 'Faith')),
            ('Thanks Robin and Faith, please reply.', (), ('Robin', 'Faith')),
            ('cc: Robin and Faith', (), ('Robin', 'Faith')),
            ('Hi Robin. Robin and Faith are here.', ('Robin',), ('Robin', 'Faith')),
            ('cc: Robin. Robin and Faith are here.', ('Robin',), ('Robin', 'Faith')),
        ):
            with self.subTest(text=text):
                initial = spans(text, *seed)
                found = nm._coordinated_names(text, initial)
                for person in people:
                    start = text.rindex(person)
                    self.assertIn((start, start + len(person), 'name'), found)

    def test_noun_veto_precedes_confirmation(self):
        for noun in ('themes', 'connectors', 'benchmarks', 'tests'):
            text = 'Robin Smith replied. Robin and Faith ' + noun + ' are ready.'
            seed = spans(text, 'Robin Smith')
            self.assertEqual(nm._coordinated_names(text, seed), seed)

    def test_model_singleton_is_not_confirmation(self):
        for text in ('Robin and Faith are here.', 'in Robin and Faith',
                     'across Robin and Faith', 'teams cover Robin and Faith'):
            seed = spans(text, 'Robin')
            with self.subTest(text=text):
                self.assertEqual(nm._coordinated_names(text, seed), seed)
                self.assertEqual(nm._singletons_with_evidence(text, spans(text, 'Robin', 'Faith')), [])

    def test_full_span_fragment_in_the_same_list_does_not_seed_an_address(self):
        for text, person in (('San Jose, CA 95110', 'San Jose'),
                             ('4874 Leslie Falls, South Jaredstad', 'Leslie Falls')):
            seed = spans(text, person)
            with self.subTest(text=text):
                self.assertEqual(nm._coordinated_names(text, seed), seed)

    def test_title_still_independently_confirms_a_singleton(self):
        text = "Apple announced it would acquire Dr. Dre and Jimmy Iovine's company Beats Electronics."
        self.assertEqual(nm.prompt_spans(text, 'deep'), spans(text, 'Dre', 'Jimmy Iovine'))


class ReversedPairEvidence(unittest.TestCase):
    def test_headers_and_csv_in_both_modes(self):
        for text, people in HEADERS:
            for mode in ('basic', 'deep'):
                with self.subTest(text=text, mode=mode):
                    self.assertEqual(nm.prompt_spans(text, mode), spans(text, *people))

    def test_signoff_is_not_a_surname_in_either_mode(self):
        for text in SIGNOFFS:
            first = text.split(',')[0]
            for mode in ('basic', 'deep'):
                with self.subTest(text=text, mode=mode):
                    self.assertFalse(any(s < len(first) for s, _, _ in nm.prompt_spans(text, mode)))
                    self.assertEqual(nm._coordinated_names(text, [], lists=False), [])

    def test_ordinary_dictionary_word_is_not_a_surname(self):
        for first in ('Best', 'Regards', 'Cheers', 'Thanks', 'Report', 'Table'):
            for text in (first + ', Anna', 'From: ' + first + ', Anna', '"' + first + ', Anna","value"'):
                with self.subTest(text=text):
                    self.assertEqual(nm._coordinated_names(text, [], lists=False), [])

    def test_inline_prose_and_list_fragments_are_not_pairs(self):
        for text in ('We reviewed Kowalska, Anna yesterday.', 'We reviewed "Kowalska, Anna" today.',
                     'We wrote this note: Kowalska, Anna', 'Kowalska, Anna, Emily', 'Kowalska, Anna and Emily'):
            with self.subTest(text=text):
                self.assertEqual(nm._coordinated_names(text, [], lists=False), [])

    def test_crlf_and_indentation_preserve_header_context(self):
        text = 'Details\r\n\tKowalska, Anna\r\nReviewer: Smith, John'
        self.assertEqual(nm._coordinated_names(text, [], lists=False), spans(text, 'Kowalska', 'Anna', 'Smith', 'John'))


if __name__ == '__main__':
    unittest.main()
