from copy import deepcopy
import unittest
import json
from unittest.mock import patch
from agro_checker.pipeline import ROOT
from agro_checker.product_data import sha
from agro_checker import cold_exposure_study as study
from agro_checker import cold_exposure as model
from agro_checker.frost_study import night_samples
from tests.test_frost_study import sample_rows


class ExposureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=sample_rows(2022)
        cls.samples=night_samples(cls.rows)
        cls.event={'cold_mask':[False]*8+[True]*6,'night_event_model_score':.5}

    def test_hourly_index_and_zero_equality(self):
        f=model.exposure([1.]*8+[-2.]*6)
        self.assertEqual(f['degree_hours_c_h'],12);self.assertEqual(f['cold_bins_h'],6)
        self.assertEqual(model.exposure([0.]*14)['degree_hours_c_h'],0)
        self.assertEqual(model.exposure([1.]*8+[-2.]*6,-1)['degree_hours_c_h'],6)

    def test_missing_or_nonfinite_measurements_stay_unknown(self):
        for bad in [None,float('nan'),True,-100]:
            t=[1.]*14;t[3]=bad;self.assertIsNone(model.exposure(t)['degree_hours_c_h'])

    def test_forecast_does_not_use_targets_or_mutate_detector(self):
        n=self.samples[0];m=model.fit(self.samples,'all_ridge_10');event=deepcopy(self.event)
        a=model.predict(m,n,event);n=deepcopy(n)
        for r in n['rows']:r['target']['temperature']=999.
        n['complete']=False;n['duration']=None
        self.assertEqual(a,model.predict(m,n,event));self.assertEqual(event,self.event)

    def test_all_control_and_validation_years_rejected_in_fit(self):
        for y in [2024,2025,2026]:
            n=deepcopy(self.samples);n[0]['year']=y
            with self.assertRaises(ValueError):model.fit(n,'all_ridge_10')

    def test_gate_gfs_does_not_overwrite_detector_or_fabricate_dose(self):
        n=deepcopy(self.samples[0]);n['curve']=[1.]*14
        self.assertEqual(model.predict({'name':'detector_gate_gfs'},n,self.event)['degree_hours_c_h'],0)
        self.assertTrue(model.predict({'name':'gfs'},n,self.event)['detector_event_unchanged'])

    def test_conditional_fit_requires_enough_independent_records(self):
        with self.assertRaises(ValueError):model.fit(self.samples,'cold_ridge_10',10)
        with self.assertRaises(ValueError):model.fit(self.samples+[self.samples[0]],'all_ridge_10')

    def test_exposure_never_becomes_crop_damage(self):
        self.assertIsNone(model.exposure([-10.]*14)['crop_damage_probability'])
        self.assertIsNone(model.predict({'name':'gfs'},self.samples[0],self.event)['crop_damage_probability'])


class FrozenExposureTests(unittest.TestCase):
    def test_prior_selection_checks_sources_without_control_observations(self):
        if not study.LOCK.exists():self.skipTest('Run exposure selection')
        original=study.load_rows;seen=[]
        def checked(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});seen.extend(years);return original(directory,years,config)
        with patch.object(study,'load_rows',side_effect=checked):s=study.selection()
        self.assertEqual(set(seen),{2022,2023,2024});self.assertTrue(all(s['gates'][s['winner']].values()))

    def test_saved_new_target_preserves_original_detector_and_missing_facts(self):
        path=ROOT/'docs/COLD_EXPOSURE_SUMMARY.json'
        if not path.exists():self.skipTest('Run exposure controls')
        summary=json.loads(path.read_text(encoding='utf-8'));file=ROOT/'data/processed/cold-exposure/night-forecasts.json'
        self.assertEqual(sha(file),summary['forecast_sha256']);self.assertEqual(sha(study.LOCK),summary['selection_sha256'])
        old={f['date']:f for f in json.loads((ROOT/'data/processed/cold-calibration/night-forecasts.json').read_text(encoding='utf-8'))}
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        for f in frames:
            self.assertEqual(f['legacy_detector'],old[f['date']]['legacy_detector'])
            self.assertEqual(f['actual_degree_hours_c_h'],model.exposure(old[f['date']]['sample']['target_temperature_c'])['degree_hours_c_h'])


if __name__=='__main__':unittest.main()
