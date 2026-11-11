"""Regression coverage for conversion and redaction of annotated PII."""
import unittest

try:  # the dataset converter lives outside the plugin package
    from bench_data.convert_en import gretel_row, group_id, validate_groups
except ImportError:
    gretel_row = group_id = validate_groups = None


@unittest.skipIf(gretel_row is None, 'the dataset converter (bench_data) is development tooling and is not shipped')
class ConverterEN(unittest.TestCase):
    def test_group_hash_is_safe_identifier(self):
        group=group_id('repeated source question')
        self.assertRegex(group,r'^g-[a-v]{20}$')

    def test_annotation_values_are_masked_and_types_survive(self):
        email='sample.person@example.invalid'
        identifier='999-12-3456'
        row=dict(text=f'Contact {email}; identifier {identifier}.',entities=repr([
            {'entity':email,'types':['email']},
            {'entity':identifier,'types':['ssn']},
        ]))
        converted,types=gretel_row(row,1)
        self.assertEqual(types,{'email','ssn'})
        self.assertIn('[EMAIL]',converted['text'])
        self.assertIn('[SSN]',converted['text'])
        self.assertNotIn(email,converted['text'])
        self.assertNotIn(identifier,converted['text'])
        self.assertEqual(converted['label'],'email')
        self.assertEqual(converted['entity_types'],['email','ssn'])

    def test_unaligned_annotation_redacts_text_and_keeps_type_marker(self):
        converted,_=gretel_row(dict(text='private record',entities=repr([
            {'entity':'not-in-text-12345','types':['ssn']},
        ])),2)
        self.assertEqual(converted['text'],'[SSN]')

    def test_group_validator_rejects_cross_split_duplicate_text(self):
        dataset=[dict(kind='triage',group='g1',text='same request',split='train'),
                 dict(kind='triage',group='g2',text='same request',split='test')]
        with self.assertRaises(RuntimeError):
            validate_groups(dataset)


if __name__=='__main__':
    unittest.main()
