import copy
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import analysis
import fetch
import validate
from test_fetch import match, detail


def full(mid=1, won=True, hero=48):
    d = detail(mid)
    d.update(radiant_win=won, lobby_type=7)
    p = d['players'][0]
    p.update(hero_id=hero, player_slot=0, kills=3, deaths=2, assists=4,
             gold_per_min=600, xp_per_min=700, last_hits=200,
             times=[0, 600, 1200, 1800], net_worth=15000,
             gold_t=[0, 3000, 7000, 12000], networth_t=[600, 2800, 6500, 11000],
             lh_t=[0, 50, 120, 180], xp_t=[0, 4000, 9000, 16000], dn_t=[0, 1, 2, 3],
             purchase_log=[{'time': 500, 'key': 'blink'}],
             kills_log=[{'time': 500}], deaths_log=[{'time': 700}], buyback_log=[],
             lane_pos={'1': {'2': 3}}, lane_efficiency_pct=70, teamfight_participation=0.5)
    d.update(objectives=[], teamfights=[])
    return d


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        fetch.API_BLOCKED = False
        fetch.LAST_ERROR = None

    def test_full_coverage_needs_actual_fields_not_just_version(self):
        self.assertTrue(analysis.completeness(full())['full_replay_data'])
        d=full();d['players'][0].pop('networth_t')
        self.assertEqual(analysis.completeness(d)['category'], 'partial_replay')
        self.assertIn('networth_t', analysis.completeness(d)['missing_core_fields'])
        self.assertEqual(analysis.completeness(detail(1, None))['category'], 'basic_only')
        self.assertEqual(analysis.completeness(None)['category'], 'history_only')

    def test_no_networth_estimation_exact_snapshots_short_matches(self):
        d=full();d['duration']=1000
        row=analysis.extract(match(1, 7), d)
        self.assertEqual(row['snapshots_minutes']['10']['net_worth']['value'], 2800)
        self.assertIsNone(row['snapshots_minutes']['20']['net_worth']['value'])
        d['players'][0].pop('networth_t')
        row=analysis.extract(match(1, 7), d)
        self.assertIsNone(row['snapshots_minutes']['10']['net_worth']['value'])
        self.assertEqual(row['snapshots_minutes']['10']['total_gold']['value'], 3000)
        d['players'][0]['times']=[0, 601, 1200, 1800]
        self.assertIsNone(analysis.extract(match(1, 7), d)['snapshots_minutes']['10']['last_hits']['value'])

    def test_missing_vs_zero_and_sample_counts(self):
        rows=analysis.build([match(1, 7), match(2, 7), match(3, 7)],
                            {1:full(1), 2:full(2, False), 3:detail(3, None)})
        self.assertEqual(rows['aggregate']['metrics']['gpm']['n'], 2)
        self.assertEqual(rows['matches'][0]['metrics']['buyback_events'], {'value': 0, 'n': 1, 'unit': 'match'})
        self.assertEqual(rows['matches'][2]['metrics']['buyback_events']['n'], 0)
        self.assertEqual(rows['win_loss']['differences']['gpm']['wins_n'], 1)
        self.assertEqual(rows['win_loss']['differences']['gpm']['losses_n'], 1)
        self.assertEqual(rows['field_sample_counts']['events.buyback_log'], 2)
        self.assertEqual(rows['by_hero']['48']['aggregate']['matches'], 3)
        self.assertEqual(rows['aggregate']['metrics']['won']['n'], 3)

    def test_deathless_kda_and_unknown_outcome(self):
        d=full();d['players'][0]['deaths']=0;d.pop('radiant_win')
        h=match(1);h.pop('radiant_win')
        row=analysis.extract(h,d)
        self.assertIsNone(row['metrics']['kda_ratio']['value'])
        self.assertIsNone(row['metrics']['won']['value'])

    def test_teamfight_index_and_matchwide_objectives(self):
        d=full();d['players'].insert(0, {'account_id': 123})
        d['teamfights']=[{'start': 5,'end': 20,'players':[{'damage': 1},{'damage': 99}]}]
        d['objectives']=[{'time':50,'type':'CHAT_MESSAGE_ROSHAN_KILL','team':3}]
        row=analysis.extract(match(1),d)
        self.assertEqual(row['events']['teamfights']['value'][0]['player']['damage'],99)
        self.assertEqual(row['events']['roshan_events']['value'][0]['team'],3)

    def test_complete_cache_never_redownloaded_and_no_files_deleted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fetch,'OUT',pathlib.Path(tmp)), \
                patch.object(fetch,'DETAIL_COUNT',1), patch.dict('os.environ',{'REQUEST_PARSING':'false'}):
            fetch.save(fetch.OUT/'matches'/'1.json',full())
            fetch.save(fetch.OUT/'matches'/'99.json',detail(99))
            with patch.object(fetch,'fetch',side_effect=[[match(1,7)],[match(1,7)]]) as request:
                fetch.main()
                self.assertEqual(request.call_count,2)
            validate.validate()
            self.assertTrue((fetch.OUT/'matches'/'99.json').exists())

    def test_parse_eligibility_age_parsed_url_cooldown_and_attempt_limit(self):
        now=time.time();h=match(1,7);h['start_time']=int(now)-3600
        d=detail(1,None);d['replay_url']='https://example.com/replay'
        self.assertEqual(fetch.parse_eligibility(h,d,{},now),'eligible')
        self.assertEqual(fetch.parse_eligibility(h,full(),{},now),'already_parsed')
        self.assertEqual(fetch.parse_eligibility(h,detail(1,None),{},now),'replay_url_unavailable')
        self.assertEqual(fetch.parse_eligibility(h,d,{'parse_request':{'next_request_at':now+1}},now),'request_cooldown')
        self.assertEqual(fetch.parse_eligibility(h,d,{'parse_request':{'attempts':3}},now),'attempt_limit')
        h['start_time']=int(now)-10*86400
        self.assertEqual(fetch.parse_eligibility(h,d,{},now),'outside_conservative_7_day_window')

    def test_parse_intent_persisted_before_post_no_duplicate_submission(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fetch,'OUT',pathlib.Path(tmp)):
            meta={};fetch.STATE={'matches':{'1':meta}}
            def post(path,method):
                saved=json.loads((fetch.OUT/'sync_state.json').read_text())
                self.assertEqual(saved['matches']['1']['parse_request']['status'],'submission_unknown')
                self.assertEqual(method,'POST')
                return {'job':{'jobId':42}}
            with patch.object(fetch,'fetch',side_effect=post):
                status=fetch.request_parse(1,meta,time.time())
            self.assertEqual(status['job_id'],42)
            self.assertEqual(status['attempts'],1)

    def test_parse_budget_and_queue_absence_refresh(self):
        now=time.time()
        with tempfile.TemporaryDirectory() as tmp, patch.object(fetch,'OUT',pathlib.Path(tmp)), \
                patch.object(fetch,'DETAIL_COUNT',4), \
                patch.dict('os.environ',{'REQUEST_PARSING':'true','MAX_PARSE_REQUESTS':'99'}):
            histories=[dict(match(mid,7),start_time=int(now)-5000-mid) for mid in (1,2,3,4)]
            def respond(path,method='GET'):
                if path.startswith('/players/'):
                    return histories
                if method=='POST':
                    return {'job':{'jobId':int(path.split('/')[-1])+100}}
                d=detail(int(path.split('/')[-1]),None)
                d['replay_url']='https://example.com/replay'
                return d
            with patch.object(fetch,'fetch',side_effect=respond) as request:
                fetch.main()
                self.assertEqual(sum(c.kwargs.get('method')=='POST' for c in request.call_args_list),3)
            meta=fetch.STATE['matches']['1']
            fetch.LAST_ERROR=None
            with patch.object(fetch,'fetch',return_value=None):
                fetch.check_parse_job(1,meta,now+1)
            self.assertEqual(meta['parse_request']['status'],'finished_or_expired')
            self.assertEqual(meta['next_detail_check_at'],now+1)
            self.assertEqual(fetch.parse_eligibility(histories[0],detail(1,None),meta,now+1),'replay_url_unavailable')

    def test_restricted_api_stops_further_calls(self):
        import urllib.error
        error=urllib.error.HTTPError('url',403,'forbidden',{},None)
        with patch.object(fetch.urllib.request,'urlopen',side_effect=error) as request, \
                patch.object(fetch.time,'sleep'):
            self.assertIsNone(fetch.fetch('/test'))
            self.assertIsNone(fetch.fetch('/other'))
            self.assertEqual(request.call_count,1)
            self.assertTrue(fetch.API_BLOCKED)

    def test_parse_post_is_not_retried(self):
        import urllib.error
        with patch.object(fetch.urllib.request,'urlopen',side_effect=urllib.error.URLError('timeout')) as request, \
                patch.object(fetch.time,'sleep') as sleep:
            self.assertIsNone(fetch.fetch('/request/1',method='POST'))
            self.assertEqual(request.call_count,1)
            sleep.assert_called_once_with(12)


if __name__=='__main__':
    unittest.main()
