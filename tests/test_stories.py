import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import analysis
import fetch
import report
import stories
from test_analysis import full
from test_fetch import match

NAMES = {'48': {'name': 'npc_dota_hero_luna', 'localized_name': 'Luna'},
         '1': {'name': 'npc_dota_hero_antimage', 'localized_name': 'Anti-Mage'}}


def fixture():
    d = full()
    p = d['players'][0]
    p.update(times=list(range(0, 601, 60)), player_slot=129,
             kills_log=[{'time': 200, 'key': 'npc_dota_hero_antimage'}],
             deaths_log=[{'time': 400, 'key': 'npc_dota_hero_antimage'}],
             purchase_log=[{'time': 125, 'key': 'black_king_bar'}, {'time': 160, 'key': 'tango'}],
             buyback_log=[{'time': 410}])
    enemy={'account_id': 123, 'hero_id': 1, 'player_slot':0,
           'deaths_log':[{'time': 200,'key':'npc_dota_hero_luna'}],
           'kills_log':[{'time':400,'key':'npc_dota_hero_luna'}],
           'purchase_log':[], 'buyback_log':[]}
    d['players'].insert(0,enemy)
    # Positive Radiant gold = adverse for this Dire player.
    d['radiant_gold_adv']=[0, 0, 100, 300, 500, 3000, 3100, 3000, 3500, 3400, 3300]
    d['radiant_xp_adv']=[0, 0, 0, 0, 100, 1000, 1200, 1500, 1500, 1600, 1700]
    d['objectives']=[{'time':310,'type':'building_kill','key':'npc_dota_badguys_tower1_mid','player_slot':0}]
    d['teamfights']=[{'start':380,'end':420,'deaths':1,
                      'players':[{'damage':100,'gold_delta':50,'xp_delta':80,'deaths':0},
                                 {'damage':25,'gold_delta':-100,'xp_delta':20,'deaths':1}]}]
    return d


def pointer_value(document, pointer):
    current=document
    for token in pointer.split('/')[1:]:
        token=token.replace('~1','/').replace('~0','~')
        current=current[int(token)] if isinstance(current,list) else current[token]
    return current


class StoryTests(unittest.TestCase):
    def test_chronology_kill_death_dedup_and_player_index(self):
        s=stories.reconstruct(match(1,7),fixture(),NAMES)
        self.assertEqual(s['side'],'Dire')
        self.assertEqual([e['time'] for e in s['events']],sorted(e['time'] for e in s['events']))
        death=next(e for e in s['events'] if e['kind']=='death' and e['time']==400)
        self.assertEqual(len(death['evidence']),2)
        self.assertIn('You (Luna) died',death['text'])
        self.assertEqual(sum(e['kind']=='death' for e in s['events']),2)
        fight=next(e for e in s['events'] if e['kind']=='teamfight')
        self.assertIn('your recorded damage: 25',fight['text'])
        self.assertTrue(any(r['pointer']=='/teamfights/0/players/1/damage' for r in fight['evidence']))
        self.assertEqual(sum(e['kind']=='major_item' for e in s['events']),1)
        self.assertEqual(sum(e['kind']=='buyback' for e in s['events']),1)

    def test_dire_sign_and_strict_preobjective_turning_point(self):
        s=stories.reconstruct(match(1,7),fixture(),NAMES)
        self.assertEqual(len(s['turning_points']),1)
        f=s['turning_points'][0]
        self.assertEqual(f['gold_change'],-3000)
        self.assertLess(f['swing_end_time'],f['objective_time'])
        d=fixture();d['objectives'][0]['time']=299
        self.assertEqual(stories.reconstruct(match(1,7),d,NAMES)['turning_points'],[])
        d=fixture();d['objectives'][0]['key']='npc_dota_goodguys_tower1_mid'
        self.assertEqual(stories.reconstruct(match(1,7),d,NAMES)['turning_points'],[])

    def test_radiant_sign_and_missing_unaligned_samples(self):
        d=fixture();d['players'][1]['player_slot']=1
        s=stories.reconstruct(match(1,7),d,NAMES)
        own=next(p for p in s['roster'] if p['you'])
        self.assertEqual(stories.advantage_series(d,own,'radiant_gold_adv')[5]['your_team_advantage'],3000)
        d['radiant_gold_adv']=[100]
        s=stories.reconstruct(match(1,7),d,NAMES)
        self.assertTrue(any('radiant_gold_adv' in v for v in s['missing_information']))
        self.assertEqual(s['turning_points'],[])
        self.assertIsNone(s['events'][0]['advantage']['gold']['change'])
        self.assertIsNone(stories.bracket([],10,10)['before'])

    def test_every_evidence_pointer_matches_original_values(self):
        d=fixture();s=stories.reconstruct(match(1,7),d,NAMES)
        refs=list(s['overview_evidence'])
        for e in s['events']:
            refs.extend(e['evidence'])
            for context in e['advantage'].values():
                for key in ('before','after'):
                    if context[key]:refs.extend(context[key]['evidence'])
        for f in s['turning_points']:refs.extend(f['evidence'])
        for r in refs:self.assertEqual(pointer_value(d,r['pointer']),r['value'])
        self.assertIn('causation and responsibility are undetermined',stories.render(s))

    def test_missing_logs_and_unparsed_index_and_priority(self):
        d=fixture();d['players'][1]['deaths_log']=None;d['players'][1]['buyback_log']=None
        s=stories.reconstruct(match(1,7),d,NAMES)
        self.assertIn('/players/1/deaths_log unavailable',s['missing_information'])
        unparsed=copy.deepcopy(d);unparsed['version']=None
        self.assertIsNone(stories.reconstruct(match(2,7),unparsed,NAMES))
        hs=[match(2,7),match(9057336754,7),match(1,7)]
        docs,index=stories.artifacts(hs,{1:d,2:unparsed,9057336754:d},NAMES,467233306,'test-time')
        self.assertEqual(index['stories'][0]['match_id'],9057336754)
        self.assertEqual(index['story_count'],2)
        self.assertEqual(index['unavailable_matches'][0]['match_id'],2)
        self.assertEqual(stories.artifacts(hs,{1:d,2:unparsed,9057336754:d},NAMES,467233306,'test-time'),(docs,index))

    def test_report_pretty_counts_missing_values_and_size_guard(self):
        d=full();d['players'][0]['networth_t']=None
        base=analysis.build([match(1,7),match(2,7)],{1:d})
        output=report.build(base,NAMES)
        self.assertEqual(output['parsed_match_count'],1)
        self.assertIn('net_worth_at_10m',output['matches'][0]['missing_fields'])
        self.assertEqual(output['aggregate']['metrics']['net_worth_at_10m'],{'mean':None,'n':0,'missing_n':1})
        self.assertEqual(output['by_outcome']['loss']['metrics']['gpm']['n'],0)
        self.assertEqual(json.loads(report.pretty(output)),output)
        self.assertIn('\n',report.pretty(output))
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)
            report.write(base,root,NAMES)
            path=root/'analysis'/'parsed11_report.json'
            before=path.read_bytes()
            with patch.object(report,'MAX_BYTES',10),self.assertRaises(ValueError):
                report.write(base,root,NAMES)
            self.assertEqual(path.read_bytes(),before)

    def test_offline_generation_never_contacts_opendota(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch.urllib.request,'urlopen',side_effect=AssertionError('network used')):
            root=pathlib.Path(temp)
            stories.write([match(1,7)],{1:fixture()},root,467233306,'test-time')
            self.assertTrue((root/'stories'/'1.md').exists())


if __name__=='__main__':
    unittest.main()
