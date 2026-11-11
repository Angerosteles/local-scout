"""Causal replay, safe outputs and independent holdout regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from plugin.scout.calibration import replay, parameters
from plugin.scout.presentation import write_html
from plugin.scout.core import Scout
from plugin.scout import storage, operator
from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a

HERE = Path(__file__).resolve().parent


def history(n=12):
    return [dict(text=('build parser module' if i%2 else 'test suite coverage'), label='build' if i%2 else 'test',kind='triage',timestamp=i) for i in range(n)]


class Calibration(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(replay(history()),replay(history()))
    def test_prefix_never_reads_future_vocabulary_labels_or_clusters(self):
        a=history(18);b=a[:9]+[dict(text='unseen adversarial vocabulary',label='dependency',timestamp=i) for i in range(9,18)]
        self.assertEqual(replay(a)['events'][:9],replay(b)['events'][:9])
        self.assertEqual(replay(a[:9])['events'],replay(a)['events'][:9])
    def test_later_timestamp_does_not_change_past(self):
        a=history();b=a+[dict(text='new family',timestamp=9999)]
        self.assertEqual(replay(a)['events'],replay(b)['events'][:len(a)])
    def test_no_pii_in_json_or_html(self):
        secret='person'+'@example.invalid';rows=[dict(text=secret+' message',id=secret,kind=secret,label=secret,timestamp=i) for i in range(5)]
        r=replay(rows);self.assertNotIn(secret,json.dumps(r))
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'r.html';write_html(r,p)
            self.assertNotIn(secret,p.read_text());self.assertNotIn('https://',p.read_text());self.assertNotIn('src=',p.read_text())
    def test_missing_model_is_lsa_without_network(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network')):
            r=replay(history(),model_path=HERE/"absent-model")
        self.assertEqual(r['backend'],'lsa_tfidf_svd');self.assertTrue(r['model_fallback'])
    def test_notice_uses_only_current_and_prior_cases(self):
        r=replay([dict(text='same task words',label='build') for _ in range(5)])
        self.assertEqual([e['prior_cases'] for e in r['events']],[0,1,2,3,4])
        self.assertEqual([e['first_notice'] for e in r['events']],[False,False,True,False,False])
        self.assertEqual(r['events'][2]['status'],'local')
    def test_timestamp_sort_and_ties(self):
        r=replay(list(reversed(history())))
        self.assertEqual(r['events'],replay(history())['events'])
        with self.assertRaises(ValueError):replay([dict(text='a',timestamp=0),dict(text='b')])
    def test_empty_vocabulary_and_unknown_error_are_not_success(self):
        r=replay([dict(text='!!!') for _ in range(4)],dict(threshold=0,family_similarity=0))
        self.assertEqual(r['summary']['accepted'],0)
        r=replay([dict(text='identical message') for _ in range(4)],dict(error_cost=1))
        self.assertIsNone(r['summary']['estimated_net']);self.assertEqual(r['summary']['unknown_errors'],2)
    def test_threshold_and_assumptions_are_explicit(self):
        r=replay(history(),dict(tokens_per_task=50,price_per_million=20))
        self.assertEqual(r['assumptions']['tokens_per_task'],50)
        self.assertEqual(r['summary']['estimated_tokens_saved'],r['summary']['accepted']*50)
        for bad in ({'threshold':float('nan')},{'seed':True},{'min_cases':0},{'currency':'pii'}):
            with self.assertRaises(ValueError):parameters(bad)
    def test_known_wrong_is_counted(self):
        r=replay([dict(text='identical words',label='build' if i<3 else 'test') for i in range(5)])
        self.assertEqual(r['summary']['errors'],2) # deterministic earliest tie, only past labels


class Holdout(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.scout=Scout(LocalContext(self.home));self.rows=list(demo_a(seed=17));self.source=self.home/'source.jsonl';self.save()
        self.scout.configure_local('on_demand',grants={s:['a'] for s in ('observe','train','report')},sources={'a':self.source})
    def tearDown(self):self.tmp.cleanup()
    def save(self):
        self.source.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
    def call(self,name,**p):return self.scout.call(name,p)
    def test_public_test_ids_always_hidden_even_after_choice(self):
        for kind,recipe in [('triage','logreg'),('search','embed_search'),('fields','regex')]:
            for stage in range(2):
                self.assertEqual(self.call('scout_predict',artifact_id=recipe,query_id=f'a-{kind}-101')['error']['code'],'NOT_FOUND')
                self.call('scout_choose',candidate_id=kind,recipe_id=recipe,reason='label_count>=2')
    def test_test_changes_do_not_change_validation_metrics_or_arrays(self):
        a=self.call('scout_train',candidate_id='triage',recipe_id='logreg',dataset_id='a')['data']
        ma,aa=storage.load_artifact(self.scout.root()/'artifacts',a['artifact_id'])
        self.rows=[dict(r,text='future unrelated word',label='dependency') if r['kind']=='triage' and r['group'].endswith(('g10','g11')) else r for r in self.rows];self.save()
        b=self.call('scout_train',candidate_id='triage',recipe_id='logreg',dataset_id='a')['data']
        mb,ab=storage.load_artifact(self.scout.root()/'artifacts',b['artifact_id'])
        self.assertEqual(a['metrics'],b['metrics']);self.assertEqual(a['metrics_scope'],'validation')
        import numpy as np
        for k in aa:np.testing.assert_array_equal(aa[k],ab[k])
    def test_agent_calibration_excludes_holdout_and_inspect_adds_facts(self):
        p=dict(dataset_id='a',threshold=.8,min_cases=3,seed=42)
        a=self.call('scout_calibrate',**p);self.assertTrue(a['ok'])
        self.assertEqual(a['data']['events']['count'],300)
        evidence=self.call('scout_inspect',dataset_id='a')['data']['candidates'][0]['calibration_evidence']['families']
        self.assertTrue(evidence)
        self.assertEqual(sum(f['profile']['records'] for f in evidence),100)
        self.assertTrue(all(f['count']==f['profile']['records'] for f in evidence))
        self.assertTrue(any(f['profile']['records']<100 for f in evidence))
        self.rows=[dict(r,text='future unrelated word') if r['group'].endswith(('g10','g11')) else r for r in self.rows];self.save()
        b=self.call('scout_calibrate',**p)
        self.assertEqual(a,b)
    def test_freeze_all_then_evaluate_once_and_disallow_further_selection(self):
        self.call('scout_inspect',dataset_id='a')
        with self.assertRaises(FileNotFoundError):operator.freeze(self.scout,'a')
        for kind,recipe in [('triage','logreg'),('search','embed_search'),('fields','regex')]:
            self.assertTrue(self.call('scout_choose',candidate_id=kind,recipe_id=recipe,reason='label_count>=2')['ok'])
            if recipe!='regex':self.assertTrue(self.call('scout_train',candidate_id=kind,recipe_id=recipe,dataset_id='a')['ok'])
        operator.freeze(self.scout,'a');result=operator.evaluate(self.scout,'a')
        self.assertEqual(result['scope'],'operator_only_final_holdout')
        with self.assertRaises(ValueError):operator.evaluate(self.scout,'a')
        self.assertFalse(self.call('scout_choose',candidate_id='triage',recipe_id='trees',reason='label_count>=2')['ok'])
        self.assertFalse(self.call('scout_train',candidate_id='triage',recipe_id='trees',dataset_id='a')['ok'])
        self.assertNotIn('operator_only_final_holdout',json.dumps(self.call('scout_inspect',dataset_id='a')))


if __name__=='__main__':unittest.main()
