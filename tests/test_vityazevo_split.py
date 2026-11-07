from copy import deepcopy
from datetime import date
import gzip
import unittest
from agro_checker import vityazevo_split_data as archive
from agro_checker import vityazevo_split_study as study

SITES = [{'id': 'vityazevo_airport', 'role': 'development', 'latitude': 45.002, 'longitude': 37.347, 'elevation_m': 53},
         {'id': 'anapa_synoptic', 'role': 'spatial_check_only', 'latitude': 44.883, 'longitude': 37.283, 'elevation_m': 32}]


def message(site=0, temperature='+0000', qc='1', year=2022, minute=0):
    line = list(' '*105); line[:15] = list('000037001099999'); line[15:27] = list(f'{year}030116{minute:02}')
    line[28:34] = list('+45002' if site == 0 else '+44883'); line[34:41] = list('+037347' if site == 0 else '+037283')
    line[41:46] = list('FM-15' if site == 0 else 'FM-12'); line[46:51] = list('+0053' if site == 0 else '+0032')
    line[56:60] = list('V020'); line[65:69] = list('0010'); line[69] = '1'
    line[87:92] = list(temperature); line[92] = qc; line[93:98] = list('-0010'); line[98] = '1'
    return ''.join(line)


def curves():
    return {k: [float(i) for i in range(14)] for k in ['temperature', 'dewpoint', 'u10', 'v10', 'rh', 'cloud']}


def night(site=0, year=2022):
    n = archive.night(date(year, 3, 1), SITES[site], curves(), {})
    n['target_temperature_c'] = [i-1. for i in range(14)]
    return n


