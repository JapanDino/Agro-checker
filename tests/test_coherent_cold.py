from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker.pipeline import ROOT
from agro_checker import coherent_cold as model
from agro_checker.coherent_cold_study import metrics, oof_detector
from tests.test_product_risk import sample
from tests.test_frost_study import sample_rows
from agro_checker.product_risk_study import station_nights
from agro_checker import coherent_cold_study as study
from agro_checker.product_data import sha


class CoherentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=json.loads((ROOT/'config/coherent_cold.json').read_text(encoding='utf-8'))
        cls.samples=[sample(day=i) for i in range(80)]
        cls.oof=[{'date':n['date'],'score':float(1/(1+np.exp(min(n['gfs_temperature_c'])-2))),
                  'fit_dates':[x['date'] for x in cls.samples if x['date']!=n['date']]} for n in cls.samples]
        cls.ordinal=model.fit_ordinal(cls.samples,cls.oof,'joint',1.,cls.p)
        cls.shared=model.fit_shared(cls.samples,32,cls.p)

    def test_nested_cdf_for_many_scores_and_forecasts(self):
        for m in [self.ordinal,self.shared]:
            for score in [0,.001,.1,.5,.999,1]:
                for day in [0,10,25,40,60]:
                    f=model.forecast(m,sample(2024,day),score)
                    p=[f[str(q)]['probability'] for q in [-3,-2,-1,0]]
                    self.assertTrue(all(0<=x<=1 for x in p));self.assertEqual(p,sorted(p))

    def test_no_target_dependency_or_model_mutation(self):
        n=sample(2024,10)
        for m in [self.ordinal,self.shared]:
            before=json.dumps(m,sort_keys=True);a=model.forecast(m,n,.3)
            other=deepcopy(n);other['target_temperature_c']=[999]*14
            self.assertEqual(a,model.forecast(json.loads(before),other,.3))
            self.assertEqual(before,json.dumps(m,sort_keys=True))

    def test_controls_and_validation_cannot_enter_training(self):
        for year in [2024,2025,2026]:
            with self.assertRaises(ValueError):model.fit_shared([sample(year)],32,self.p)
            with self.assertRaises(ValueError):model.fit_ordinal([sample(year)],[],'joint',1,self.p)

    def test_oof_scores_must_exclude_target_date(self):
        oof=deepcopy(self.oof);oof[0]['fit_dates'].append(oof[0]['date'])
        with self.assertRaisesRegex(ValueError,'leakage'):model.fit_ordinal(self.samples,oof,'joint',1,self.p)
        oof=deepcopy(self.oof);oof[0]['fit_dates']=['2025-03-01']
        with self.assertRaisesRegex(ValueError,'leakage'):model.fit_ordinal(self.samples,oof,'joint',1,self.p)

    def test_scores_align_and_must_be_finite_probabilities(self):
        with self.assertRaises(ValueError):model.fit_ordinal(self.samples,self.oof[:-1],'joint',1,self.p)
        for bad in [None,float('nan'),-.1,1.1,True]:
            with self.assertRaises(ValueError):model.forecast(self.ordinal,sample(2024),bad)

    def test_shared_crossfit_excludes_all_sites_of_date(self):
        ns=[sample(day=i,site=s) for i in range(80) for s in ['one','two']]
        m=model.fit_shared(ns,32,self.p)
        self.assertEqual(sum(len(a['held_dates']) for a in m['audits']),80)
        self.assertTrue(all(not set(a['fit_dates'])&set(a['held_dates']) for a in m['audits']))

    def test_incomplete_truth_not_filled(self):
        ns=deepcopy(self.samples);ns[0]['target_temperature_c'][3]=None
        self.assertEqual(model.fit_shared(ns,32,self.p)['training_nights'],79)

    def test_probability_only_does_not_invent_timing_damage_or_range(self):
        f=model.forecast(self.ordinal,sample(2024),.5)['0']
        self.assertIsNone(f['crop_damage_probability'])
        self.assertNotIn('minimum_range_c',f);self.assertNotIn('early_onset_index',f)

    def test_legacy_metrics_use_original_event_not_new_threshold(self):
        n=sample(2024);n['target_temperature_c']=[1]*14
        a=metrics([{'sample':n,'probabilities':{'-3':None,'-2':None,'-1':None,'0':.9},'legacy_event':False}])
        self.assertEqual(a['thresholds']['0']['false_alarms'],0)
        self.assertEqual(a['thresholds']['-1']['complete_nights'],0)

    def test_true_nested_oof_detector_fit_and_alignment(self):
        rows=sample_rows(2022)
        for r in rows:
            r['forecast_run_at_utc']=r['date']+'T06:00:00+00:00'
            r['issued_at_utc']=r['date']+'T15:00:00+00:00'
            r['base']['wind']=2.;r['target']['wind']=2.
            r['target']['temperature']=-1. if int(r['date'][-2:])%2 and r['hour_index']>=8 else 3.
        ns=station_nights(rows)
        records=oof_detector(rows,ns,self.p)
        self.assertEqual({n['date'] for n in ns},{r['date'] for r in records})
        self.assertTrue(all(r['date'] not in r['fit_dates'] for r in records))

    def test_invalid_forecast_and_threshold_registry(self):
        n=sample(2024);n['issued_at_utc']=n['times'][0]
        with self.assertRaises(ValueError):model.forecast(self.ordinal,n,.5)
        p=deepcopy(self.p);p['weather_thresholds_c']=[0,-1]
        with self.assertRaises(ValueError):model.fit_ordinal(self.samples,self.oof,'joint',1,p)


class FrozenCoherentTests(unittest.TestCase):
    def test_repeat_selection_opens_only_prior_observations(self):
        if not study.LOCK.exists():self.skipTest('Run selection first')
        original=study.load_rows;seen=[]
        def checked(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});seen.extend(years)
            return original(directory,years,config)
        with patch.object(study,'load_rows',side_effect=checked):s=study.selection()
        self.assertEqual(set(seen),{2022,2023,2024})
        self.assertEqual(s['observations_loaded_years'],[2022,2023,2024])

    def test_saved_controls_reproduce_cdf_without_touching_originals(self):
        path=ROOT/'docs/COHERENT_COLD_SUMMARY.json'
        if not path.exists():self.skipTest('Run controls first')
        summary=json.loads(path.read_text(encoding='utf-8'));s=summary['selection']
        file=ROOT/'data/processed/coherent-cold/night-forecasts.json'
        self.assertEqual(sha(file),summary['forecast_sha256']);self.assertEqual(sha(study.LOCK),summary['selection_sha256'])
        fitted=json.loads((study.DIRECTORY/s['models'][s['diagnostic']]).read_text(encoding='utf-8'))
        old={f['date']:f for f in json.loads((ROOT/'data/processed/product-risk/night-forecasts.json').read_text(encoding='utf-8'))}
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        for f in frames:
            self.assertEqual(f['legacy_detector'],old[f['date']]['legacy_detector'])
            self.assertEqual(f['legacy_point'],old[f['date']]['legacy_point'])
            score=f['legacy_detector']['night_event_model_score']
            self.assertEqual(f['threshold_forecasts'],model.forecast(fitted,f['sample'],score))


if __name__=='__main__':unittest.main()
