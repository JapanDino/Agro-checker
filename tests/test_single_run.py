from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from agro_checker.gfs_archive import (
    CONNECTIONS, aws_fetch, decode_temperature, issued_at, object_url,
    range_download, run_at, temperature_range,
)
from agro_checker.pipeline import ROOT, night_hours
from agro_checker.single_run import (
    build_tables, compare, corrected_temperature, fit_correction, load_single_config,
    load_points, seal_points,
)

try:
    import eccodes as ec
except ImportError:
    ec = None


class SingleRunTests(unittest.TestCase):
    def setUp(self):
        self.config = load_single_config(ROOT / "config" / "single_run.json")
        self.config["season_end"] = self.config["season_start"]
        self.points, self.observations = [], {}
        for year in self.config["years"]:
            evening = date(year, 3, 1)
            run, issue = run_at(self.config, evening), issued_at(self.config, evening)
            modified = run + timedelta(hours=4)
            for stamp in night_hours(self.config, evening):
                self.points.append({"night_date_local": evening.isoformat(), "valid_at_utc": stamp.isoformat(),
                                    "forecast_run_at_utc": run.isoformat(), "issued_at_utc": issue.isoformat(),
                                    "lead_hours": int((stamp - run).total_seconds() / 3600),
                                    "forecast_temperature_c": 2.0,
                                    "archive_last_modified_at_utc": modified.isoformat(),
                                    "availability_gate_at_utc": (modified + timedelta(minutes=10)).isoformat(),
                                    "available_by_issue_proxy": True})
                self.observations[stamp] = {"temperature_c": 0.0, "source": "metar"}

    def test_same_run_covers_midnight_with_different_leads(self):
        hourly, nights = build_tables(self.config, self.points, self.observations)
        first = [r for r in hourly if r["night_date_local"] == "2022-03-01"]
        self.assertEqual({r["forecast_run_at_utc"] for r in first}, {"2022-03-01T06:00:00+00:00"})
        self.assertEqual({r["issued_at_utc"] for r in first}, {"2022-03-01T15:00:00+00:00"})
        self.assertEqual([r["lead_hours"] for r in first], list(range(10, 24)))
        self.assertTrue(all(n["eligible"] for n in nights))

    def test_mixed_run_is_rejected(self):
        points = deepcopy(self.points)
        points[4]["forecast_run_at_utc"] = "2022-03-01T12:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "Mixed"):
            build_tables(self.config, points, self.observations)

    def test_late_archive_object_excluded_from_whole_night(self):
        points = deepcopy(self.points)
        modified = datetime(2022, 3, 1, 15, tzinfo=timezone.utc)
        points[0]["archive_last_modified_at_utc"] = modified.isoformat()
        points[0]["availability_gate_at_utc"] = (modified + timedelta(minutes=10)).isoformat()
        points[0]["available_by_issue_proxy"] = False
        hourly, nights = build_tables(self.config, points, self.observations)
        self.assertFalse(nights[0]["eligible"])
        self.assertEqual(nights[0]["late_forecast_hours"], 1)
        self.assertIsNone(nights[0]["forecast_min_c"])
        self.assertIsNone(hourly[0]["forecast_available_at_utc"])

    def test_missing_forecast_is_not_filled_from_another_run(self):
        _, nights = build_tables(self.config, self.points[1:], self.observations)
        self.assertEqual(nights[0]["forecast_hours"], 13)
        self.assertFalse(nights[0]["eligible"])

    def test_missing_observation_excludes_night_from_training(self):
        del self.observations[datetime.fromisoformat(self.points[0]["valid_at_utc"])]
        hourly, nights = build_tables(self.config, self.points, self.observations)
        comparison, _ = compare(self.config, hourly, nights)
        self.assertEqual(comparison["selected_model"]["trained_nights"], 1)

    def test_test_targets_cannot_change_fit_or_selection(self):
        hourly, nights = build_tables(self.config, self.points, self.observations)
        result, evaluations = compare(self.config, hourly, nights)
        altered_observations = deepcopy(self.observations)
        for stamp, obs in altered_observations.items():
            if stamp.year == 2025:
                obs["temperature_c"] = 40
        changed_hours, changed_nights = build_tables(self.config, self.points, altered_observations)
        changed, _ = compare(self.config, changed_hours, changed_nights)
        self.assertEqual(result["models"], changed["models"])
        self.assertEqual(result["selected_model"], changed["selected_model"])
        self.assertNotEqual(result["scores"]["night_min_median"]["test"], changed["scores"]["night_min_median"]["test"])
        self.assertEqual(result["selected_model"]["name"], "night_min_median")
        denominators = [e[0]["test"]["paired_nights"] for e in evaluations.values()]
        self.assertEqual(denominators, [1, 1, 1])

    def test_baseline_wins_when_correction_harms_validation(self):
        for stamp, obs in self.observations.items():
            if stamp.year == 2024:
                obs["temperature_c"] = 2
        hourly, nights = build_tables(self.config, self.points, self.observations)
        result, _ = compare(self.config, hourly, nights)
        self.assertEqual(result["selected_model"]["name"], "identity")

    def test_fitting_validation_rows_rejected(self):
        hourly, nights = build_tables(self.config, self.points, self.observations)
        with self.assertRaisesRegex(ValueError, "development"):
            fit_correction("night_min_median", [r for r in hourly if r["split"] == "validation"],
                           [n for n in nights if n["split"] == "validation"])

    def test_prediction_does_not_use_target(self):
        model = {"offset_c": -2}
        first = {"forecast_temperature_c": 3, "target_temperature_c": -10}
        second = {"forecast_temperature_c": 3, "target_temperature_c": 40}
        self.assertEqual(corrected_temperature(model, first), corrected_temperature(model, second))
        self.assertIsNone(corrected_temperature(model, {"forecast_temperature_c": None}))

    def test_index_selects_temperature_and_checks_run_and_lead(self):
        run = datetime(2024, 3, 9, 6, tzinfo=timezone.utc)
        index = b"1:0:d=2024030906:TMP:surface:10 hour fcst:\n2:100:d=2024030906:TMP:2 m above ground:10 hour fcst:\n3:250:d=2024030906:SPFH:2 m above ground:10 hour fcst:\n"
        self.assertEqual(temperature_range(index, run, 10), (100, 249))
        with self.assertRaisesRegex(ValueError, "run/lead"):
            temperature_range(index, run, 11)
        self.assertIn("/20240309", object_url(run, 10).replace("gfs.", ""))

    def test_server_ignoring_range_is_closed_without_reading_full_file(self):
        connection = Mock()
        response = Mock(status=200)
        response.headers = {}
        connection.getresponse.return_value = response
        CONNECTIONS.connection = None
        try:
            with patch("agro_checker.gfs_archive.HTTPSConnection", return_value=connection):
                with self.assertRaisesRegex(ValueError, "honor"):
                    aws_fetch("https://noaa-gfs-bdp-pds.s3.amazonaws.com/a", (100, 249))
            response.read.assert_not_called()
            connection.close.assert_called_once()
        finally:
            CONNECTIONS.connection = None

    def test_connection_reset_is_retryable_and_connection_is_discarded(self):
        connection = Mock()
        connection.getresponse.side_effect = ConnectionResetError("reset")
        CONNECTIONS.connection = None
        try:
            with patch("agro_checker.gfs_archive.HTTPSConnection", return_value=connection):
                with self.assertRaises(URLError):
                    aws_fetch("https://noaa-gfs-bdp-pds.s3.amazonaws.com/a")
            self.assertIsNone(CONNECTIONS.connection)
            connection.close.assert_called_once()
        finally:
            CONNECTIONS.connection = None

    def test_range_download_rejects_truncated_grib(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("agro_checker.gfs_archive.aws_fetch", return_value=(b"GRIBbad7777", {"Last-Modified": "Sat, 09 Mar 2024 09:31:10 GMT"})):
                with self.assertRaisesRegex(ValueError, "truncated"):
                    range_download("https://example.com", Path(directory) / "x.grib2", 100, 249)

    def test_extraction_cache_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "raw").mkdir()
            (data_dir / "raw" / "single-run-manifest.json").write_text('{"downloads":[],"failures":[]}')
            seal_points(data_dir, self.config, self.points, [])
            self.assertEqual(len(load_points(data_dir, self.config)[0]), 56)
            path = data_dir / "raw" / "single-run-points.json"
            path.write_text("{}")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_points(data_dir, self.config)

    def test_reversed_year_splits_rejected(self):
        c = deepcopy(self.config)
        c["development_years"], c["test_years"] = [2023, 2025], [2022]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError, "ordered"):
                load_single_config(path)


