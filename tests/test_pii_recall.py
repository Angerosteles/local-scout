"""Synthetic contract regressions for candidate boundaries and safe model failures."""
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
from plugin.scout import privacy, pii_model, tool_guard


class RecallRegression(unittest.TestCase):
    def test_complete_numeric_redaction_in_json(self):
        raw=json.dumps({'phone':4155552671,'ssn':'123-45-6789','ok':True})
        replacement,categories,partial=tool_guard.transform(raw,'tool_result','test')
        self.assertFalse(partial)
        self.assertTrue({'phone','ssn'}<=set(categories))
        self.assertNotIn('4155552671',replacement)
        self.assertNotIn('123-45-6789',replacement)
        self.assertIs(json.loads(replacement)['ok'],True)

    def test_phone_extension_and_international_boundaries(self):
        for text in ('Phone: +1 415 555 2671 ext. 123', 'Phone: +44 20 7946 0958',
                     'SSN: 123 45 6789'):
            masked,_=privacy.mask_local(text)
            self.assertFalse(any(ch.isdigit() for ch in masked),masked)

    def test_international_ssn_masks_letters_too(self):
        for text in ('National Insurance number: AB 12 34 56 Z', 'Social security: 123 456 789'):
            masked,_=privacy.mask_local(text)
            self.assertIn('[SSN]',masked)
            self.assertNotIn('12',masked)

    def test_address_parts_join_across_comma_without_losing_tail(self):
        masked,_=privacy.mask_local('Address: 123 Main Street, Boston, MA 02110')
        self.assertIn('[ADDRESS]',masked)
        self.assertNotIn('02110',masked)

    def test_dates_ips_and_reference_numbers_are_not_phones_or_ssn(self):
        for text in ('phone service released on 2026-11-07', 'phone server IP 192.168.100.10',
                     'Batch 123-45-6789; invoice 456-78-9012', 'build 1234567890; version 202611070'):
            self.assertFalse({'phone','ssn'} & set(privacy.categories(text)),text)

    def test_ssn_does_not_match_inside_international_phone(self):
        self.assertEqual(privacy.categories('Phone: +48 000 000 001'),['phone'])

    def test_missing_model_withholds_candidate(self):
        with patch.object(pii_model,'model',side_effect=OSError('missing model')):
            replacement,_,partial=tool_guard.transform('Reference 4155552671','tool_result','failure')
        self.assertTrue(partial)
        self.assertIn('action=withheld',replacement)
        self.assertNotIn('4155552671',replacement)

    def test_dense_result_is_withheld_and_budget_does_not_leak(self):
        raw=('Phone: +1 415 555 2671; '*600)
        replacement,_,partial=tool_guard.transform(raw,'tool_result','dense')
        self.assertTrue(partial)
        self.assertIn('action=withheld',replacement)
        self.assertNotIn('415',replacement)
        self.assertIsNone(pii_model._CANDIDATE_BUDGET.get())
        self.assertIn('phone',privacy.categories('Phone: +1 415 555 2671'))

    def test_corrupt_coefficients_fail_closed(self):
        root=Path(__file__).resolve().parents[1]
        original=json.loads(pii_model.MODEL_PATH.read_text())
        original['models']['phone']['weights'][0]=float('nan')
        with tempfile.TemporaryDirectory() as home:
            path=Path(home)/'model.json'
            path.write_text(json.dumps(original))
            pii_model.model.cache_clear()
            try:
                with patch.object(pii_model,'MODEL_PATH',path):
                    replacement,_,partial=tool_guard.transform('Reference 4155552671','tool_result','corrupt')
                self.assertTrue(partial)
                self.assertIn('action=withheld',replacement)
                self.assertNotIn('4155552671',replacement)
            finally:
                pii_model.model.cache_clear()

    def test_model_is_small_and_contains_no_vocabulary_or_examples(self):
        artifact=json.loads(pii_model.MODEL_PATH.read_text())
        self.assertLess(pii_model.MODEL_PATH.stat().st_size,1000000)
        self.assertEqual(artifact['dimension'],2048)
        for item in artifact['models'].values():
            self.assertEqual(set(item),{'weights','intercept','threshold'})
            self.assertEqual(len(item['weights']),2048)
            self.assertTrue(all(isinstance(w,(int,float)) for w in item['weights']))
        for field in ('texts','examples','vocabulary','input_id','uid'):
            self.assertNotIn(field,artifact)

if __name__=='__main__':unittest.main()
