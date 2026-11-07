from copy import deepcopy
from datetime import date, timedelta
import unittest
from agro_checker import station_transfer_study as study
from agro_checker import vityazevo_split_data as archive
from agro_checker import vityazevo_split_study as ref

SITES = [dict(id='vityazevo_airport', role='development', latitude=45.002, longitude=37.347, elevation_m=53),
         dict(id='anapa_synoptic', role='spatial_check_only', latitude=44.883, longitude=37.283, elevation_m=32)]


def nights(year=2022):
    nn = []
    for j in range(6):
        for site in SITES:
            g = {k: [float(j+i) for i in range(14)] for k in ['temperature', 'dewpoint', 'u10', 'v10', 'rh', 'cloud']}
            n = archive.night(date(year, 3, 1)+timedelta(days=j), site, g, {})
            n['target_temperature_c'] = [t-2 if site['id'] == 'vityazevo_airport' else t+1 for t in g['temperature']]
            nn.append(n)
    return nn


def setup(name='paired_delta_5'):
    rr = study.rows(nights()); rm = ref.fit('ridge_weather_100', [r for r in rr if r['id'] == 'vityazevo_airport'])
    model = study.fit(name, rr, rm, [2022], 2023)
    valid = study.rows(nights(2023)); return rr, rm, model, valid