class VityazevoSplitTests(unittest.TestCase):
    def test_raw_same_id_same_time_two_positions_split(self):
        groups, unknown = archive.parse_full(gzip.compress((message()+'\n'+message(site=1, temperature='-0020')).encode()), 2022, SITES)
        self.assertFalse(unknown)
        self.assertEqual(groups['vityazevo_airport'][0]['temperature_c'], 0.)
        self.assertEqual(groups['anapa_synoptic'][0]['temperature_c'], -2.)
        self.assertEqual(groups['vityazevo_airport'][0]['observed_at_utc'], groups['anapa_synoptic'][0]['observed_at_utc'])

    def test_unknown_position_quarantined_not_nearest_assigned(self):
        row = message().replace('+45002', '+45003')
        groups, unknown = archive.parse_full(gzip.compress(row.encode()), 2022, SITES)
        self.assertEqual(len(unknown), 1); self.assertFalse(any(groups.values()))

    def test_noaa_qc_not_roshydromet_and_raw_value_retained(self):
        for qc in ['0', '1', '2', '5', '9', 'I', 'M']:
            groups, _ = archive.parse_full(gzip.compress(message(temperature='-0010', qc=qc).encode()), 2022, SITES)
            r = groups['vityazevo_airport'][0]
            self.assertEqual(r['raw_temperature_c'], -1.)
            self.assertEqual(r['temperature_c'], -1. if qc in ['1', '5'] else None)

    def test_missing_sentinel_not_zero(self):
        groups, _ = archive.parse_full(gzip.compress(message(temperature='+9999').encode()), 2022, SITES)
        self.assertIsNone(groups['vityazevo_airport'][0]['temperature_c'])

    def test_bad_station_year_and_schema_rejected(self):
        with self.assertRaises(ValueError): archive.parse_full(gzip.compress(message().replace('37001099999','34929099999').encode()), 2022, SITES)
        with self.assertRaises(ValueError): archive.parse_full(gzip.compress(message(year=2023).encode()), 2022, SITES)
        with self.assertRaises(ValueError): archive.parse_full(gzip.compress(b'HTML is not observations'), 2022, SITES)
        for year in [2025, 2026]:
            with self.assertRaises(ValueError): archive.parse_full(b'', year, SITES)

    def test_conflicting_same_site_duplicates_unavailable(self):
        groups, _ = archive.parse_full(gzip.compress((message()+'\n'+message(temperature='+0010')).encode()), 2022, SITES)
        values, conflicts = archive.index_observations(groups['vityazevo_airport'], SITES[0])
        self.assertFalse(values); self.assertEqual(len(conflicts), 1)

    def test_different_sites_cannot_be_indexed_together(self):
        groups, _ = archive.parse_full(gzip.compress((message()+'\n'+message(site=1)).encode()), 2022, SITES)
        with self.assertRaises(ValueError): archive.index_observations(groups['vityazevo_airport']+groups['anapa_synoptic'], SITES[0])

    def test_exact_time_join_does_not_round_half_hour(self):
        groups, _ = archive.parse_full(gzip.compress(message(minute=30).encode()), 2022, SITES)
        values, _ = archive.index_observations(groups['vityazevo_airport'], SITES[0])
        n = archive.night(date(2022, 3, 1), SITES[0], curves(), values)
        self.assertTrue(all(t is None for t in n['target_temperature_c']))
        self.assertEqual(n['run_at_utc'], '2022-03-01T06:00:00+00:00')
        self.assertEqual(n['issued_at_utc'], '2022-03-01T15:00:00+00:00')

    def test_fit_refuses_anapa_validation_and_controls(self):
        with self.assertRaises(ValueError): study.fit('bias', study.rows([night(site=1)]))
        for year in [2024, 2025, 2026]:
            rr = study.rows([night()]); rr[0]['year'] = year
            with self.assertRaises(ValueError): study.fit('bias', rr)

    def test_forecast_features_and_inference_independent_of_targets(self):
        rr = study.rows([night()])
        for name in study.CANDIDATES:
            model = study.fit(name, rr); clean = {k: v for k, v in rr[0].items() if not k.startswith('target')}
            self.assertEqual(study.predict(model, rr[0]), study.predict(model, clean))
            self.assertEqual(study.features(rr[0], True), study.features(clean, True))

    def test_full_night_metrics_not_inferred_from_partial_terms(self):
        n = night(); n['target_temperature_c'][0] = None
        rr = [{**r, 'prediction_c': 0.} for r in study.rows([n])]; m = study.metrics(rr)
        self.assertEqual(m['all_slots']['n'], 13)
        self.assertEqual(m['complete_14_slot_nights']['nights'], 0)
        self.assertIsNone(m['complete_14_slot_nights']['minimum_error']['mae_c'])
        self.assertEqual(m['available_term_sampled_minima']['nights'], 1)
        self.assertFalse(m['is_original_detector_score'])

    def test_sites_not_pooled_by_shared_date_or_time(self):
        rr = [{**r, 'prediction_c': 0.} for r in study.rows([night(), night(site=1)])]
        self.assertEqual(study.metrics(rr)['complete_14_slot_nights']['nights'], 2)

    def test_selection_cannot_hide_minimum_or_false_cold_degradation(self):
        metric = {'all_slots': {'mae_c': 2.}, 'cold_slots': {'n': 5, 'mae_c': 2.},
                  'complete_14_slot_nights': {'cold_nights': 1, 'misses': 1, 'false_cold': 1, 'minimum_error': {'mae_c': 2.}}}
        results = {name: {str(y): deepcopy(metric) for y in [2023, 2024]} for name in study.CANDIDATES}
        for year in ['2023', '2024']: results['bias'][year]['all_slots']['mae_c'] = 1.
        self.assertEqual(study.select(results)[0], 'bias')
        results['bias']['2024']['complete_14_slot_nights']['minimum_error']['mae_c'] = 2.1
        self.assertEqual(study.select(results)[0], 'gfs')
        results['bias']['2024']['complete_14_slot_nights']['minimum_error']['mae_c'] = 2.
        results['bias']['2024']['complete_14_slot_nights']['false_cold'] = 2
        self.assertEqual(study.select(results)[0], 'gfs')


if __name__ == '__main__': unittest.main()
