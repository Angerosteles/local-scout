"""Coverage for Local Scout 1.5.0 user datasets and real corpus search."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a
from plugin import register
from plugin.scout.core import Scout, ScoutError
from plugin.scout import learning, storage
from plugin.scout.profile import facts



class RealDatasetSupport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.scout = Scout(LocalContext(self.home))

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, name, rows):
        path=self.home/name
        path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        return path

    def test_dynamic_labels_unknown_profile_and_numeric_vocabulary(self):
        labels=[f'intent.v{i}:9' for i in range(8)]
        rows=[]
        for i,label in enumerate(labels):
            rows.append(dict(id=f'row-{i}',group=f'train-{i}',kind='triage',text=f'route feature{i} value{i}',
                             label=label,destination='local',split='train'))
            rows.append(dict(id=f'validation-{i}',group=f'validation-{i}',kind='triage',text=f'route feature{i} check',
                             label=label,destination='local',split='validation'))
        rows.append(dict(id='unknown-case-9',group='unknown-group',kind='triage',text='unrelated moon rocks',
                         label='unknown',destination='local',split='validation'))
        rows.append(dict(id='test-case-10',group='test-group',kind='triage',text='route feature0 final',
                         label=labels[0],destination='local',split='test'))
        source=self.write_source('dynamic.jsonl',rows)
        self.scout.configure_local('on_demand',grants={'observe':['bench'],'train':['bench']},sources={'bench':source})
        loaded=self.scout.rows('bench')
        self.assertEqual({r['label'] for r in loaded if r['label']!='unknown'},set(labels))
        profile=facts(loaded,'triage')
        self.assertEqual(profile['label_count'],8)
        self.assertEqual(profile['min_examples_per_label_train'],1)
        self.assertEqual(profile['unknown_examples'],1)
        self.assertGreater(profile['unknown_rate'],0)

    def test_label_and_row_limits_are_enforced(self):
        labels=[dict(id=f'label-{i}',group=f'label-group-{i}',kind='triage',text='plain example',
                     label=f'class-{i:03d}',destination='local') for i in range(257)]
        label_source=self.write_source('too-many-labels.jsonl',labels)
        self.scout.configure_local('on_demand',sources={'labels':label_source})
        with self.assertRaises(ScoutError) as raised:
            self.scout.rows('labels')
        self.assertEqual(raised.exception.args[0],'UNSUPPORTED_SOURCE')

        rows=[dict(id=f'row-{i}',group=f'group-{i}',kind='triage',text='plain example',
                   label='class-a',destination='local') for i in range(20_001)]
        row_source=self.write_source('too-many-rows.jsonl',rows)
        self.scout.configure_local('on_demand',sources={'rows':row_source})
        with self.assertRaises(ScoutError) as raised:
            self.scout.rows('rows')
        self.assertEqual(raised.exception.args[0],'UNSUPPORTED_SOURCE')

    def test_unknown_metrics_count_correct_refusals_and_false_acceptances(self):
        meta=dict(engine='tfidf_search',vocabulary=['alpha','beta'],classes=['class:alpha','class.beta'],threshold=.5)
        arrays=dict(idf=np.ones(2),train=np.eye(2),labels=np.array([0,1]))
        rows=[dict(text='alpha',label='class:alpha'),
              dict(text='outside',label='unknown'),
              dict(text='alpha',label='unknown')]
        result=learning.metrics(meta,arrays,rows)
        self.assertEqual(result['unknown_count'],2)
        self.assertEqual(result['correct_unknown_refusals'],1)
        self.assertEqual(result['false_acceptances_unknown'],1)
        self.assertEqual(result['coverage'],2/3)

    def test_document_search_uses_corpus_and_only_returns_id_title(self):
        rows=[]
        docs=[('doc-alpha','Alpha guide','alpha installation steps release2026'),
              ('doc-beta','Beta guide','beta configuration steps'),
              ('doc-gamma','Gamma guide','gamma deployment steps')]
        for did,title,text in docs:
            rows.append(dict(id=did,group='corpus',kind='doc',text=text,label=did,title=title,destination='local'))
        for split,count in (('train',6),('validation',6),('test',3)):
            for i in range(count):
                did=docs[i%len(docs)][0]
                topic=did.split('-')[1]
                rows.append(dict(id=f'{split}-query-{i}',group=f'{split}-group-{i}',kind='search',
                    text=f'{topic} installation guide',label=did,destination='local',split=split))
        source=self.write_source('search.jsonl',rows)
        self.scout.configure_local('on_demand',grants={'observe':['search'],'train':['search']},sources={'search':source})
        loaded=self.scout.rows('search')
        meta,arrays=learning.train(loaded,'tfidf_search')
        meta.update(dataset_id='search',candidate_id='search')
        storage.validate_model(meta,arrays)
        self.assertIn('release2026',meta['vocabulary'])
        query=next(r for r in loaded if r['kind']=='search' and r['split']=='test')
        result=learning.predict(meta,arrays,query)
        self.assertEqual(result['document_id'],query['label'])
        self.assertIn(result['title'],{d[1] for d in docs})
        self.assertNotIn('text',result)
        self.assertTrue(meta['corpus_mode'])
        self.assertGreaterEqual(meta['metrics']['n_test'],6)

    def test_synthetic_pii_is_masked_before_rows_reach_models(self):
        secret='person@example.invalid'
        row=dict(id='field-row-2026',group='field-group',kind='fields',
                 text=f'Contact {secret} for help.',label='email',entity_types=['email'],destination='local')
        source=self.write_source('pii.jsonl',[row])
        self.scout.configure_local('on_demand',grants={'observe':['fields']},sources={'fields':source},synthetic_pii=True)
        loaded=self.scout.rows('fields')[0]
        self.assertIn('[EMAIL]',loaded['text'])
        self.assertIn('email',loaded['_detected_types'])
        self.assertNotIn(secret,json.dumps(loaded))
        prediction=self.scout._predict_approved('regex','fields',loaded)
        self.assertIn('email',prediction['matched_fields'])
        self.assertNotIn(secret,json.dumps(prediction))

    def test_pii_without_explicit_synthetic_flag_is_rejected(self):
        row=dict(id='field-row',group='field-group',kind='fields',text='Contact person@example.invalid',
                 label='email',destination='local')
        source=self.write_source('unapproved-pii.jsonl',[row])
        self.scout.configure_local('on_demand',sources={'fields':source})
        with self.assertRaises(ScoutError) as raised:
            self.scout.rows('fields')
        self.assertEqual(raised.exception.args[0],'UNSUPPORTED_SOURCE')

    def test_all_tool_responses_stay_below_25k(self):
        context=LocalContext(self.home)
        register(context)
        source=self.write_source('demo.jsonl',demo_a())
        scout=context.tools['scout_inspect']['handler'].scout
        scout.configure_local('on_demand',grants={scope:['demo_a'] for scope in ('observe','report','train')},
                              sources={'demo_a':source})
        calls=[
            ('scout_inspect',dict(dataset_id='demo_a')),
            ('scout_recipes',dict(candidate_id='triage')),
            ('scout_recipes',dict(candidate_id='search')),
            ('scout_recipes',dict(candidate_id='fields')),
            ('scout_calibrate',dict(dataset_id='demo_a',threshold=.8,min_cases=3,seed=42)),
            ('scout_choose',dict(candidate_id='triage',recipe_id='logreg',reason='label_count>=2; stable local test.')),
            ('scout_train',dict(candidate_id='triage',recipe_id='logreg',dataset_id='demo_a')),
            ('scout_predict',dict(artifact_id='model-placeholder',query_id='a-triage-081')),
            ('scout_report',dict(dataset_id='demo_a')),
            ('scout_calculate',dict(n=100,cloud=dict(amount=.10,currency='PLN'),local=.01,
                p_abstain=.2,fallback=.10,setup=2,error_rate=.1,error_cost=.05)),
        ]
        sizes=[]
        for name,params in calls:
            raw=context.tools[name]['handler'](params)
            sizes.append((name,len(raw.encode('utf-8'))))
            self.assertLess(len(raw.encode('utf-8')),25_000,name)
        trained=json.loads(context.tools['scout_train']['handler'](
            dict(candidate_id='triage',recipe_id='logreg',dataset_id='demo_a')))
        self.assertTrue(trained['ok'])
        prediction=context.tools['scout_predict']['handler'](
            dict(artifact_id=trained['data']['artifact_id'],query_id='a-triage-081'))
        self.assertLess(len(prediction.encode('utf-8')),25_000)


if __name__=='__main__':
    unittest.main()
