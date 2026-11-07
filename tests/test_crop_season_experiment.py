"""Unknown crop truth, causal seasonal history and 2024-only fitting constraints."""
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker.crop_season_experiment import phase_features,calendar_features,train,select,freeze,matrix


def row(issue='2024-06-01',target='2024-06-08',event=False,fid='R01'):
    return dict(fid=fid,issue=issue,target=target,baseline='05.10',x=[.6,.2,-.01,-.01,5,0,1,20,2],
        features=[.6,.2,-.01,-.01,5,0,1]+[0.]*16,season=[0.]*17,calendar=[0.]*7,
        y=[.45 if event else .6,.2])


def obs(d,v,fid='R01',baseline='05.10'):
    return dict(fid=fid,date=d,baseline=baseline,values=dict(core=dict(value=v)))


def calendar(sos=347,eos=191,weight=1.):
    return dict(weight=weight,properties={'tc-wintercereals_sos':sos,'tc-wintercereals_eos':eos,
        'tc-maize-main_sos':116,'tc-maize-main_eos':294})


class CropSeasonTests(unittest.TestCase):
    def test_future_peak_never_becomes_issue_time_phase(self):
        rr=[obs('2024-05-01',[.4,.1]),obs('2024-05-15',[.7,.3]),obs('2024-06-01',[.6,.2])]
        a=phase_features(row(),rr)
        b=phase_features(row(),rr+[obs('2024-06-08',[.99,.95]),obs('2024-05-10',[1,1],fid='R27')])
        np.testing.assert_equal(a,b)
        self.assertAlmostEqual(a[3],.7)
        self.assertEqual(a[7],17)

    def test_processing_change_resets_history_support(self):
        rr=[obs('2024-01-01',[.8,.4]),obs('2024-06-01',[.6,.2],baseline='05.11')]
        r=row();r['baseline']='05.11'
        features=phase_features(r,rr)
        self.assertEqual(features[0],0)
        self.assertEqual(features[2],0)

    def test_flat_history_has_no_invented_relative_phase(self):
        rr=[obs('2024-05-01',[.6,.2]),obs('2024-06-01',[.6,.2])]
        features=phase_features(row(),rr)
        self.assertTrue(np.isfinite(features).all())
        self.assertEqual(features[11:15],[0.,0.,0,0])
        self.assertEqual(features[2],0)

    def test_overlap_is_unknown_not_a_probability_mixture(self):
        result=calendar_features('2024-06-01',[calendar(),calendar(sos=42,eos=222)])
        self.assertEqual(result,[0.]*7)

    def test_circular_winter_season_and_leap_date(self):
        # Winter calendar spans the year boundary and includes January.
        values=calendar_features('2024-01-10',[calendar()])
        self.assertEqual(values[0],1.)
        self.assertEqual(values[-1],1.)
        np.testing.assert_allclose(calendar_features('2024-03-01',[calendar()]),calendar_features('2023-03-01',[calendar()]))

    def test_selection_and_fit_reject_future_years(self):
        for year in [2025,2026]:
            rr=[row(f'{year}-06-01',f'{year}-06-08')]
            with self.assertRaisesRegex(ValueError,'2024'):
                train(rr,0,{},dict(holdout_ids=[]))
            with self.assertRaisesRegex(ValueError,'2024'):
                select(rr,[],{},[{},{}])

    def test_fit_rejects_spatial_holdout_even_in_2024(self):
        with self.assertRaisesRegex(ValueError,'Spatial holdout'):
            train([row(fid='R27')],0,{},dict(holdout_ids=['R27']))

    def test_no_false_alarms_is_hard_constraint(self):
        rr=[row(event=True),row('2024-06-03','2024-06-10',True),row('2024-06-06','2024-06-13')]
        records=[dict(candidate=dict(id='candidate'),scores=[[.9,.9,.9],[.1,.1,.1]])]
        p=dict(gate='joint_momentum',thresholds=[.6,.8])
        chosen,_=select(rr,records,p,[dict(date_balanced_recall=0),{}])
        self.assertFalse(chosen[0]['enabled'])

    def test_recall_floor_prevents_achieving_precision_by_silence(self):
        rr=[row(event=True),row('2024-06-03','2024-06-10',True),row('2024-06-06','2024-06-13',True)]
        records=[dict(candidate=dict(id='candidate'),scores=[[.9,.9,.1],[.1,.1,.1]])]
        p=dict(gate='joint_momentum',thresholds=[.6])
        chosen,_=select(rr,records,p,[dict(date_balanced_recall=1),{}])
        self.assertFalse(chosen[0]['enabled'])

    def test_target_crop_and_audit_never_enter_matrix(self):
        r=row();a=matrix([r],dict(family='calendar_season'))
        r.update(crop='invented',actual_phase='invented',audit={'future':100},y=[-1,-1])
        np.testing.assert_equal(a,matrix([r],dict(family='calendar_season')))

    def test_no_retuning_after_test_report(self):
        with TemporaryDirectory() as folder:
            p=Path(folder);(p/'report.json').write_text('{}',encoding='utf-8')
            with patch('agro_checker.crop_season_experiment.D',p):
                with self.assertRaisesRegex(ValueError,'retuned'):
                    freeze()


if __name__=='__main__':
    unittest.main()
