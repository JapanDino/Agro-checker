from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from agro_checker.pipeline import ROOT
from agro_checker.field_store import FieldStore,digest
from agro_checker.product_field_bridge import update_field
from agro_checker.product_server import make_server
from agro_checker.gefs_product import temperature_range
from test_field_pilot import profile,forecast,ISSUE,START


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=FieldStore(Path(self.tmp.name))
        self.p=profile();self.pr=self.store.save_profile(self.p,now=ISSUE);self.f=self.store.issue(self.p['id'],'z1',forecast(self.pr['id']),ISSUE)
        self.now=START+timedelta(hours=2,minutes=15)
        self.csv='sensor_id,observed_at,temperature_c,rh_pct\n'+'\n'.join(f't1,{(START+timedelta(hours=i)).isoformat()},{2.5+i*.25},80' for i in range(3))
        self.store.import_measurements(self.p['id'],self.csv,now=self.now)

    def decision(self,status):
        return self.store.journal(self.p['id'],{'forecast_id':self.f['id'],'actor':'Test','action':'observe','decision_at_utc':ISSUE.isoformat(),'protection_status':status},ISSUE)

    def test_shadow_update_is_separate_append_only_issue_unchanged(self):
        self.decision('not_used');before=digest(self.f)
        r=update_field(self.store,self.p['id'],self.f['id'],self.now)
        self.assertEqual(r['status'],'shadow_only');self.assertEqual(digest(self.store.records('forecast')[0]),before)
        self.assertEqual(len(self.store.records('local_update')),1)
        self.assertTrue(r['update']['payload']['not_used_for_field_metrics'])

    def test_unknown_or_used_protection_blocks_record_creation(self):
        self.assertEqual(update_field(self.store,self.p['id'],self.f['id'],self.now)['status'],'protection_unknown_or_used')
        self.decision('used');self.assertIsNone(update_field(self.store,self.p['id'],self.f['id'],self.now)['update'])
        self.assertFalse(self.store.records('local_update'))

    def test_foreign_profile_cannot_read_or_update_issue(self):
        with self.assertRaises(ValueError):update_field(self.store,'other',self.f['id'],self.now)

    def test_changed_sensor_metadata_blocks_old_issue_update(self):
        self.decision('not_used');p=deepcopy(self.p);p['sensors'][0]['measurement_type']='canopy'
        self.store.save_profile(p,self.pr['id'],self.now)
        self.assertEqual(update_field(self.store,self.p['id'],self.f['id'],self.now)['status'],'reference_sensor_changed')

    def test_changed_coordinates_block_local_update_for_old_issue(self):
        self.decision('not_used');p=deepcopy(self.p);p['zones'][0]['latitude']+=.1
        self.store.save_profile(p,self.pr['id'],self.now)
        self.assertEqual(update_field(self.store,self.p['id'],self.f['id'],self.now)['status'],'reference_zone_changed')
        self.assertFalse(self.store.records('local_update'))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT/'docs/PRODUCT_RISK_SUMMARY.json').exists():raise unittest.SkipTest('Run frozen research first')
        cls.tmp=tempfile.TemporaryDirectory();cls.server=make_server(0,Path(cls.tmp.name));cls.thread=Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    def state(self):return json.load(urlopen(self.url+'/api/state'))
    def post(self,path,body,token):return json.load(urlopen(Request(self.url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Product-Token':token})))

    def test_initial_state_has_no_later_truth_or_measurements(self):
        state=self.state();self.assertEqual(len(state['cases']),184)
        encoded=json.dumps(state['cases']);self.assertNotIn('target_temperature_c',encoded);self.assertNotIn('actual_minimum_c',encoded)
        self.assertEqual(set(state['presets']),{'hit','miss','false_alarm'})

    def test_coherent_probabilities_are_separate_from_old_timing_and_field_detector(self):
        s=self.state()
        if not s['coherent_quality']:self.skipTest('Run coherent experiment')
        self.assertFalse(s['coherent_quality']['field_promoted']);self.assertFalse(s['coherent_quality']['timing_updated'])
        for f in s['cases']:
            p=[f['coherent_forecasts'][str(q)]['probability'] for q in [-3,-2,-1,0]]
            self.assertEqual(p,sorted(p))
            self.assertNotIn('early_onset_index',f['coherent_forecasts']['0'])
            self.assertNotIn('target_temperature_c',json.dumps(f['coherent_forecasts']))

    def test_calibration_identity_restores_zero_score_and_does_not_expose_truth(self):
        s=self.state()
        if not s['calibration_quality']:self.skipTest('Run calibration study')
        self.assertFalse(s['calibration_quality']['detector_changed']);self.assertFalse(s['calibration_quality']['timing_updated'])
        self.assertFalse(s['calibration_quality']['field_promoted'])
        for f in s['cases']:
            r=f['calibrated_forecasts'];values=[r[str(q)]['probability'] for q in [-3,-2,-1,0]]
            self.assertEqual(values,sorted(values));self.assertNotIn('target_temperature_c',json.dumps(r));self.assertNotIn('mask',r['0'])
            if s['calibration_quality']['winner']=='identity':
                self.assertEqual(r['0']['probability'],f['legacy_detector']['night_event_model_score'])

    def test_agronom_brief_and_state_have_no_future_facts(self):
        s=json.load(urlopen(self.url+'/api/agronom/state'));self.assertEqual(len(s['dates']),184)
        b=json.load(urlopen(self.url+'/api/agronom/brief?date='+s['presets']['disagreement']))
        self.assertNotIn('target_temperature_c',json.dumps(b));self.assertNotIn('actual_minimum_c',json.dumps(b))
        self.assertTrue(b['brief']['signals_disagree']);self.assertFalse(b['brief']['operational_actionable'])
        self.assertIsNone(b['brief']['prepare_by_utc'])

    def test_agronom_rehearsal_decision_then_result_is_separate_from_field(self):
        s=json.load(urlopen(self.url+'/api/agronom/state'));date=s['presets']['disagreement']
        with self.assertRaises(HTTPError):urlopen(self.url+'/api/agronom/result?decision_id=unknown')
        b=json.load(urlopen(self.url+'/api/agronom/brief?date='+date))
        before=FieldStore(self.tmp.name).records()
        d=self.post('/api/agronom/decision',{'date':date,'threshold_c':0,'lead_hours':None,'forecast_sha256':b['forecast_sha256'],
                                           'action':'verify_data','notes':'Engineering check: verify local measurement next'},s['token'])
        r=json.load(urlopen(self.url+'/api/agronom/result?decision_id='+d['id']))
        saved=json.load(urlopen(self.url+'/api/agronom/decision?decision_id='+d['id']))
        self.assertEqual(saved['forecast_sha256'],b['forecast_sha256']);self.assertNotIn('actual_minimum_c',json.dumps(saved))
        reopened=json.load(urlopen(self.url+'/api/agronom/state'))
        self.assertIn(d['id'],[v['id'] for v in reopened['rehearsal_decisions']])
        self.assertEqual(r['date'],date);self.assertEqual(r['gfs_weather_comparison'],'miss')
        self.assertTrue(r['not_field_metrics']);self.assertEqual(before,FieldStore(self.tmp.name).records())

    def test_agronom_requires_current_issue_hash_and_token(self):
        s=json.load(urlopen(self.url+'/api/agronom/state'));body={'date':s['dates'][0],'action':'prepare','notes':'Engineering test only','forecast_sha256':'wrong'}
        with self.assertRaises(HTTPError):self.post('/api/agronom/decision',body,s['token'])
        with self.assertRaises(HTTPError):self.post('/api/agronom/decision',body,'wrong-token')

    def test_result_is_separate_and_incomplete_truth_remains_unknown(self):
        s=self.state();date=s['cases'][0]['date'];r=json.load(urlopen(self.url+'/api/result?date='+date))
        self.assertEqual(len(r['temperature_c']),14)
        frames=json.loads((ROOT/'data/processed/product-risk/night-forecasts.json').read_text(encoding='utf-8'))
        missing=next(f['date'] for f in frames if None in f['sample']['target_temperature_c'])
        r=json.load(urlopen(self.url+'/api/result?date='+missing));self.assertFalse(r['complete']);self.assertIsNone(r['actual_minimum_c'])

    def test_advanced_cutoff_does_not_return_future_truth(self):
        s=self.state();date=s['presets']['false_alarm'];r=json.load(urlopen(self.url+'/api/local-preview?date='+date))
        self.assertNotIn('truth',r);self.assertNotIn('target_temperature_c',r)
        if r['status']=='historical_simulation':self.assertEqual(len(r['first_three_observations']),3)

    def test_post_requires_token_and_host_is_local(self):
        with self.assertRaises(HTTPError) as c:self.post('/api/economics',{'protection_cost_rub':1,'season_price_rub':1},'wrong')
        self.assertEqual(c.exception.code,403)
        with self.assertRaises(HTTPError) as c:urlopen(Request(self.url+'/api/state',headers={'Host':'evil.invalid'}))
        self.assertEqual(c.exception.code,403)

    def test_preparation_unknown_phase_and_missing_lead_not_invented(self):
        s=self.state();r=self.post('/api/preparation',{'date':s['cases'][0]['date'],'threshold_c':0,'lead_hours':None},s['token'])
        self.assertIsNone(r['slack_hours']);self.assertFalse(r['phase_confirmed']);self.assertFalse(r['protocol_reviewed'])

    def test_inconsistent_thresholds_cannot_produce_preparation_deadline(self):
        s=self.state();date=next(f['date'] for f in s['cases'] if not f['threshold_order_valid'])
        r=self.post('/api/preparation',{'date':date,'threshold_c':0,'lead_hours':2},s['token'])
        self.assertIsNone(r['slack_hours']);self.assertEqual(r['reason'],'inconsistent_threshold_order')

    def test_season_break_even_includes_service_cost_and_zero_decisions_stays_unknown(self):
        token=self.state()['token'];body={'protection_cost_rub':5000,'season_price_rub':10000,'decisions_per_season':3,'damaging_event_probability':.2,'avoided_loss_if_event_rub':100000}
        r=self.post('/api/economics',body,token)
        self.assertAlmostEqual(r['break_even_event_probability'],1/12);self.assertIsNone(r['confirmed_savings_rub'])
        body['decisions_per_season']=0;r=self.post('/api/economics',body,token);self.assertIsNone(r['break_even_event_probability'])


class GEFSIndexTests(unittest.TestCase):
    def test_actual_gefs_sample_contains_all_members_with_coarse_step(self):
        file=ROOT/'data/processed/product-gefs/2023-03-01.json'
        if not file.exists():self.skipTest('Collect GEFS sample')
        r=json.loads(file.read_text(encoding='utf-8'))
        self.assertEqual(r['status'],'complete_sample');self.assertTrue(r['complete_operational_ensemble'])
        self.assertEqual(sorted(m['member'] for m in r['members']),list(range(31)))
        for m in r['members']:
            self.assertEqual([h['lead_hours'] for h in m['hours']],[12,15,18,21])
            self.assertTrue(all(-90<=h['temperature_c']<=65 for h in m['hours']))
        self.assertFalse(r['used_in_fitting']);self.assertTrue(r['not_hourly_duration_evidence'])
    def test_index_checks_run_lead_member_and_unique_range(self):
        run=START.replace(year=2023,month=3,day=1,hour=6)
        index=b'1:0:d=2023030106:TMP:2 m above ground:12 hour fcst:ENS=low-res ctl\n2:100:d=2023030106:RH:2 m above ground:12 hour fcst:ENS=low-res ctl\n'
        self.assertEqual(temperature_range(index,run,12,0),(0,99))
        with self.assertRaises(ValueError):temperature_range(index,run,15,0)
        with self.assertRaises(ValueError):temperature_range(index.replace(b'ctl',b'perturbed'),run,12,0)

    def test_risk_and_local_frozen_choices_match_reports(self):
        for folder,report in [('research-product-risk','PRODUCT_RISK_SUMMARY.json'),('research-local-update','LOCAL_UPDATE_SUMMARY.json')]:
            if not (ROOT/'docs'/report).exists():self.skipTest('Run research')
            lock=json.loads((ROOT/'models'/folder/'selection-before-controls.json').read_text(encoding='utf-8'))
            self.assertEqual(lock,json.loads((ROOT/'docs'/report).read_text(encoding='utf-8'))['selection'])


if __name__=='__main__':unittest.main()
