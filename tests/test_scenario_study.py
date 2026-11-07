from copy import deepcopy
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agro_checker.pipeline import ROOT, UTC
from agro_checker.scenario_study import derived, fit_candidate, load_model, load_scenario_protocol, nights, predict_candidate, save_model, score_predictions
from agro_checker.scenario_fresh import collect_fresh
from agro_checker.scenario_observation_audit import parse_metar


def rows(count=24):
    data = []
    for j in range(count):
        day = datetime(2022, 3, 1, 16, tzinfo=UTC)+timedelta(days=j)
        for i in range(14):
            temp = 3-i*.2+j*.03; rh = 75+i*.5; wind = 3+j*.01
            features = [temp, rh, 1., temp-1, wind, 30., -.2, temp-1., temp, 2., rh, 5., wind, 30., 0., 0.]+[float(i == k) for k in range(14)]
            data.append({"date": day.date().isoformat(), "year": 2022, "hour_index": i,
                         "valid_at_utc": (day+timedelta(hours=i)).isoformat(), "forecast_run_at_utc": day.replace(hour=6).isoformat(),
                         "issued_at_utc": day.replace(hour=15).isoformat(), "base": {"temperature": temp, "humidity": rh, "wind": wind},
                         "target": {"temperature": temp+(-3 if j%2 else .5), "humidity": rh+5, "wind": wind-.5},
                         "features_weather": features, "source": {t: "fixture" for t in ["temperature", "humidity", "wind"]}})
    return data


