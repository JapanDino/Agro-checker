from copy import deepcopy
from datetime import date, timedelta
import gzip
import json
import unittest
from unittest.mock import patch
from agro_checker import cold_reliability as model
from agro_checker import cold_reliability_study as study
from agro_checker import cold_reliability_archive as archive
from agro_checker.pipeline import ROOT
from agro_checker.product_data import sha


class ColdReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.p = json.loads(study.PROTOCOL.read_text(encoding='utf-8'))
        self.bank = []
        for i in range(92):
            d = (date(2023, 3, 1)+timedelta(days=i)).isoformat()
            for sid in ['ref', 'a', 'b', 'c']:
                self.bank.append({'id': sid, 'role': 'reference' if sid == 'ref' else 'development',
                                  'date': d, 'year': 2023, 'elevation_m': 30., 'actual_c': -1.,
                                  'point_huber_c': 1., 'point_gfs_c': 2., 'point_fit_last_year': 2022})
        self.spec = model.registry(self.p)[1]

    def cal(self, bank=None, **kwargs):
        args = dict(anchor='gfs', query_id='new', elevation_m=30.)
        args.update(kwargs)
        return model.calibrate(self.bank if bank is None else bank, self.spec, self.p, **args)

    def predict(self, cal, **kwargs):
        args = dict(point_c=2., gfs_minimum_c=2., date_string='2024-03-01', query_id='new', elevation_m=30.)
        args.update(kwargs)
        return model.forecast(cal, **args)

    def test_purge_excludes_entire_week_and_neighbours_at_all_sites(self):
        day = '2023-03-15'; cal = self.cal(exclude_day=day)
        self.assertEqual(cal['excluded_window'], ('2023-03-11', '2023-03-21'))
        self.assertFalse(any('2023-03-11' <= d <= '2023-03-21' for d in cal['donor_dates']))
        self.predict(cal, date_string=day)

    def test_neighbour_stations_are_one_tail_date_not_independent_nights(self):
        a = self.cal(); b = self.cal(self.bank+[{**r, 'id': 'duplicate-site'} for r in self.bank if r['id'] == 'a'])
        self.assertEqual(a['tail_dates'], 92)
        self.assertEqual(a['tail_rank'], b['tail_rank'])
        self.assertEqual(a['tail_radius_c'], b['tail_radius_c'])

    def test_regional_calibration_leaves_query_station_out(self):
        bank = deepcopy(self.bank)
        for r in bank:
            if r['id'] == 'a': r['actual_c'] = -50.
        a = self.cal(bank, query_id='a')
        self.assertNotIn('a', a['donor_sites'])
        self.assertEqual(a['tail_radius_c'], 3.)

    def test_future_and_heldout_labels_rejected(self):
        for year in [2022, 2025, 2026]:
            bank = deepcopy(self.bank); bank[0]['year'] = year; bank[0]['date'] = f'{year}-03-01'
            with self.assertRaises(ValueError): self.cal(bank)
        bank = deepcopy(self.bank); bank[0]['role'] = 'heldout_site'
        with self.assertRaises(ValueError): self.cal(bank)
        bank = deepcopy(self.bank); bank[0]['point_fit_last_year'] = 2023
        with self.assertRaises(ValueError): self.cal(bank)

    def test_no_future_calibration_or_wrong_site_at_inference(self):
        cal = self.cal()
        for kwargs in [dict(date_string='2023-03-01'), dict(query_id='other'), dict(elevation_m=31.), dict(point_c=float('nan'))]:
            with self.assertRaises(ValueError): self.predict(cal, **kwargs)

    def test_rare_tail_uses_explicit_fallback_not_capped_quantile_rank(self):
        bank = deepcopy(self.bank)
        for r in bank:
            if r['date'] > '2023-03-05': r['actual_c'] = 10.
        cal = self.cal(bank)
        self.assertTrue(cal['tail_unavailable']); self.assertIsNone(cal['tail_radius_c'])
        f = self.predict(cal)
        self.assertTrue(f['tail_unavailable'])

    def test_tail_only_widens_lower_bound_preserves_point_and_warm_forecast(self):
        cal = self.cal(); before = deepcopy(cal)
        cold = self.predict(cal); warm = self.predict(cal, point_c=10., gfs_minimum_c=10.)
        self.assertEqual(cold['point_c'], 2.)
        self.assertEqual(warm['lower_radius_c'], cal['baseline']['base_radius_c'])
        self.assertFalse(warm['tail_extension_active'])
        self.assertEqual(cold['upper_radius_c'], cal['baseline']['upper_radius_c'])
        self.assertFalse(cold['coverage_guaranteed']); self.assertIsNone(cold['probability_of_crop_damage'])
        self.assertEqual(cal, before)

    def test_bad_duplicate_or_nonfinite_calibration_rejected(self):
        with self.assertRaises(ValueError): self.cal(self.bank+[self.bank[0]])
        for value in [float('inf'), True]:
            bank = deepcopy(self.bank); bank[0]['actual_c'] = value
            with self.assertRaises(ValueError): self.cal(bank)

    def test_isd_parser_rejects_duplicate_timestamps_and_missing_not_imputed(self):
        line = '2024 3 1 16 -9999 -9999 0 0 0 0 0 0\n'
        raw = gzip.compress(line.encode())
        self.assertIsNone(next(iter(archive.parse_observations(raw, 2024).values())))
        with self.assertRaises(ValueError): archive.parse_observations(gzip.compress((line+line).encode()), 2024)
        with self.assertRaises(ValueError): archive.parse_observations(raw, 2023)


