"""Regression coverage for large replay, restart, operator and metadata integration."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from plugin import register
from plugin.cli import main
from plugin.demo.generate import demo_a
from plugin.scout.context import LocalContext
from plugin.scout.core import Scout
from plugin.scout import calibration, insights, learning, operator, storage
from plugin.tools import handler_for
from plugin.schemas import SCHEMAS


class Reliability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.rows = [r for r in demo_a(seed=17) if r['kind'] == 'fields']
        self.source = self.home/'fields.jsonl'
        self.source.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        self.s = Scout(LocalContext(self.home))
        self.s.configure_local('report', grants={k:['a', 'profile'] for k in ('observe','report','train','insights')}, sources={'a':self.source})

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self):
        self.s.scout_inspect('a')
        self.s.scout_choose('fields','regex','modality=text')
        storage.write_json(self.s.root()/'calibration.json', dict(dataset_id='a',input_digest=operator.digest(self.s.decision_rows('a')),parameters={'tokens_per_task':123},events=[]))

    def test_stratified_sample_and_full_digest(self):
        rows = [dict(text='sample alpha',kind='triage',label=f'label-{i%17}',timestamp=i) for i in range(20000)]
        a, _ = calibration.replay_rows(rows, 42)
        b, _ = calibration.replay_rows(rows, 42)
        self.assertEqual(a,b)
        self.assertEqual(len(a),500)
        self.assertEqual(len({r['label'] for r in a}),17)
        self.assertEqual(a,sorted(a,key=lambda r:r['timestamp']))
        c, _ = calibration.replay_rows(rows, 43)
        self.assertNotEqual(a,c)
        changed = [dict(r) for r in rows]
        not_selected = next(i for i,r in enumerate(rows) if r not in a)
        changed[not_selected]['text']='changed full data'
        self.assertNotEqual(operator.digest(rows),operator.digest(changed))

    def test_single_family_calibration_facts(self):
        with patch.object(self.s,'rows',wraps=self.s.rows) as read_rows:
            result = self.s.call('scout_calibrate',dict(dataset_id='a',threshold=.8,min_cases=3,seed=42))
        self.assertEqual(read_rows.call_count,1)
        self.assertTrue(result['ok'],result)
        self.assertFalse(result['data']['sampled'])
        self.assertEqual(result['data']['n_total'],100)
        from plugin.scout.calibration_output import read_details
        detail=read_details(storage.read_json(self.s.root()/'calibration.json'),self.s.root())
        self.assertEqual(detail['input_digest'],operator.digest(self.s.decision_rows('a')))
        self.assertEqual(detail['card_facts']['triage']['families'],[])
        original = Scout.rows
        with patch.object(Scout,'rows',autospec=True,side_effect=original) as read_rows, contextlib.redirect_stdout(io.StringIO()):
            main(['--home',str(self.home),'calibrate','--dataset','a','--seed','42','--json',str(self.home/'cli-report.json')])
        self.assertEqual(read_rows.call_count,1)
        self.assertEqual(json.loads((self.home/'cli-report.json').read_text())['n_total'],100)

    def test_handle_restart_and_cli_without_inspect(self):
        self.prepare()
        self.s = Scout(LocalContext(self.home))
        result = self.s.call('scout_handle',dict(task='fields',text='PROJ-123'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['data']['status'],'handled_locally')
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            main(['--home',str(self.home),'handle','--task','fields','--text','PROJ-123'])
        self.assertEqual(json.loads(out.getvalue())['data']['status'],'handled_locally')
        summary=insights.summarize(self.s.root())
        self.assertEqual(summary['handled_locally'],2)
        self.assertEqual(summary['estimated_tokens_avoided'],246)
        self.assertIsNone(summary['savings']['net_tokens'])
        self.assertIn('usage not observed',summary['savings']['net_note'])

    def test_trained_handle_restarts_with_decision_and_artifact(self):
        rows = [r for r in demo_a(seed=17) if r['kind'] == 'triage']
        self.source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.s.scout_inspect('a')
        self.s.scout_choose('triage','logreg','label_count>=2')
        trained = self.s.call('scout_train',dict(candidate_id='triage',recipe_id='logreg',dataset_id='a'))
        self.assertTrue(trained['ok'], trained)
        storage.write_json(self.s.root()/'calibration.json', dict(dataset_id='a',input_digest=operator.digest(self.s.decision_rows('a')),parameters={'tokens_per_task':123},events=[]))
        text = next(r['text'] for r in rows)
        before = self.s.call('scout_handle',dict(task='triage',text=text))
        after = Scout(LocalContext(self.home)).call('scout_handle',dict(task='triage',text=text))
        self.assertEqual(before, after)
        self.assertTrue(after['ok'],after)
        self.assertEqual(storage.read_json(self.s.root()/'active.json'),{'dataset_id':'a'})

    def test_stale_calibration_and_revoked_consent_still_block_after_restart(self):
        self.prepare()
        self.source.write_text(self.source.read_text().replace('issue','ticket'))
        s=Scout(LocalContext(self.home))
        self.assertEqual(s.call('scout_handle',dict(task='fields',text='PROJ-123'))['error']['code'],'NOT_FOUND')
        s.configure_local('report',grants={'observe':['a']})
        self.assertEqual(s.call('scout_handle',dict(task='fields',text='PROJ-123'))['error']['code'],'CONSENT_REQUIRED')

    def test_one_family_freeze_and_fields_entity_types(self):
        rows=[]
        for part in ('train','validation','test'):
            for i in range(8):
                rows.append(dict(id=f'{part}-{i}',group=f'{part}-{i}',kind='fields',text='[EMAIL] [PHONE]',label='email',entity_types=['phone','email'],split=part,destination='local'))
        self.source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.s.scout_inspect('a');self.s.scout_choose('fields','regex','modality=text')
        operator.freeze(self.s,'a')
        result=operator.evaluate(self.s,'a')
        self.assertEqual(result['results'],{'fields':{'accuracy':1.0,'n_test':8}})
        with self.assertRaises(ValueError):operator.evaluate(self.s,'a')

    def test_observe_no_write_but_pruning_is_persisted(self):
        self.s.observe(text='ordinary safe text')
        self.assertFalse((self.s.root()/'aggregates.json').exists())
        with patch('plugin.scout.storage.write_json',wraps=storage.write_json) as write:
            self.s.observe(text='ordinary safe text')
            self.assertEqual(write.call_count,0)
        self.s.profile()['events']=[dict(category='email',timestamp=0,scope='unknown')]
        self.s.observe(text='ordinary safe text')
        self.assertEqual(storage.read_json(self.s.root()/'aggregates.json')['events'],[])

    def test_prompt_notice_last_user_only_no_raw_text_and_no_report_grant_needed(self):
        self.s.configure_local('report',grants={'observe':['profile']})
        event=dict(api_request_id='one',request_messages=[{'role':'user','content':'old private@example.invalid'},{'role':'assistant','content':'other@example.invalid'},{'role':'user','content':[{'type':'text','text':'new@example.invalid'}]}])
        self.s.observe(**event);self.s.observe(**event)
        output=self.s.notify_output(response_text='reply')
        self.assertIn('your prompt contained: email; detected in a request; protection and delivery are not confirmed',output)
        self.assertNotIn('@',output)
        self.assertIsNone(self.s.notify_output(response_text='reply'))
        summary=insights.summarize(self.s.root())
        self.assertEqual(summary['prompt_warnings'],1)
        self.assertNotIn('email',summary['pii_masks'])
        self.assertEqual(summary['observed_categories']['email'],1)
        self.assertEqual(len(self.s.profile()['events']),1)
        self.assertNotIn('@', (self.s.root()/'aggregates.json').read_text())
        self.s.observe(request_messages=[{'role':'user','content':'old@example.invalid'},{'role':'user','content':'safe'}])
        self.assertIsNone(self.s.notify_output(response_text='reply'))

    def test_prompt_warning_once_per_prompt_across_api_requests(self):
        # Live Hermes behaviour: one turn = several API requests with the same prompt -> one warning.
        self.s.configure_local('report',grants={'observe':['profile']})
        msgs=[{'role':'user','content':'contact me at live@example.invalid'}]
        for rid in ('r1','r2','r3'):
            self.s.observe(api_request_id=rid,request_messages=msgs+[{'role':'assistant','content':'step '+rid}])
        output=self.s.notify_output(response_text='reply')
        self.assertEqual(output.count('your prompt contained: email'),1)
        self.assertEqual(insights.summarize(self.s.root())['prompt_warnings'],1)

    def test_masked_prompt_single_notice_no_contradiction(self):
        # With masking active: only 'masked in the main request', never 'sent unmasked', no double counting.
        from plugin.scout.prompt_guard import PromptGuard
        self.s.configure_local('report',grants={'observe':['profile'],'mask':['profile']})
        msgs=[{'role':'user','content':'contact me at live@example.invalid'}]
        self.s.observe(api_request_id='m1',request_messages=msgs)
        result=PromptGuard(self.s)(request={'messages':msgs},api_mode='chat_completions')
        self.assertNotIn('live@example.invalid',str(result['request']))
        output=self.s.notify_output(response_text='reply')
        self.assertIn('masked in the main request',output)
        self.assertNotIn('sent unmasked',output)
        self.assertEqual(output.count('your prompt contained: email'),1)

    def test_prompt_optional_api_and_consent(self):
        with patch('plugin.scout.privacy.prompt_warning', create=True, return_value={'categories':['email','arbitrary-secret']}):
            self.s.observe(request_messages=[{'role':'user','content':'safe'}])
        self.assertIn('email',self.s.notify_output(response_text='reply'))
        self.s.configure_local('report',grants={})
        self.s.observe(request_messages=[{'role':'user','content':'private@example.invalid'}])
        self.assertIsNone(self.s.notify_output(response_text='reply'))

    def test_registration_manifest_and_tool_hook_no_duplicate_or_content(self):
        import yaml
        ctx=LocalContext(self.home);register(ctx)
        manifest=yaml.safe_load((Path(__file__).parents[1]/'plugin.yaml').read_text())
        self.assertEqual(set(manifest['provides_tools']),set(ctx.tools))
        self.assertEqual(set(manifest['provides_hooks']),set(ctx.hooks))
        hook=ctx.hooks['post_tool_call'][0]
        hook(tool_name='read_file',status='ok',duration_ms=7,args={'private':'SENTINEL'},result='SENTINEL',error_message='SENTINEL')
        hook(tool_name='terminal',status='error',duration_ms=11)
        hook(tool_name='scout_handle',status='ok',duration_ms=5)
        hook(tool_name='invalid@example.invalid',status='ok')
        summary=insights.summarize(self.s.root())
        self.assertEqual(summary['hermes_operations'],{'read_file':{'ok':1,'errors':0,'duration_ms':7},'terminal':{'ok':0,'errors':1,'duration_ms':11}})
        self.assertNotIn('SENTINEL',json.dumps(summary))
        self.assertEqual(insights.TOOLS,set(SCHEMAS))
        self.assertIn('QUALITY_GATE_NOT_MET',insights.ERROR_CODES)
        self.assertIn('prompt_injection',insights.PII_CATEGORIES)
        storage.write_json(self.s.root()/'aggregates.json',{'events':[dict(category='prompt_injection',source='tool_result',timestamp=__import__('time').time())]})
        self.assertEqual(insights.summarize(self.s.root())['pii_by_source']['tool_result']['prompt_injection'],1)
        exported=insights.community_payload(summary)
        self.assertNotIn('read_file',json.dumps(exported))

    def test_net_uses_real_handles_and_observed_session_usage_once(self):
        self.prepare()
        handler=handler_for(self.s,'scout_handle')
        # Usage seen before first Scout tool is included once as conservative session cost.
        self.s.usage_hook(api_request_id='before',usage={'prompt_tokens':10,'completion_tokens':2})
        handler(dict(task='fields',text='PROJ-123'))
        self.s.usage_hook(api_request_id='after',usage={'prompt_tokens':20,'completion_tokens':3})
        self.s.usage_hook(api_request_id='after',usage={'prompt_tokens':20,'completion_tokens':3})
        handler(dict(task='fields',text='plain words'))
        summary=insights.summarize(self.s.root())
        self.assertEqual(summary['estimated_tokens_avoided'],123)
        self.assertEqual(summary['handled_locally'],1)
        self.assertEqual(summary['abstained'],1)
        self.assertEqual(summary['savings']['setup_tokens'],35)
        self.assertEqual(summary['savings']['net_tokens'],88)
        restarted=insights.summarize(Scout(LocalContext(self.home)).root())
        self.assertEqual(summary,restarted)

    def test_session_cost_excludes_sessions_without_scout(self):
        self.prepare()
        handler=handler_for(self.s,'scout_handle')
        self.s.usage_hook(session_id='unused-session',api_request_id='u',usage={'input_tokens':900,'output_tokens':100})
        self.s.usage_hook(session_id='used-session',api_request_id='a',usage={'input_tokens':20,'output_tokens':3})
        handler(dict(task='fields',text='PROJ-123'), session_id='used-session')
        self.s.usage_hook(session_id='unused-session',api_request_id='u2',usage={'input_tokens':900,'output_tokens':100})
        summary=insights.summarize(self.s.root())
        self.assertEqual(summary['savings']['setup_tokens'],23)
        self.assertEqual(summary['savings']['net_tokens'],100)
        self.assertNotIn('used-session',(self.s.root()/'agent-cost.json').read_text())
        self.assertNotIn('unused-session',(self.s.root()/'insights.jsonl').read_text())

    def test_hermes_hook_off_or_no_consent_writes_nothing(self):
        self.s.configure_local('background',grants={})
        self.s.post_tool_call(tool_name='terminal',status='ok',duration_ms=1)
        self.assertFalse((self.s.root()/'insights.jsonl').exists())
        self.s.configure_local('off',grants={'insights':['profile']})
        self.s.post_tool_call(tool_name='terminal',status='ok',duration_ms=1)
        self.assertFalse((self.s.root()/'insights.jsonl').exists())

    def test_sublinear_training_inference_parity_and_local_vocab_disclosure(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        texts=['sentinelfeature alpha alpha beta','beta gamma gamma gamma']
        vec=TfidfVectorizer(token_pattern=learning.TOKEN,sublinear_tf=True)
        expected=vec.fit_transform(texts).toarray()
        meta=dict(engine='tfidf_search',vocabulary=vec.get_feature_names_out().tolist(),sublinear_tf=True)
        np.testing.assert_allclose(expected,learning.transform(meta,{'idf':vec.idf_},texts))
        rows=[r for r in demo_a(seed=17) if r['kind']=='triage']
        meta, arrays=learning.train(rows,'logreg')
        self.assertIn('local vocabulary contains training terms',meta['artifact_disclosure'])
        self.assertTrue(meta['vocabulary'])
        meta.update(dataset_id='a',candidate_id='triage')
        aid=storage.save_artifact(self.s.root()/'artifacts',meta,arrays)
        self.assertIn('vocabulary',storage.load_artifact(self.s.root()/'artifacts',aid)[0])
        self.assertNotIn('vocabulary',json.dumps(insights.community_payload(insights.summarize(self.s.root()))))


class SplitWithoutTest(unittest.TestCase):
    def test_explicit_train_validation_without_test_is_usable(self):
        from plugin.scout.learning import split
        rows=[dict(id=f'r{i}',group=f'g{i}',kind='triage',text='t',label='a',split='train' if i<8 else 'validation') for i in range(10)]
        tr,va,te=split(rows)
        self.assertEqual((len(tr),len(va),len(te)),(8,2,0))

