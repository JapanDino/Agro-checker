"""Arithmetic fixtures; these rows are not real inspections or research data."""
import copy
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from agro_checker import triage_retention as m
from tests.test_spectral_triage import row


def config(k=3):
    return dict(variants=m.variants(),budget_k=k,fixed_parameters=dict(seed=20241021))


class TriageRetentionTests(unittest.TestCase):
    def test_blend_endpoints_exactly_preserve_prior_rankings(self):
        rr=[row(str(i)) for i in range(6)];a=[0,0,.2,.5,.5,0];b=[.8,.2,.1,0,0,.1];p=config()
        for variant,scores in zip(m.variants()[:2],[a,b]):
            np.testing.assert_array_equal(m.choose(rr,a,b,variant,p),m.base.topk(rr,scores,p))

    def test_tied_rank_percentiles_do_not_encode_fragment_id(self):
        np.testing.assert_array_equal(m.percentiles([1,2,2,4]),[0,.5,.5,1])
        np.testing.assert_array_equal(m.percentiles([2,2,2]),[.5,.5,.5])
        np.testing.assert_array_equal(m.percentiles([2]),[1])

    def test_every_variant_keeps_budget_and_input_pool(self):
        rr=[row(str(i),issue=d) for d in ['2024-06-01','2024-06-02'] for i in range(5)]
        rr+=[row('small',issue='2024-06-03')];a=np.arange(len(rr));b=-a;p=config()
        for v in m.variants():
            chosen=m.choose(rr,a,b,v,p);self.assertEqual(len(chosen),len(rr))
            for issue,count in [('2024-06-01',3),('2024-06-02',3),('2024-06-03',1)]:
                self.assertEqual(sum(c for r,c in zip(rr,chosen) if r['issue']==issue),count)

    def test_outcomes_and_future_dates_cannot_change_order(self):
        rr=[row(str(i)) for i in range(5)];p=config();a=np.arange(5)/10;b=a[::-1];poisoned=copy.deepcopy(rr)
        for r in poisoned:r.update(y=[-999,999],target='2026-06-01',target_scene='future')
        for v in m.variants():
            np.testing.assert_array_equal(m.choose(rr,a,b,v,p),m.choose(poisoned,a,b,v,p))

    def test_anchor_keeps_requested_strongest_baseline_choices(self):
        rr=[row(str(i)) for i in range(6)];a=[6,5,4,3,2,1];b=[1,2,3,4,5,6];p=config()
        v=next(v for v in m.variants() if v['id']=='anchor_2')
        np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,0,0,0,1])

    def test_strong_baseline_signals_protected_without_extra_slots(self):
        rr=[row(str(i)) for i in range(6)];p=config();a=[.5,.4,.3,.2,.1,0];b=np.arange(6)/10
        v=next(v for v in m.variants() if v['id']=='protect_0.2_margin_0')
        np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,1,0,0,0])

    def test_margin_must_be_strictly_exceeded_and_no_extra_places(self):
        rr=[row(str(i)) for i in range(4)];p=config();a=[.4,.3,.01,0];b=[.4,.3,.10,.14]
        v=next(v for v in m.variants() if v['id']=='protect_0.2_margin_0.05')
        np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,1,0])
        b[-1]=.16;np.testing.assert_array_equal(m.choose(rr,a,b,v,p),[1,1,0,1])

    def test_rules_are_invariant_to_input_row_permutation(self):
        rr=[row(str(i)) for i in range(6)];a=np.array([1,2,2,4,1,1]);b=a[::-1];p=config()
        for v in m.variants():
            first=m.choose(rr,a,b,v,p);second=m.choose(rr[::-1],a[::-1],b[::-1],v,p)
            np.testing.assert_array_equal(first,second[::-1])

    def test_unknown_exchange_not_claimed_as_improvement(self):
        rr=[row('old',None),row('new',True)];p=config(1);rec={'recent_drop':[1,0],'extra':[0,1]}
        selected,table=m.selection(rr,rec,p)
        self.assertFalse(selected['enabled']);self.assertEqual(selected['variant']['id'],'baseline')
        candidate=next(v for v in table if v['variant']['id']=='extra')
        self.assertEqual(candidate['paired_baseline']['difference_lower'],0)

    def test_same_number_events_does_not_preserve_event_identity(self):
        rr=[row('old',True),row('new',True)];p=config(1)
        e=m.assess(rr,[1,0],[0,1],m.variants()[1],p)
        self.assertFalse(e['eligible']);self.assertFalse(e['checks']['four_restored'])
        self.assertFalse(e['checks']['all_baseline_events_retained'])

    def test_restore_old_event_but_increase_negatives_vs_extra_is_rejected(self):
        rr=[row('old_tp',True),row('old_fp',False),row('new_tp',True),row('new_unknown',None)];p=config(2)
        selected,table=m.selection(rr,{'recent_drop':[4,3,2,1],'extra':[1,2,4,3]},p)
        baseline=table[0];self.assertTrue(baseline['checks']['four_restored'])
        self.assertFalse(baseline['checks']['known_negatives_not_above_either']);self.assertFalse(baseline['eligible'])
        self.assertTrue(selected['enabled']);self.assertEqual(selected['metrics']['tp'],2)

    def test_non2024_and_spatial_reserve_rejected_from_selection(self):
        for rr in [[row('R01',True,issue='2026-06-01')],[row(m.base.HOLDOUT[0],True)]]:
            with self.assertRaises(ValueError):m.selection(rr,{'recent_drop':[1],'extra':[1]},config())

    def test_duplicate_nan_and_unregistered_rules_rejected(self):
        r=row();p=config()
        for rr,a,b,v in [([r,r],[1,1],[1,1],m.variants()[0]),([r],[float('nan')],[1],m.variants()[0]),
                         ([r],[1],[1],dict(id='invented',kind='blend',alpha=.123))]:
            with self.assertRaises(ValueError):m.choose(rr,a,b,v,p)

    def test_freeze_prevents_retuning(self):
        with TemporaryDirectory() as folder,patch.object(m,'D',Path(folder)):
            (Path(folder)/'frozen-selection.json').write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):m.run()


if __name__=='__main__':unittest.main()
