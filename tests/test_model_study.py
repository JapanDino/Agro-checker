from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agro_checker.model_study import (
    NAMES, assemble_samples, cold_sequences, export_model, fit_model, forecast_features,
    feature_shift, forward_validation, load_protocol, load_research_model, paired_block_bootstrap,
    predict_model, score, select_model,
)
from agro_checker.pipeline import ROOT

SCIENCE = all(importlib.util.find_spec(p) for p in ["numpy", "sklearn", "catboost"])


def sample(year, day=1, shift=0.0):
    d = date(year, 3, day)
    curve = [6 + day * .2 - i * .4 for i in range(14)]
    target = [t - .8 + day * .025 + shift + (i % 3) * .1 for i, t in enumerate(curve)]
    return {"date": d.isoformat(), "year": year, "curve": curve, "features": forecast_features(d, curve),
            "target": min(target), "target_curve": target}


class ModelStudyTests(unittest.TestCase):
    def setUp(self):
        self.protocol = load_protocol(ROOT / "config" / "model_study.json")
        self.protocol["catboost"]["iterations"] = 4
        self.samples = [sample(y, d) for y in range(2022, 2026) for d in range(1, 9)]

    def test_features_require_complete_forecast_and_known_date_only(self):
        s = sample(2023)
        self.assertEqual(len(s["features"]), 12)
        self.assertEqual(s["features"][0], min(s["curve"]))
        with self.assertRaises(ValueError):
            forecast_features(date(2023, 3, 1), s["curve"][:-1])
        with self.assertRaises(ValueError):
            forecast_features(date(2023, 3, 1), [float("nan")] * 14)
        self.assertEqual(forecast_features(date(2024, 3, 1), s["curve"]), s["features"])

    def test_incomplete_nights_never_enter_samples(self):
        self.assertEqual(assemble_samples([], [{"night_date_local": "2023-03-01", "eligible": False}]), [])

    def test_historical_targets_cannot_enter_fit(self):
        with self.assertRaisesRegex(ValueError, "historical-check"):
            fit_model("night_min_median", [sample(2025)], self.protocol)

    def test_selection_rejects_historical_scores(self):
        with self.assertRaisesRegex(ValueError, "validation"):
            select_model([{"role": "historical", "scores": {}}], NAMES)

    def test_protocol_rejects_overlapping_future_fold(self):
        p = deepcopy(self.protocol)
        p["folds"][0]["train_years"] = [2023]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "protocol.json"
            path.write_text(json.dumps(p), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "precede"):
                load_protocol(path)

    def test_weather_threshold_and_warning_margin_are_distinct(self):
        s = sample(2025)
        s["target"] = 0.0
        pred = [{"date": s["date"], "prediction": 0.0, "curve": None}]
        self.assertEqual(score([s], pred)["correct_negatives"], 1)
        self.assertEqual(score([s], pred, warning_margin=1)["false_alarms"], 1)
        s["target"] = -1.0
        self.assertEqual(score([s], pred)["misses"], 1)
        self.assertEqual(score([s], pred, warning_margin=1)["hits"], 1)
        self.assertIsNone(score([s], pred)["hourly_mae_c"])

    def test_event_grouping_respects_calendar_gaps(self):
        samples = [sample(2025, d) for d in [1, 2, 4]]
        for s in samples:
            s["target"] = -1
        p = [{"date": s["date"], "prediction": 1, "curve": None} for s in samples]
        groups = cold_sequences(samples, p)
        self.assertEqual([len(g["nights"]) for g in groups], [2, 1])
        self.assertFalse(any(g["any_detected"] for g in groups))

    def test_feature_envelope_is_trained_on_past_only(self):
        train = [sample(2022, 1), sample(2023, 2)]
        future = sample(2025, 3)
        result = feature_shift(train, [future])
        self.assertEqual(result["training_feature_ranges"]["gfs_min"]["max"], max(s["features"][0] for s in train))
        self.assertEqual(result["outside_training_range_nights"][0]["date"], future["date"])

    @unittest.skipUnless(SCIENCE, "Install requirements-model.txt for scientific tests")
    def test_changed_future_targets_do_not_change_fit_selection_or_predictions(self):
        altered = deepcopy(self.samples)
        for s in altered:
            if s["year"] == 2025:
                s["target"] = -1000
                s["target_curve"] = [-1000] * 14
        folds, winner, pooled = forward_validation(self.samples, self.protocol)
        other_folds, other_winner, other_pooled = forward_validation(altered, self.protocol)
        self.assertEqual((folds, winner, pooled), (other_folds, other_winner, other_pooled))
        train = [s for s in self.samples if s["year"] < 2024]
        for name in NAMES:
            model = fit_model(name, train, self.protocol)
            p = predict_model(model, [s for s in self.samples if s["year"] == 2025])
            q = predict_model(model, [s for s in altered if s["year"] == 2025])
            self.assertEqual(p, q)
            if name in ("ridge_min", "catboost_min"):
                self.assertTrue(all(r["curve"] is None for r in p))

    @unittest.skipUnless(SCIENCE, "Install requirements-model.txt for scientific tests")
    def test_all_saved_models_reproduce_predictions(self):
        train = [s for s in self.samples if s["year"] < 2024]
        check = [s for s in self.samples if s["year"] == 2025]
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name in NAMES:
                model = fit_model(name, train, self.protocol)
                export_model(model, directory)
                loaded = load_research_model(directory, name)
                original, replay = predict_model(model, check), predict_model(loaded, check)
                for a, b in zip(original, replay):
                    self.assertAlmostEqual(a["prediction"], b["prediction"], places=10)
                    if a["curve"] is not None:
                        for x, y in zip(a["curve"], b["curve"]):
                            self.assertAlmostEqual(x, y, places=10)

    @unittest.skipUnless(SCIENCE, "Install requirements-model.txt for scientific tests")
    def test_bootstrap_uses_calendar_blocks_and_preserves_pairs(self):
        samples = [sample(2025, d) for d in [1, 3, 8]]
        baseline = [{"date": s["date"], "prediction": s["target"] + 2, "curve": None} for s in samples]
        corrected = [{"date": s["date"], "prediction": s["target"] + 1, "curve": None} for s in samples]
        settings = {"block_days": [3], "replicates": 100, "seed": 2}
        result = paired_block_bootstrap(samples, baseline, corrected, settings)["3"]
        self.assertEqual(result["blocks"], 2)
        self.assertEqual(result["improvement_c"], 1)
        self.assertEqual(result["interval_95_c"], [1, 1])


if __name__ == "__main__":
    unittest.main()
