from copy import deepcopy
from datetime import date, timedelta
import gzip
import unittest
from agro_checker import cold_station_data as data, cold_station_study as study
from agro_checker import vityazevo_split_data as split

SITES = [dict(id='345600-99999', latitude=48.783, longitude=44.346, elevation_m=147., role='development'),
         dict(id='348610-99999', latitude=46.367, longitude=44.333, elevation_m=153., role='development'),
         dict(id='349490-99999', latitude=45.109, longitude=42.113, elevation_m=453., role='spatial_check')]


def message(qc='1', minute=0, lat='+48783', year=2022, t='-0010'):
    line = list(' '*105); line[:15] = list('000034560099999'); line[15:27] = list(f'{year}030116{minute:02}')
    line[28:34] = list(lat); line[34:41] = list('+044346'); line[41:46] = list('FM-15'); line[46:51] = list('+0147')
    line[56:60] = list('V020'); line[87:92] = list(t); line[92] = qc
    return gzip.compress(''.join(line).encode())


def nights(year=2022, sites=SITES[:2]):
    nn = []
    for j in range(22):
        for k, site in enumerate(sites):
            curves = {key: [float((j%5)+i-3) for i in range(14)] for key in ['temperature', 'dewpoint', 'u10', 'v10', 'rh', 'cloud']}
            n = split.night(date(year, 3, 1)+timedelta(days=j), site, curves, {})
            n['gfs_surface_height_m'] = site['elevation_m']-50
            n['target_temperature_c'] = [v-1.5+k for v in curves['temperature']]; nn.append(n)
    return nn


def trained(name='pooled_geo_1000'):
    rr = study.rows(nights()); valid = study.rows(nights(2023, sites=[SITES[2]]))
    m = study.fit(name, rr, [2022], 2023, SITES[2]['id'], study.previous.physical_key(SITES[2]))
    return rr, m, valid


