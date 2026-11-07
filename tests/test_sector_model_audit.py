"""Synthetic arithmetic fixtures, never persisted as agronomist observations."""
import unittest
import numpy as np
from agro_checker.sector_model_audit import (balanced_mean, point_quality,
    observation_opportunities, merged_event_windows, confusion, block_error_intervals)


def pair(issue='2026-08-01',target='2026-08-08',fid='fixture',y=.3):
    return dict(fid=fid,issue=issue,target=target,x=[.5,.5,0,0,7,0,1],y=[y,y])


class SectorAuditTests(unittest.TestCase):
    def test_dates_not_pixel_counts_weighted(self):
        rows=[pair(),pair(fid='other'),pair(issue='2026-08-02')]
        self.assertAlmostEqual(balanced_mean(rows,[0,0,1]),.5)

    def test_missing_data_is_unknown(self):
        q=point_quality([],np.empty((0,2)),[.1,.1])
        self.assertIsNone(q['mae'])
        self.assertIsNone(confusion([],[])['recall'])

    def test_conditional_coverage_does_not_hide_bad_events(self):
        rows=[pair(),pair(issue='2026-08-02',y=.5)]
        q=point_quality(rows,[[.5,.5],[.5,.5]],[.1,.1])['per_index']['ndvi']
        self.assertEqual(q['coverage'],.5)
        self.assertEqual(q['decline_coverage'],0)
        self.assertEqual(q['other_coverage'],1)

    def test_future_cloud_gap_retained_in_denominator(self):
        rows=[dict(date=d,baseline='same') for d in ('2026-08-01','2026-08-04','2026-08-20')]
        q=observation_opportunities(rows,'2026-08-01','2026-08-31')
        self.assertEqual(q['eligible_issue_days'],2)
        self.assertEqual(q['scorable_6_8_day_issues'],0)
        self.assertEqual(q['max_elapsed_gap_days'],16)

    def test_baseline_reset_no_mixed_history(self):
        rows=[dict(date='2026-08-01',baseline='old'),dict(date='2026-08-04',baseline='new')]
        self.assertEqual(observation_opportunities(rows,'2026-08-01','2026-08-31')['eligible_issue_days'],0)

    def test_last_eight_days_censored_without_fake_failures(self):
        rows=[dict(date=d,baseline='same') for d in ('2026-08-23','2026-08-28')]
        self.assertEqual(observation_opportunities(rows,'2026-08-01','2026-08-31')['eligible_issue_days'],0)

    def test_overlapping_windows_not_independent_events(self):
        rows=[pair(),pair(issue='2026-08-04',target='2026-08-11'),
              pair(issue='2026-08-20',target='2026-08-27'),pair(fid='other')]
        self.assertEqual(merged_event_windows(rows,0),3)

    def test_blocks_deterministic_and_pair_difference(self):
        rows=[pair(),pair(issue='2026-08-10',target='2026-08-17')]
        a=block_error_intervals(rows,[[.4,.4],[.4,.4]],repeats=20)
        self.assertEqual(a,block_error_intervals(rows,[[.4,.4],[.4,.4]],repeats=20))
        self.assertAlmostEqual(a['mae_difference'],-.1)


if __name__=='__main__': unittest.main()
