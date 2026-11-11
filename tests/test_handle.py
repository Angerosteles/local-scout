import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a
from plugin.scout.core import Scout
from plugin.scout import storage, learning


class Handle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.source = self.home/'data.jsonl'
        self.source.write_text(''.join(json.dumps(r)+'\n' for r in demo_a(seed=17)))
        self.s = Scout(LocalContext(self.home))
        self.s.configure_local('on_demand', grants={k:['a'] for k in ('observe','report','train')}, sources={'a':str(self.source)})
        self.call('scout_inspect', dataset_id='a')
        storage.write_json(self.s.root()/'calibration.json', {'dataset_id':'a','input_digest':'digest','parameters':{'tokens_per_task':1000},'events':[]})
        self.digest = patch('plugin.scout.operator.digest', return_value='digest'); self.digest.start()
    def tearDown(self):
        self.digest.stop(); self.tmp.cleanup()
    def call(self, name, **kwargs):
        return self.s.call(name, kwargs)
    def test_consent_and_artifact_required(self):
        self.assertEqual(self.call('scout_handle',task='triage',text='hello')['error']['code'], 'NOT_FOUND')
        self.s.configure_local('on_demand', grants={'observe':['a'],'report':['a']})
        self.assertEqual(self.call('scout_handle',task='triage',text='hello')['error']['code'], 'CONSENT_REQUIRED')
    def test_live_mask_and_telemetry(self):
        self.call('scout_choose',candidate_id='fields',recipe_id='regex',reason='text')
        secret='person@example.invalid'
        accepted=self.call('scout_handle',task='fields',text=secret)
        self.assertTrue(accepted['ok'],accepted)
        self.assertEqual(accepted['data']['status'],'handled_locally')
        self.assertNotIn(secret,json.dumps(accepted))
        refused=self.call('scout_handle',task='fields',text='plain ordinary words')
        self.assertEqual(refused['data']['status'],'abstained')
        telemetry=self.call('scout_report',dataset_id='a')['data']['handle_telemetry']
        self.assertEqual((telemetry['handled_locally'],telemetry['abstained'],telemetry['estimated_tokens_avoided']),(1,1,1000))
        self.assertIsNone(telemetry['net_tokens'])
        for path in self.s.root().rglob('*.json'):
            self.assertNotIn(secret,path.read_text())
        self.assertLess(len(json.dumps(accepted).encode()),2048)
    def test_model_mask_before_prediction(self):
        self.call('scout_choose',candidate_id='triage',recipe_id='logreg',reason='label_count>=2')
        trained=self.call('scout_train',candidate_id='triage',recipe_id='logreg',dataset_id='a')
        self.assertTrue(trained['ok'],trained)
        secret='person@example.invalid'
        with patch('plugin.scout.learning.predict', return_value={'decision':'unknown','score':0.1}) as predict:
            result=self.call('scout_handle',task='triage',text='hello '+secret)
        self.assertTrue(result['ok'], result)
        self.assertNotIn(secret,predict.call_args.args[2]['text'])
        self.assertEqual(result['data']['status'],'abstained')
    def test_quality_gate_message(self):
        with patch('plugin.scout.learning.train', side_effect=learning.QualityGateNotMet((.2,5,.9),20,5)):
            result=self.call('scout_train',candidate_id='triage',recipe_id='logreg',dataset_id='a')
        self.assertEqual(result['error']['code'],'QUALITY_GATE_NOT_MET')
        self.assertIn('20.0%',result['error']['message']); self.assertIn('accepted n=5',result['error']['message'])
    def test_upgrade_cards_bounded(self):
        (self.s.root()/'calibration.json').unlink()
        result=self.call('scout_inspect',dataset_id='a')
        self.assertEqual(len(result['data']['upgrade_cards']),5)
        self.assertTrue(all('unknown until measured' in c['expected_gain'] for c in result['data']['upgrade_cards']))
        self.assertLess(len(json.dumps(result)),25000)
