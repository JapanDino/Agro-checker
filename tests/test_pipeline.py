import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agro_checker.pipeline import (
    ROOT, build_nights, cached_download, episode_count, load_config,
    metrics, night_hours, number, parse_forecast, parse_observations,
)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config" / "pilot.json")

    def observation_bytes(self, rows):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["year", "month", "day", "hour", "temp", "temp_source"])
        writer.writeheader()
        writer.writerows(rows)
        return gzip.compress(output.getvalue().encode())

    def test_model_fill_and_unknown_source_are_not_targets(self):
        rows = [{"year": 2024, "month": 4, "day": 1, "hour": hour, "temp": -4,
                 "temp_source": source} for hour, source in enumerate(["isd_lite", "metar", "dwd_mosmix", "unknown", ""])]
        records = list(parse_observations(self.observation_bytes(rows), self.config["observation_sources"]).values())
        self.assertEqual([r["temperature_c"] for r in records], [-4, -4, None, None, None])
        self.assertEqual(records[2]["raw_temperature_c"], -4)

    def test_duplicate_observation_rejected(self):
        row = {"year": 2024, "month": 4, "day": 1, "hour": 0, "temp": 1, "temp_source": "metar"}
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_observations(self.observation_bytes([row, row]), ["metar"])

    def test_missing_source_column_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema"):
            parse_observations(gzip.compress(b"year,month,day,hour,temp\n2024,4,1,0,-3\n"), ["metar"])

    def test_night_boundary_utc_and_month_transition(self):
        stamps = night_hours(self.config, date(2024, 3, 31))
        self.assertEqual(len(stamps), 14)
        self.assertEqual(stamps[0], datetime(2024, 3, 31, 16, tzinfo=timezone.utc))
        self.assertEqual(stamps[-1], datetime(2024, 4, 1, 5, tzinfo=timezone.utc))

    def test_incomplete_night_cannot_supply_minimum_or_confusion_count(self):
        stamps = night_hours(self.config, date(2024, 3, 1))
        observations = {s: {"temperature_c": -3, "source": "metar"} for s in stamps[:-1]}
        forecasts = {s: -2 for s in stamps}
        hourly, nights = build_nights(self.config, 2024, observations, forecasts)
        self.assertIsNone(nights[0]["observed_min_c"])
        self.assertFalse(nights[0]["eligible"])
        self.assertEqual(metrics(nights, hourly, 0)["paired_nights"], 0)
        self.assertTrue(all(r["issued_at_utc"] is None and r["forecast_available_at_utc"] is None for r in hourly))

    def test_confusion_matrix_uses_cold_event_not_overall_accuracy(self):
        pairs = [(-2, -1), (-2, 1), (2, -1), (2, 1)]
        nights = [{"eligible": True, "observed_min_c": o, "forecast_min_c": f,
                   "night_date_local": f"2024-04-0{i + 1}"} for i, (o, f) in enumerate(pairs)]
        result = metrics(nights, [], 0)
        self.assertEqual([result[k] for k in ("hits", "misses", "false_alarms", "correct_negatives")], [1, 1, 1, 1])
        self.assertEqual(result["cold_night_min_mae_c"], 2)

    def test_threshold_equality_is_not_below_zero(self):
        n = {"eligible": True, "observed_min_c": 0, "forecast_min_c": 0, "night_date_local": "2024-04-01"}
        result = metrics([n], [], 0)
        self.assertEqual(result["paired_cold_nights"], 0)
        self.assertEqual(result["correct_negatives"], 1)
        self.assertIsNone(result["cold_night_min_mae_c"])

    def test_missing_forecast_excluded_without_interpolation(self):
        stamps = night_hours(self.config, date(2024, 3, 1))
        observations = {s: {"temperature_c": -3, "source": "metar"} for s in stamps}
        forecasts = {s: -1 for s in stamps[:-1]}
        _, nights = build_nights(self.config, 2024, observations, forecasts)
        self.assertEqual(nights[0]["observed_min_c"], -3)
        self.assertIsNone(nights[0]["forecast_min_c"])
        self.assertFalse(nights[0]["eligible"])

    def test_consecutive_nights_form_one_descriptive_sequence(self):
        nights = [{"observed_min_c": -1, "night_date_local": d} for d in ["2024-03-31", "2024-04-01", "2024-04-03"]]
        self.assertEqual(episode_count(nights, 0), 2)

    def test_empty_metrics_are_null_not_zero_error(self):
        result = metrics([], [], 0)
        self.assertIsNone(result["night_min_mae_c"])
        self.assertIsNone(result["hourly_mae_c"])

    def test_nonfinite_numbers_are_missing(self):
        self.assertIsNone(number("nan"))
        self.assertIsNone(number("inf"))
        self.assertIsNone(number(""))

    def test_forecast_validation(self):
        variable = self.config["forecast_variable"]
        payload = {"utc_offset_seconds": 0, "hourly_units": {variable: "°C"},
                   "hourly": {"time": ["2024-04-01T00:00"], variable: [-1]}}
        records, _ = parse_forecast(json.dumps(payload).encode(), variable)
        self.assertEqual(records[datetime(2024, 4, 1, tzinfo=timezone.utc)], -1)
        payload["utc_offset_seconds"] = 10800
        with self.assertRaisesRegex(ValueError, "UTC"):
            parse_forecast(json.dumps(payload).encode(), variable)
        payload["utc_offset_seconds"] = 0
        payload["hourly_units"][variable] = "°F"
        with self.assertRaisesRegex(ValueError, "Celsius"):
            parse_forecast(json.dumps(payload).encode(), variable)
        payload["hourly_units"][variable] = "°C"
        payload["hourly"]["time"].append("2024-04-01T00:00")
        with self.assertRaisesRegex(ValueError, "length"):
            parse_forecast(json.dumps(payload).encode(), variable)
        payload["hourly"][variable].append(-1)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_forecast(json.dumps(payload).encode(), variable)

    def test_offline_cache_checks_integrity_and_never_calls_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.bin"
            content = b"test"
            path.write_bytes(content)
            path.with_name("data.bin.meta.json").write_text(json.dumps({"url": "https://example.com", "sha256": hashlib.sha256(content).hexdigest()}))
            with patch("agro_checker.pipeline.urlopen", side_effect=AssertionError("Network prohibited")):
                self.assertEqual(cached_download("https://example.com", path, True)[0], content)
                path.write_bytes(b"altered")
                with self.assertRaisesRegex(ValueError, "Cache mismatch"):
                    cached_download("https://example.com", path, True)
                with self.assertRaisesRegex(ValueError, "Missing cached"):
                    cached_download("https://example.com", Path(directory) / "missing", True)

    def test_overlapping_split_years_rejected(self):
        config = dict(self.config)
        config["test_years"] = [2024, 2025]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "partition"):
                load_config(path)

    def test_future_development_years_rejected(self):
        config = dict(self.config)
        config["development_years"] = [2023, 2024, 2025]
        config["validation_years"] = [2022]
        config["test_years"] = [2021]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "ordered"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
