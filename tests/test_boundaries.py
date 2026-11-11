"""Regression tests for privacy/consent boundaries missed by happy-path demos."""
import json
from pathlib import Path
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch

from plugin import register
from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a,demo_b
from plugin.scout import storage



class Boundaries(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.ctx=LocalContext(self.home);register(self.ctx)
        self.scout=self.ctx.tools['scout_inspect']['handler'].scout
        self.source=self.home/'a.jsonl'
        with self.source.open('w') as out:
            for row in demo_a(seed=19): out.write(json.dumps(row)+'\n')
    def tearDown(self):
        self.temp.cleanup()
    def configure(self):
        self.scout.configure_local('on_demand',grants={s:['a','profile','other'] for s in ('observe','report','train')},sources={'a':self.source})
    def call(self,name,**p):
        return json.loads(self.ctx.tools[name]['handler'](p))
    def test_off_hook_does_not_create_state(self):
        for event in demo_b(1000): self.scout.observe(event)
        self.assertFalse(self.scout.root().exists())
        self.assertEqual(self.call('scout_inspect',dataset_id='a')['error']['code'],'OFF')

    def test_cli_demo_consent_never_adds_profile_for_observe_or_mask(self):
        from plugin.cli import main
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            main(['--home',str(self.home/'cli'),'configure','--mode','report','--dataset','demo_a','--approve','observe,mask'])
        saved=json.loads(output.getvalue())
        self.assertEqual(saved['effective_scope'],{'observe':['demo_a'],'mask':['demo_a']})
        self.scout.configure_local('report',grants=saved['effective_scope'])
        self.scout.observe(api_request_id='profile-event',started_at=self.scout.clock(),
                           request_messages=[{'role':'user','content':'user@example.com'}])
        self.assertIsNone(self.scout.transform_tool_result(result='user@example.com',session_id='s',tool_call_id='c'))
        self.assertEqual(self.scout.profile()['events'],[])
        self.scout.configure_local('report',grants={'observe':['profile'],'mask':['profile']})
        self.assertIn('[EMAIL]',self.scout.transform_tool_result(result='user@example.com',session_id='s',tool_call_id='d'))
    def test_profile_switch_does_not_share_consent(self):
        self.configure();self.assertTrue(self.call('scout_inspect',dataset_id='a')['ok'])
        self.ctx.home=self.home/'new-profile'
        self.assertEqual(self.call('scout_inspect',dataset_id='a')['error']['code'],'OFF')
        self.assertFalse(self.scout.root().exists())
    def test_reports_do_not_cross_dataset_scope(self):
        self.configure();self.scout.configure_local('report');self.scout.clock=lambda:1000
        for event in demo_b(1000): self.scout.observe(event)
        self.assertEqual(len(self.call('scout_report',dataset_id='profile')['data']['alerts']),4)
        other=self.call('scout_report',dataset_id='other')['data']
        self.assertEqual(other['alerts'],[])
        self.assertEqual(other['coverage_counts'],dict(text_parts=0,unsupported_parts=0,truncated_parts=0))
    def test_expired_events_and_unknown_origin(self):
        self.configure();self.scout.configure_local('report');self.scout.clock=lambda:1000
        for event in demo_b(1000): self.scout.observe(dict(event,destination='unknown'))
        report=self.call('scout_report',dataset_id='profile')['data']
        self.assertTrue(all(a['scope']=='unknown' for a in report['alerts']))
        self.scout.clock=lambda:1000+86401
        self.assertEqual(self.call('scout_report',dataset_id='profile')['data']['alerts'],[])
    def test_usage_auxiliary_and_dedup(self):
        self.configure();self.scout.configure_local('background')
        event=dict(api_request_id='a-request',usage={'prompt_tokens':12,'completion_tokens':3})
        hook=self.ctx.hooks['post_auxiliary_call'][0]
        hook(event);hook(event)
        self.assertEqual(self.scout.profile()['usage'],dict(input_tokens=12,output_tokens=3,quality='measured'))
    def test_artifact_reload_and_malicious_metadata_rejected(self):
        self.configure()
        result=self.call('scout_train',candidate_id='triage',recipe_id='logreg',dataset_id='a')
        self.assertTrue(result['ok']);aid=result['data']['artifact_id']
        query='a-triage-081'
        first=self.call('scout_predict',artifact_id=aid,query_id=query)
        other=self.scout.__class__(self.ctx)
        self.assertEqual(first,other.call('scout_predict',dict(artifact_id=aid,query_id=query)))
        path=self.scout.root()/'artifacts'/(aid+'.json')
        wrapper=storage.read_json(path);wrapper['metadata']['classes'][0]='unapproved'
        wrapper['sha256']=storage.digest(json.dumps(wrapper['metadata'],sort_keys=True,allow_nan=False).encode())
        storage.write_json(path,wrapper)
        self.assertEqual(self.call('scout_predict',artifact_id=aid,query_id=query)['error']['code'],'ARTIFACT_INVALID')
    def test_revocation_prevents_predictions(self):
        self.configure();trained=self.call('scout_train',candidate_id='triage',recipe_id='knn',dataset_id='a')['data']
        self.scout.configure_local('on_demand',grants={'train':['a']})
        result=self.call('scout_predict',artifact_id=trained['artifact_id'],query_id='a-triage-101')
        self.assertFalse(result['ok'])
    def test_plan_never_creates_artifact(self):
        self.configure()
        result=self.call('scout_train',candidate_id='triage',recipe_id='agent_mlp',dataset_id='a')
        self.assertEqual(result['error']['code'],'PLAN_ONLY')
        self.assertFalse((self.scout.root()/'artifacts').exists())
    def test_operator_changes_reload_in_active_instance_and_delete_does_not_resurrect(self):
        self.configure()
        other=self.scout.__class__(self.ctx)
        other.configure_local('off')
        self.assertEqual(self.call('scout_inspect',dataset_id='a')['error']['code'],'OFF')
        other.configure_local('report')
        self.assertTrue(self.call('scout_inspect',dataset_id='a')['ok'])
        other.delete_local()
        self.scout.observe(text='synthetic@example.invalid')
        self.assertFalse(self.scout.root().exists())
        self.assertEqual(self.call('scout_inspect',dataset_id='a')['error']['code'],'OFF')

    def test_invalid_and_nonfinite_calculator_inputs(self):
        self.configure()
        base=dict(n=2,cloud=dict(amount=.1,currency='PLN'),local=.01,p_abstain=.2,fallback=.1,setup=2,error_rate=.1,error_cost=.05)
        for change in ({'n':True},{'local':float('nan')},{'p_abstain':1.1},{'setup':-1}):
            self.assertEqual(self.call('scout_calculate',**dict(base,**change))['error']['code'],'INVALID_INPUT')
    def test_atomic_failure_leaves_no_pending_or_artifact(self):
        self.configure()
        with patch.object(storage.os,'replace',side_effect=OSError('hidden')):
            result=self.call('scout_choose',candidate_id='triage',recipe_id='knn',reason='Local labeled corpus supports nearest neighbors.')
        self.assertEqual(result['error']['code'],'IO_ERROR')
        self.assertFalse(list(self.scout.root().glob('*.pending')))


if __name__=='__main__': unittest.main()
