from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agro_checker.minimum_model import calibrate, features, fit_minimum, point_minimum, predict_minimum, safe_prediction
from agro_checker.minimum_study import protocol, select_minimum
from agro_checker.pipeline import ROOT
from agro_checker.scenario_study import nights
from test_scenario_study import rows


def season(year, count=40):
    result = rows(count)
    for r in result:
        r["year"] = year
        r["date"] = r["date"].replace("2022", str(year))
        for k in ["valid_at_utc", "forecast_run_at_utc", "issued_at_utc"]:
            r[k] = r[k].replace("2022", str(year))
    return result


class MinimumTests(unittest.TestCase):
    def setUp(self):
        self.p = protocol(ROOT/"config/minimum_study.json")
        self.spec = self.p["candidates"][2]
        self.samples = nights(season(2022))
        self.model = fit_minimum(self.spec, self.samples)
        self.calibration = calibrate(self.model, nights(season(2024)), self.p)

    def test_inference_ignores_targets_and_does_not_mutate_forecast(self):
        n = deepcopy(self.samples[0]); before = deepcopy(n)
        first = predict_minimum(self.model, self.calibration, n, self.p)
        self.assertEqual(n, before)
        n["target"] = {"temperature": [999]*14}
        self.assertEqual(first, predict_minimum(self.model, self.calibration, n, self.p))
        self.assertIsNone(first["probability_of_crop_damage"])
        self.assertFalse(first["coverage_guaranteed"])

    def test_fitting_and_calibration_reject_control_labels(self):
        for year in [2024, 2025, 2026]:
            with self.assertRaises(ValueError): fit_minimum(self.spec, nights(season(year)))
        for year in [2022, 2023, 2025, 2026]:
            with self.assertRaises(ValueError): calibrate(self.model, nights(season(year)), self.p)
        model = deepcopy(self.model); model["training_dates"] = [nights(season(2024))[0]["date"]]
        with self.assertRaisesRegex(ValueError, "overlaps"): calibrate(model, nights(season(2024)), self.p)

    def test_missing_targets_are_excluded_never_filled_and_too_few_rejected(self):
        calibration = nights(season(2024)); calibration[0]["target"]["temperature"][0] = None
        result = calibrate(self.model, calibration, self.p)
        self.assertEqual(result["calibration_nights"], 39)
        self.assertEqual(result["excluded_calibration_nights"], 1)
        with self.assertRaises(ValueError): calibrate(self.model, calibration[:20], self.p)
        training = deepcopy(self.samples); training[0]["target"]["temperature"][0] = None
        self.assertEqual(fit_minimum(self.spec, training)["excluded_training_nights"], 1)

    def test_envelope_uses_finite_sample_rank_and_scalar_minimum(self):
        result = predict_minimum(self.model, self.calibration, self.samples[0], self.p)
        self.assertEqual(self.calibration["rank"], 37)
        self.assertLessEqual(result["lower_c"], result["point_c"])
        self.assertLessEqual(result["point_c"], result["upper_c"])
        self.assertIsInstance(result["point_c"], float)

    def test_guard_rejects_wrong_site_run_horizon_season_mixed_issue_and_missing_fields(self):
        base = self.samples[0]
        self.assertIsNone(safe_prediction(self.model, self.calibration, base, self.p, "orchard")["prediction"])
        variants = []
        n = deepcopy(base); n["rows"] = n["rows"][:-1]; variants.append(n)
        n = deepcopy(base); n["rows"][1]["forecast_run_at_utc"] = "2022-03-01T00:00:00+00:00"; variants.append(n)
        n = deepcopy(base); n["rows"][0]["features_weather"][4] = None; variants.append(n)
        n = deepcopy(base); n["base"]["temperature"][0] = -99; variants.append(n)
        n = deepcopy(base); n["rows"][0]["valid_at_utc"] = "2022-03-01T17:00:00+00:00"; variants.append(n)
        n = deepcopy(base); n["date"] = "2022-10-01"; variants.append(n)
        for n in variants:
            self.assertIsNone(safe_prediction(self.model, self.calibration, n, self.p, "34929")["prediction"])
        self.assertEqual(safe_prediction(self.model, self.calibration, base, self.p, "34929")["status"], "research_only")

    def test_guard_blocks_extrapolation_instead_of_publishing_confident_correction(self):
        n = deepcopy(self.samples[0])
        for r in n["rows"]: r["features_weather"][4] = 95.
        self.assertGreater(point_minimum(self.model, n)[1], 4)
        self.assertEqual(safe_prediction(self.model, self.calibration, n, self.p, "34929"),
                         {"status": "extrapolation", "prediction": None})

    def test_selector_never_opens_controls_and_freeze_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)/"data"; directory = Path(temp)/"models/research-minimum"
            calls = []
            def load(_, years):
                calls.append(years)
                if years == [2024]: self.assertTrue((directory/"point-choice-before-calibration.json").exists())
                self.assertNotIn(2025, years); self.assertNotIn(2026, years)
                return [r for y in years for r in season(y)], {"years": years}
            with patch("agro_checker.minimum_study.load_rows", side_effect=load):
                result = select_minimum(ROOT/"config/minimum_study.json", data)
                self.assertEqual(calls, [[2022, 2023], [2024]])
                before = (directory/"selection-before-controls.json").read_bytes()
                self.assertEqual(select_minimum(ROOT/"config/minimum_study.json", data), result)
                self.assertEqual((directory/"selection-before-controls.json").read_bytes(), before)
                (directory/(result["winner"]+".json")).write_text("{}")
                with self.assertRaisesRegex(ValueError, "hash"): select_minimum(ROOT/"config/minimum_study.json", data)
