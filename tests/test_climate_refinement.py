import json
import unittest
from unittest.mock import patch
from agro_checker import climate_refinement as study
from agro_checker.pipeline import ROOT
from agro_checker.product_data import sha


class RefinementTests(unittest.TestCase):
    def test_nonnegative_anchor_never_becomes_a_new_cold_prediction(self):
        for a in [0.,.01,1.,20.]:
            for e in [-100.,-1.,0.,10.]:
                for w in [.25,.5,1]:self.assertEqual(study.refine(a,e,w),a)

    def test_cold_minimum_can_recover_above_zero_without_sign_clipping(self):
        self.assertEqual(study.refine(-1.,2.,1),2.)
        self.assertEqual(study.refine(-1.,2.,.5),.5)
        self.assertEqual(study.refine(-1.,-3.,.5),-2.)

    def test_nonfinite_and_unregistered_weights_rejected(self):
        for bad in [float('nan'),float('inf')]:
            with self.assertRaises(ValueError):study.refine(bad,1.,.25)
            with self.assertRaises(ValueError):study.refine(-1.,bad,.25)
        with self.assertRaises(ValueError):study.refine(-1.,1.,.2)


class FrozenRefinementTests(unittest.TestCase):
    def test_selection_loads_no_control_or_spatial_targets(self):
        if not study.LOCK.exists():self.skipTest('Run refinement selection')
        load=study.data.load;rows=study.load_rows;terrain=study.terrain_data.load
        def checked_load(partition):
            self.assertEqual(partition,'development');return load(partition)
        def checked_rows(directory,years,config=None):
            self.assertTrue(set(years)<={2022,2023,2024});return rows(directory,years,config)
        def checked_terrain(controls=False):
            self.assertFalse(controls);return terrain(controls)
        with patch.object(study.data,'load',side_effect=checked_load),patch.object(study,'load_rows',side_effect=checked_rows),patch.object(study.terrain_data,'load',side_effect=checked_terrain):s=study.selection()
        self.assertFalse(s['heldout_targets_loaded_in_selection'])
        self.assertEqual(json.loads((study.DIRECTORY/'expert.json').read_text(encoding='utf-8'))['training_years'],[2022,2023])

    def test_replay_preserves_detector_and_warm_anchor(self):
        path=ROOT/'docs/CLIMATE_REFINEMENT_SUMMARY.json'
        if not path.exists():self.skipTest('Run refinement controls')
        r=json.loads(path.read_text(encoding='utf-8'));file=study.terrain_data.DIRECTORY/'refinement-controls.json'
        self.assertEqual(sha(file),r['forecast_sha256']);self.assertEqual(sha(study.LOCK),r['selection_sha256'])
        frames=json.loads(file.read_text(encoding='utf-8'));self.assertEqual(len(frames),184)
        old={f['date']:f for f in json.loads((ROOT/'data/processed/regional-minimum/control-forecasts.json').read_text(encoding='utf-8'))}
        for f in frames:
            self.assertEqual(f['points']['anchor'],old[f['date']]['points']['legacy']);self.assertEqual(f['legacy_detector'],old[f['date']]['legacy_detector'])
            if f['points']['anchor']>=0:self.assertEqual(f['points']['diagnostic'],f['points']['anchor'])
        s=study.selection()
        if s['winner']=='anchor':self.assertFalse(any(all(g.values()) for g in s['gates'].values()))


if __name__=='__main__':unittest.main()
