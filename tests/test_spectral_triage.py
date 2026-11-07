"""Arithmetic fixtures only, never reported as real visits or crop labels."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker import spectral_triage as m


def observation(d,values=(.6,.2),baseline='05.10',fid='R01'):
    return dict(fid=fid,date=d,baseline=baseline,scene_id=d,terrain=[1.,1.],
        values=dict(core=dict(value=list(values),coverage=1.)))


def row(fid='R01',value=None,issue='2024-06-01',past_score=.1):
    x=[.6,.2,-past_score/7,-past_score/7,5,0.,1.,1.,1.]
    return dict(fid=fid,issue=issue,previous='2024-05-27',target='2024-06-08' if value is not None else None,
        issue_scene='current',previous_scene='past',baseline='05.10',x=x,
        y=None if value is None else ([.4,.0] if value else [.6,.2]),triage_features=[0.]*len(m.FEATURES),
        quality=dict(all_boundaries_supported=True,worst_boundary_delta=[-.1,-.1]))


def protocol():
    return dict(budget_k=1,fixed_parameters=dict(seed=20241021,c=.1,trees=16,depth=3,min_leaf=2,threads=1),
        support=dict(min_pairs=4,min_dates=2,min_positive_dates=2,min_negative_dates=2))


class SpectralTriageTests(unittest.TestCase):
    def test_future_clear_scene_not_required_for_issue_eligibility(self):
        past=observation('2024-05-27');now=observation('2024-06-01');future=observation('2024-06-08')
        a=m.issue_pool([past,now]);b=m.issue_pool([past,now,future])
        self.assertEqual(a,[r for r in b if r['issue']=='2024-06-01'])
        self.assertNotIn('y',a[0]);self.assertNotIn('target',a[0])

    def test_previous_must_have_same_processing_baseline(self):
        rr=[observation('2024-05-27'),observation('2024-05-29',baseline='05.11'),observation('2024-06-01')]
        now=next(r for r in m.issue_pool(rr) if r['issue']=='2024-06-01')
        self.assertEqual(now['previous'],'2024-05-27')

    def test_attaching_missing_outcome_never_creates_negative_label(self):
        source=m.issue_pool([observation('2024-05-27'),observation('2024-06-01')])
        result=m.attach_outcomes(source,[])
        self.assertEqual([m.key(r) for r in result],[m.key(r) for r in source])
        self.assertEqual(m.labels(result),[None]);self.assertIsNone(result[0]['target_scene'])

    def test_topk_does_not_drop_unknown_outcome_or_use_target(self):
        rr=[row('A',None,past_score=.9),row('B',True,past_score=.2),row('C',False,past_score=.1)]
        a=m.topk(rr,m.rule_score(rr,'recent_drop'),protocol());poisoned=copy.deepcopy(rr)
        for r in poisoned:r.update(y=[999,999],target='2026-01-01')
        np.testing.assert_array_equal(a,m.topk(poisoned,m.rule_score(poisoned,'recent_drop'),protocol()))
        self.assertTrue(a[0]);self.assertEqual(m.rank_metrics(rr,a,protocol())['unknown'],1)

    def test_precision_bounds_keep_unknowns_in_denominator(self):
        rr=[row('A',True),row('B',False),row('C',None)];p=protocol();p['budget_k']=3
        v=m.rank_metrics(rr,np.ones(3,bool),p)
        self.assertAlmostEqual(v['precision_lower'],1/3);self.assertAlmostEqual(v['precision_upper'],2/3)
        self.assertEqual(v['slots'],3)

    def test_shared_unknown_choices_cancel_in_paired_difference(self):
        rr=[row('A',None),row('B',False),row('C',True)]
        v=m.paired_metrics(rr,[True,True,False],[True,False,True])
        self.assertEqual(v['difference_lower'],.5);self.assertEqual(v['difference_upper'],.5)
        self.assertEqual(v['negative_growth_upper'],-1)

    def test_swapping_unknown_for_known_event_cannot_prove_strict_gain(self):
        rr=[row('A',None),row('B',True)]
        v=m.paired_metrics(rr,[True,False],[False,True])
        self.assertEqual(v['difference_lower'],0.);self.assertEqual(v['difference_upper'],1.)

    def test_lost_known_baseline_event_tracked_by_case_identity(self):
        rr=[row('A',True),row('B',True)]
        v=m.paired_metrics(rr,[True,False],[False,True])
        self.assertEqual(v['lost_cases'],[dict(fid='A',issue='2024-06-01')])

    def test_ties_deterministic_under_row_reordering(self):
        rr=[row('A'),row('B'),row('C')]
        aa=m.topk(rr,[0,0,0],protocol());rr.reverse();bb=m.topk(rr,[0,0,0],protocol())
        self.assertEqual({m.key(r) for r,a in zip(list(reversed(rr)),aa) if a},{m.key(r) for r,b in zip(rr,bb) if b})

    def test_quality_uses_same_clear_support_and_all_boundary_variants(self):
        rr=row();a=np.tile([.6,.2],(20,1));b=np.tile([.8,.4],(20,1));v=np.ones(20,bool);v[0]=False
        aa={n:(a.copy(),v.copy()) for n in m.SCENARIOS};bb={n:(b.copy(),v.copy()) for n in m.SCENARIOS}
        q=m.pixel_quality(rr,aa,bb);self.assertTrue(q['all_boundaries_supported'])
        aa['core'][0][0]=999;bb['core'][0][0]=-999
        self.assertEqual(q,m.pixel_quality(rr,aa,bb))
        bb['outer'][1][:8]=False
        self.assertFalse(m.pixel_quality(rr,aa,bb)['all_boundaries_supported'])

    def test_feature_vector_ignores_future_observations_and_labels(self):
        r=row();r['issue_scene']='2024-06-01';r['previous_scene']='2024-05-27'
        a=np.tile([.6,.2],(20,1));b=np.tile([.7,.3],(20,1));v=np.ones(20,bool)
        aa={n:(a,v) for n in m.SCENARIOS};bb={n:(b,v) for n in m.SCENARIOS}
        h=[observation('2024-05-27',(.7,.3)),observation('2024-06-01')]
        features,_=m.input_vector(r,h,aa,bb,1.)
        r.update(y=[999,999],target='2026-01-01')
        h += [observation('2024-06-08',(-1,-1)),observation('2024-05-29',(-1,-1),baseline='05.11')]
        other,_=m.input_vector(r,h,aa,bb,1.)
        np.testing.assert_array_equal(features,other)

    def test_unsupported_geometry_is_not_deleted(self):
        rr=[row('A',None)];rr[0]['quality']=dict(all_boundaries_supported=False,worst_boundary_delta=None)
        scores=m.rule_score(rr,'robust_change');self.assertEqual(scores[0],0.)
        self.assertTrue(m.topk(rr,scores,protocol())[0])

    def test_fit_rejects_future_reserve_and_unknown_labels(self):
        for year in [2025,2026]:
            r=row(value=True);r['issue']=f'{year}-06-01';r['target']=f'{year}-06-08'
            with self.assertRaisesRegex(ValueError,'2024'):m.fit_model([r],'logit',protocol())
        with self.assertRaisesRegex(ValueError,'reserve'):m.fit_model([row('R27',True)],'logit',protocol())
        with self.assertRaisesRegex(ValueError,'Known'):m.fit_model([row()],'logit',protocol())

    def test_fallback_retains_simple_ordering(self):
        rr=[row('A',True),row('B',False,past_score=.9)]
        model=m.fit_model(rr,'extra',protocol());self.assertEqual(model['method'],'recent_drop')
        np.testing.assert_array_equal(m.score(model,rr),m.rule_score(rr,'recent_drop'))

    def test_empty_pool_has_no_claimed_precision(self):
        result=m.rank_metrics([],[],protocol());self.assertIsNone(result['precision_lower'])
        self.assertIsNone(result['precision_upper']);self.assertEqual(result['status'],'no_data')

    def test_unequal_budget_cannot_be_compared(self):
        with self.assertRaisesRegex(ValueError,'budgets'):m.paired_metrics([row('A'),row('B')],[True,False],[True,True])

    def test_selection_rejects_future_and_unknown_advantage(self):
        rr=[row('A',None,past_score=.9),row('B',True,past_score=.1)]
        selected,_=m.select(rr,[dict(method='logit',scores=[.1,.9])],protocol())
        self.assertEqual(selected['method'],'recent_drop')
        rr[0]['issue']='2026-06-01'
        with self.assertRaisesRegex(ValueError,'2024'):m.select(rr,[],protocol())

    def test_frozen_experiment_cannot_refit(self):
        with TemporaryDirectory() as folder:
            path=Path(folder);(path/'frozen-selection.json').write_text('{}')
            with patch.object(m,'D',path):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):m.fit()


if __name__=='__main__':unittest.main()
