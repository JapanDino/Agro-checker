from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from agro_checker.pipeline import ROOT
from agro_checker import cold_calibration as model
from agro_checker import cold_calibration_study as study
from agro_checker.product_data import sha
from tests.test_product_risk import sample


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=json.loads((ROOT/'config/cold_calibration.json').read_text(encoding='utf-8'))
        cls.base=json.loads((ROOT/'models/research-coherent-cold/ordinal_score_0.1-0.25.json').read_text(encoding='utf-8'))
        cls.records=[{'date':sample(day=i)['date'],'year':2022,'score':.02+.01*i,'target':int(i>=65),
                      'fit_dates':[sample(day=(i+1)%80)['date']]} for i in range(80)]
        cls.cal=model.fit_affine(cls.records,'brier',1.,cls.p)

    def test_fit_forbids_validation_controls_and_self_fit_dates(self):
        for key,value in [('year',2024),('year',2025),('year',2026),('fit_dates',['2025-03-01']),('fit_dates',[self.records[0]['date']])]:
            r=deepcopy(self.records);r[0][key]=value
            with self.assertRaises(ValueError):model.fit_affine(r,'brier',1.,self.p)

    def test_probability_order_including_extreme_finite_scores(self):
        for h in [{'kind':'identity'},{'kind':'ordinal'},{'kind':'mixture','weight':.5},
                  {'kind':'affine','calibration':self.cal}]:
            m={'ordinal_base':self.base,'head':h}
            for raw in [0,1e-12,.001,.1,.25,.8,1-1e-12,1]:
                f=model.forecast(m,sample(2024),raw);values=[f[str(q)]['probability'] for q in [-3,-2,-1,0]]
                self.assertEqual(values,sorted(values));self.assertTrue(all(0<=v<=1 for v in values))

    def test_identity_anchor_exactly_preserves_original_zero_score(self):
        m={'ordinal_base':self.base,'head':{'kind':'identity'}}
        for raw in [1e-12,.001,.01,.25,.9,1]:self.assertEqual(model.forecast(m,sample(2024),raw)['0']['probability'],raw)

    def test_inference_ignores_target_and_does_not_mutate_source(self):
        m={'ordinal_base':self.base,'head':{'kind':'affine','calibration':self.cal}}
        before=json.dumps(m,sort_keys=True);n=sample(2024);a=model.forecast(m,n,.3)
        n['target_temperature_c']=[None]*14
        self.assertEqual(a,model.forecast(m,n,.3));self.assertEqual(before,json.dumps(m,sort_keys=True))

    def test_rare_or_unknown_calibration_falls_back_without_fabricating(self):
        r=deepcopy(self.records)
        for x in r:x['target']=0
        c=model.fit_affine(r,'brier',1.,self.p);self.assertEqual(c['status'],'insufficient_events_identity')
        self.assertEqual(model.probability_at_zero({'kind':'affine','calibration':c},.3,.5),.3)
        r[0]['target']=None
        with self.assertRaises(ValueError):model.fit_affine(r,'brier',1.,self.p)

    def test_brier_fit_is_positive_and_improves_training_objective(self):
        a,b=self.cal['a'],self.cal['b'];self.assertTrue(.25<=a<=4)
        self.assertLessEqual(self.cal['objective'],sum((r['score']-r['target'])**2 for r in self.records)+1e-6)

    def test_invalid_scores_settings_and_duplicate_records(self):
        for bad in [float('nan'),None,-.1,1.1,True]:
            r=deepcopy(self.records);r[0]['score']=bad
            with self.assertRaises(ValueError):model.fit_affine(r,'brier',1.,self.p)
        with self.assertRaises(ValueError):model.fit_affine(self.records+[self.records[0]],'brier',1.,self.p)
        with self.assertRaises(ValueError):model.fit_affine(self.records,'wrong',1.,self.p)

    def test_probabilities_do_not_create_crop_damage_or_hourly_timing(self):
        f=model.forecast({'ordinal_base':self.base,'head':{'kind':'identity'}},sample(2024),.3)['0']
        self.assertIsNone(f['crop_damage_probability']);self.assertNotIn('early_onset_index',f);self.assertNotIn('mask',f)

    def test_incomplete_observations_are_excluded_from_brier_not_filled(self):
        n=sample(2024);n['target_temperature_c'][2]=None
        r=study.score([{'sample':n,'probabilities':{str(q):.3 for q in [-3,-2,-1,0]},'decision_threshold':.25}])
        self.assertEqual(r['thresholds']['0']['complete_nights'],0);self.assertEqual(r['thresholds']['0']['excluded_nights'],1)


class FrozenCalibrationTests(unittest.TestCase):
    def test_selection_loader_uses_only_prior_observations_and_locks_sources(self):
        if not study.LOCK.exists():self.skipTest('Select calibration first')
        original=study.load_rows;seen=[]
        def checked(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});seen.extend(years);return original(directory,years,config)
        with patch.object(study,'load_rows',side_effect=checked):s=study.selection()
        self.assertEqual(set(seen),{2022,2023,2024})
        self.assertTrue(all(s['gates'][s['winner']].values()))
        self.assertEqual(s['protocol']['decision_threshold'],.25)

    def test_saved_controls_reproduce_calibration_and_preserve_all_originals(self):
        path=ROOT/'docs/COLD_CALIBRATION_SUMMARY.json'
        if not path.exists():self.skipTest('Evaluate calibration first')
        summary=json.loads(path.read_text(encoding='utf-8'));s=summary['selection']
        file=ROOT/'data/processed/cold-calibration/night-forecasts.json'
        self.assertEqual(sha(file),summary['forecast_sha256']);self.assertEqual(sha(study.LOCK),summary['selection_sha256'])
        m=json.loads((study.DIRECTORY/s['models'][s['winner']]).read_text(encoding='utf-8'))
        old={f['date']:f for f in json.loads((ROOT/'data/processed/coherent-cold/night-forecasts.json').read_text(encoding='utf-8'))}
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        for f in frames:
            self.assertEqual(f['legacy_detector'],old[f['date']]['legacy_detector']);self.assertEqual(f['legacy_point'],old[f['date']]['legacy_point'])
            self.assertEqual(f['threshold_forecasts'],model.forecast(m,f['sample'],f['legacy_detector']['night_event_model_score']))


if __name__=='__main__':unittest.main()
