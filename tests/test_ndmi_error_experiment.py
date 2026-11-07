"""Synthetic technical fixtures only; no agronomist records are created."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker import ndmi_error_experiment as m


def row(issue='2024-08-01',target='2024-08-08',y=-.1):
    return dict(fid='R01',issue=issue,target=target,x=[.5,.3,0,-.01,7,0,1,5,2],
                y=[.5,y],residual=[0,y-.3])


class NdmiErrorOnlyTests(unittest.TestCase):
    def test_reject_future_and_holdout_labels(self):
        for rr in ([row('2026-08-01','2026-08-08')],[dict(row(),fid='R23')]):
            with self.assertRaises(ValueError):m.calibrate(dict(spec=dict(kind='legacy')),rr)

    def test_features_cannot_see_future_decline(self):
        a=[row()];b=deepcopy(a);b[0].update(target='2026-08-08',y=[-1,1],residual=[999,999])
        np.testing.assert_array_equal(m.features(a),m.features(b))

    def test_legacy_bounds_calibration_identity(self):
        model=dict(spec=dict(kind='legacy'),constant=[-.23,.23])
        cal=m.calibrate(model,[row()]);self.assertEqual(cal['adjustment'],[0,0])
        low,high=m.bounds(model,cal,[row()],np.array([[.5,.3]]))
        np.testing.assert_allclose([low[0],high[0]],[.07,.53])

    def test_small_event_support_is_explicit(self):
        model=dict(spec=dict(kind='event_envelope'),constant=[-.1,.1])
        cal=m.calibrate(model,[row()]);self.assertTrue(cal['event_maximum_fallback'])
        self.assertGreaterEqual(cal['adjustment'][0],.3-1e-12)

    def test_empty_event_group_is_unknown(self):
        rr=[row(y=.3)]
        q=m.metrics(rr,np.array([.2]),np.array([.4]))
        self.assertIsNone(q['decline_coverage']);self.assertEqual(q['decline_pairs'],0)

    def test_event_only_lower_fit_not_inference(self):
        model=dict(spec=dict(kind='event_envelope'),constant=[-.4,.1]);cal=dict(adjustment=[0,0])
        a=[row()];b=[row(y=.9)]
        np.testing.assert_array_equal(m.bounds(model,cal,a,np.array([[.5,.3]])),m.bounds(model,cal,b,np.array([[.5,.3]])))

    def test_each_fold_must_pass_not_only_average(self):
        good=dict(coverage=.95,decline_coverage=.95,mean_width=.4,weighted_score=.8)
        table={name:dict(pooled=deepcopy(good),folds={'a':deepcopy(good),'b':deepcopy(good)}) for name in ['legacy','new']}
        table['new']['pooled']['weighted_score']=.5
        table['new']['folds']['a']['decline_coverage']=.5
        winner,_=m.choose(table,dict(coverage_floor=.9,decline_floor=.9,width_limit=1.1))
        self.assertEqual(winner,'legacy');self.assertIn('a: decline',table['new']['rejection_reasons'])

    def test_width_guard(self):
        good=dict(coverage=1.,decline_coverage=1.,mean_width=.4,weighted_score=.8)
        table={name:dict(pooled=deepcopy(good),folds={'a':deepcopy(good)}) for name in ['legacy','new']}
        table['new']['pooled'].update(mean_width=.6,weighted_score=.1)
        self.assertEqual(m.choose(table,dict(coverage_floor=.9,decline_floor=.9,width_limit=1.1))[0],'legacy')

    def test_hybrid_errors_do_not_include_in_sample_point_errors(self):
        early=row('2024-06-01','2024-06-08');late=row('2024-08-01','2024-08-08')
        reference=dict(early,residual=[.123,.234],point_oof=[.1,.1])
        old=dict(selected_model=dict(name='persistence',fit_max_target='2024-07-21'))
        with patch.object(m,'causal_oof',return_value=([reference],[])):
            out,_=m.residual_archive([early,late],old)
        self.assertEqual(out[0]['residual'],[.123,.234])
        self.assertEqual(out[0]['residual_origin'],'causal_reference_oof')
        self.assertEqual(out[1]['residual_origin'],'frozen_point_out_of_fit')
        np.testing.assert_allclose(out[1]['residual'],[0,-.4])

    def test_fit_locked_after_freeze(self):
        with patch.object(m,'D',m.ROOT/'data/farm-research/full-season-intervals'):
            with self.assertRaisesRegex(ValueError,'locked'):m.fit()


if __name__=='__main__':unittest.main()
