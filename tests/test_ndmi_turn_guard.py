"""Synthetic arithmetic fixtures; never observations or inspection labels."""
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from tests.test_spectral_triage import row
from agro_checker import ndmi_turn_guard as m


def config():return dict(variants=m.variants(),budget_k=3,fixed_parameters=dict(seed=20241021))


def divergent(fid='A',value=None,issue='2024-06-01'):
    r=row(fid,value,issue=issue);r['x'][2:4]=[-.005,.003];r['x'][1]=-.1
    r['quality']['boundary_delta']={n:[-.02,.03] for n in m.base.SCENARIOS}
    return r


class NdmiTurnGuardTests(unittest.TestCase):
    def test_original_hybrid_reproduced_exactly(self):
        rr=[divergent(str(i)) for i in range(6)];a=np.array([.3,.21,.08,.07,.06,0]);b=a[::-1];p=config()
        v=next(v for v in p['variants'] if v['id']=='old_hybrid')
        oldp=dict(variants=[dict(id='old',kind='protect',threshold=.2,margin=0.)],budget_k=3,fixed_parameters=dict(seed=20241021))
        np.testing.assert_array_equal(m.choose(rr,a,b,v,p),m.retention.choose(rr,a,b,oldp['variants'][0],oldp))

    def test_divergence_requires_all_boundary_support_and_both_signs(self):
        r=divergent();self.assertTrue(m.divergent(r))
        for mutation in ['one_positive_ndvi','one_nonpositive_ndmi','missing','flag','raw_sign']:
            v=copy.deepcopy(r)
            if mutation=='one_positive_ndvi':v['quality']['boundary_delta']['N'][0]=.001
            if mutation=='one_nonpositive_ndmi':v['quality']['boundary_delta']['N'][1]=0
            if mutation=='missing':v['quality']['boundary_delta'].pop('N')
            if mutation=='flag':v['quality']['all_boundaries_supported']=False
            if mutation=='raw_sign':v['x'][3]=-.001
            self.assertFalse(m.divergent(v),mutation)

    def test_weak_round_guard_is_round_not_candidate_condition(self):
        rr=[divergent(str(i)) for i in range(6)];p=config();v=next(v for v in p['variants'] if v['id']=='weak_round_0.1')
        a=[.09,.08,.07,0,0,0];b=[.1,.2,.3,.4,.5,.6]
        np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,1,0,0,0])
        a[0]=.11;self.assertFalse(np.array_equal(m.choose(rr,a,b,v,p),[1,1,1,0,0,0]))

    def test_strong_anchors_remain_in_all_guard_variants(self):
        rr=[divergent(str(i)) for i in range(6)];a=[.5,.4,.3,.2,.1,0];b=[0,.1,.2,.3,.4,.5];p=config()
        for v in p['variants']:
            if v['id']=='extra':continue
            np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,1,0,0,0])

    def test_divergence_protects_baseline_does_not_add_outsider(self):
        rr=[divergent(str(i)) for i in range(6)];p=config();v=next(v for v in p['variants'] if v['id']=='divergent')
        np.testing.assert_array_equal(m.choose(rr,[.09,.08,.07,.06,.05,.04],[0,1,2,3,4,5],v,p),[1,1,1,0,0,0])

    def test_low_index_guard_is_explicit_input_condition_not_crop_label(self):
        r=divergent();r['x'][0]=.4;rr=[r]+[divergent(str(i)) for i in range(1,4)];p=config()
        a=[.09,.08,.07,.01];b=[0,1,2,3]
        low=next(v for v in p['variants'] if v['id']=='divergent_low_ndvi')
        plain=next(v for v in p['variants'] if v['id']=='divergent_negative_ndmi')
        self.assertFalse(m.choose(rr,a,b,low,p)[0]);self.assertTrue(m.choose(rr,a,b,plain,p)[0])

    def test_whole_divergent_round_requires_all_baseline_cases(self):
        rr=[divergent(str(i)) for i in range(4)];p=config();v=next(v for v in p['variants'] if v['id']=='weak_divergent_round')
        a=[.09,.08,.07,.01];b=[0,1,2,3]
        np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,1,0])
        rr[0]['x'][3]=-.01;self.assertFalse(np.array_equal(m.choose(rr,a,b,v,p),[1,1,1,0]))

    def test_future_outcomes_and_metadata_do_not_change_choices(self):
        rr=[divergent(str(i)) for i in range(6)];p=config();a=np.arange(6)/10;b=a[::-1];other=copy.deepcopy(rr)
        for r in other:r.update(y=[999,-999],target='2099-01-01',target_scene='future')
        for v in p['variants']:
            np.testing.assert_array_equal(m.choose(rr,a,b,v,p),m.choose(other,a,b,v,p))

    def test_same_budget_small_pools_and_tie_order_invariance(self):
        rr=[divergent(str(i)) for i in range(6)]+[divergent('small',issue='2024-06-02')];p=config();a=np.zeros(7);b=np.zeros(7)
        for v in p['variants']:
            first=m.choose(rr,a,b,v,p);second=m.choose(rr[::-1],a[::-1],b[::-1],v,p)
            np.testing.assert_array_equal(first,second[::-1]);self.assertEqual(int(first.sum()),4);self.assertTrue(first[-1])

    def test_guards_have_no_field_or_calendar_specific_rescue(self):
        rr=[divergent(str(i)) for i in range(6)];a=np.arange(6)/100;b=np.arange(6)[::-1]/10;p=config()
        other=[dict(r,fid='unrelated-'+r['fid'],issue='2024-10-01') for r in rr]
        for v in p['variants']:np.testing.assert_array_equal(m.choose(rr,a,b,v,p),m.choose(other,a,b,v,p))

    def test_unknown_swaps_cannot_prove_gain(self):
        rr=[divergent('A',None),divergent('B',True)];p=config();p['budget_k']=1
        refs=dict(baseline=np.array([0,1],bool),old_hybrid=np.array([1,0],bool),extra=np.array([1,0],bool))
        result=m.assessment(rr,np.array([0,1],bool),refs,p)
        self.assertFalse(result['repair_passed']);self.assertEqual(result['paired']['old_hybrid']['difference_lower'],0)

    def test_current_month_outcomes_do_not_choose_current_month_rule(self):
        rr=[divergent(str(i),True,issue='2024-06-01') for i in range(6)];p=config()
        rec=dict(recent_drop=np.arange(6)/100,extra=np.arange(6)[::-1]/10)
        a,aa=m.forward(rr,rec,p);poisoned=copy.deepcopy(rr)
        for r in poisoned:r['y']=[999,999]
        b,bb=m.forward(poisoned,rec,p)
        np.testing.assert_array_equal(a,b);self.assertEqual(aa,bb)

    def test_forward_rule_after_supported_history_ignores_current_month_labels(self):
        from datetime import date,timedelta
        rr=[]
        for d in ['2024-06-01','2024-06-03','2024-06-06','2024-06-08','2024-06-11','2024-06-13','2024-06-16','2024-07-01']:
            for i in range(4):
                r=divergent(str(i),bool(i%2),issue=d)
                r['target']=(date.fromisoformat(d)+timedelta(days=7)).isoformat();rr.append(r)
        p=config();rec=dict(recent_drop=np.tile([.09,.08,.07,0],8),extra=np.tile([.1,.2,.3,.4],8))
        first,a=m.forward(rr,rec,p);other=copy.deepcopy(rr)
        for r in other:
            if r['issue'].startswith('2024-07'):r['y']=[999,-999]
        second,b=m.forward(other,rec,p)
        np.testing.assert_array_equal(first,second)
        ja=next(x for x in a if x['start']=='2024-07-01');jb=next(x for x in b if x['start']=='2024-07-01')
        self.assertEqual(ja,jb);self.assertEqual(ja['prior_known'],28);self.assertLess(ja['max_known_target'],ja['start'])

    def test_selection_rejects_other_year_and_spatial_reserve(self):
        for rr in [[divergent('R01',issue='2026-06-01')],[divergent(m.base.HOLDOUT[0])]]:
            with self.assertRaises(ValueError):m.compare(rr,dict(recent_drop=np.array([1.]),extra=np.array([1.])),config())

    def test_duplicates_nonfinite_and_unregistered_variants_rejected(self):
        r=divergent();p=config();v=p['variants'][0]
        for rr,a,b,variant in [([r,r],[1,1],[0,1],v),([r],[np.nan],[0],v),([r],[1],[1],dict(id='custom'))]:
            with self.assertRaises(ValueError):m.choose(rr,a,b,variant,p)

    def test_freeze_blocks_retuning(self):
        with TemporaryDirectory() as folder,patch.object(m,'D',Path(folder)):
            (Path(folder)/'frozen-selection.json').write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):m.run()


if __name__=='__main__':unittest.main()
