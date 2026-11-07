from copy import deepcopy
from datetime import date, datetime, timedelta
import unittest
from agro_checker.pipeline import UTC
from agro_checker.krymsk_observed_data import join_slots
from agro_checker.krymsk_observed_study import features, fit, predict, metrics, select, CANDIDATES


def fixture(day=date(2022, 3, 1)):
    curves = {k: [float(i) for i in range(14)] for k in ['temperature', 'dewpoint', 'rh', 'u10', 'v10', 'cloud']}
    observations = {}
    start = datetime(day.year, day.month, day.day, 18, tzinfo=UTC)
    for i in range(4):
        stamp = (start+timedelta(hours=i*3)).isoformat()
        observations[stamp] = {'station_id': '37002', 'observed_at_utc': stamp, 'temperature_c': float(i-2),
                               'provider_model_filled_temperature': False}
    return curves, observations


class KrymskObservedTests(unittest.TestCase):
    def test_exact_utc_join_and_forecast_leads(self):
        rows = join_slots(date(2022, 3, 1), *fixture())
        self.assertEqual([r['lead_hours'] for r in rows], [12, 15, 18, 21])
        self.assertEqual([r['gfs']['temperature'] for r in rows], [2, 5, 8, 11])
        self.assertEqual(rows[2]['observed_at_utc'], '2022-03-02T00:00:00+00:00')
        self.assertEqual(rows[0]['scenario_issued_at_utc'], '2022-03-01T15:00:00+00:00')
        self.assertTrue(all(not r['is_orchard_truth'] for r in rows))

    def test_missing_never_interpolated_or_zero_filled(self):
        curves, obs = fixture(); del obs[next(iter(obs))]
        rows = join_slots(date(2022, 3, 1), curves, obs)
        self.assertIsNone(rows[0]['target_temperature_c'])
        self.assertEqual(len(rows), 4)
        m = metrics([{**r, 'prediction_c': 1.} for r in rows])
        self.assertEqual(m['all_slots']['n'], 3); self.assertEqual(m['sampled_night']['complete_nights'], 0)

    def test_model_marked_value_excluded(self):
        curves, obs = fixture(); obs[next(iter(obs))]['provider_model_filled_temperature'] = True
        self.assertIsNone(join_slots(date(2022, 3, 1), curves, obs)[0]['target_temperature_c'])

    def test_nearby_time_is_not_actual_time(self):
        curves, obs = fixture(); stamp = next(iter(obs)); r = obs.pop(stamp)
        r['observed_at_utc'] = '2022-03-01T18:01:00+00:00'; obs[r['observed_at_utc']] = r
        self.assertIsNone(join_slots(date(2022, 3, 1), curves, obs)[0]['target_temperature_c'])

    def test_wrong_station_and_identity_rejected(self):
        curves, obs = fixture(); obs[next(iter(obs))]['station_id'] = '34929'
        with self.assertRaises(ValueError): join_slots(date(2022, 3, 1), curves, obs)
        with self.assertRaises(ValueError): join_slots(date(2022, 3, 1), *fixture(), station_id='34929')

    def test_control_years_rejected_in_join_and_training(self):
        for year in [2025, 2026]:
            with self.assertRaises(ValueError): join_slots(date(year, 3, 1), *fixture(date(year, 3, 1)))
            rows = join_slots(date(2022, 3, 1), *fixture()); rows[0]['year'] = year
            with self.assertRaises(ValueError): fit('gfs', rows)

    def test_invalid_forecast_schema_or_value_rejected(self):
        curves, obs = fixture(); curves['temperature'][3] = float('nan')
        with self.assertRaises(ValueError): join_slots(date(2022, 3, 1), curves, obs)
        curves, obs = fixture(); curves['temperature'].pop()
        with self.assertRaises(ValueError): join_slots(date(2022, 3, 1), curves, obs)

    def test_features_and_inference_do_not_need_targets(self):
        rows = join_slots(date(2022, 3, 1), *fixture())
        for name in CANDIDATES:
            model = fit(name, rows); r = rows[0]; clean = {k: v for k, v in r.items() if not k.startswith('target')}
            self.assertEqual(predict(model, r), predict(model, clean))
            r = {**r, 'target_temperature_c': 999., 'target_observed_rh': 100.}
            self.assertEqual(features(r, True), features(clean, True))

    def test_cold_diagnostics_require_complete_sampled_nights(self):
        rows = join_slots(date(2022, 3, 1), *fixture())
        m = metrics([{**r, 'prediction_c': 1.} for r in rows])
        self.assertEqual(m['cold_slots']['n'], 2)
        self.assertEqual(m['sampled_night']['misses'], 1)
        self.assertFalse(m['sampled_night']['is_original_detector_score'])
        self.assertFalse(m['sampled_night']['continuous_minimum_validated'])

    def test_missing_training_labels_not_used(self):
        rows = join_slots(date(2022, 3, 1), *fixture()); rows[0]['target_temperature_c'] = None
        self.assertEqual(fit('bias', rows)['training_slots'], 3)
        for r in rows: r['target_temperature_c'] = None
        with self.assertRaises(ValueError): fit('gfs', rows)

    def test_selection_cannot_trade_cold_quality_for_average_mae(self):
        b = {'all_slots': {'mae_c': 2.}, 'cold_slots': {'mae_c': 2.}, 'sampled_night': {'false_cold': 1}}
        results = {n: {str(y): deepcopy(b) for y in [2023, 2024]} for n in CANDIDATES}
        for y in [2023, 2024]: results['bias'][str(y)]['all_slots']['mae_c'] = 1.
        results['bias']['2024']['cold_slots']['mae_c'] = 2.1
        winner, gates = select(results)
        self.assertEqual(winner, 'gfs'); self.assertFalse(gates['bias']['eligible'])
        results['bias']['2024']['cold_slots']['mae_c'] = 2.
        results['bias']['2024']['sampled_night']['false_cold'] = 2
        self.assertEqual(select(results)[0], 'gfs')
        results['bias']['2024']['sampled_night']['false_cold'] = 1
        self.assertEqual(select(results)[0], 'bias')


if __name__ == '__main__': unittest.main()
