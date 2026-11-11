"""Boundary regressions for transferred mechanisms, not happy-path snapshots."""
import json
from pathlib import Path
import tempfile
import unittest
from plugin import register
from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a
from plugin.scout import decisions,learning,storage
from plugin.scout.profile import facts



class Hybrid(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.ctx=LocalContext(self.home)
        register(self.ctx)
        self.scout=self.ctx.tools['scout_inspect']['handler'].scout
        self.source=self.home/'a.jsonl'
        self.rows=list(demo_a(seed=71))
        self.save(self.rows)
        self.scout.configure_local('on_demand',grants={s:['a','profile'] for s in ('observe','train','report')},sources={'a':self.source})
    def tearDown(self):
        self.tmp.cleanup()
    def save(self,rows):
        with self.source.open('w') as out:
            for row in rows:
                out.write(json.dumps(row)+'\n')
    def call(self,name,**params):
        return self.scout.call(name,params)
    def test_profile_does_not_change_after_test_content_changes(self):
        rows=[r for r in self.rows if r['kind']=='triage']
        before=facts(rows,'triage')
        changed=[dict(r,text='different vocabulary',label='test') if r['group'].endswith(('g10','g11')) else r for r in rows]
        self.assertEqual(before,facts(changed,'triage'))
        self.assertEqual(before['records'],100)
    def test_explicit_group_overlap_refused(self):
        rows=[dict(r,split='train') for r in self.rows if r['kind']=='triage']
        rows[0]['split']='test'
        with self.assertRaises(learning.Insufficient):
            learning.split(rows)
    def test_unverified_and_false_premises_are_not_certified(self):
        facts_={'records':100,'label_count':3}
        self.assertEqual(decisions.audit('records=999',facts_,[])['verdict'],'premise_false')
        self.assertEqual(decisions.audit('label_cout=999 etykiety',facts_,[])['verdict'],'unverified')
        self.assertEqual(decisions.audit('etykiety są nieobecne',facts_,[])['verdict'],'unverified')
        self.assertEqual(decisions.audit('label_count>=2',facts_,[dict(ok=None)])['verdict'],'unverified')
    def test_poisoned_label_refused_before_artifact_or_output(self):
        poison='test'+'@example.invalid'
        self.save([dict(r,label=poison) if i==0 else r for i,r in enumerate(self.rows)])
        result=self.call('scout_train',dataset_id='a',candidate_id='triage',recipe_id='logreg')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'],'UNSUPPORTED_SOURCE')
        self.assertFalse(poison in json.dumps(result))
        self.assertFalse((self.scout.root()/'artifacts').exists())
    def test_kwargs_text_blocks_system_and_metadata_boundaries(self):
        self.scout.configure_local('report')
        self.scout.clock=lambda:1000
        probe='probe'+'@example.invalid'
        hook=self.ctx.hooks['pre_api_request'][0]
        for n in range(3):
            hook(api_request_id='req-'+str(n),started_at=1000,request_messages=[
                dict(role='user',content=[dict(type='text',text=probe),dict(type='image_url',image_url='ignored')])],
                system_prompt=[dict(type='text',text='safe')],metadata={'private':probe})
        report=self.call('scout_report',dataset_id='profile')['data']
        self.assertTrue(len(report['alerts'])==1 and report['alerts'][0]['count']==3)
        self.assertTrue(report['alerts'][0]['scope']=='unknown')
        self.assertEqual(report['coverage_counts']['unsupported_parts'],3)
        notice=self.ctx.hooks['transform_llm_output'][0](response_text='Odpowiedź')
        self.assertTrue(isinstance(notice,str) and 'email: 3' in notice and probe not in notice)
        self.assertIsNone(self.scout.notify_output(response_text='Odpowiedź'))
        # Metadata alone must never produce an observation.
        self.scout.observe(api_request_id='meta',started_at=1000,request_messages=[],request={'text':probe},metadata=probe)
        self.assertEqual(len(self.scout.profile()['events']),3)
        for p in self.scout.root().rglob('*.json'):
            self.assertFalse(probe.encode() in p.read_bytes())
    def test_system_prompt_included_and_notice_revoked(self):
        self.scout.configure_local('report');self.scout.clock=lambda:1000
        for n in range(3):
            self.scout.observe(api_request_id=str(n),started_at=1000,system_prompt='system'+'@example.invalid')
        self.assertTrue(self.scout.profile()['notifications'])
        self.scout.configure_local('report',grants={'observe':['profile']})
        self.assertIsNone(self.scout.notify_output(response_text='safe'))
        self.scout.configure_local('off')
        self.assertIsNone(self.scout.notify_output(response_text='safe'))
    def test_retention_is_persisted_and_no_raw_request_id(self):
        self.scout.configure_local('background',retention_days=1);self.scout.clock=lambda:1000
        rid='request'+'@example.invalid'
        self.scout.observe(api_request_id=rid,started_at=1000,text='test'+'@example.invalid')
        self.scout.clock=lambda:1000+86401
        self.call('scout_report',dataset_id='profile')
        saved=storage.read_json(self.scout.root()/'aggregates.json')
        self.assertFalse(saved['events'])
        self.assertFalse(rid in json.dumps(saved))
    def test_artifacts_reload_and_decision_measured_outcome(self):
        self.call('scout_inspect',dataset_id='a')
        for engine,candidate in [('logreg','triage'),('knn','triage'),('embed_search','search'),('own_cnn','triage')]:
            decision=self.call('scout_choose',candidate_id=candidate,recipe_id=engine,reason='label_count>=2; local CPU validation.')
            self.assertTrue(decision['ok'])
            result=self.call('scout_train',candidate_id=candidate,recipe_id=engine,dataset_id='a')
            self.assertTrue(result['ok'])
            data=result['data']
            self.assertEqual(data['decision_id'],decision['data']['decision_id'])
            aid=data['artifact_id']
            query='a-'+candidate+'-081'
            initial=self.call('scout_predict',artifact_id=aid,query_id=query)
            other=self.scout.__class__(self.ctx)
            self.assertEqual(initial,other.call('scout_predict',dict(artifact_id=aid,query_id=query)))
            self.assertTrue(initial['ok'])
            if engine=='logreg':
                decisions=storage.read_json(self.scout.root()/'decisions.json')
                outcome=next(row['outcome'] for row in decisions if row['decision_id']==data['decision_id'])
                self.assertEqual(outcome['status'],'measured')
                self.assertIn('metrics',outcome)
                self.assertEqual(len(outcome['predictions']),2)
            meta,arrays=storage.load_artifact(self.scout.root()/'artifacts',aid)
            if engine=='own_cnn':
                self.assertTrue(meta['sequence_length']==64 and arrays['cnn_conv.weight'].shape==(32,24,3))
                forward=learning.transform(meta,arrays,['parser tests','tests parser'])
                self.assertFalse((forward[0]==forward[1]).all())
            if engine=='embed_search':
                self.assertTrue('svd' in arrays and bool(initial['data'].get('top_k')))
        entries=storage.read_json(self.scout.root()/'decisions.json')
        self.assertTrue(all(e['outcome']['status']=='measured' for e in entries))
    def test_regex_decision_has_measured_result_without_raw_values(self):
        self.call('scout_inspect',dataset_id='a')
        self.call('scout_choose',candidate_id='fields',recipe_id='regex',reason='rule_label_agreement>=0.9; local rule.')
        result=self.call('scout_predict',artifact_id='regex',query_id='a-fields-081')
        self.assertTrue(result['ok'])
        entries=storage.read_json(self.scout.root()/'decisions.json')
        outcome=entries[-1]['outcome']
        self.assertEqual(outcome['status'],'measured_prediction')
        self.assertNotIn('text',outcome)

    def test_weighted_knn_uses_multiple_neighbors(self):
        import numpy as np
        meta=dict(engine='knn',neighbors=3,classes=['build','test'])
        arrays=dict(train=np.array([[1.,0.],[.9,.1],[.8,.2]]),labels=np.array([0,1,1]))
        predicted,scores=learning.raw_scores(meta,arrays,np.array([[1.,0.]]))
        self.assertEqual(int(predicted[0]),1)


if __name__=='__main__':
    unittest.main()
