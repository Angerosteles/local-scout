"""Explicit benchmark formats and the decision-to-artifact boundary."""
import json
from pathlib import Path
import tempfile
import unittest

from plugin import register
from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a
from plugin.scout import decisions
from plugin.schemas import SCHEMAS



class Premises(unittest.TestCase):
    facts = dict(label_count=3, min_examples_per_label_train=26, labels_present=True,
                 modality='text', score=.75, delta=-2, records=100)

    def check(self, reason, verdict, count=None, facts=None, fit=None):
        result = decisions.audit(reason, self.facts if facts is None else facts, fit or [])
        self.assertEqual(result['verdict'], verdict, reason)
        self.assertEqual(result['prose_status'], 'unverified')
        if count is not None:
            self.assertEqual(len(result['premises']), count, reason)
        return result

    def test_benchmark_chain_with_canonical_name_and_explicit_alias(self):
        for name in ('min_examples_per_label_train', 'min_examples'):
            with self.subTest(name=name):
                result = self.check(f'label_count=3>=2, {name}=26>=5',
                                    'explicit_premises_consistent', 4)
                self.assertEqual(result['premises'][2]['fact'], 'min_examples_per_label_train')

    def test_thresholds_and_colon_values(self):
        for reason in ('label_count>=2', 'label_count: 3',
                       'labels_present: true, modality: text',
                       ' score : 0.75 , delta=-2<=-1 ', 'LABEL_COUNT = 3 >= 2'):
            with self.subTest(reason=reason):
                self.check(reason, 'explicit_premises_consistent')

    def test_benchmark_spacing_and_equality_examples(self):
        facts = dict(self.facts, rule_match_ratio=1.0, rule_label_agreement=1.0)
        for reason in ('label_count=3 >=2', 'label_count = 3 >= 2', 'rule_match_ratio=1.0'):
            with self.subTest(reason=reason):
                result = self.check(reason, 'explicit_premises_consistent', facts=facts)
                self.assertTrue(result['premises'])

    def test_chain_verifies_claimed_value_and_threshold_independently(self):
        for reason in ('label_count=4>=2', 'label_count=3>=4',
                       'min_examples=25>=5', 'label_count: 2',
                       'score=0.75>0.8', 'delta=-1<=0'):
            with self.subTest(reason=reason):
                self.check(reason, 'premise_false')

    def test_unknown_facts_missing_values_and_prose_stay_unverified(self):
        for reason in ('label_cout=3>=2', 'labels_present', 'Etykiet jest trzy i wystarczą.',
                       'label_count=three', 'label_count>=true', 'modality>=text'):
            with self.subTest(reason=reason):
                self.check(reason, 'unverified')
        self.check('label_count: 3', 'unverified', facts={'label_count':None})
        self.check('min_examples=26>=5', 'unverified', facts={})
        self.check('label_count=3>=2, mystery: 5', 'explicit_premises_consistent')

    def test_malformed_values_and_chains_are_not_partially_certified(self):
        for reason in ('label_count=3abc', 'label_count=3.5.2', 'label_count=3>=',
                       'label_count=3>=2>=1', 'label_count=3==3', 'label_count=3e0'):
            with self.subTest(reason=reason):
                self.check(reason, 'unverified', 0)

    def test_prose_colon_is_ignored_and_sentence_period_is_valid(self):
        for reason in ('label_count=3>=2; tfidf_search: EXECUTABLE',
                       'min_examples_per_label_train=26>=5.'):
            with self.subTest(reason=reason):
                result=self.check(reason,'explicit_premises_consistent')
                self.assertTrue(result['premises'])
        self.check('min_examples_per_label_train=26>=5>=2.', 'unverified', 0)

    def test_alias_is_explicit_and_does_not_override_exact_fact(self):
        self.check('min_examples: 7', 'explicit_premises_consistent',
                   facts={'min_examples':7, 'min_examples_per_label_train':26})
        self.check('minimum_examples: 26', 'unverified')

    def test_card_conditions_still_have_authority(self):
        self.check('label_count=3>=2', 'against_card_conditions', fit=[dict(ok=False)])
        self.check('label_count=3>=2', 'unverified', fit=[dict(ok=None)])
        self.check('label_count=4>=2', 'premise_false', fit=[dict(ok=False)])


