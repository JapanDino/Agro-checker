from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from agro_checker.pipeline import ROOT
from agro_checker.scenario_study import nights
from agro_checker.minimum_model import fit_minimum, target_minimum
from agro_checker.conditional_error import (crossfit_residuals, fit_head, shape, calibration_pool,
    calibrate_shape, forecast, safe_conditional_prediction)
from agro_checker.conditional_error_study import select, select_winner
from test_minimum_model import season


class ConditionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = json.loads((ROOT/'config/conditional_error.json').read_text(encoding='utf-8'))
        cls.spec = {'name': 'huber_1', 'kind': 'huber', 'alpha': 1.}
        cls.samples = nights(season(2022))
        cls.point = fit_minimum(cls.spec, cls.samples)
        cls.oof, cls.audit = crossfit_residuals(cls.samples, cls.spec)
        cls.head = fit_head(cls.oof, cls.audit, cls.p['candidates'][2])
        cls.valid = nights(season(2024))
        cls.pool = calibration_pool(cls.point, cls.head, cls.valid, cls.p, 2024)
        cls.cal = calibrate_shape(cls.pool, cls.p)

    def test_blocked_point_predictions_exclude_all_held_targets(self):
        changed = deepcopy(self.samples)
        held = set(self.audit[0]['held_dates'])
        for n in changed:
            if n['date'] in held: n['target']['temperature'] = [x+100 for x in n['target']['temperature']]
        residuals, audit = crossfit_residuals(changed, self.spec)
        old = {r['date']: r for r in self.oof}
        for r in residuals:
            if r['date'] in held: self.assertAlmostEqual(r['error_c']-old[r['date']]['error_c'], 100)
        for b in audit: self.assertFalse(set(b['fit_dates']) & set(b['held_dates']))

    def test_control_targets_and_overlap_rejected(self):
        for year in [2024, 2025, 2026]:
            with self.assertRaises(ValueError): crossfit_residuals(nights(season(year)), self.spec)
        for year in [2022, 2025, 2026]:
            with self.assertRaises(ValueError): calibration_pool(self.point, self.head, nights(season(year)), self.p, year)
        head = deepcopy(self.head); head['fit_dates'].append(self.valid[0]['date'])
        with self.assertRaises(ValueError): calibration_pool(self.point, head, self.valid, self.p, 2024)
        audit = deepcopy(self.audit); audit[0]['fit_dates'].append(audit[0]['held_dates'][0])
        with self.assertRaises(ValueError): fit_head(self.oof, audit, self.p['candidates'][2])

    def test_inference_ignores_facts_json_roundtrip_and_no_damage_claim(self):
        n = deepcopy(self.valid[0]); old = deepcopy(n)
        result = forecast(self.point, self.head, self.cal, n, self.p)
        self.assertEqual(n, old)
        n['target'] = {'temperature': [999]*14}
        self.assertEqual(result, forecast(self.point, json.loads(json.dumps(self.head)), self.cal, n, self.p))
        self.assertFalse(result['coverage_guaranteed']); self.assertIsNone(result['probability_of_crop_damage'])

    def test_loo_excludes_held_calibration_and_missing_truth_is_not_filled(self):
        pool = deepcopy(self.pool); date = pool[0]['date']; pool[0]['score'] = 1e9
        self.assertEqual(calibrate_shape(pool, self.p, date), calibrate_shape(self.pool, self.p, date))
        valid = deepcopy(self.valid); valid[0]['target']['temperature'][0] = None
        self.assertEqual(len(calibration_pool(self.point, self.head, valid, self.p, 2024)), 39)
        with self.assertRaises(ValueError): calibrate_shape(pool[:10], self.p)
        bad = deepcopy(pool); bad[0]['score'] = float('nan')
        with self.assertRaises(ValueError): calibrate_shape(bad, self.p)

    def test_crossed_tails_are_sorted_and_include_point(self):
        head = deepcopy(self.head); head['spec']['mode'] = 'quantile'
        head['heads'] = [{'coef': [0]*15, 'intercept': 3}, {'coef': [0]*15, 'intercept': -2}]
        s = shape(head, self.valid[0], self.p)
        self.assertEqual((s['lower'], s['upper']), (-2, 3))
        result = forecast(self.point, head, {'value': 1}, self.valid[0], self.p)
        self.assertAlmostEqual(result['upper_c']-result['lower_c'], 7)
        with self.assertRaises(ValueError): forecast(self.point, head, {'value': -1}, self.valid[0], self.p)

    def test_tree_json_inference_matches_sklearn(self):
        from sklearn.ensemble import GradientBoostingRegressor
        spec = self.p['candidates'][-1]; head = fit_head(self.oof, self.audit, spec)
        x = np.asarray([r['x'] for r in self.oof]); z = (x-np.array(head['mean']))/np.array(head['scale'])
        y = [r['error_c'] for r in self.oof]
        pred = []
        for q in [.05, .95]:
            est = GradientBoostingRegressor(loss='quantile', alpha=q, n_estimators=spec['estimators'],
                max_depth=spec['depth'], min_samples_leaf=spec['minimum_leaf'], learning_rate=spec['learning_rate'],
                random_state=spec['seed']).fit(z, y)
            pred.append(est.predict(z[:1])[0])
        s = shape(json.loads(json.dumps(head)), self.samples[0], self.p)
        self.assertAlmostEqual(s['lower'], min(*pred, 0), places=10)
        self.assertAlmostEqual(s['upper'], max(*pred, 0), places=10)

    def test_domain_and_missing_weather_guards(self):
        self.assertEqual(safe_conditional_prediction(self.point, self.head, self.cal, self.valid[0], self.p, 'orchard')['status'], 'unsupported_site')
        n = deepcopy(self.valid[0]); n['rows'][0]['features_weather'][4] = None
        self.assertIsNone(safe_conditional_prediction(self.point, self.head, self.cal, n, self.p, '34929')['prediction'])
        n = deepcopy(self.valid[0]); n['rows'][0]['forecast_run_at_utc'] = '2024-03-01T12:00:00+00:00'
        self.assertIsNone(safe_conditional_prediction(self.point, self.head, self.cal, n, self.p, '34929')['prediction'])

    def test_width_gates_reject_covering_everything_by_expansion(self):
        base = {'covered': 80, 'cold_nights': 7, 'cold_covered': 5, 'cold_interval_score': 8.,
                'warm_interval_score': 6., 'mean_width_c': 6., 'warm_mean_width_c': 6.}
        scores = {s['name']: deepcopy(base) for s in self.p['candidates']}
        scores['scale_ridge_10'].update(cold_covered=7, mean_width_c=20, warm_mean_width_c=20)
        winner, gates = select_winner(scores, self.p)
        self.assertEqual(winner, 'legacy_symmetric'); self.assertFalse(gates['scale_ridge_10']['warm_width_within_limit'])

    def test_challenger_nomination_uses_only_prior_scores_and_remains_frozen(self):
        from agro_checker.conditional_error_challenger import nominate
        base = {'covered': 80, 'cold_nights': 7, 'cold_covered': 5, 'cold_interval_score': 8.,
                'warm_interval_score': 6., 'mean_width_c': 6., 'warm_mean_width_c': 6.}
        scores = {s['name']: deepcopy(base) for s in self.p['candidates']}
        scores['tails_ridge_10']['cold_covered'] = 6
        selection = {'protocol': self.p, 'pooled_validation': scores,
                     'gates': {s['name']: {'ok': True} for s in self.p['candidates']}}
        rows = season(2022)+season(2023)+season(2024)
        parent = {'point_model': fit_minimum(self.spec, nights(season(2022)+season(2023)))}
        calls = []
        def loader(directory, years):
            calls.append(years); self.assertEqual(years, [2022, 2023, 2024]); return rows, {'test': 'prior'}
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'; data.mkdir(); directory = data.parent/'models/research-conditional-errors'; directory.mkdir(parents=True)
            (directory/'selection-before-controls.json').write_text(json.dumps(selection), encoding='utf-8')
            with patch('agro_checker.conditional_error_challenger.select', return_value=selection), patch('agro_checker.conditional_error_challenger.parent_assets', return_value=parent), patch('agro_checker.conditional_error_challenger.load_rows', side_effect=loader):
                result = nominate(ROOT/'config/conditional_error.json', data)
                self.assertEqual(result['name'], 'tails_ridge_10')
                self.assertEqual(result, nominate(ROOT/'config/conditional_error.json', data))
                (directory/'challenger-head.json').write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'artifact'): nominate(ROOT/'config/conditional_error.json', data)
        self.assertEqual(calls, [[2022, 2023, 2024]])

    def test_selection_reads_only_prior_years_and_freeze_rejects_tampering(self):
        rows = season(2022)+season(2023)+season(2024)
        point = fit_minimum(self.spec, nights(season(2022)+season(2023)))
        calls = []
        def loader(directory, years):
            calls.append(years); self.assertEqual(years, [2022, 2023, 2024]); return rows, {'test': 'prior'}
        parent = {'point_model': point, 'hashes': {'test': 'fixed'}}
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'; data.mkdir()
            with patch('agro_checker.conditional_error_study.parent_assets', return_value=parent), patch('agro_checker.conditional_error_study.load_rows', side_effect=loader):
                a = select(ROOT/'config/conditional_error.json', data)
                artifact = data.parent/'models/research-conditional-errors/head.json'; before = artifact.read_bytes()
                self.assertEqual(a, select(ROOT/'config/conditional_error.json', data)); self.assertEqual(before, artifact.read_bytes())
                artifact.write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'artifact'): select(ROOT/'config/conditional_error.json', data)
        self.assertEqual(calls, [[2022, 2023, 2024]]*2)


if __name__ == '__main__': unittest.main()
