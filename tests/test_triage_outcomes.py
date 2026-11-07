"""Synthetic arithmetic fixtures, never real fields, visits or research evidence."""
import copy
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import numpy as np
from agro_checker import triage_outcomes as m
from tests.test_spectral_triage import row


def scene(day='2024-06-08',values=(.3,.0),coverage=1.,pb='05.10',sid='target',status='ready'):
    return dict(id=sid,acquired_at_utc=day+'T08:30:00Z',baseline=pb,status=status,
        fields=dict(R01=dict(values=dict(core=dict(value=list(values),coverage=coverage)))))


def item(day='2024-06-08',pb='05.10',sun=40,sid='target'):
    return dict(id=sid,properties={'datetime':day+'T08:30:00Z','s2:processing_baseline':pb,'view:sun_elevation':sun})


class TriageOutcomeTests(unittest.TestCase):
    def test_nearest_day_seven_not_largest_decline(self):
        r=row();ss=[scene('2024-06-07',(-.9,-.9),sid='six'),scene('2024-06-08',(.9,.9),sid='seven')]
        out=m.choose_target(r,ss);self.assertEqual(out['target_scene'],'seven');self.assertEqual(out['y'],[.9,.9])

    def test_equal_distance_prefers_earlier_day(self):
        out=m.choose_target(row(),[scene('2024-06-09',sid='eight'),scene('2024-06-07',sid='six')])
        self.assertEqual(out['target_scene'],'six')

    def test_window_not_widened_and_version_not_mixed(self):
        r=row();self.assertIsNone(m.choose_target(r,[scene('2024-06-10'),scene(pb='05.11')]))

    def test_daily_quality_and_time_ties_not_chosen_by_value(self):
        a=scene(values=(-.9,-.9),coverage=.8,sid='low');b=scene(values=(.8,.8),coverage=1.,sid='high')
        self.assertEqual(m.choose_target(row(),[a,b])['target_scene'],'high')
        a['fields']['R01']['values']['core']['coverage']=1.
        a['acquired_at_utc']='2024-06-08T09:00:00Z'
        self.assertEqual(m.choose_target(row(),[a,b])['target_scene'],'low')

    def test_failed_reads_and_scl_only_never_supply_spectral_labels(self):
        for status in ['read_error','metadata_rejected','scl_only_no_eligible_core']:
            self.assertIsNone(m.choose_target(row(),[scene(status=status)]))

    def test_original_known_target_never_replaced_by_new_scene(self):
        r=row(value=True);r['target']='2024-06-07';old=copy.deepcopy(r)
        new,ledger=m.extend([r],[r],[scene(values=(.9,.9))],[item()])
        self.assertEqual(new,[old]);self.assertEqual(ledger[0]['reason'],'original_known')

    def test_month_boundary_restoration_uses_original_target_and_keeps_input(self):
        full=row(value=True);full['target_scene']='existing';masked=dict(full,y=None);before=copy.deepcopy(masked)
        out,ledger=m.extend([masked],[full],[scene(values=(.9,.9))],[item()])
        self.assertEqual(out[0]['y'],full['y']);self.assertEqual(out[0]['target_scene'],full.get('target_scene'))
        self.assertEqual(out[0]['x'],masked['x']);self.assertEqual(masked,before)
        self.assertEqual(ledger[0]['reason'],'month_boundary_restored')

    def test_additional_spectral_target_restores_unknown_without_visits(self):
        r=row();out,ledger=m.extend([r],[r],[scene()],[item()])
        self.assertEqual(out[0]['y'],[.3,0.]);self.assertEqual(out[0]['x'],r['x'])
        self.assertEqual(ledger[0]['reason'],'additional_scene_restored');self.assertNotIn('visit',out[0])

    def test_no_acquisition_version_cloud_and_read_error_are_distinct_unknowns(self):
        cases=[([],[],'no_catalogue_acquisition_in_window'),
            ([],[item(pb='05.11')],'no_same_baseline_in_window'),
            ([scene(status='read_error')],[item()],'unresolved_source_read_error'),
            ([scene(status='scl_only_no_eligible_core')],[item()],'insufficient_clear_core'),
            ([scene(status='metadata_rejected')],[item(sun=10)],'metadata_quality_rejected')]
        for ss,ff,reason in cases:
            out,ledger=m.extend([row()],[row()],ss,ff)
            self.assertIsNone(out[0]['y']);self.assertEqual(ledger[0]['reason'],reason)

    def test_no_indices_interpolation_from_days_five_and_nine(self):
        rr=[row()];out,ledger=m.extend(rr,rr,[scene('2024-06-06'),scene('2024-06-10')],[])
        self.assertIsNone(out[0]['y'])

    def test_metadata_rules_not_relaxed(self):
        self.assertTrue(m.item_accepted(item()))
        for f in [item(pb='05.09'),item(pb='04.00'),item(sun=19.9)]:self.assertFalse(m.item_accepted(f))
        f=item();f['properties']['earthsearch:boa_offset_applied']=True;self.assertFalse(m.item_accepted(f))

    def test_original_quality_minimum_and_no_partial_median(self):
        grids={'R01':{'core':np.arange(100)}};a=np.full((10,10),.4);valid=np.ones((10,10),bool);scl=np.full((10,10),4)
        with patch.object(m,'grids',return_value=(grids,(10,10),None)):
            valid.flat[:20]=False
            self.assertIn('core',m.summary(a,a,valid,scl)['R01']['values'])
            valid.flat[20]=False
            self.assertNotIn('core',m.summary(a,a,valid,scl)['R01']['values'])
        with patch.object(m,'grids',return_value=({'R01':{'core':np.arange(9)}},(10,10),None)):
            self.assertNotIn('core',m.summary(a,a,np.ones_like(valid),scl)['R01']['values'])

    def test_boundless_nodata_and_cloud_edges_not_recovered_as_land(self):
        scl=np.full((7,7),4);valid=np.ones((7,7),bool);scl[:2]=0;scl[4,4]=9
        land=m.land_support(scl,valid)
        self.assertFalse(land[:3].any());self.assertFalse(land[3:6,3:6].any());self.assertTrue(land[3,1])

    def test_other_years_and_spatial_reserve_never_enter_comparison(self):
        choices={name:[True] for name in ['baseline','extra','hybrid']}
        for rr in [[row(issue='2026-06-01')],[row(fid=m.base.HOLDOUT[0])]]:
            with self.assertRaises(ValueError):m.comparison(rr,choices,dict(budget_k=3))

    def test_frozen_lists_use_only_archived_choices_and_exact_pool(self):
        rr=[row()];data=dict(variant=dict(id=m.HYBRID),candidates=[dict(fid='R01',issue='2024-06-01',baseline_selected=True,extra_selected=False,candidate_selected=True)])
        with patch.object(m,'read',return_value=data):
            self.assertTrue(m.fixed_choices(rr)['hybrid'][0]);self.assertFalse(m.fixed_choices(rr)['extra'][0])
            with self.assertRaises(ValueError):m.fixed_choices([row('R02')])

    def test_freeze_prevents_recollection_or_evidence_driven_retries(self):
        with TemporaryDirectory() as folder,patch.object(m,'D',Path(folder)):
            (Path(folder)/'frozen-evaluation.json').write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):m.collect()
            with self.assertRaises(ValueError):m.evaluate()


if __name__=='__main__':unittest.main()
