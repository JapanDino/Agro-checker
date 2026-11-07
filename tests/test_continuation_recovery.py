"""Arithmetic fixtures, never reported as agronomist observations."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker import continuation_recovery as m
from tests.test_seasonal_calibration import row


def observation(date,values,fid='R01',baseline='05.10',coverage=.95):
    return dict(date=date,values=dict(core=dict(value=values,coverage=coverage)),fid=fid,
        baseline=baseline,scene_id=date,acquired_at_utc=date)


def history():
    return [observation('2024-05-17',[.8,.4]),observation('2024-05-27',[.7,.3]),
        observation('2024-06-01',[.6,.2])]


def example(day=1,event=True):
    r=row(day,event);r['raw_score']=[.8,.8]
    r['continuation']=m.temporal_features(r,history()) if day==1 else [0.]*len(m.TEMPORAL)
    r['pixel_continuation']=[0.]*len(m.PIXELS)
    return r


def protocol():
    return dict(families=m.FAMILIES,thresholds=[0.,.5,.7,.9],
        base_choices=[dict(threshold=.5,gate='joint_momentum')]*2,
        support=dict(min_pairs=4,min_dates=2,min_positive_dates=2,min_negative_dates=2),
        fixed_parameters=dict(c=.1,extra_trees=16,max_depth=3,min_leaf=2,seed=1,threads=1))


class ContinuationTests(unittest.TestCase):
    def test_temporal_whitelist_ignores_future_targets_other_fragments_and_versions(self):
        r=example();h=history();original=m.temporal_features(r,h)
        poisoned=copy.deepcopy(r);poisoned.update(y=[1,1],target='2026-12-31',audit={'common_delta':[100,100]})
        h += [observation('2024-06-03',[-1,-1]),observation('2024-05-30',[-1,-1],fid='other'),
            observation('2024-05-30',[-1,-1],baseline='05.11'),observation('2023-06-01',[-1,-1])]
        np.testing.assert_array_equal(m.temporal_features(poisoned,h),original)

    def test_unequal_lags_use_per_day_rates_and_acceleration(self):
        r=example();r['x'][2:4]=[-.02,-.02]
        x=m.temporal_features(r,history())
        np.testing.assert_allclose(x[4:6],[-.07,-.07])
        np.testing.assert_allclose(x[6:8],[-.07,-.07])
        self.assertEqual(x[21],10/7)

    def test_negative_run_stops_at_latest_recovery(self):
        r=example();h=history()+[observation('2024-05-22',[.6,.2])]
        x=m.temporal_features(r,h)
        np.testing.assert_allclose(x[12:14],[.1,.1])

    def test_missing_third_keeps_finite_schema_and_support_flag(self):
        r=example();x=m.temporal_features(r,history()[1:])
        self.assertEqual(len(x),len(m.TEMPORAL));self.assertEqual(x[22],0.)
        np.testing.assert_array_equal(x[4:8],0.)

    def test_third_never_uses_one_day_neighbor_or_future(self):
        r=example();h=history()+[observation('2024-05-26',[0,0]),observation('2024-06-02',[0,0])]
        self.assertEqual(m.third_observation(r,m.past_history(r,h))['date'],'2024-05-17')

    def test_pixels_use_only_same_valid_support(self):
        r=example();third=history()[0]
        a=np.tile([.6,.2],(20,1));b=np.tile([.7,.3],(20,1));c=np.tile([.8,.4],(20,1))
        valid=np.ones(20,bool);valid[0]=False
        z=m.pixel_features(r,third,(a,valid),(b,valid),(c,valid))
        a[0]=999;b[0]=-999
        np.testing.assert_array_equal(z,m.pixel_features(r,third,(a,valid),(b,valid),(c,valid)))
        np.testing.assert_allclose(z[:4],[-.14,-.14,-.07,-.07],atol=1e-12)
        self.assertEqual(z[-1],1.)

    def test_bad_triple_support_is_missing_not_removed(self):
        r=example();a=np.ones((20,2));valid=np.zeros(20,bool);valid[:8]=True
        z=m.pixel_features(r,history()[0],(a,valid),(a,valid),(a,valid))
        self.assertEqual(len(z),len(m.PIXELS));self.assertEqual(z[-1],0.)
        self.assertEqual(z[-2],.4)

    def test_model_whitelist_ignores_labels_and_feature_audit(self):
        a=example();b=copy.deepcopy(a);b.update(y=[-1,-1],audit={'future':100},feature_audit={'future':100},fid='other',target='2026-01-01')
        for family in m.FAMILIES[1:]:np.testing.assert_array_equal(m.matrix([a],family),m.matrix([b],family))

    def test_fit_rejects_future_year_and_reserved_fragments(self):
        for value in ['2025','2026']:
            r=example();r['issue']=value+'-06-01';r['target']=value+'-06-08'
            with self.assertRaisesRegex(ValueError,'2024'):m.fit_filter([r],0,'temporal_logit',protocol())
        r=example();r['fid']='R27'
        with self.assertRaisesRegex(ValueError,'reserve'):m.fit_filter([r],0,'temporal_logit',protocol())

    def test_fallback_never_filters_when_classes_unsupported(self):
        rr=[example(1,False),example(2,False)]
        fitted=m.fit_filter(rr,0,'recovery_logit',protocol())
        np.testing.assert_array_equal(m.score_filter(fitted,rr),[1.,1.])

    def test_recovery_head_does_not_call_small_declines_recovery(self):
        rr=[example(i,i%2==1) for i in range(1,7)]
        # All non-events here are unchanged; not one true >=+.03 recovery.
        fitted=m.fit_filter(rr,0,'recovery_logit',protocol())
        self.assertIn('constant',fitted)

    def test_selector_preserves_every_found_case_not_only_tp_count(self):
        rr=[example(i,i<3) for i in range(1,5)]
        records=[dict(family='temporal_logit',scores=[[.1,.9,.1,.1]]*2)]
        selected,_=m.choose(rr,records,protocol())
        self.assertEqual(selected[0]['family'],'identity')

    def test_selector_requires_strict_benefit_and_no_new_alerts(self):
        rr=[example(i,True) for i in range(1,4)]
        selected,_=m.choose(rr,[dict(family='temporal_logit',scores=[[.9]*3]*2)],protocol())
        self.assertFalse(selected[0]['enabled'])

    def test_selector_finds_lossless_filter_and_prefers_lower_threshold(self):
        rr=[example(i,i<3) for i in range(1,5)]
        selected,_=m.choose(rr,[dict(family='temporal_logit',scores=[[.9,.9,.1,.1]]*2)],protocol())
        self.assertEqual(selected[0]['threshold'],.5)
        self.assertEqual(selected[0]['selection']['lost_tp'],0)
        self.assertEqual(selected[0]['selection']['metrics']['fp'],0)

    def test_frozen_experiment_cannot_refit(self):
        with TemporaryDirectory() as folder:
            path=Path(folder);(path/'frozen-selection.json').write_text('{}')
            with patch.object(m,'D',path):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):m.fit()


if __name__=='__main__':unittest.main()
