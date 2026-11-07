from copy import deepcopy
from datetime import datetime,timedelta,timezone
import gzip
import json
import unittest
import numpy as np
from agro_checker.pipeline import ROOT
from agro_checker.product_data import parse_isd,counts
from agro_checker import product_risk as model


def sample(year=2022,day=0,site='one'):
    d=datetime(year,3,1,tzinfo=timezone.utc)+timedelta(days=day)
    t=[2+3*np.sin(day/7)+abs(i-8)*.35 for i in range(14)]
    return {'date':d.date().isoformat(),'year':year,'site_id':site,'elevation_m':33,'run_at_utc':d.replace(hour=6).isoformat(),
      'issued_at_utc':d.replace(hour=15).isoformat(),'times':[(d.replace(hour=16)+timedelta(hours=i)).isoformat() for i in range(14)],
      'gfs_temperature_c':t,'gfs_dewpoint_c':[v-2 for v in t],
      'target_temperature_c':[v-1.4+np.sin(day/4)*.4 for v in t]}


class RiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=json.loads((ROOT/'config/product_risk.json').read_text(encoding='utf-8'));cls.spec={'name':'test','neighbors':16}
        cls.samples=[sample(day=i,site=s) for i in range(80) for s in ['one','two']]
        cls.model=model.fit(cls.samples,cls.spec);cls.cal=model.calibrate(cls.samples,cls.spec,cls.p)

    def test_no_control_or_validation_targets_enter_fit_calibration(self):
        for year in [2024,2025,2026]:
            with self.assertRaises(ValueError):model.fit([sample(year)],self.spec)
            with self.assertRaises(ValueError):model.calibrate([sample(year)],self.spec,self.p)

    def test_all_sites_of_held_dates_are_excluded_in_crossfit(self):
        held=[]
        for block in self.cal['audits']:
            self.assertFalse(set(block['fit_dates'])&set(block['held_dates']));held+=block['held_dates']
        self.assertEqual(sorted(held),sorted({n['date'] for n in self.samples}))

    def test_whole_trajectory_probability_hours_and_expected_duration_are_consistent(self):
        n=sample(2024,30);f=model.forecast(self.model,self.cal,n,0,self.p)
        self.assertTrue(all(0<=v<=f['probability']+1e-10 for v in f['hour_probabilities']))
        self.assertAlmostEqual(sum(f['hour_probabilities']),f['expected_cold_hours'])
        if f['conditional_duration_range_h']:
            a,b=f['conditional_duration_range_h'];self.assertLessEqual(a,b);self.assertTrue(1<=a<=b<=14)
        self.assertIsNone(f['crop_damage_probability']);self.assertFalse(f['coverage_guaranteed'])

    def test_truth_does_not_affect_inference_and_json_roundtrip(self):
        n=sample(2024,25);a=model.forecast(self.model,self.cal,n,0,self.p)
        n['target_temperature_c']=[999]*14
        self.assertEqual(a,model.forecast(json.loads(json.dumps(self.model)),self.cal,n,0,self.p))

    def test_invalid_issue_horizon_features_and_unknown_threshold_rejected(self):
        for key,value in [('issued_at_utc',sample(2024)['times'][0]),('gfs_temperature_c',[1]*13),('gfs_dewpoint_c',[float('nan')]*14),('elevation_m',float('nan'))]:
            n=sample(2024);n[key]=value
            with self.assertRaises(ValueError):model.forecast(self.model,self.cal,n,0,self.p)
        with self.assertRaises(ValueError):model.forecast(self.model,self.cal,sample(2024),-9,self.p)

    def test_incomplete_targets_are_excluded_not_filled(self):
        ns=deepcopy(self.samples);ns[0]['target_temperature_c'][2]=None
        self.assertEqual(model.fit(ns,self.spec)['training_nights'],159)

    def test_sparse_station_reports_cannot_become_complete_nights(self):
        lines='\n'.join(f'2023 3 1 {h} -20 -25 10000 100 10 4 -9999 -9999' for h in range(0,24,3))
        obs=parse_isd(gzip.compress(lines.encode()),2023)
        self.assertEqual(counts(obs,2023)['complete_nights'],0)
        with self.assertRaises(ValueError):parse_isd(gzip.compress((lines+'\n'+lines).encode()),2023)
        with self.assertRaises(ValueError):parse_isd(gzip.compress(lines.encode()),2025)

    def test_calibration_rare_threshold_has_explicit_status(self):
        ns=deepcopy(self.samples)
        for n in ns:n['target_temperature_c']=[10]*14
        c=model.calibrate(ns,self.spec,self.p)
        self.assertTrue(all(h['status']=='insufficient_events' for h in c['heads'].values()))

    def test_left_edge_does_not_invent_early_start_before_window(self):
        n=sample(2024);n['gfs_temperature_c']=[-10]*14;n['gfs_dewpoint_c']=[-12]*14
        f=model.forecast(self.model,self.cal,n,0,self.p)
        self.assertTrue(f['onset_left_censored']);self.assertIsNone(f['early_onset_index'])

    def test_feature_scaler_never_uses_new_forecast_or_truth(self):
        before=json.dumps(self.model,sort_keys=True)
        model.forecast(self.model,self.cal,sample(2024,32),0,self.p)
        self.assertEqual(before,json.dumps(self.model,sort_keys=True))


if __name__=='__main__':unittest.main()
