from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agro_checker.cold_interval import calibrate_envelope, finite_quantile, forecast_envelope, residual_pool
from agro_checker.cold_interval_study import load_protocol, select_intervals, summarize
from agro_checker.cold_interval_inference import safe_cold_envelope
from agro_checker.minimum_model import calibrate, fit_minimum
from agro_checker.pipeline import ROOT
from agro_checker.scenario_study import nights
from test_minimum_model import season


def pool():
    return [{"date": str(i), "risk_temperature_c": 1 if i < 35 else 10,
             "overprediction_c": (i%8)-2., "absolute_error_c": abs((i%8)-2.)} for i in range(50)]


class ColdIntervalTests(unittest.TestCase):
    def setUp(self):
        self.p = load_protocol(ROOT/"config/cold_interval.json")
        self.spec = self.p["candidates"][3]

    def test_held_night_is_excluded_from_global_and_risk_tail(self):
        data = pool(); data[0]["absolute_error_c"] = 999; data[0]["overprediction_c"] = 999
        e = calibrate_envelope(data, self.spec, self.p, exclude_date="0")
        self.assertEqual(e["pool_nights"], 49)
        self.assertEqual(e["risk_pool_nights"], 34)
        self.assertLess(e["lower_radius_c"], 999)
        self.assertEqual(data[0]["absolute_error_c"], 999)

    def test_sparse_risk_pool_falls_back_and_never_caps_quantile_rank(self):
        data = pool()
        for r in data[5:]: r["risk_temperature_c"] = 10
        e = calibrate_envelope(data, self.spec, self.p)
        self.assertTrue(e["fallback_to_global"])
        self.assertEqual(e["tail_ranks"]["lower_radius_c"]["nights"], 50)
        self.assertIsNone(finite_quantile([1, 2, 3], .95))
        with self.assertRaises(ValueError): calibrate_envelope(data[:15], self.spec, self.p)
        with self.assertRaises(ValueError): finite_quantile([float("nan")]*40, .95)

    def test_signed_tails_preserve_legacy_and_do_not_force_symmetry(self):
        data = pool()
        for i, r in enumerate(data):
            r["risk_temperature_c"] = 1
            r["overprediction_c"] = i/2
            r["absolute_error_c"] = i/2
        e = calibrate_envelope(data, self.p["candidates"][-1], self.p)
        self.assertGreater(e["lower_radius_c"], e["upper_radius_c"])
        self.assertGreaterEqual(e["upper_radius_c"], e["base_radius_c"])

    def test_risk_group_uses_only_forecast_and_warm_keeps_original_bounds(self):
        samples = nights(season(2022))
        spec = {"name": "huber_1", "kind": "huber", "alpha": 1.}
        model = fit_minimum(spec, samples)
        e = calibrate_envelope(pool(), self.spec, self.p)
        n = deepcopy(samples[0])
        first = forecast_envelope(model, e, n, self.p)
        n["target"]["temperature"] = [-999]*14
        self.assertEqual(first, forecast_envelope(model, e, n, self.p))
        self.assertLessEqual(first["lower_c"], first["legacy_lower_c"])
        self.assertGreaterEqual(first["upper_c"], first["legacy_upper_c"])
        n = deepcopy(samples[0])
        for r in n["rows"]:
            r["base"]["temperature"] += 20; r["features_weather"][0] += 20
        n["base"]["temperature"] = [r["base"]["temperature"] for r in n["rows"]]
        warm = forecast_envelope(model, e, n, self.p)
        self.assertFalse(warm["risk_extension_active"])
        self.assertEqual(warm["lower_c"], warm["legacy_lower_c"])
        self.assertEqual(warm["upper_c"], warm["legacy_upper_c"])

    def test_calibration_rejects_controls_overlap_and_duplicate_nights(self):
        model = fit_minimum({"name": "huber_1", "kind": "huber", "alpha": 1.}, nights(season(2022)))
        for year in [2025, 2026]:
            with self.assertRaises(ValueError): residual_pool(model, nights(season(year)), [2024])
        with self.assertRaisesRegex(ValueError, "overlaps"): residual_pool(model, nights(season(2022)), [2022])
        data = nights(season(2024))
        with self.assertRaisesRegex(ValueError, "Duplicate"): residual_pool(model, data+[data[0]], [2024])
        data[0]["target"]["temperature"][0] = None
        self.assertEqual(len(residual_pool(model, data, [2024])), 39)

    def test_score_reports_conditional_misses_width_and_unknown_truth(self):
        records = [
            {"forecast": {"point_c": 0, "lower_c": -2, "upper_c": 2}, "actual_minimum_c": -3},
            {"forecast": {"point_c": 3, "lower_c": 2, "upper_c": 4}, "actual_minimum_c": 3},
            {"forecast": {"point_c": 3, "lower_c": 2, "upper_c": 4}, "actual_minimum_c": None}]
        s = summarize(records)
        self.assertEqual(s["cold_covered"], 0); self.assertEqual(s["lower_cold_misses"], 1)
        self.assertEqual(s["nights"], 2); self.assertEqual(s["excluded_nights"], 1)
        self.assertEqual(s["mean_width_c"], 3); self.assertEqual(s["cold_mean_width_c"], 4)

    def test_parent_domain_guard_is_preserved_for_new_interval(self):
        spec = {"name": "huber_1", "kind": "huber", "alpha": 1.}
        model = fit_minimum(spec, nights(season(2022)))
        parent_protocol = {"calibration_year": 2024, "minimum_calibration_nights": 30, "interval_alpha": .1, "ood_max_abs_z": 4}
        samples = nights(season(2024))
        model["calibration"] = calibrate(model, samples, parent_protocol)
        envelope = calibrate_envelope(residual_pool(model, samples, [2024]), self.spec, self.p)
        n = nights(season(2023))[0]
        self.assertEqual(safe_cold_envelope(model, envelope, n, self.p, parent_protocol, "orchard")["status"], "unsupported_site")
        self.assertEqual(safe_cold_envelope(model, envelope, n, self.p, parent_protocol, "34929")["status"], "research_only")
        for r in n["rows"]: r["features_weather"][4] = 95.
        self.assertIsNone(safe_cold_envelope(model, envelope, n, self.p, parent_protocol, "34929")["prediction"])

    def test_selector_only_reads_prior_years_and_frozen_envelope_is_not_refitted(self):
        spec = {"name": "huber_1", "kind": "huber", "alpha": 1.}
        model = fit_minimum(spec, nights(season(2022)+season(2023)))
        model["calibration"] = calibrate(model, nights(season(2024)), {"calibration_year": 2024,
            "minimum_calibration_nights": 30, "interval_alpha": .1})
        parent = {"point_model": model, "hashes": {"fixture": "fixed"}}
        calls = []
        def load(_, years):
            calls.append(years); self.assertEqual(years, [2022, 2023, 2024])
            return season(2022)+season(2023)+season(2024), {"fixture": "fixed"}
        with tempfile.TemporaryDirectory() as temp, patch("agro_checker.cold_interval_study.parent_assets", return_value=parent), patch("agro_checker.cold_interval_study.load_rows", side_effect=load):
            data = Path(temp)/"data"
            selected = select_intervals(ROOT/"config/cold_interval.json", data)
            path = Path(temp)/"models/research-cold-intervals/selection-before-controls.json"
            before = path.read_bytes()
            self.assertEqual(select_intervals(ROOT/"config/cold_interval.json", data), selected)
            self.assertEqual(path.read_bytes(), before)
            (path.parent/"envelope.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "hash"): select_intervals(ROOT/"config/cold_interval.json", data)
