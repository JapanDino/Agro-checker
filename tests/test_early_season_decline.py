"""Leakage and provenance tests; fixtures never become observations."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np

from agro_checker.early_season_data import reject_reason,year_history
from agro_checker.early_season_decline import (validate_fit,selection2024,training_rows,weights,select,fit)
from agro_checker.conservative_decline import matrix
from agro_checker.crop_season_experiment import phase_features


def pair(year=2024, issue=None, target=None, fid='R01', event=True):
    x=[.6,.2,-.02,-.02,5,0,1]
    return dict(fid=fid,issue=issue or f'{year}-06-01',target=target or f'{year}-06-08',
        previous=f'{year}-05-27',baseline='05.00',x=x,features=x+[0.]*16,
        season=[0.]*17,y=[.4 if event else .6,.2])


def item(baseline='05.00',elevation=35):
    return dict(properties={'s2:processing_baseline':baseline,'view:sun_elevation':elevation},
        assets={k:{'proj:transform':[20,0,0,0,-20,200,0,0,1],'proj:shape':[10,10]} for k in ['scl','red','nir','swir16']})


class EarlySeasonTests(unittest.TestCase):
    def test_collection_baselines_are_explicit_not_numeric_at_least_five(self):
        p=dict(bounds_utm=[20,20,180,180],allowed_baselines=['05.00','05.10','05.11'])
        self.assertIsNone(reject_reason(item(),p))
        self.assertEqual(reject_reason(item('05.09'),p),'unsupported_processing_baseline')
        self.assertEqual(reject_reason(item('06.00'),p),'unsupported_processing_baseline')

    def test_reject_partial_grid_and_unknown_solar_geometry(self):
        p=dict(bounds_utm=[20,20,220,180],allowed_baselines=['05.00'])
        self.assertEqual(reject_reason(item(),p),'outside_full_grid')
        p['bounds_utm']=[20,20,180,180]
        self.assertEqual(reject_reason(item(elevation=19),p),'solar_elevation_below_20_or_unknown')
        self.assertEqual(reject_reason(item(elevation=None),p),'solar_elevation_below_20_or_unknown')

    def test_offset_flag_never_double_corrected(self):
        f=item();f['properties']['earthsearch:boa_offset_applied']=True
        self.assertEqual(reject_reason(f,dict(bounds_utm=[20,20,180,180],allowed_baselines=['05.00'])),'ambiguous_applied_offset')

    def test_earlier_years_fit_but_cannot_select(self):
        validate_fit([pair(2020),pair(2024)])
        with self.assertRaisesRegex(ValueError,'Selection requires2024'):
            selection2024([pair(2020),pair(2024)])

    def test_future_years_and_holdout_rejected(self):
        for year in [2025,2026]:
            with self.assertRaisesRegex(ValueError,'2019-2024'):
                validate_fit([pair(year)])
        with self.assertRaisesRegex(ValueError,'reserve'):
            validate_fit([pair(2020,fid='R27')])

    def test_earlier_source_cannot_include2024_or_cross_year_targets(self):
        contaminated=pair(2024,issue='2024-04-01',target='2024-04-08')
        contaminated['previous']='2024-03-27'
        with self.assertRaisesRegex(ValueError,'precede2024'):
            training_rows([pair()],[contaminated],'all_early_plus_2024')
        with self.assertRaisesRegex(ValueError,'same-year'):
            validate_fit([pair(2021,target='2024-06-08')])

    def test_latest_year_is_chosen_by_availability_not_labels(self):
        early=[pair(2019,event=True),pair(2021,event=False)]
        rr=training_rows([pair()],early,'nearest_early_plus_2024')
        self.assertEqual([r['issue'][:4] for r in rr],['2021','2024'])
        early[0]['y']=[-1,-1];early[1]['y']=[-1,-1]
        self.assertEqual([r['issue'][:4] for r in training_rows([pair()],early,'nearest_early_plus_2024')],['2021','2024'])

    def test_recency_weights_and_date_balance_are_explicit(self):
        rr=[pair(2020),pair(2020,fid='R02'),pair(2024)]
        w=weights(rr,'all_early_plus_2024')
        self.assertAlmostEqual(w[0]+w[1],w[2])
        w=weights(rr,'all_early_decay2_plus_2024')
        self.assertAlmostEqual((w[0]+w[1])/w[2],.25)
        self.assertAlmostEqual(w.mean(),1.)

    def test_season_features_never_take_previous_year_crop_peak(self):
        r=pair(2021)
        def obs(d,v):return dict(fid='R01',date=d,baseline='05.00',values={'core':{'value':v}})
        observations=[obs('2020-09-01',[.99,.8]),obs('2021-05-27',[.65,.25]),obs('2021-06-01',[.6,.2]),obs('2021-06-08',[1,1])]
        history=year_history(r,observations)
        a=phase_features(r,history)
        self.assertAlmostEqual(a[3],.65)
        self.assertEqual(a[0],5)

    def test_target_audits_never_change_inputs(self):
        rr=[pair()];changed=copy.deepcopy(rr)
        changed[0].update(y=[-1,-1],target='2026-01-01',audit={'future':100},fid='R27')
        for features in ['basic','history_season']:
            np.testing.assert_equal(matrix(rr,dict(features=features)),matrix(changed,dict(features=features)))

    def test_false_alarm_budget_and_multiple_dates_are_hard(self):
        rr=[pair(),pair(issue='2024-06-03',target='2024-06-10'),pair(issue='2024-06-05',target='2024-06-12',event=False)]
        p=dict(gates=['none'],thresholds=[.5,.8,1.01])
        records=[dict(regime='all_early_plus_2024',scores=[[.9,.9,.9],[.1,.1,.1]])]
        primary,_,_=select(rr,records,p)
        self.assertFalse(primary[0]['enabled'])
        records[0]['scores'][0]=[.9,.9,.7]
        primary,_,_=select(rr,records,p)
        self.assertEqual(primary[0]['threshold'],.8)
        self.assertEqual(primary[0]['metrics']['fp'],0)
        rr[1]['issue']=rr[0]['issue'];rr[1]['target']=rr[0]['target'];rr[1]['fid']='R02'
        primary,_,_=select(rr,records,p)
        self.assertFalse(primary[0]['enabled'])

    def test_primary_ties_prefer_control_not_extra_years(self):
        rr=[pair(),pair(issue='2024-06-03',target='2024-06-10')]
        p=dict(gates=['none'],thresholds=[.5,.8])
        records=[dict(regime=k,scores=[[.9,.9],[.1,.1]]) for k in ['2024_only','all_early_plus_2024']]
        primary,_,_=select(rr,records,p)
        self.assertEqual(primary[0]['regime'],'2024_only')

    def test_frozen_fit_cannot_be_repeated(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'frozen-selection.json').write_text('{}',encoding='utf-8')
            with patch('agro_checker.early_season_decline.D',path):
                with self.assertRaisesRegex(ValueError,'Cannot refit'):
                    fit()


if __name__=='__main__':unittest.main()
