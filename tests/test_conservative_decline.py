"""Arithmetic fixtures test leakage and false-alarm guards, not field observations."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker.conservative_decline import only2024, matrix, select, train, fit, infer


def row(issue='2024-06-01', target='2024-06-08', event=True, fid='R01'):
    x = [.6, .2, -.02, -.02, 5, 0, 1]
    return dict(fid=fid, issue=issue, target=target, x=x,
        features=x+[0.]*16, season=[0.]*17, y=[.4 if event else .6, .2])


def selection_protocol():
    return dict(gates=['none'], probability_thresholds=[.5, .8, .95, 1.01], decline_thresholds=[.1, .2, 2.01])


class ConservativeDeclineTests(unittest.TestCase):
    def test_training_and_selection_reject_future_years(self):
        for year in [2025, 2026]:
            rr = [row(issue=f'{year}-06-01', target=f'{year}-06-08')]
            with self.assertRaisesRegex(ValueError, '2024'):
                train(rr, 0, dict(family='momentum'), {})
            with self.assertRaisesRegex(ValueError, '2024'):
                select(rr, [], selection_protocol())

    def test_spatial_reserve_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Reserved'):
            only2024([row(fid='R23')])

    def test_duplicate_field_date_does_not_inflate_support(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            only2024([row(), row()])

    def test_future_outcomes_and_audits_are_not_inputs(self):
        rr = [row()]
        changed = copy.deepcopy(rr)
        changed[0].update(y=[-1, -1], target='2026-01-01', fid='R27',
                          audit=dict(common_delta=[-1, -1]), calendar=[999]*20)
        for kind in ['basic', 'history_season']:
            np.testing.assert_array_equal(matrix(rr, dict(features=kind)), matrix(changed, dict(features=kind)))

    def test_false_alarm_budget_never_relaxed_for_recall(self):
        rr = [row(), row('2024-06-03', '2024-06-10'), row('2024-06-05', '2024-06-12', False)]
        records = [dict(spec=dict(id='bad', family='logit'), scores=[[.99]*3, [.1]*3])]
        choice, diagnostic, _ = select(rr, records, selection_protocol())
        self.assertFalse(choice[0]['enabled'])
        self.assertFalse(diagnostic[0]['enabled'])

    def test_two_true_positives_same_date_not_enough(self):
        rr = [row(), row(fid='R02')]
        records = [dict(spec=dict(id='cluster', family='logit'), scores=[[.9]*2, [.1]*2])]
        choice, diagnostic, _ = select(rr, records, selection_protocol())
        self.assertFalse(choice[0]['enabled'])
        self.assertTrue(diagnostic[0]['enabled'])
        self.assertFalse(diagnostic[0]['eligible'])

    def test_threshold_selected_from_2024_without_false_alarms(self):
        rr = [row(), row('2024-06-03', '2024-06-10'), row('2024-06-05', '2024-06-12', False)]
        records = [dict(spec=dict(id='good', family='logit'), scores=[[.9, .9, .7], [.1]*3])]
        choice, _, _ = select(rr, records, selection_protocol())
        self.assertTrue(choice[0]['enabled'])
        self.assertEqual(choice[0]['threshold'], .8)
        self.assertEqual(choice[0]['metrics']['fp'], 0)

    def test_incomplete_scores_cannot_drop_hard_rows(self):
        records = [dict(spec=dict(id='missing', family='logit'), scores=[[.9], [.1]])]
        with self.assertRaisesRegex(ValueError, 'entire selection cohort'):
            select([row(), row(fid='R02')], records, selection_protocol())

    def test_cannot_refit_after_freeze(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'frozen-selection.json').write_text('{}', encoding='utf-8')
            with patch('agro_checker.conservative_decline.D', root):
                with self.assertRaisesRegex(ValueError, 'cannot be retuned'):
                    fit()

    def test_no_selected_model_means_exact_original_alarms_and_point(self):
        from agro_checker.field_index_experiment import predict
        model = dict(name='linear_trend')
        rr = [row(), row(fid='R02', event=False)]
        with patch('agro_checker.conservative_decline.read', return_value=dict(selected_model=model)):
            alarms, scores, point, original = infer(rr, {}, dict(selected=[dict(enabled=False)]*2))
        np.testing.assert_array_equal(point, predict(model, rr))
        np.testing.assert_array_equal(alarms, original)
        self.assertEqual(scores, [[None, None], [None, None]])


if __name__ == '__main__':
    unittest.main()