class StationTransferTests(unittest.TestCase):
    def test_previous_years_only_and_wrong_reference_fold_rejected(self):
        rr, rm, _, _ = setup()
        for years, val in [([2022, 2023], 2023), ([2022], 2024), ([2022, 2023, 2024], 2025)]:
            with self.assertRaises(ValueError): study.fit('local_bias_5', rr, rm, years, val)
        contaminated = rr+study.rows(nights(2023))
        with self.assertRaises(ValueError): study.fit('local_bias_5', contaminated, rm, [2022], 2023)
        bad = deepcopy(rm); bad['training_years'] = [2022, 2023]
        with self.assertRaises(ValueError): study.fit('local_bias_5', rr, bad, [2022], 2023)

    def test_observation_timestamp_cannot_hide_control_labels(self):
        rr, rm, _, _ = setup(); rr[0]['observed_at_utc'] = '2026-03-01T16:00:00+00:00'
        with self.assertRaises(ValueError): study.fit('local_bias_5', rr, rm, [2022], 2023)

    def test_identity_includes_latitude_longitude_and_height(self):
        rr, rm, model, valid = setup()
        for key in ['latitude', 'longitude', 'elevation_m']:
            bad = deepcopy(rr); bad[0][key] += .001
            with self.assertRaises(ValueError): study.fit('local_bias_5', bad, rm, [2022], 2023)
            q = deepcopy(valid[0]); q[key] += .001
            with self.assertRaises(ValueError): study.predict(model, q)

    def test_duplicate_terms_not_extra_calibration_support(self):
        rr, rm, _, _ = setup()
        with self.assertRaises(ValueError): study.fit('local_bias_5', rr+[rr[0]], rm, [2022], 2023)

    def test_reference_predictions_exactly_preserved(self):
        rr, rm, _, valid = setup()
        for name in study.CANDIDATES[2:]:
            model = study.fit(name, rr, rm, [2022], 2023)
            for r in valid:
                if r['id'] == 'vityazevo_airport': self.assertEqual(study.predict(model, r)['prediction_c'], ref.predict(rm, r))

    def test_new_site_falls_back_even_in_same_grid_cell(self):
        _, _, model, valid = setup(); r = next(r for r in valid if r['id'] == 'anapa_synoptic')
        r = {**r, 'id': 'new_farm', 'latitude': 45.024106, 'longitude': 37.911914}
        p = study.predict(model, r)
        self.assertEqual(p['prediction_c'], r['gfs']['temperature']); self.assertEqual(p['route'], 'new_position_no_prior_observations')
        self.assertFalse(p['local_calibration_applied']); self.assertFalse(p['site_cold_validated'])

    def test_few_shot_bias_uses_only_historical_local_observations(self):
        _, _, model, valid = setup('local_bias_5'); r = next(r for r in valid if r['id'] == 'anapa_synoptic')
        p = study.predict(model, r)
        self.assertTrue(p['local_calibration_applied']); self.assertAlmostEqual(p['prediction_c']-r['gfs']['temperature'], 6/11)

    def test_matched_station_delta_correct_sign(self):
        _, rm, model, valid = setup(); r = next(r for r in valid if r['id'] == 'anapa_synoptic')
        self.assertAlmostEqual(study.predict(model, r)['prediction_c']-ref.predict(rm, r), 3*6/11)

    def test_fewer_than_three_prior_nights_falls_back(self):
        rr, rm, _, valid = setup(); count = 0
        for r in rr:
            if r['id'] == 'anapa_synoptic' and r['lead_hours'] == 10:
                count += 1
                if count > 2: r['target_temperature_c'] = None
        model = study.fit('paired_delta_5', rr, rm, [2022], 2023)
        q = next(r for r in valid if r['id'] == 'anapa_synoptic' and r['lead_hours'] == 10)
        self.assertEqual(study.predict(model, q)['route'], 'insufficient_prior_exact_lead_support')

    def test_unmatched_reference_observation_not_fabricated(self):
        rr, rm, _, valid = setup()
        for r in rr:
            if r['id'] == 'vityazevo_airport' and r['lead_hours'] == 10: r['target_temperature_c'] = None
        model = study.fit('paired_delta_5', rr, rm, [2022], 2023)
        q = next(r for r in valid if r['id'] == 'anapa_synoptic' and r['lead_hours'] == 10)
        self.assertEqual(study.predict(model, q)['route'], 'insufficient_prior_exact_lead_support')

    def test_cold_forecast_outside_warm_support_falls_back(self):
        _, _, model, valid = setup(); r = next(r for r in valid if r['id'] == 'anapa_synoptic')
        r['gfs']['temperature'] = -3.
        p = study.predict(model, r)
        self.assertEqual(p['prediction_c'], -3.); self.assertFalse(p['site_cold_validated'])
        self.assertEqual(p['route'], 'outside_prior_local_temperature_support')

    def test_target_free_inference_and_control_year_guard(self):
        rr, rm, _, valid = setup()
        for name in study.CANDIDATES:
            m = study.fit(name, rr, rm, [2022], 2023)
            for r in valid[:28]:
                clean = {k: v for k, v in r.items() if not k.startswith('target')}
                self.assertEqual(study.predict(m, r), study.predict(m, clean))
                poisoned = {**r, 'target_temperature_c': -1000.}
                self.assertEqual(study.predict(m, r), study.predict(m, poisoned))
            for y in [2024, 2025, 2026]:
                q = deepcopy(valid[0]); q['year'] = y; q['date'] = f'{y}-03-01'
                with self.assertRaises(ValueError): study.predict(m, q)

    def test_selection_cannot_trade_one_year_or_safety_for_other(self):
        scores = {}; preserved = {}
        for name in study.CANDIDATES:
            scores[name] = {sid: {y: {'all_slots': {'n': 10, 'mae_c': 1.}, 'observed_term_false_cold': 0,
                                     'observed_term_missed_cold': 0} for y in ['2023', '2024']} for sid in ['vityazevo_airport', 'anapa_synoptic']}
            preserved[name] = True
        for year in ['2023', '2024']: scores['local_bias_5']['anapa_synoptic'][year]['all_slots']['mae_c'] = .9
        self.assertEqual(study.select(scores, preserved)[0], 'local_bias_5')
        scores['local_bias_5']['anapa_synoptic']['2024']['all_slots']['mae_c'] = 1.01
        self.assertEqual(study.select(scores, preserved)[0], 'identity_gfs')
        scores['local_bias_5']['anapa_synoptic']['2024']['all_slots']['mae_c'] = .9
        preserved['local_bias_5'] = False
        self.assertEqual(study.select(scores, preserved)[0], 'identity_gfs')
        preserved['local_bias_5'] = True; scores['local_bias_5']['anapa_synoptic']['2024']['observed_term_false_cold'] = 1
        self.assertEqual(study.select(scores, preserved)[0], 'identity_gfs')


if __name__ == '__main__': unittest.main()
