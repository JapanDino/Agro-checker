"""Prevent leakage, budget relaxation and automatic promotion in decline research."""
import copy
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import numpy as np
from agro_checker.field_decline_experiment import chronological_folds,choose,gate,matrix,train,confusion,historical_vector,fit_experiment


def row(issue='2024-06-01',target='2024-06-08',event=False,fid='R01'):
    return dict(fid=fid,issue=issue,target=target,baseline='05.10',x=[.6,.2,-.01,-.01,5,0,1,20,2],
        features=[.6,.2,-.01,-.01,5,0,1]+[0.]*16,y=[.45 if event else .6,.2])


class DeclineIsolationTests(unittest.TestCase):
    def protocol(self):
        return dict(thresholds=[.5,.8,1.01],negative_alarm_budget=0,minimum_true_positives=2,minimum_event_dates=2)

    def test_fold_never_sees_targets_from_validation(self):
        rr=[row('2024-05-01','2024-05-08'),row('2024-05-28','2024-06-04'),
            row('2024-06-01','2024-06-08'),row('2024-06-28','2024-07-05')]
        _,fit,validation=next(chronological_folds(rr,['2024-06-01']))
        self.assertEqual(fit,[rr[0]])
        self.assertEqual(validation,[rr[2]])
        self.assertLess(max(r['target'] for r in fit),min(r['issue'] for r in validation))

    def test_future_year_rejected_by_training_and_selection(self):
        for yr in [2025,2026]:
            rr=[row(f'{yr}-06-01',f'{yr}-06-08')]
            with self.assertRaisesRegex(ValueError,'2024 only'):
                list(chronological_folds(rr,['2024-06-01']))
            with self.assertRaisesRegex(ValueError,'2024 only'):
                choose(rr,[],self.protocol())
            with self.assertRaisesRegex(ValueError,'2024'):
                train(dict(features='basic'),rr,0,{})

    def test_zero_fp_budget_is_hard_even_for_many_true_positives(self):
        rr=[row(event=True),row('2024-06-03','2024-06-10',True),row('2024-06-06','2024-06-13')]
        records=[dict(candidate=dict(id='candidate'),scores=[[.9,.9,.9],[.1,.1,.1]])]
        selected,_=choose(rr,records,self.protocol())
        self.assertFalse(selected[0]['enabled'])

    def test_single_date_cluster_cannot_pass_as_multiple_events(self):
        rr=[row(event=True),row(event=True,fid='R02')]
        records=[dict(candidate=dict(id='candidate'),scores=[[.9,.9],[.1,.1]])]
        selected,_=choose(rr,records,self.protocol())
        self.assertFalse(selected[0]['enabled'])

    def test_threshold_chosen_without_budget_increase(self):
        rr=[row(event=True),row('2024-06-03','2024-06-10',True),row('2024-06-06','2024-06-13')]
        records=[dict(candidate=dict(id='candidate'),scores=[[.9,.9,.7],[.1,.1,.1]])]
        selected,_=choose(rr,records,self.protocol())
        self.assertTrue(selected[0]['enabled'])
        self.assertEqual(selected[0]['threshold'],.8)
        self.assertEqual(selected[0]['oof_metrics']['fp'],0)

    def test_target_audit_and_field_id_are_not_features_or_gates(self):
        rr=[row()];other=copy.deepcopy(rr)
        other[0].update(y=[-1,-1],target='2026-01-01',audit=dict(common_delta=[-1,-1]),fid='R27')
        c=dict(features='history_pixels')
        np.testing.assert_equal(matrix(rr,c),matrix(other,c))
        for name in ['none','above_floor','joint_momentum','pixel_momentum']:
            np.testing.assert_equal(gate(rr,0,name),gate(other,0,name))

    def test_pixel_momentum_uses_past_pixel_fraction(self):
        rr=[row()];rr[0]['features'][18]=.5
        self.assertTrue(gate(rr,0,'pixel_momentum')[0])
        rr[0]['features'][18]=.1
        self.assertFalse(gate(rr,0,'pixel_momentum')[0])

    def test_report_counts_false_alarm_dates_separately(self):
        rr=[row(),row(fid='R02'),row('2024-06-03','2024-06-10')]
        m=confusion(rr,[False]*3,[True]*3)
        self.assertEqual(m['fp'],3)
        self.assertEqual(m['fp_dates'],2)

    def test_history_builder_excludes_future_and_other_fields(self):
        r=row();pixels=np.array([[.6,.2],[.61,.21]])
        valid=np.array([True,True])
        def obs(d,v,fid='R01'):
            return dict(fid=fid,baseline='05.10',date=d,values={'core':{'value':v}})
        history=[obs('2024-05-27',[.65,.25]),obs('2024-06-01',[.6,.2])]
        first=historical_vector(r,history,pixels,valid,pixels+.05,valid,1.)
        other=history+[obs('2024-06-08',[-1,-1]),obs('2024-05-20',[-1,-1],fid='R27')]
        second=historical_vector(r,other,pixels,valid,pixels+.05,valid,1.)
        np.testing.assert_allclose(first,second)

    def test_evaluated_experiment_cannot_be_retuned_in_place(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'report.json').write_text('{}',encoding='utf-8')
            with patch('agro_checker.field_decline_experiment.D',root):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):
                    fit_experiment(gated=True)


if __name__=='__main__':
    unittest.main()