class ScenarioStudyTests(unittest.TestCase):
    def setUp(self):
        self.p = load_scenario_protocol(ROOT/"config/scenario_study.json")
        self.r = rows(); self.n = nights(self.r)
        self.smooth = next(s for s in self.p["candidates"] if s["name"] == "smooth_ridge_10")
        self.analog = next(s for s in self.p["candidates"] if s["name"] == "analog_weather_8")

    def test_fit_rejects_both_historical_and_fresh_target_years(self):
        for year in [2025, 2026]:
            r = deepcopy(self.r); r[-1]["year"] = year
            with self.assertRaisesRegex(ValueError, "2025/2026"): fit_candidate(self.smooth, r, self.p)

    def test_prediction_independent_of_validation_target_poisoning(self):
        for spec in [self.smooth, self.analog]:
            model = fit_candidate(spec, self.r, self.p)
            n = deepcopy(self.n[0]); before = predict_candidate(model, n, self.p)
            n["target"] = {t: [-50]*14 for t in self.p["targets"]}
            for r in n["rows"]: r["target"] = {t: -50 for t in self.p["targets"]}
            self.assertEqual(before, predict_candidate(model, n, self.p))

    def test_gfs_prediction_does_not_mutate_source_input(self):
        n = deepcopy(self.n[0]); before = deepcopy(n)
        model = fit_candidate(self.p["candidates"][0], self.r, self.p)
        predict_candidate(model, n, self.p)
        self.assertEqual(n, before)

    def test_analog_library_excludes_incomplete_joint_targets_without_imputation(self):
        r = deepcopy(self.r); r[0]["target"]["wind"] = None
        m = fit_candidate(self.analog, r, self.p)
        self.assertEqual(m["complete_library_nights"], 23); self.assertEqual(m["excluded_joint_nights"], 1)
        self.assertNotIn(r[0]["date"], [a["date"] for a in m["library"]])

    def test_warning_is_one_whole_conditional_path_not_filled_hours(self):
        m = fit_candidate(self.analog, self.r, self.p); f = predict_candidate(m, self.n[0], self.p)
        self.assertIn(f["warning_curve"], [member["temperature"] for member in f["members"]])
        self.assertEqual(f["warning"]["cold_bins_hours"] > 0, f["event_score"] >= .25)
        self.assertEqual(f["member_count"], 8)
        self.assertTrue(all(date.startswith("2022") for date in f["analog_dates"]))

    def test_median_minimum_is_not_minimum_of_marginal_median(self):
        n = deepcopy(self.n[0]); base = n["base"]["temperature"]
        paths = [[-1, 3]+[3]*12, [3, -1]+[3]*12]
        spec = {**self.analog, "neighbors": 2, "feature_groups": ["temperature"]}
        features = [r["features_weather"][0] for r in n["rows"]]
        m = {"spec": spec, "scale": [1.]*14, "library": [{"date": str(i), "forecast_features": features,
             "residual": {"temperature": [t-b for t, b in zip(path, base)], "humidity": [0.]*14, "wind": [0.]*14}} for i, path in enumerate(paths)]}
        f = predict_candidate(m, n, self.p)
        self.assertEqual(f["aggregate_quantiles"]["minimum_c"]["median"], -1)
        self.assertEqual(min(f["point"]["temperature"]), 1)
        self.assertTrue(f["warning"]["cold_bins_hours"] > 0)

    def test_derived_targets_preserve_strict_boundaries_and_missingness(self):
        curves = {"temperature": [-1.]*7+[0.]*7, "humidity": [90.]*14, "wind": [2.]*14}
        d = derived(curves, self.p)
        self.assertEqual(d["cold_degree_hours"], 7); self.assertEqual(d["cold_bins_hours"], 7)
        self.assertEqual(d["high_humidity_hours"], 14); self.assertEqual(d["low_wind_hours"], 0)
        self.assertEqual(d["cold_high_humidity_hours"], 7)
        curves["temperature"][0] = None
        self.assertIsNone(derived(curves, self.p)["cold_degree_hours"])
        self.assertIsNone(derived(curves, self.p)["cold_high_humidity_hours"])

    def test_score_counts_partial_targets_separately(self):
        n = deepcopy(self.n[0]); n["target"]["humidity"][0] = None
        m = fit_candidate(self.p["candidates"][0], self.r, self.p); f = predict_candidate(m, n, self.p)
        s = score_predictions([n], [f], self.p)
        self.assertEqual(s["paired_hours"]["temperature"], 14); self.assertEqual(s["paired_hours"]["humidity"], 13)
        self.assertEqual(s["aggregate_pairs"]["high_humidity"], 0)

    def test_serialized_smooth_model_repeats_forecast_and_tampering_rejected(self):
        m = fit_candidate(self.smooth, self.r, self.p)
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp); h = save_model(m, d); loaded = load_model(d, h)
            self.assertEqual(predict_candidate(m, self.n[0], self.p), predict_candidate(loaded, self.n[0], self.p))
            (d/"model.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash"): load_model(d, h)

    def test_new_season_cannot_open_before_frozen_selection(self):
        with tempfile.TemporaryDirectory() as temp, patch("agro_checker.scenario_fresh.cached_download") as download:
            with self.assertRaisesRegex(ValueError, "Freeze"): collect_fresh(Path(temp)/"data", ROOT/"config/scenario_study.json")
            download.assert_not_called()

    def test_new_archive_resume_uses_real_directory_and_sealed_provenance(self):
        from agro_checker.scenario_fresh import fresh_config
        from agro_checker.scenario_study import sha
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)/"data"; model_dir = data.parent/"models/research-scenarios"; model_dir.mkdir(parents=True)
            selection = {"protocol_sha256": sha(ROOT/"config/scenario_study.json"), "selection_saved_at_utc": "2026-10-12T00:00:00Z",
                         "model_artifact_sha256": {s["name"]: "fixture" for s in self.p["candidates"]}}
            (model_dir/"selection-before-history.json").write_text(json.dumps(selection), encoding="utf-8")
            with patch("agro_checker.scenario_fresh.load_model"), patch("agro_checker.scenario_fresh.cached_download"), patch("agro_checker.scenario_fresh.collect", return_value=([{"fixture": True}], [], [])), patch("agro_checker.scenario_fresh.seal_points") as seal, patch("agro_checker.scenario_fresh.collect_covariates"), patch("agro_checker.scenario_fresh.collect_humidity"):
                result = collect_fresh(data, ROOT/"config/scenario_study.json")
                self.assertEqual(seal.call_args.args[0], result)
                marker = json.loads((result/"new-season-open.json").read_text(encoding="utf-8"))
                self.assertEqual(marker["selection_sha256"], sha(model_dir/"selection-before-history.json"))

    def test_metar_wind_units_and_signed_temperature_are_explicit(self):
        m = parse_metar("URKK 072000Z 32002MPS CAVOK M02/M04 Q1030")
        self.assertEqual(m["wind_ms"], 2); self.assertEqual(m["temperature_c"], -2)
        self.assertAlmostEqual(parse_metar("TEST 010000Z 00010KT 00/00 Q1000")["wind_ms"], 10*1852/3600)
        self.assertEqual(parse_metar("TEST 010000Z VRB18KMH 00/00 Q1000")["wind_ms"], 5)


if __name__ == "__main__": unittest.main()
