"""Temporal/spatial isolation and missing-label rules for the research experiment."""
import copy
import unittest
import numpy as np
from agro_checker.field_index_experiment import make_pairs, partition, date_weights, fit_model, predict, calibrate


def observation(d, value=.4, baseline='05.10', fid='R01'):
    return dict(fid=fid,date=d,scene_id=d,baseline=baseline,terrain=[20,2],
        values={'core':dict(value=[value,value/2],coverage=1)})


def pair(issue='2024-05-01', target='2024-05-08',fid='R01'):
    return dict(fid=fid,issue=issue,target=target,x=[.4,.2,.01,.005,5,0,1,20,2],y=[.45,.23])


class FieldIndexIsolationTests(unittest.TestCase):
    def test_future_value_never_in_features(self):
        rows=[observation('2024-05-01'),observation('2024-05-06',.5),observation('2024-05-13',.6)]
        first=make_pairs(rows)
        rows[-1]['values']['core']['value']=[-.9,-.7]
        second=make_pairs(rows)
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]['x'],second[0]['x'])
        self.assertNotEqual(first[0]['y'],second[0]['y'])

    def test_no_interpolated_or_wrong_baseline_target(self):
        rows=[observation('2024-05-01'),observation('2024-05-06'),observation('2024-05-13',baseline='05.11')]
        self.assertEqual(make_pairs(rows),[])
        rows[-1]['date']='2024-05-20';rows[-1]['baseline']='05.10'
        self.assertEqual(make_pairs(rows),[])

    def test_target_chosen_by_time_not_value(self):
        rows=[observation('2024-05-01'),observation('2024-05-06'),observation('2024-05-12',-.9),observation('2024-05-13',.9)]
        found=next(r for r in make_pairs(rows) if r['issue']=='2024-05-06')
        self.assertEqual(found['target'],'2024-05-13')

    def test_boundary_crossing_pairs_are_purged(self):
        rr=[pair('2024-05-28','2024-06-04'),pair('2024-07-28','2024-08-04'),pair('2024-06-01','2024-06-08')]
        out=partition(rr,[])
        self.assertEqual(len(out['selection']),1)
        self.assertEqual(sum(len(v) for v in out.values()),1)

    def test_spatial_and_future_labels_never_fit_or_calibrate(self):
        rr=[pair(fid='R27'),pair('2024-08-01','2024-08-08',fid='R27'),pair('2026-08-01','2026-08-08'),pair()]
        out=partition(rr,['R27'])
        self.assertEqual(out['train'],[rr[-1]])
        self.assertEqual(out['calibration'],[])
        self.assertEqual(len(out['spatial2024']),2)
        self.assertEqual(len(out['temporal2026']),1)

    def test_equal_date_weight_despite_fragment_counts(self):
        rr=[pair(),pair(fid='R02'),pair('2024-05-02','2024-05-09')]
        np.testing.assert_allclose(date_weights(rr),[.5,.5,1])

    def test_predict_uses_only_frozen_parameters_and_features(self):
        rr=[pair(),pair('2024-05-02','2024-05-09')]
        model=fit_model('temporal_ridge_1',rr)
        other=copy.deepcopy(rr)
        for r in other:
            r['y']=[-1,-1];r['target']='2026-01-01';r['fid']='OTHER'
        np.testing.assert_allclose(predict(model,rr),predict(model,other))

    def test_interval_rejects_too_few_dates(self):
        rr=[pair(f'2024-08-{i:02}',f'2024-08-{i+7:02}') for i in range(1,9)]
        with self.assertRaisesRegex(ValueError,'Too few'):
            calibrate({'name':'persistence'},rr)


if __name__=='__main__':
    unittest.main()
