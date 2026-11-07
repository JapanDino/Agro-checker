from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from agro_checker.frost_study import (
    candidate_specs, evaluate_head, fit_cold, load_frost_protocol, load_rows, night_samples,
    pool_scores, predict_cold, replay_frost, run_frost_study, score_cold, select_frost, sha, validate_cold, validate_saved_model,
)
from agro_checker.model_study import load_protocol
from agro_checker.pipeline import ROOT, write_json
from tests.test_hourly_study import synthetic_rows


def sample_rows(year):
    rows = []
    for day in range(1, 9):
        group = synthetic_rows(year, day)
        # Two cold nights, one intermittent. Plenty of warm targets for both heads.
        for i, r in enumerate(group):
            r["target"]["temperature"] = -1. if day == 1 and i >= 8 or day == 2 and i in [8, 10] else 1.+day
        rows.extend(group)
    return rows


class FrostTests(unittest.TestCase):
    def setUp(self):
        self.p = load_frost_protocol(ROOT / "config/frost_study.json")
        self.m = load_protocol(ROOT / "config/model_study.json")

    def test_historical_targets_rejected_in_both_fit_and_selection(self):
        with self.assertRaisesRegex(ValueError, "2025"):
            fit_cold("cold_hour_logistic", sample_rows(2025), self.p, self.m)
        with self.assertRaisesRegex(ValueError, "2025"):
            validate_cold(sample_rows(2022)+sample_rows(2023)+sample_rows(2024)+sample_rows(2025), self.p, self.m)

    def test_development_loader_never_opens_2025_station_observations(self):
        from agro_checker.frost_study import cached_download as original
        opened = []
        def checked(url, path, *args):
            opened.append(url)
            self.assertNotIn("/2025/", url)
            return original(url, path, *args)
        # Verify the real season loader and existing SHA-protected field joins, not a mocked dataset.
        if not (ROOT / "data/raw/humidity-points.json").exists():
            self.skipTest("Archive caches not present")
        with patch("agro_checker.frost_study.cached_download", side_effect=checked):
            rows, _ = load_rows(ROOT / "data", [2022, 2023, 2024])
        self.assertEqual(len(opened), 3)
        self.assertEqual({r["year"] for r in rows}, {2022, 2023, 2024})

    def test_targets_cannot_change_forecast_and_missing_night_still_forecasts(self):
        model = fit_cold("event_duration_weather", sample_rows(2022), self.p, self.m)
        original = sample_rows(2025)
        poisoned = deepcopy(original)
        for r in poisoned:
            r["target"]["temperature"] = None
            r["target"]["humidity"] = -9999
        self.assertEqual(predict_cold(model, original, .5), predict_cold(model, poisoned, .5))
        self.assertEqual(len(predict_cold(model, poisoned, .5)), 8)

    def test_incomplete_night_is_excluded_from_every_training_head(self):
        rows = sample_rows(2022)
        rows[0]["target"]["temperature"] = None
        model = fit_cold("event_duration_weather", rows, self.p, self.m)
        self.assertEqual(model["training_nights"], 7)
        self.assertEqual(model["training_cold_nights"], 1)
        self.assertEqual(model["training_cold_hours"], 2)

    def test_mask_score_includes_misses_false_alarms_and_not_fake_temperature(self):
        rows = sample_rows(2025)
        predictions = [{"date": s["date"], "cold_mask": [False]*14, "cold_hour_model_scores": None, "night_event_model_score": None} for s in night_samples(rows)]
        predictions[2]["cold_mask"] = [True]*14
        score = score_cold(rows, predictions)
        self.assertEqual(score["misses"], 2)
        self.assertEqual(score["false_alarms"], 1)
        self.assertEqual(score["cold_bin_duration_mae_cold_h"], 4)
        self.assertEqual(score["cold_bin_duration_mae_warm_h"], 14/6)
        self.assertEqual(score["first_sample_pairs"], 0)
        self.assertNotIn("night_min_mae_c", score)
        self.assertTrue(all("predicted_min_c" not in n for n in score["nights"]))
        expected = .25*(1+1/6+4/14+(14/6)/14)
        self.assertAlmostEqual(score["selection_loss"], expected)

    def test_pooled_score_uses_class_counts_not_mean_of_fold_rates(self):
        scores = [
            {"complete_nights": 10, "cold_nights": 1, "hits": 0, "misses": 1, "false_alarms": 0, "correct_negatives": 9,
             "cold_bin_duration_mae_cold_h": 10., "cold_bin_duration_mae_warm_h": 0.},
            {"complete_nights": 10, "cold_nights": 4, "hits": 4, "misses": 0, "false_alarms": 2, "correct_negatives": 4,
             "cold_bin_duration_mae_cold_h": 0., "cold_bin_duration_mae_warm_h": 2.},
        ]
        pooled = pool_scores(scores)
        self.assertEqual(pooled["selection_components"]["miss_rate"], .2)
        self.assertEqual(pooled["cold_bin_duration_mae_cold_h"], 2.)
        self.assertEqual(pooled["cold_bin_duration_mae_warm_h"], 12/15)

    def test_decoder_respects_gate_count_separate_episodes_and_censoring(self):
        model = fit_cold("event_duration_weather", sample_rows(2022), self.p, self.m)
        def head(length, value, kind):
            return {"kind": kind, "scaler_mean": [0.]*length, "scaler_scale": [1.]*length, "coefficients": [0.]*length, "intercept": value}
        model["event_head"] = head(14, 10., "logistic")
        model["duration_head"] = head(8, 2., "ridge")
        group = sample_rows(2025)[:14]
        # Make hours 0 and 13 highest probability: no bridging 12 intervening warm hours.
        model["hour_head"] = head(10, 0., "logistic")
        model["hour_head"]["coefficients"][0] = -1.
        for i, r in enumerate(group):
            r["features_weather"][0] = -5. if i in [0, 13] else 5.
        p = predict_cold(model, group, .5)[0]
        self.assertEqual(sum(p["cold_mask"]), 2)
        self.assertEqual(len(p["frost"]["episodes"]), 2)
        self.assertTrue(p["frost"]["left_censored"])
        self.assertTrue(p["frost"]["right_censored"])
        model["event_head"]["intercept"] = -10.
        self.assertEqual(sum(predict_cold(model, group, .5)[0]["cold_mask"]), 0)
        model["event_head"]["intercept"] = 10.
        model["duration_head"]["intercept"] = -10.
        self.assertEqual(sum(predict_cold(model, group, .5)[0]["cold_mask"]), 1)
        model["duration_head"]["intercept"] = 100.
        self.assertEqual(sum(predict_cold(model, group, .5)[0]["cold_mask"]), 14)

    def test_stable_classifier_and_serialized_coefficients(self):
        model = fit_cold("cold_hour_logistic", sample_rows(2022), self.p, self.m)
        saved = validate_saved_model(json.loads(json.dumps(model)))
        self.assertEqual(predict_cold(model, sample_rows(2025), .25), predict_cold(saved, sample_rows(2025), .25))
        head = {"kind": "logistic", "scaler_mean": [0.], "scaler_scale": [1.], "coefficients": [1.], "intercept": 0.}
        self.assertEqual(evaluate_head(head, [10000.]), 1.)
        self.assertEqual(evaluate_head(head, [-10000.]), 0.)
        bad = deepcopy(saved); bad["training_years"] = [2025]
        with self.assertRaises(ValueError): validate_saved_model(bad)

    def test_fixed_registry_seven_candidates_and_protocol_rejects_historical_tuning(self):
        self.assertEqual(len(candidate_specs(self.p)), 7)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"protocol.json"
            changed = deepcopy(self.p); changed["decision_thresholds"] = [.1, .2]
            write_json(path, changed)
            with self.assertRaises(ValueError): load_frost_protocol(path)

    def test_replay_checks_hash_and_separates_hindsight(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d); path = data/"processed/frost-study/forecast-snapshot.json"
            n = {"date": "2025-03-07", "forecast": [{"predicted_cold": False}], "hindsight_observations": [{"observed_temperature_c": -1.}]}
            write_json(path, {"selected_candidate": {}, "nights": [n]})
            write_json(path.with_name(path.name+".meta.json"), {"sha256": sha(path)})
            replay = replay_frost(data, n["date"])
            self.assertNotIn("observed_temperature_c", replay["forecast"][0])
            self.assertIsNone(replay["forecast_available_at_utc"])
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                replay_frost(data, n["date"])

    def test_frozen_choice_survives_rerun_and_rejects_training_changes(self):
        rows = sample_rows(2022)+sample_rows(2023)+sample_rows(2024)
        def loader(data, years):
            self.assertNotIn(2025, years)
            return [r for r in rows if r["year"] in years], {"synthetic": "same"}
        with tempfile.TemporaryDirectory() as d, patch("agro_checker.frost_study.load_rows", side_effect=loader):
            data = Path(d)/"data"
            first = select_frost(ROOT/"config/frost_study.json", data)
            path = Path(d)/"models/research-frost/selection-before-2025.json"
            original = path.read_bytes()
            self.assertEqual(first, select_frost(ROOT/"config/frost_study.json", data))
            self.assertEqual(path.read_bytes(), original)
            rows[8]["target"]["temperature"] = 1.
            with self.assertRaisesRegex(ValueError, "Frozen frost selection/model changed"):
                select_frost(ROOT/"config/frost_study.json", data)
            self.assertEqual(path.read_bytes(), original)

    def test_tampered_model_rejected_before_opening_historical_targets(self):
        rows = sample_rows(2022)+sample_rows(2023)+sample_rows(2024)
        def loader(data, years):
            self.assertNotIn(2025, years)
            return [r for r in rows if r["year"] in years], {"synthetic": "same"}
        with tempfile.TemporaryDirectory() as d, patch("agro_checker.frost_study.load_rows", side_effect=loader):
            data = Path(d)/"data"
            select_frost(ROOT/"config/frost_study.json", data)
            path = Path(d)/"models/research-frost/cold_hour_logistic/model.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model hash mismatch"):
                run_frost_study(ROOT/"config/frost_study.json", data, Path(d)/"docs")


if __name__ == "__main__":
    unittest.main()
