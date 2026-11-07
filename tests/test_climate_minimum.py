from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from agro_checker import climate_minimum as model
from agro_checker import climate_minimum_study as study
from agro_checker import regional_minimum as common
from agro_checker.product_data import sha
from agro_checker.pipeline import ROOT
from tests.test_regional_minimum import night


class ClimateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples=[night(f'2022-03-{i:02}',sid=sid,cold=i<8) for i in range(1,25) for sid in ['a','b']]
        cls.metadata={'a':{'latitude':45.,'longitude':39.,'elevation_m':30.},'b':{'latitude':46.,'longitude':40.,'elevation_m':2000.}}
        cls.scales={'elevation_m':500,'mean_gfs_min_c':3,'p10_gfs_min_c':3,'mean_gfs_wind_ms':2,'mean_gfs_rh_pct':15,'mean_gfs_cloud_pct':25}
        cls.catalog=model.backgrounds(cls.samples,cls.metadata,[2022]);cls.terrain={}
        for n in cls.samples+[night('2023-03-01',sid=sid) for sid in ['a','b','c']]:
            cls.terrain.setdefault(n['date'],{})[n['id']]={'gfs_surface_height':20.,'units':'m'}
        cls.spec={'name':'test','family':'matched3_climate','cold_weight':1}
        cls.base=common.fit(cls.samples,{'name':'base','kind':'ridge','alpha':100,'cold_weight':1,'balance':'date'})
        cls.bundle=model.fit_bundle(cls.samples,cls.catalog,cls.terrain,cls.spec,cls.scales)

    def test_backgrounds_do_not_read_observed_temperature_or_labels(self):
        samples=deepcopy(self.samples)
        for n in samples:del n['target_temperature_c'];n['future_cold_label']=True
        self.assertEqual(model.backgrounds(samples,self.metadata,[2022]),self.catalog)

    def test_validation_and_control_years_rejected_in_background_and_fit(self):
        for year in [2024,2025,2026]:
            samples=deepcopy(self.samples);samples[0]['year']=year
            with self.assertRaises(ValueError):model.backgrounds(samples,self.metadata,[2022])
            with self.assertRaises(ValueError):model.fit_bundle(samples,self.catalog,self.terrain,self.spec,self.scales)

    def test_spatial_holdout_labels_rejected_in_fit(self):
        samples=deepcopy(self.samples);samples[0]['role']='heldout_site'
        with self.assertRaises(ValueError):model.fit_bundle(samples,self.catalog,self.terrain,self.spec,self.scales)

    def test_inference_reads_no_target_and_preserves_base(self):
        n=night('2023-03-01',sid='a');base=deepcopy(self.base);a=model.predict(self.bundle,base,n,self.terrain,.25)
        del n['target_temperature_c'];self.assertEqual(a,model.predict(self.bundle,base,n,self.terrain,.25));self.assertEqual(base,self.base)

    def test_future_context_or_observed_context_rejected(self):
        q=deepcopy(self.catalog['a']);q['knowledge_cutoff_utc']='2023-03-02T15:00:00+00:00'
        with self.assertRaises(ValueError):model.predict(self.bundle,self.base,night('2023-03-01',sid='a'),self.terrain,.25,q)
        q=deepcopy(self.catalog['a']);q['observed_targets_used']=True
        with self.assertRaises(ValueError):model.predict(self.bundle,self.base,night('2023-03-01',sid='a'),self.terrain,.25,q)

    def test_climate_distance_accounts_for_altitude_without_target(self):
        q=deepcopy(self.catalog['a']);q['id']='c';near=model.neighbourhood(q,self.catalog,1,self.scales)
        self.assertEqual(near[0]['id'],'a');q['elevation_m']=2000
        self.assertEqual(model.neighbourhood(q,self.catalog,1,self.scales)[0]['id'],'b')

    def test_unknown_site_requires_past_context_and_uses_fixed_experts(self):
        n=night('2023-03-01',sid='c')
        with self.assertRaises(ValueError):model.predict(self.bundle,self.base,n,self.terrain,.25)
        q=deepcopy(self.catalog['a']);q['id']='c'
        with patch.object(model,'fit_bundle',side_effect=AssertionError('No fitting at inference')):
            a=model.predict(self.bundle,self.base,n,self.terrain,.25,q)
        self.assertIsInstance(a,float)

    def test_invalid_terrain_and_wrong_station_context_rejected(self):
        for value in [float('nan'),True,10000]:
            terrain=deepcopy(self.terrain);terrain['2023-03-01']['a']['gfs_surface_height']=value
            with self.assertRaises(ValueError):model.predict(self.bundle,self.base,night('2023-03-01',sid='a'),terrain,.25)
        with self.assertRaises(ValueError):model.predict(self.bundle,self.base,night('2023-03-01',sid='a'),self.terrain,.25,self.catalog['b'])

    def test_duplicate_forecast_night_rejected_in_backgrounds(self):
        with self.assertRaises(ValueError):model.backgrounds(self.samples+[self.samples[0]],self.metadata,[2022])


class FrozenClimateTests(unittest.TestCase):
    def test_selection_never_loads_heldout_or_controls(self):
        if not study.LOCK.exists():self.skipTest('Run climate selection')
        original_load=study.data.load;original_rows=study.load_rows;original_terrain=study.terrain_data.load
        def checked(partition):
            self.assertEqual(partition,'development');return original_load(partition)
        def checked_rows(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});return original_rows(directory,years,config)
        def checked_terrain(controls=False):
            self.assertFalse(controls);return original_terrain(controls)
        with patch.object(study.data,'load',side_effect=checked),patch.object(study,'load_rows',side_effect=checked_rows),patch.object(study.terrain_data,'load',side_effect=checked_terrain):s=study.selection()
        self.assertFalse(s['context_observed_targets_used']);self.assertFalse(s['heldout_targets_loaded_in_selection'])
        for file in s['models'].values():
            m=json.loads((study.DIRECTORY/file).read_text(encoding='utf-8'));self.assertEqual(m['training_years'],[2022,2023])
            self.assertFalse({'349490-99999','370540-99999'}&set(m['training_sites']))

    def test_frozen_replay_preserves_point_and_full_detector(self):
        path=ROOT/'docs/CLIMATE_MINIMUM_SUMMARY.json'
        if not path.exists():self.skipTest('Run climate controls')
        s=json.loads(path.read_text(encoding='utf-8'));file=study.terrain_data.DIRECTORY/'control-forecasts.json'
        self.assertEqual(sha(file),s['forecast_sha256']);self.assertEqual(sha(study.LOCK),s['selection_sha256'])
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        old={f['date']:f for f in json.loads((ROOT/'data/processed/regional-minimum/control-forecasts.json').read_text(encoding='utf-8'))}
        for f in frames:
            self.assertEqual(f['legacy_detector'],old[f['date']]['legacy_detector']);self.assertEqual(f['points']['legacy'],old[f['date']]['points']['legacy'])
            self.assertFalse(f['point_crossings_are_alerts'])


if __name__=='__main__':unittest.main()
