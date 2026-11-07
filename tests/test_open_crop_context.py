"""Synthetic arithmetic fixtures only, not field crop or operation observations."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker import open_crop_context as m
from tests.test_continuation_recovery import example,protocol as base_protocol


def entries():
    return [dict(weight=1.,properties={'tc-wintercereals_sos':347,'tc-wintercereals_eos':191,
        'tc-maize-main_sos':116,'tc-maize-main_eos':294}),
        dict(weight=1.,properties={'tc-wintercereals_sos':42,'tc-wintercereals_eos':222,
        'tc-maize-main_sos':115,'tc-maize-main_eos':292,'tc-springcereals_sos':115,'tc-springcereals_eos':292})]


def evidence():
    return dict(fragments={'R01':dict(scenarios={s:entries() for s in ['base','N']})},
        regional_report=dict(publication_date='2024-06-27',usable_after='2024-06-28',expires='2025-01-01'))


def protocol():
    p=base_protocol();p['families']=m.FAMILIES
    return p


class CropContextTests(unittest.TestCase):
    def test_circular_winter_calendar_and_leap_handling(self):
        self.assertTrue(m.cyclic_between(59,347,191));self.assertFalse(m.cyclic_between(250,347,191))
        self.assertEqual(m.annual_day('2024-06-01'),m.annual_day('2026-06-01'))
        self.assertEqual(m.annual_day('2024-02-29'),59.5)

    def test_conflicting_overlapping_regions_are_alternatives_not_weights(self):
        a=m.calendar_for_scenario('2024-06-06',entries())
        self.assertEqual(a[2],1.);self.assertEqual(a[3],0.)
        self.assertAlmostEqual(a[8],31/90)
        changed=entries()
        for x in changed:x['weight']=3.
        np.testing.assert_array_equal(a,m.calendar_for_scenario('2024-06-06',changed))

    def test_unknown_crop_alternative_cannot_become_consensus(self):
        x=m.calendar_for_scenario('2024-10-01',entries())
        spring=x[18:27]
        self.assertEqual(spring[2],1.);self.assertEqual(spring[3],0.)
        self.assertEqual(spring[6],1.);self.assertEqual(spring[7],0.)

    def test_incomplete_spatial_coverage_is_not_full_support(self):
        e=[dict(weight=.4,properties=entries()[0]['properties'])]
        x=m.calendar_for_scenario('2024-06-06',e)
        self.assertEqual(x[2],1.);self.assertEqual(x[3],0.);self.assertEqual(x[7],0.)

    def test_future_publication_not_backfilled(self):
        r=example();r['issue']='2024-06-11'
        self.assertEqual(m.context(r,evidence())[27],0.)
        r['issue']='2024-06-27';self.assertEqual(m.context(r,evidence())[27],0.)
        r['issue']='2024-06-28';self.assertEqual(m.context(r,evidence())[27],1.)

    def test_previous_year_news_not_presented_as_current(self):
        r=example();r['issue']='2026-06-28';x=m.context(r,evidence())
        np.testing.assert_array_equal(x[27:30],[0.,0.,0.])

    def test_context_never_uses_target_actual_or_damage_audit(self):
        r=example();other=copy.deepcopy(r);other.update(y=[-100,-100],target='2026-01-01',audit={'delta':100},harvest_date='2024-06-01',crop='wheat')
        np.testing.assert_array_equal(m.context(r,evidence()),m.context(other,evidence()))

    def test_no_field_truth_manufactured_from_calendar(self):
        r=m.enrich([example()],evidence())[0]
        self.assertIsNone(r['crop']);self.assertIsNone(r['harvest_date'])
        self.assertFalse(r['context_audit']['actual_harvest_known'])

    def test_missing_scenario_retained_with_flags(self):
        rr=m.enrich([example()],evidence(),'outer')
        self.assertEqual(len(rr),1);self.assertEqual(len(rr[0]['crop_context']),36)
        np.testing.assert_array_equal(rr[0]['crop_context'][:27],0.)

    def test_date_ablation_excludes_external_calendar(self):
        a=m.enrich([example()],evidence());b=copy.deepcopy(a);b[0]['crop_context']=[999.]*36
        np.testing.assert_array_equal(m.matrix(a,'date_logit'),m.matrix(b,'date_logit'))
        self.assertFalse(np.array_equal(m.matrix(a,'calendar_logit'),m.matrix(b,'calendar_logit')))

    def test_base_scores_and_truth_cannot_change_direct_inputs(self):
        a=m.enrich([example()],evidence());b=copy.deepcopy(a);b[0].update(y=[999,999],raw_score=[0,1])
        b[0]['continuation'][18:20]=[-999,999]
        np.testing.assert_array_equal(m.matrix(a,'calendar_logit'),m.matrix(b,'calendar_logit'))

    def test_future_years_reserve_cannot_fit(self):
        for year in [2025,2026]:
            r=example();r['issue']=f'{year}-06-01';r['target']=f'{year}-06-08'
            with self.assertRaisesRegex(ValueError,'2024'):m.fit_filter([r],0,'calendar_extra',protocol())
        r=example();r['fid']='R27'
        with self.assertRaisesRegex(ValueError,'reserve'):m.fit_filter([r],0,'calendar_logit',protocol())

    def test_one_lost_tp_under_boundary_shift_rejects_filter(self):
        rr=[example(i,i<3) for i in range(1,5)]
        record=dict(family='calendar_logit',scores=[[.9,.9,.1,.1]]*2,scenario_scores={'N':[[.1,.9,.1,.1]]*2})
        selected,_=m.select(rr,[record],protocol())
        self.assertFalse(selected[0]['enabled'])

    def test_lossless_filter_can_pass_when_all_scenarios_agree(self):
        rr=[example(i,i<3) for i in range(1,5)]
        record=dict(family='calendar_logit',scores=[[.9,.9,.1,.1]]*2,scenario_scores={'N':[[.9,.9,.1,.1]]*2})
        selected,_=m.select(rr,[record],protocol());self.assertTrue(selected[0]['enabled'])
        self.assertEqual(selected[0]['selection']['lost_tp'],0)

    def test_future_years_cannot_select(self):
        r=example();r['issue']='2026-06-01';r['target']='2026-06-08'
        with self.assertRaisesRegex(ValueError,'2024'):m.select([r],[],protocol())

    def test_no_refit_after_frozen_selection(self):
        with TemporaryDirectory() as folder:
            path=Path(folder);(path/'frozen-selection.json').write_text('{}')
            with patch.object(m,'D',path):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):m.fit()


if __name__=='__main__':unittest.main()