class ToolContract(unittest.TestCase):
    def test_english_visible_text_has_no_polish_diacritics(self):
        from plugin.scout.core import MESSAGES
        skill = (Path(__file__).resolve().parents[1] / 'skills/decide/SKILL.md').read_text()
        texts = [skill, *MESSAGES.values()]
        for schema in SCHEMAS.values():
            texts.append(schema['description'])
            texts.extend(p.get('description', '') for p in schema['parameters']['properties'].values())
        for text in texts:
            self.assertNotRegex(text, r'[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')

    def test_all_registered_descriptions_start_with_bridge_rule(self):
        for name, schema in SCHEMAS.items():
            with self.subTest(tool=name):
                self.assertTrue(schema['description'].startswith(
                    'Make one Scout tool call at a time; Hermes rejects batches. '))

    def test_registered_skill_is_declared_and_discoverable(self):
        from plugin import register
        from plugin.scout.context import LocalContext
        from pathlib import Path
        import yaml
        manifest=yaml.safe_load((Path(__file__).resolve().parents[1]/'plugin.yaml').read_text())
        with tempfile.TemporaryDirectory() as home:
            ctx=LocalContext(Path(home))
            register(ctx)
        # Hermes 0.23.0 does not know a provides_skills field ('unknown manifest field'); register() registers the skill.
        self.assertNotIn('provides_skills',manifest)
        self.assertEqual({'decide'},set(ctx.skills))

    def test_choice_requires_separate_training_and_does_not_pick_another_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            ctx = LocalContext(home)
            register(ctx)
            scout = ctx.tools['scout_choose']['handler'].scout
            source = home/'demo.jsonl'
            source.write_text(''.join(json.dumps(row)+'\n' for row in demo_a()))
            scout.configure_local('on_demand', grants={s:['a'] for s in ('observe','train')}, sources={'a':source})
            chosen = scout.scout_choose('triage', 'knn', 'label_count=3>=2')
            self.assertIn('scout_train', chosen['next_step'])
            self.assertIn('dataset_id=a', chosen['next_step'])
            self.assertFalse((scout.root()/'artifacts').exists())
            trained = scout.scout_train('triage', 'knn', 'a')
            self.assertEqual(chosen['decision_id'], trained['decision_id'])
            self.assertTrue((scout.root()/'artifacts'/(trained['artifact_id']+'.json')).is_file())
            mismatch = scout.scout_choose('fields', 'knn', 'label_count>=2')
            self.assertEqual(mismatch['recipe_id'], 'knn')
            self.assertIn('conflict', mismatch['next_step'])
            self.assertNotIn('call scout_train separately', mismatch['next_step'])
            direct = scout.scout_choose('fields', 'regex', 'label_count: 3')
            self.assertIn('scout_predict', direct['next_step'])
            self.assertIn('artifact_id=regex', direct['next_step'])
            plan = scout.scout_choose('triage', 'agent_mlp', 'label_count>=2')
            self.assertIn('do not call scout_train', plan['next_step'])

    def test_regex_prediction_only_follows_compatible_audit_and_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp); source=home/'demo.jsonl'
            source.write_text(''.join(json.dumps(row)+'\n' for row in demo_a()))
            ctx=LocalContext(home);register(ctx)
            scout=ctx.tools['scout_choose']['handler'].scout
            scout.configure_local('on_demand',grants={s:['a'] for s in ('observe','train')},sources={'a':source})
            wrong=scout.scout_choose('triage','regex','label_count=3>=2')
            self.assertIn('do not call scout_train or scout_predict',wrong['next_step'])
            valid=scout.scout_choose('fields','regex','label_count=3>=2')
            self.assertIn('scout_predict',valid['next_step'])
            self.assertNotIn('scout_train',valid['next_step'])

    def test_reason_rejection_explains_unsafe_content(self):
        from plugin.scout import privacy
        self.assertTrue(privacy.safe_reason('train/val plus logreg/knn'))
        failures = {
            '/etc/x': 'file path syntax', '~/a/b': 'file path syntax',
            'src/app/main.py': 'file path syntax', r'C:\x': 'file path syntax',
            'https://example.invalid/x': 'URL', 'build 12345': 'sequence of at least 5 digits',
            'kontakt a.test@example.com': 'PII category: email',
            'a' * 455: 'length 455 > 400', 'control\x01': 'control character',
        }
        for unsafe, expected in failures.items():
            with self.subTest(rule=expected):
                self.assertFalse(privacy.safe_reason(unsafe))
                self.assertEqual(privacy.reason_issue(unsafe), expected)
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);source=home/'demo.jsonl'
            source.write_text(''.join(json.dumps(row)+'\n' for row in demo_a()))
            ctx=LocalContext(home);register(ctx)
            scout=ctx.tools['scout_choose']['handler'].scout
            scout.configure_local('on_demand',grants={s:['a'] for s in ('observe','train')},sources={'a':source})
            for unsafe, expected in failures.items():
                with self.subTest(tool_rule=expected):
                    result=scout.call('scout_choose',dict(candidate_id='triage',recipe_id='logreg',reason=unsafe))
                    self.assertEqual(result['error']['code'],'INVALID_INPUT')
                    self.assertIn(expected, result['error']['message'])
                    self.assertNotIn(unsafe, result['error']['message'])

    def test_benchmark_reason_examples_pass_privacy_and_schema_validation(self):
        from plugin.scout import privacy
        from plugin.schemas import validate
        examples = (
            'label_count=3 >=2; torch/basal/cloud; train/val/test',
            'label_count = 3 >= 2',
            'extraction goal, rule_match_ratio=1.0, rule_label_agreement=1.0, tiny vocab',
        )
        facts = dict(label_count=3, rule_match_ratio=1.0, rule_label_agreement=1.0)
        for reason in examples:
            with self.subTest(reason=reason):
                self.assertTrue(privacy.safe_reason(reason))
                validate('scout_choose', dict(candidate_id='fields', recipe_id='regex', reason=reason))
                self.assertEqual(decisions.audit(reason, facts, [])['verdict'],
                                 'explicit_premises_consistent')

    def test_reason_limit_and_argument_guidance_are_published(self):
        reason = SCHEMAS['scout_choose']['parameters']['properties']['reason']
        self.assertEqual(reason['maxLength'], 400)
        self.assertIn('label_count=3>=2', reason['description'])


if __name__ == '__main__':
    unittest.main()
