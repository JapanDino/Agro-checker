"""Technical fixtures only. Outcomes cannot influence issue-time guard decisions."""
from copy import deepcopy
from unittest.mock import patch
import unittest

import numpy as np
from scripts import experiment_consensus_ndmi_guard as e


def fixture():
    scenarios=e.base.SCENARIOS
    rows=[dict(fid=f'R{i:02d}',issue='2024-07-01',target=None,y=None,
               x=[.2,-.2,-.001,.002,3,0,1],
               quality=dict(all_boundaries_supported=True,
                            boundary_delta={name:[-.003,.006] for name in scenarios})) for i in range(1,7)]
    records=dict(recent_drop=np.array([.09,.08,.07,.06,.05,.04]),
                 logit=np.array([.4,.3,.2,.9,.1,.05]),extra=np.array([.4,.3,.2,.9,.1,.05]))
    p=dict(variants=e.variants(),budget_k=3,fixed_parameters=dict(seed=20241021),
           old_variant=dict(id=e.OLD,protect=.20,margin=0.),minimum_selection_known=20,minimum_selection_dates=5)
    return rows,records,p


class GuardTest(unittest.TestCase):
    def variant(self,p,name):return next(v for v in p['variants'] if v['id']==name)

    def test_weak_divergent_round_keeps_all_baseline_places_not_only_one(self):
        rows,records,p=fixture()
        old=e.choose(rows,records,p['variants'][1],p)
        new,trace=e.choose(rows,records,self.variant(p,'protect_weak_divergent_round'),p,True)
        original=e.choose(rows,records,p['variants'][0],p)
        self.assertFalse(np.array_equal(old,original))
        np.testing.assert_array_equal(new,original)
        self.assertEqual(trace[0]['protected'],['R01','R02','R03'])
        self.assertTrue(trace[0]['weak_round_guard'])

    def test_guard_refuses_missing_boundary_and_does_not_use_future_values(self):
        rows,records,p=fixture()
        v=self.variant(p,'protect_weak_divergent_round')
        changed=deepcopy(rows)
        for row in changed:row.update(y=[-1,1],target='2026-12-31',target_cloud=0)
        np.testing.assert_array_equal(e.choose(rows,records,v,p),e.choose(changed,records,v,p))
        del rows[0]['quality']['boundary_delta'][e.base.SCENARIOS[0]]
        self.assertFalse(e.choose(rows,records,v,p,True)[1][0]['weak_round_guard'])

    def test_weak_guard_uses_maximum_of_whole_round_and_strict_threshold(self):
        rows,records,p=fixture();v=self.variant(p,'protect_weak_divergent_round')
        records['recent_drop'][5]=.10
        self.assertFalse(e.choose(rows,records,v,p,True)[1][0]['weak_round_guard'])

    def test_not_specific_to_names_or_calendar_date(self):
        rows,records,p=fixture();v=self.variant(p,'protect_weak_divergent_round')
        modified=deepcopy(rows)
        for i,row in enumerate(modified):row.update(fid=f'R{i+10:02d}',issue='2024-08-01')
        np.testing.assert_array_equal(e.choose(rows,records,v,p),e.choose(modified,records,v,p))

    def test_same_budget_and_at_most_one_swap(self):
        rows,records,p=fixture()
        baseline=e.choose(rows,records,p['variants'][0],p)
        for v in p['variants']:
            chosen=e.choose(rows,records,v,p)
            self.assertEqual(int(chosen.sum()),3)
            self.assertLessEqual(int(np.sum(baseline!=chosen)),2)

    def test_nonfinite_scores_duplicate_candidates_and_unregistered_rule_rejected(self):
        rows,records,p=fixture()
        with self.assertRaises(ValueError):e.choose(rows,records,dict(id='not registered'),p)
        records['extra'][0]=np.nan
        with self.assertRaises(ValueError):e.choose(rows,records,p['variants'][1],p)
        rows,records,p=fixture();rows[1]['fid']=rows[0]['fid']
        with self.assertRaises(ValueError):e.choose(rows,records,p['variants'][1],p)

    def test_evaluation_rejects_2025_2026_and_spatial_reserve(self):
        rows,records,p=fixture()
        for issue in ('2025-07-01','2026-07-01'):
            changed=deepcopy(rows);changed[0]['issue']=issue
            with self.assertRaises(ValueError):e.assess(changed,records,p['variants'][1],p)
        rows[0]['fid']=next(iter(e.base.HOLDOUT))
        with self.assertRaises(ValueError):e.assess(rows,records,p['variants'][1],p)

    def test_forward_masks_late_targets_before_selection(self):
        rows,records,p=fixture()
        for row in rows:row.update(issue='2024-06-28',target='2024-07-06',y=[.1,-.4])
        p['minimum_selection_known']=0;p['minimum_selection_dates']=1
        seen=[]
        original=e.assess
        def check(training,scores,v,p):
            seen.append(deepcopy(training))
            return original(training,scores,v,p)
        with patch.object(e,'assess',side_effect=check):e.forward(rows,records,p)
        self.assertTrue(seen)
        self.assertTrue(all(row['y'] is None for row in seen[0]))

    def test_actual_repair_only_one_known_swap_unknown_identities_unchanged(self):
        report=e.read(e.D/'report.json')
        self.assertEqual(report['candidate_count'],699)
        self.assertEqual(report['selection_years'],[2024]);self.assertEqual(report['test_years_evaluated'],[])
        selected=next(v for v in report['comparison'] if v['variant']==report['selected_repair'])
        self.assertTrue(selected['repair_passed']);self.assertFalse(selected['global_passed'])
        self.assertEqual({k:selected['metrics'][k] for k in ('tp','fp','unknown')},dict(tp=23,fp=45,unknown=65))
        self.assertTrue(report['diagnosis']['unknown_selected_identities_equal'])
        self.assertEqual(report['diagnosis']['changed_dates'],[dict(issue='2024-09-29',
            removed=[dict(fid='R19',outcome=False)],added=[dict(fid='R07',outcome=True)])])
        self.assertEqual(report['selected_global']['id'],'baseline')
        self.assertTrue(all(v['variant']['id']=='old_consensus' for v in report['forward_repair']['folds']))
        self.assertFalse(report['production_promoted']);self.assertFalse(report['model_refit_performed'])
        self.assertFalse(report['detector_changed']);self.assertFalse(report['point_forecast_changed'])
        self.assertEqual(report['human_visits_created'],0)
