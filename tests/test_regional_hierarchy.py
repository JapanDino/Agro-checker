from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from agro_checker import regional_hierarchy as study
from agro_checker import regional_minimum as common
from agro_checker.pipeline import ROOT
from agro_checker.product_data import sha
from tests.test_regional_minimum import night


class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.samples=[night(f'2022-03-{i:02}',sid=sid,cold=i<8) for i in range(1,25) for sid in ['a','b']]
        self.spec={'name':'test','kind':'ridge','alpha':10,'site_scale':4,'cold_weight':2}
        self.base=common.fit(self.samples,{'name':'base','kind':'ridge','alpha':100,'cold_weight':1,'balance':'date'})

    def test_inference_no_targets_or_mutation_of_baseline(self):
        model=study.fit(self.samples,self.spec);n=night(sid='a');base=deepcopy(self.base);a=study.predict(model,base,n,.25)
        del n['target_temperature_c'];self.assertEqual(a,study.predict(model,base,n,.25));self.assertEqual(base,self.base)

    def test_unknown_site_does_not_invent_a_learned_station_offset(self):
        model=study.fit(self.samples,self.spec);model['coef']=[0.]*15+[100.,200.];model['intercept']=0
        a=study.predict(model,self.base,night(sid='unknown1'),1);b=study.predict(model,self.base,night(sid='unknown2'),1)
        self.assertEqual(a,b);self.assertEqual(a,min(night()['gfs']['temperature']))
        self.assertNotEqual(a,study.predict(model,self.base,night(sid='a'),1))

    def test_heldout_and_future_cannot_enter_training(self):
        for y in [2024,2025,2026]:
            samples=deepcopy(self.samples);samples[0]['year']=y
            with self.assertRaises(ValueError):study.fit(samples,self.spec)
        samples=deepcopy(self.samples);samples[0]['role']='heldout_site'
        with self.assertRaises(ValueError):study.fit(samples,self.spec)

    def test_unregistered_blending_rejected(self):
        with self.assertRaises(ValueError):study.predict(study.fit(self.samples,self.spec),self.base,night(),.9)


class FrozenHierarchyTests(unittest.TestCase):
    def test_selection_never_loads_heldout_or_controls(self):
        if not study.LOCK.exists():self.skipTest('Run selection')
        original_load=study.data.load;original_rows=study.load_rows;seen=[]
        def checked_load(partition):
            self.assertEqual(partition,'development');seen.append(partition);return original_load(partition)
        def checked_rows(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});return original_rows(directory,years,config)
        with patch.object(study.data,'load',side_effect=checked_load),patch.object(study,'load_rows',side_effect=checked_rows):s=study.selection()
        self.assertTrue(seen);self.assertFalse(s['heldout_targets_loaded_in_selection'])
        for file in s['models'].values():
            m=json.loads((study.DIRECTORY/file).read_text(encoding='utf-8'));self.assertEqual(m['training_years'],[2022,2023])

    def test_better_controls_do_not_change_failed_prior_choice(self):
        path=ROOT/'docs/REGIONAL_HIERARCHY_SUMMARY.json'
        if not path.exists():self.skipTest('Run controls')
        r=json.loads(path.read_text(encoding='utf-8'));s=study.selection()
        self.assertEqual(sha(study.LOCK),r['selection_sha256']);self.assertEqual(s,r['selection']);self.assertEqual(r['original_detector_verified'],184)
        file=study.data.DIRECTORY/'hierarchy-control-forecasts.json';self.assertEqual(sha(file),r['forecast_sha256'])
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        if s['winner']=='legacy':
            self.assertFalse(any(all(g.values()) for g in s['gates'].values()))
            for f in frames:self.assertEqual(f['points']['legacy'],f['points']['selected'])


if __name__=='__main__':unittest.main()
