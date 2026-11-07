"""Leakage, domain-weighting and unseen-version arithmetic, not field labels."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np

from agro_checker.season_version_decline import (SPECS,train,training_weights,overlap_weights,
    season_matrix,design,score,fit,select2024)


def row(year=2024,fid='R01',issue=None,target=None,event=True,baseline='05.10'):
    x=[.6,.2,-.02,-.01,5,0,1]
    # span/count/support, peak2,floor2,since2,drawdown2,relative2,support2,slopes2
    season=[40,7,1,.8,.4,.2,-.2,7,7,-.2,-.2,2/3,2/3,1,1,-.01,-.01]
    return dict(fid=fid,issue=issue or f'{year}-06-01',target=target or f'{year}-06-08',
        previous=f'{year}-05-27',baseline=baseline,x=x,features=x+[0.]*16,season=season,
        y=[.4 if event else .6,.2])


def protocol():
    return dict(algorithms=[dict(features='basic',family='logit',c=1.)]*2,
        fixed_hyperparameters=dict(seed=1729,extra_trees=16,extra_max_depth=3,threads=1),
        source_cap=.25,candidates=SPECS,gates=['none'],thresholds=[.5,.8,1.01])


class BaselineResponse:
    def predict_proba(self,x):
        prob=.1*x[:,-3]+.2*x[:,-2]+.8*x[:,-1]
        return np.column_stack([1-prob,prob])


class SeasonVersionTests(unittest.TestCase):
    def test_future_training_and_reserve_rejected(self):
        for year in [2025,2026]:
            with self.assertRaisesRegex(ValueError,'2019-2024'):
                train([row(year)],0,SPECS[0],protocol())
        with self.assertRaisesRegex(ValueError,'reserve'):
            train([row(fid='R27')],0,SPECS[0],protocol())

    def test_control_excludes_source_years(self):
        rr=[row(2020,baseline='05.00'),row()]
        m=train(rr,0,SPECS[0],protocol())
        self.assertEqual(m['fit_years'],['2024'])
        self.assertEqual(m['fit_pairs'],1)
        self.assertEqual(m['weight_info']['source_weight_share'],0)

    def test_source_cap_and_effective_mass(self):
        rr=[row(2020),row(2020,fid='R02'),row(2021),row(),row(fid='R02')]
        spec=next(c for c in SPECS if c['id']=='balanced25')
        w,a=training_weights(rr,spec,protocol())
        self.assertAlmostEqual(w[:3].sum()/w.sum(),.25)
        self.assertAlmostEqual(w.sum(),2.)
        self.assertAlmostEqual(a['source_weight_share'],.25)

    def test_fixed_mass_control_keeps_pool_proportions(self):
        rr=[row(2020),row(2021),row()]
        pooled=next(c for c in SPECS if c['id']=='raw_pool')
        fixed=next(c for c in SPECS if c['id']=='pool_fixed_mass')
        a,_=training_weights(rr,pooled,protocol());b,_=training_weights(rr,fixed,protocol())
        np.testing.assert_allclose(a/a.sum(),b/b.sum())
        self.assertAlmostEqual(b.sum(),1.)

    def test_overlap_fitting_never_uses_event_targets(self):
        rr=[row(2020),row(2021,fid='R02'),row(),row(fid='R02',event=False)]
        changed=copy.deepcopy(rr)
        for r in changed:r['y']=[-1,-1];r['audit']={'future':100}
        a,_=overlap_weights(rr);b,_=overlap_weights(changed)
        np.testing.assert_allclose(a,b,rtol=0,atol=1e-12)
        self.assertTrue(np.all((a[:2]>=.1)&(a[:2]<=10)))

    def test_overlap_cannot_bypass_source_cap(self):
        rr=[row(2020),row(2021),row(),row(fid='R02',event=False)]
        spec=next(c for c in SPECS if c['id']=='overlap_season_version25')
        w,a=training_weights(rr,spec,protocol())
        self.assertAlmostEqual(w[:2].sum()/w.sum(),.25)
        self.assertEqual(a['overlap']['fit_max_target'],'2024-06-08')

    def test_supported_relative_slopes_and_flat_history(self):
        r=row();s=season_matrix([r])[0]
        self.assertAlmostEqual(s[6],-.14/.6)
        r['season'][3:7]=[.4,.2,.4,.2];r['season'][13:15]=[0,0]
        z=season_matrix([r])[0]
        self.assertTrue(np.isfinite(z).all())
        np.testing.assert_array_equal(z[6:10],[0,0,0,0])

    def test_metadata_does_not_shift_observed_indices(self):
        a=row(baseline='05.00');b=row(baseline='05.11')
        spec=next(c for c in SPECS if c['id']=='version25')
        x=design([a,b],0,spec,protocol())
        np.testing.assert_array_equal(x[0,:7],x[1,:7])
        np.testing.assert_array_equal(x[0,-3:],[1,0,0])
        np.testing.assert_array_equal(x[1,-3:],[0,0,1])

    def test_future_outcomes_ids_and_audit_are_not_inputs(self):
        rr=[row()];other=copy.deepcopy(rr)
        other[0].update(fid='R27',y=[-1,-1],target='2026-01-01',audit={'future':999})
        for spec in SPECS:
            np.testing.assert_array_equal(design(rr,0,spec,protocol()),design(other,0,spec,protocol()))

    def test_unknown_version_uses_frozen_target_mixture(self):
        spec=next(c for c in SPECS if c['id']=='version25')
        m=dict(kind='classifier',model=BaselineResponse(),spec=spec,baseline_mixture={'05.10':.25,'05.11':.75})
        actual=score(m,[row(baseline='06.99'),row(baseline='05.00')],0,protocol())
        np.testing.assert_allclose(actual,[.65,.1])
        self.assertEqual(m['baseline_mixture'],{'05.10':.25,'05.11':.75})

    def test_consensus_cannot_add_alarms_above_either_component(self):
        spec=next(c for c in SPECS if c['id']=='version25')
        def constant(v):return dict(kind='constant',value=v,spec=spec,baseline_mixture={'05.10':1.})
        m=dict(kind='consensus',control=constant(.9),adapted=constant(.3))
        self.assertAlmostEqual(score(m,[row()],0,protocol())[0],.3)

    def test_selection_budget_and_dates_cannot_be_relaxed(self):
        rr=[row(),row(issue='2024-06-03',target='2024-06-10'),row(issue='2024-06-05',target='2024-06-12',event=False)]
        records=[dict(regime='balanced25',scores=[[.9,.9,.9],[.1,.1,.1]])]
        choice,_,_=select2024(rr,records,protocol());self.assertFalse(choice[0]['enabled'])
        records[0]['scores'][0]=[.9,.9,.7]
        choice,_,_=select2024(rr,records,protocol());self.assertEqual(choice[0]['threshold'],.8)
        rr[1]['fid']='R02';rr[1]['issue']=rr[0]['issue'];rr[1]['target']=rr[0]['target']
        choice,_,_=select2024(rr,records,protocol());self.assertFalse(choice[0]['enabled'])

    def test_old_years_cannot_become_selection_labels(self):
        with self.assertRaisesRegex(ValueError,'Selection requires2024'):
            select2024([row(2020)],[],protocol())

    def test_freeze_cannot_be_retrained(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'frozen-selection.json').write_text('{}',encoding='utf-8')
            with patch('agro_checker.season_version_decline.D',path):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):fit()


if __name__=='__main__':unittest.main()
