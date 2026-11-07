"""Calibration/veto fixtures are arithmetic only, never agronomist observations."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np

from agro_checker.seasonal_calibration import (FAMILIES,fit_calibrator,calibrate,context,veto,
    probability_metrics,detection_metrics,choose,fit)


def row(day=1,event=True,fid='R01',year=2024,raw=.8):
    x=[.6,.2,-.02,-.02,5,0,1]
    s=[40,7,1,.8,.4,.2,-.2,7,7,-.2,-.2,2/3,2/3,1,1,-.01,-.01]
    return dict(fid=fid,issue=f'{year}-06-{day:02}',target=f'{year}-06-{day+7:02}',previous=f'{year}-05-27',
        baseline='05.10',x=x,features=x+[0.]*16,season=s,y=[.4 if event else .6,.0 if event else .2],raw_score=[raw,raw])


def protocol():
    return dict(families=FAMILIES,thresholds=[0.,.5,.7,.9],reliability_bins=[0.,.1,.2,.4,.6,.8,1.],
        base_choices=[dict(threshold=.5,gate='joint_momentum')]*2,
        calibration_support=dict(min_pairs=4,min_dates=2,min_positive_dates=2,min_negative_dates=2),
        fixed_parameters=dict(probability_clip=1e-5,temperature_bounds=[.25,4.],monotonic_penalty=.01,
            conditional_penalty=.1,maxiter=500,ftol=1e-12))


def rows():
    return [row(1,True,'A',raw=.6),row(2,False,'A',raw=.9),row(3,True,'A',raw=.8),row(4,False,'A',raw=.2)]


class SeasonalCalibrationTests(unittest.TestCase):
    def test_future_and_reserve_labels_cannot_calibrate(self):
        for year in [2025,2026]:
            with self.assertRaisesRegex(ValueError,'2024'):
                fit_calibrator([row(year=year)],0,'platt',protocol())
        with self.assertRaisesRegex(ValueError,'reserve'):
            fit_calibrator([row(fid='R27')],0,'platt',protocol())

    def test_poor_class_support_returns_identity_no_synthetic_positives(self):
        rr=[row(1,False),row(2,False),row(3,False),row(4,False)]
        m=fit_calibrator(rr,0,'platt',protocol())
        self.assertEqual(m['family'],'identity')
        np.testing.assert_array_equal(calibrate(m,rr,[.1]*4,0,protocol()),[.1]*4)

    def test_positive_pairs_same_date_not_independent_calibration_support(self):
        rr=[row(fid='A'),row(fid='B'),row(2,False),row(3,False)]
        self.assertEqual(fit_calibrator(rr,0,'beta',protocol())['family'],'identity')

    def test_monotone_methods_do_not_reverse_rank(self):
        rr=rows();raw=np.linspace(.01,.99,100);evaluation=[row() for _ in raw]
        for family in ['temperature','platt','beta','isotonic_shrink50']:
            m=fit_calibrator(rr,0,family,protocol());q=calibrate(m,evaluation,raw,0,protocol())
            self.assertTrue(np.all(np.diff(q)>=-1e-12))
            self.assertTrue(np.all((q>=0)&(q<=1)))

    def test_veto_can_never_add_alerts_even_when_calibration_high(self):
        old=[True,False,True,False];q=[.1,.99,.8,.99]
        np.testing.assert_array_equal(veto(old,q,.5),[False,False,True,False])

    def test_missing_probabilities_do_not_silently_remove_rows(self):
        with self.assertRaisesRegex(ValueError,'Missing probability'):
            veto([True,True],[.9,np.nan],.5)

    def test_detection_guard_tracks_lost_cases_not_only_tp_count(self):
        rr=rows()
        m=detection_metrics(rr,0,[True,False,True,False],[False,False,True,False],[.1,.2,.8,.1],protocol())
        self.assertEqual(m['lost_tp'],1)

    def test_selection_must_keep_every_existing_tp(self):
        rr=rows();q=[.1,.1,.9,.1]
        # Any threshold removing the false alarm also loses the first true event.
        chosen,_=choose(rr,[dict(family='identity',scores=[q,q])],protocol())
        self.assertEqual(chosen[0]['threshold'],0.)
        self.assertEqual(chosen[0]['selection_metrics']['lost_tp'],0)
        self.assertEqual(chosen[0]['selection_metrics']['removed_fp'],0)

    def test_no_false_alarm_evidence_prefers_no_veto(self):
        rr=[row(1,True,raw=.8),row(2,True,raw=.9),row(3,False,raw=.1),row(4,False,raw=.2)]
        record=dict(family='identity',scores=[[r['raw_score'][j] for r in rr] for j in range(2)])
        chosen,_=choose(rr,[record],protocol())
        self.assertTrue(chosen[0]['enabled'])
        self.assertEqual(chosen[0]['threshold'],0.)

    def test_honest_threshold_can_remove_fp_without_losing_tp(self):
        rr=[row(1,True,raw=.9),row(2,True,raw=.8),row(3,False,raw=.6),row(4,False,raw=.2)]
        record=dict(family='identity',scores=[[r['raw_score'][j] for r in rr] for j in range(2)])
        chosen,_=choose(rr,[record],protocol())
        self.assertEqual(chosen[0]['threshold'],.7)
        self.assertEqual(chosen[0]['selection_metrics']['lost_tp'],0)
        self.assertEqual(chosen[0]['selection_metrics']['metrics']['fp'],0)

    def test_conditional_features_and_transform_ignore_future_outcomes(self):
        rr=rows();m=fit_calibrator(rr,0,'conditional',protocol());other=copy.deepcopy(rr)
        for r in other:r.update(y=[-1,-1],target='2026-01-01',audit={'future':100},fid='R27')
        np.testing.assert_array_equal(context(rr,0),context(other,0))
        raw=[r['raw_score'][0] for r in rr]
        np.testing.assert_allclose(calibrate(m,rr,raw,0,protocol()),calibrate(m,other,raw,0,protocol()))

    def test_probability_quality_uses_equal_date_mass(self):
        rr=[row(1,False,'A'),row(1,False,'B'),row(2,False,'A')]
        m=probability_metrics(rr,0,[0,0,1],protocol())
        self.assertAlmostEqual(m['brier'],.5)

    def test_boundary_probabilities_have_finite_logloss(self):
        rr=[row(1,True),row(2,False)]
        m=probability_metrics(rr,0,[0,1],protocol())
        self.assertTrue(np.isfinite(m['logloss']))

    def test_source_detector_raw_scores_not_overwritten(self):
        rr=rows();saved=copy.deepcopy(rr);m=fit_calibrator(rr,0,'isotonic_shrink50',protocol())
        calibrate(m,rr,[r['raw_score'][0] for r in rr],0,protocol())
        self.assertEqual(rr,saved)

    def test_no_refit_after_frozen_selection(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'frozen-selection.json').write_text('{}',encoding='utf-8')
            with patch('agro_checker.seasonal_calibration.D',path):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):fit()


if __name__=='__main__':unittest.main()
