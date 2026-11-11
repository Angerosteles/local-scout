"""Regressions for concise evidence and file-backed calibration presentation."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from plugin.demo.__main__ import LocalContext
from plugin.demo.generate import demo_a
from plugin.scout.core import Scout
from plugin.scout import storage
from plugin.scout.calibration_output import read_details, save_report
from plugin.scout.presentation import terminal, write_html



class ResponseSize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.scout = Scout(LocalContext(self.home))
        source = self.home/'a.jsonl'
        source.write_text(''.join(json.dumps(r)+'\n' for r in demo_a()))
        self.scout.configure_local('on_demand', grants={s:['a'] for s in ('observe','report')}, sources={'a':source})

    def tearDown(self):
        self.tmp.cleanup()

    def test_summary_matches_details_and_roundtrip_presentations(self):
        response = self.scout.call('scout_calibrate', dict(dataset_id='a', threshold=.8, min_cases=3, seed=42))
        self.assertTrue(response['ok'])
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False).encode()), 6144)
        brief = response['data']
        self.assertTrue({'events','families','curves','summary','parameters','assumptions'} <= brief.keys())
        full = read_details(brief, self.scout.root())
        self.assertEqual(brief['events']['count'],len(full['events']))
        self.assertEqual(brief['summary'],full['summary'])
        self.assertLessEqual(len(brief['curves']['thresholds']),5)
        for f in brief['families']:
            events = [e for e in full['events'] if e['family']==f['id']]
            self.assertEqual(f['count'],len(events))
            self.assertEqual(f['noticed_at'],next((e['t'] for e in events if e['noticed']),None))
            self.assertEqual(f['local_at'],next((e['t'] for e in events if e['route']=='local'),None))
            for kind, cards in f['cards_satisfying_conditions'].items():
                facts = next(x for x in full['card_facts'][kind]['families'] if x['id']==f['id'])
                self.assertEqual(cards,[x['recipe_id'] for x in facts['card_conditions'] if all(c['ok'] is True for c in x['conditions'])])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            terminal(brief, detail_root=self.scout.root())
        self.assertIn('CALIBRATION',out.getvalue())
        self.assertNotRegex(out.getvalue(), r'[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')
        path = self.home/'calibration.html'
        write_html(brief,path,detail_root=self.scout.root())
        page = path.read_text()
        import re
        headings = re.findall(r'<(?:title|h[12])[^>]*>(.*?)</(?:title|h[12])>', page)
        self.assertTrue(headings)
        self.assertNotRegex(' '.join(headings), r'[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')
        self.assertIn('"events": [',page)
        self.assertIn('"map": [',page)
        self.assertNotIn('https://',page)
        inspect = self.scout.call('scout_inspect',dict(dataset_id='a'))
        self.assertLess(len(json.dumps(inspect,ensure_ascii=False).encode()),25000)
        self.assertNotIn('card_conditions',json.dumps(inspect))
        self.assertIn('card_conditions',json.dumps(full))

    def test_detail_reference_is_immutable_and_rejects_traversal_or_tampering(self):
        args = dict(dataset_id='a',threshold=.8,min_cases=3,seed=42)
        first = self.scout.scout_calibrate(**args)
        second = self.scout.scout_calibrate(**dict(args,threshold=.9))
        self.assertNotEqual(first['detail_ref'],second['detail_ref'])
        self.assertEqual(read_details(first,self.scout.root())['parameters']['threshold'],.8)
        with self.assertRaises(ValueError):
            read_details(dict(detail_ref='../settings.json'),self.scout.root())
        storage.write_json(self.scout.root()/first['detail_ref'],dict(events=[]))
        with self.assertRaises(ValueError):
            read_details(first,self.scout.root())

    def test_many_families_are_bounded_with_explicit_omission_count(self):
        from plugin.scout.calibration import replay
        full = replay([dict(text=f'unique{i} word{i}') for i in range(50)])
        brief = save_report(self.scout.root(),full)
        self.assertLessEqual(len(json.dumps(dict(ok=True,data=brief),ensure_ascii=False).encode()),6144)
        self.assertGreater(brief['families_omitted'],0)
        self.assertEqual(brief['families_total'],len(brief['families'])+brief['families_omitted'])
        self.assertEqual(len(read_details(brief,self.scout.root())['families']),50)