class ColdStationTransferTests(unittest.TestCase):
    def test_full_source_station_year_qc_and_minutes(self):
        for qc in ['0', '1', '2', '5', '9']:
            r = data.parse(message(qc, minute=30), '345600-99999', 2022)[0]
            self.assertEqual(r['temperature_c'], -1. if qc in ['1', '5'] else None)
            self.assertEqual(r['raw_temperature_c'], -1.)
            self.assertIn('16:30', r['observed_at_utc'])
        with self.assertRaises(ValueError): data.parse(message(), '349490-99999', 2022)
        with self.assertRaises(ValueError): data.parse(message(year=2023), '345600-99999', 2022)
        for y in [2025, 2026]:
            with self.assertRaises(ValueError): data.parse(message(year=y), '345600-99999', y)
        self.assertIsNone(data.parse(message(t='+9999'), '345600-99999', 2022)[0]['temperature_c'])

    def test_position_choice_uses_counts_not_temperatures(self):
        r = data.parse(message(), '345600-99999', 2022)[0]
        other = {**r, 'latitude': 48.8, 'temperature_c': -50.}
        records = [r, other, other, other]
        self.assertEqual(data.choose_position(records, SITES[0]), (48.783, 44.346, 147))

    def test_position_tie_or_different_grid_rejected(self):
        r = data.parse(message(), '345600-99999', 2022)[0]
        other = {**r, 'latitude': 48.784}
        with self.assertRaises(ValueError): data.choose_position([r, other], SITES[0])
        site = {**SITES[0], 'latitude': 48.876}; r['latitude'] = 48.874
        with self.assertRaises(ValueError): data.choose_position([r], site)
        with self.assertRaises(ValueError): data.cell(48.875, 44.346)

    def test_zero_shot_excludes_whole_target_and_spatial_labels(self):
        rr, m, _ = trained()
        self.assertNotIn(m['target_id'], m['training_sites'])
        with self.assertRaises(ValueError): study.fit('pooled_100', rr, [2022], 2023, SITES[0]['id'], study.previous.physical_key(SITES[0]))
        bad = deepcopy(rr); bad[0]['role'] = 'spatial_check'
        with self.assertRaises(ValueError): study.fit('pooled_100', bad, [2022], 2023, SITES[2]['id'], study.previous.physical_key(SITES[2]))

    def test_local_history_only_own_site_with_minimum_support(self):
        rr = study.rows(nights(sites=[SITES[2]])); pos = study.previous.physical_key(SITES[2])
        m = study.fit('local_bias_20', rr, [2022], 2023, SITES[2]['id'], pos)
        self.assertEqual(m['training_sites'], [SITES[2]['id']])
        self.assertAlmostEqual(m['lead_offsets']['10']['offset_c'], -1.5*22/42)
        with self.assertRaises(ValueError): study.fit('local_ridge_1000', rr[:14], [2022], 2023, SITES[2]['id'], pos)
        with self.assertRaises(ValueError): study.fit('local_bias_20', rr+study.rows(nights(sites=[SITES[0]])), [2022], 2023, SITES[2]['id'], pos)

    def test_fit_refuses_validation_control_and_wrong_reference_year(self):
        rr, _, _ = trained(); pos = study.previous.physical_key(SITES[2])
        for y in [2023, 2024, 2025, 2026]:
            bad = deepcopy(rr); bad[0]['year'] = y
            with self.assertRaises(ValueError): study.fit('pooled_100', bad, [2022], 2023, SITES[2]['id'], pos)
        with self.assertRaises(ValueError): study.fit('pooled_100', rr, [2022, 2023], 2023, SITES[2]['id'], pos)

    def test_inference_independent_of_actual_and_future_labels(self):
        for name in ['pooled_100', 'pooled_1000', 'pooled_geo_1000']:
            rr, model, valid = trained(name); r = valid[0]
            clean = {k: v for k, v in r.items() if not k.startswith('target')}
            self.assertEqual(study.predict(model, r), study.predict(model, clean))
            r['target_temperature_c'] = -1000.
            self.assertEqual(study.predict(model, r), study.predict(model, clean))

    def test_inference_refuses_changed_location_lead_and_control_year(self):
        _, m, valid = trained()
        for key in ['latitude', 'longitude', 'elevation_m']:
            q = deepcopy(valid[0]); q[key] += .01
            with self.assertRaises(ValueError): study.predict(m, q)
        q = deepcopy(valid[0]); q['observed_at_utc'] = '2023-03-01T16:30:00+00:00'
        with self.assertRaises(ValueError): study.predict(m, q)
        for y in [2024, 2025, 2026]:
            q = deepcopy(valid[0]); q['year'] = y
            with self.assertRaises(ValueError): study.predict(m, q)

    def test_local_blend_matches_frozen_fraction(self):
        rr = study.rows(nights(sites=[SITES[0]])); valid = study.rows(nights(2023, sites=[SITES[0]]))[0]
        mm = {name: study.fit(name, rr, [2022], 2023, SITES[0]['id'], study.previous.physical_key(SITES[0]))
              for name in ['local_ridge_1000', 'local_blend_25', 'local_blend_50']}
        full = study.predict(mm['local_ridge_1000'], valid); base = valid['gfs']['temperature']
        for name, w in [('local_blend_25', .25), ('local_blend_50', .5)]:
            self.assertAlmostEqual(study.predict(mm[name], valid), base+w*(full-base))

    def test_partial_cold_night_cannot_be_full_night_truth(self):
        rr = study.rows(nights(sites=[SITES[0]]))[:14]
        rr[0]['target_temperature_c'] = None
        scored = [{**r, 'prediction_c': -2., 'route': 'gfs', 'local_calibration_applied': False} for r in rr]
        m = study.score(scored)
        self.assertEqual(m['complete_nights'], 0); self.assertEqual(m['cold_complete_nights'], 0)
        self.assertFalse(m['is_original_detector_score'])

    def test_no_station_false_cold_tradeoff_or_year_tradeoff(self):
        metric = {'site_macro_hourly_mae_c': 2., 'site_macro_cold_slot_mae_c': 3., 'site_macro_minimum_mae_c': 2., 'misses': 2,
                  'by_site': {sid: {'complete_14_slot_nights': {'cold_nights': 2, 'false_cold': 1}} for sid in ['a', 'b']}}
        results = {name: {y: deepcopy(metric) for y in ['2023', '2024']} for name in study.CANDIDATES}
        for y in ['2023', '2024']: results['pooled_100'][y]['site_macro_hourly_mae_c'] = 1.5
        self.assertEqual(study.select(results)[0]['zero_shot'], 'pooled_100')
        results['pooled_100']['2024']['by_site']['a']['complete_14_slot_nights']['false_cold'] = 2
        results['pooled_100']['2024']['by_site']['b']['complete_14_slot_nights']['false_cold'] = 0
        self.assertEqual(study.select(results)[0]['zero_shot'], 'gfs')
        results['pooled_100']['2024']['by_site']['a']['complete_14_slot_nights']['false_cold'] = 1
        results['pooled_100']['2024']['site_macro_hourly_mae_c'] = 2.01
        self.assertEqual(study.select(results)[0]['zero_shot'], 'gfs')

    def test_cold_error_and_misses_cannot_be_hidden_by_general_gain(self):
        metric = {'site_macro_hourly_mae_c': 2., 'site_macro_cold_slot_mae_c': 3., 'site_macro_minimum_mae_c': 2., 'misses': 2,
                  'by_site': {'a': {'complete_14_slot_nights': {'cold_nights': 2, 'false_cold': 1}}}}
        results = {name: {y: deepcopy(metric) for y in ['2023', '2024']} for name in study.CANDIDATES}
        for y in ['2023', '2024']: results['local_ridge_100'][y]['site_macro_hourly_mae_c'] = 1.5
        for field, value in [('site_macro_cold_slot_mae_c', 3.1), ('site_macro_minimum_mae_c', 2.1), ('misses', 3)]:
            trial = deepcopy(results); trial['local_ridge_100']['2024'][field] = value
            self.assertEqual(study.select(trial)[0]['local_history'], 'gfs')


if __name__ == '__main__': unittest.main()