class FrozenColdReliabilityTests(unittest.TestCase):
    def test_selection_never_opens_new_site_or_control_targets(self):
        if not study.LOCK.exists(): self.skipTest('Run prior selection first')
        original_load = study.data.load; original_rows = study.load_rows
        def checked_load(partition):
            self.assertEqual(partition, 'development'); return original_load(partition)
        def checked_rows(directory, years, config=None):
            self.assertTrue(set(years) <= {2022, 2023, 2024}); return original_rows(directory, years, config)
        with patch.object(study.data, 'load', side_effect=checked_load), patch.object(study, 'load_rows', side_effect=checked_rows), \
             patch.object(archive, 'load', side_effect=AssertionError('No test labels in selection')), \
             patch.object(archive, 'parse_observations', side_effect=AssertionError('No test labels in selection')):
            s = study.selection()
        self.assertFalse(s['new_station_labels_loaded_in_selection'])
        self.assertFalse(s['old_heldout_sites_loaded_in_selection'])

    def test_frozen_choice_and_original_detector_match_evaluation(self):
        path = ROOT/'docs/COLD_RELIABILITY_SUMMARY.json'
        if not path.exists(): self.skipTest('Run controls first')
        result = json.loads(path.read_text(encoding='utf-8')); s = study.selection()
        self.assertEqual(s, result['selection']); self.assertEqual(sha(study.LOCK), result['selection_sha256'])
        self.assertEqual(result['original_detector_verified'], 184)
        for file, key in [('reference-control-forecasts.json', 'reference_forecasts_sha256'), ('spatial-control-forecasts.json', 'spatial_forecasts_sha256')]:
            self.assertEqual(sha(archive.DIRECTORY/file), result[key])
        frames = json.loads((archive.DIRECTORY/'spatial-control-forecasts.json').read_text(encoding='utf-8'))
        for f in frames:
            self.assertIsNone(f['detector'])
            self.assertEqual(f['forecasts']['previous']['point_c'], f['forecasts']['selected']['point_c'])
        if s['winner'] != 'previous': self.assertTrue(all(s['gates'][s['winner']].values()))


if __name__ == '__main__': unittest.main()