@unittest.skipIf(ec is None, "Optional archive decoder not installed")
class NativeGribTests(unittest.TestCase):
    def test_decode_checks_native_metadata_and_converts_kelvin(self):
        handle = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
        try:
            keys = {"centre": "kwbc", "Ni": 2, "Nj": 2,
                    "latitudeOfFirstGridPointInDegrees": 45.0, "longitudeOfFirstGridPointInDegrees": 39.0,
                    "latitudeOfLastGridPointInDegrees": 44.75, "longitudeOfLastGridPointInDegrees": 39.25,
                    "iDirectionIncrementInDegrees": 0.25, "jDirectionIncrementInDegrees": 0.25,
                    "shortName": "2t", "dataDate": 20240309, "dataTime": 600,
                    "stepUnits": 1, "forecastTime": 10}
            for key, value in keys.items():
                ec.codes_set(handle, key, value)
            ec.codes_set_values(handle, [280, 281, 282, 283])
            content = ec.codes_get_message(handle)
        finally:
            ec.codes_release(handle)
        config = load_single_config(ROOT / "config" / "single_run.json")
        config["latitude"] = 44.99  # Inside the small synthetic grid.
        run = datetime(2024, 3, 9, 6, tzinfo=timezone.utc)
        valid = run + timedelta(hours=10)
        point = decode_temperature(content, config, run, valid)
        self.assertAlmostEqual(point["forecast_temperature_c"], 7.85)
        self.assertEqual((point["grid_latitude"], point["grid_longitude"]), (45.0, 39.25))
        with self.assertRaisesRegex(ValueError, "validityTime"):
            decode_temperature(content, config, run, valid + timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
