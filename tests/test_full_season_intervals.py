"""Technical synthetic fixtures; no fabricated field observations are persisted."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker import full_season_intervals as m


def row(issue='2024-05-01',target='2024-05-08',fid='R01',y=.2):
    return dict(fid=fid,issue=issue,target=target,x=[.5,.3,-.01,-.01,7,0,1,1,2],
                y=[y,y],point_oof=[.5,.3],residual=[y-.5,y-.3])


class FullSeasonIntervalTests(unittest.TestCase):
    def test_rejects_future_labels_and_reserved_fields(self):
        for rr in ([row(issue='2026-05-01',target='2026-05-08')],[row(target='2025-01-01')],[row(fid='R23')]):
            with self.assertRaises(ValueError):m.train_point(rr)

    def test_duplicate_keys_not_extra_samples(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):m.only2024([row(),row()])

    def test_tail_features_do_not_use_future_event_or_label(self):
        rr=[row()];other=deepcopy(rr);other[0].update(y=[-1,-1],residual=[-9,-9],target='2026-01-01')
        np.testing.assert_array_equal(m.features(rr),m.features(other))

    def test_oof_fit_strictly_purges_target_overlap(self):
        rows=[row(f'2024-05-{i:02}',f'2024-05-{i+7:02}',f'R{j:02}') for i in range(1,19) for j in range(1,5)]
        _,folds=m.causal_oof(rows)
        self.assertTrue(folds)
        self.assertTrue(all(f['fit_max_target']<f['issue'] for f in folds))

    def test_meta_fit_and_calibration_and_outer_disjoint(self):
        rows=[row(f'2024-06-{i:02}',f'2024-06-{i+7:02}',f'R{j:02}') for i in range(1,24) for j in range(1,5)]
        fit,calib=m.meta_split(rows,'2024-07-01')
        self.assertLess(max(r['target'] for r in fit),min(r['issue'] for r in calib))
        self.assertLess(max(r['target'] for r in calib),'2024-07-01')
        self.assertFalse(set(map(m.key,fit))&set(map(m.key,calib)))

    def test_weighted_quantile_keeps_equal_date_weight(self):
        self.assertEqual(m.weighted_quantile([1,2,3],[1,1,10],.5),3)
        with self.assertRaises(ValueError):m.weighted_quantile([],[],.5)

    def test_rare_calibration_events_explicit(self):
        model=dict(spec='event_weighted',constant=[[-.05,.05],[-.05,.05]])
        calib=m.calibrate_tails(model,[row()])
        self.assertTrue(calib['support'][0]['event_maximum_fallback'])
        self.assertGreaterEqual(calib['adjustment'][0][0],.25-1e-12)

    def test_bounds_cannot_see_target(self):
        model=dict(spec='global_asymmetric',constant=[[-.1,.05],[-.2,.1]])
        cal=dict(adjustment=[[.1,0],[0,.1]])
        rr=[row()];other=deepcopy(rr);other[0]['y']=[-1,1]
        a=m.bounds(model,cal,rr,np.array([[.5,.3]]));b=m.bounds(model,cal,other,np.array([[.5,.3]]))
        np.testing.assert_array_equal(a,b)

    def test_empty_decline_group_does_not_get_perfect_coverage(self):
        rr=[row(y=.5)];q=m.interval_metrics(rr,np.array([[.5,.5]]),np.array([[.4,.4]]),np.array([[.6,.6]]))
        self.assertIsNone(q['per_index']['ndvi']['decline_coverage'])

    def test_selection_enforces_width_and_coverage(self):
        good=dict(coverage=.95,decline_coverage=.9,mean_width=.2)
        table={s:dict(per_index={j:deepcopy(good) for j in ['ndvi','ndmi']},weighted_interval_score=i+1)
               for i,s in enumerate(m.SPECS)}
        table['conditional']['per_index']['ndvi']['mean_width']=.4
        table['event_weighted']['per_index']['ndmi']['decline_coverage']=.5
        p=dict(interval_candidates=m.SPECS,minimum_overall_coverage=.9,minimum_decline_coverage=.85,maximum_width_ratio_vs_date_max=1.1)
        choice,passed=m.select_candidate(table,p)
        self.assertTrue(passed);self.assertEqual(choice,'date_max')
        self.assertTrue(table['conditional']['selection_rejection_reasons'])
        self.assertTrue(table['event_weighted']['selection_rejection_reasons'])

    def test_fit_locked_after_freeze(self):
        with patch.object(m,'D',m.ROOT/'data/farm-research/field-index-model'):
            with self.assertRaisesRegex(ValueError,'locked'):m.fit()


if __name__=='__main__':unittest.main()
