import csv
from datetime import timezone
import json
from pathlib import Path
import tempfile
import unittest

from agro_checker.gfs_archive import issued_at, run_at
from agro_checker.pipeline import ROOT, night_hours, season_dates, write_json
from agro_checker.pilot_ui import build_demo_cases, build_snapshot, build_ui


class PilotUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = json.loads((ROOT / "config" / "single_run.json").read_text())
        self.config["season_end"] = "03-01"
        self.config_path = self.root / "config.json"
        write_json(self.config_path, self.config)
        self.processed = self.root / "data" / "processed" / "single-run"
        write_json(self.processed / "summary.json", {"config": self.config, "selected_model": {"name": "night_min_median", "offset_c": -0.5},
                                                   "scores": {}, "generated_at_utc": "2026-10-12T10:00:00+00:00", "grids": [[45,39.25,8.69]]})
        self.rows = []
        for year in self.config["years"]:
            for day in season_dates(self.config,year):
                for stamp in night_hours(self.config,day):
                    self.rows.append({"night_date_local": day.isoformat(), "split": "test" if year == 2025 else "development",
                                      "valid_at_utc": stamp.isoformat(), "forecast_run_at_utc": run_at(self.config,day).isoformat(),
                                      "issued_at_utc": issued_at(self.config,day).isoformat(), "lead_hours": int((stamp-run_at(self.config,day)).total_seconds()/3600),
                                      "forecast_temperature_c": "1", "target_temperature_c": "0", "target_source": "metar",
                                      "status": "available_by_proxy", "archive_last_modified_at_utc": "", "availability_gate_at_utc": ""})
        self.save_rows()

    def tearDown(self):
        self.temp.cleanup()

    def save_rows(self):
        with (self.processed / "hourly.csv").open("w",encoding="utf-8",newline="") as file:
            writer = csv.DictWriter(file,fieldnames=list(self.rows[0]))
            writer.writeheader(); writer.writerows(self.rows)

    def test_export_converts_reference_units_and_derives_correction(self):
        snapshot = build_snapshot(self.root / "data",self.config_path)
        self.assertEqual(len(snapshot["nights"]),4)
        self.assertEqual(snapshot["nights"][0]["points"][0]["local_time"],"19:00")
        self.assertEqual(snapshot["nights"][0]["points"][0]["corrected"],0.5)
        bloom = next(s for s in snapshot["thresholds"]["stages"] if s["id"] == "full_bloom_post")
        self.assertAlmostEqual(bloom["threshold_c"],-2.2222222222222223)

    def test_missing_observation_keeps_gap_and_has_no_complete_minimum(self):
        self.rows[0]["target_temperature_c"] = ""
        self.save_rows()
        snapshot = build_snapshot(self.root / "data",self.config_path)
        self.assertFalse(snapshot["nights"][0]["observations_complete"])
        self.assertIsNone(snapshot["nights"][0]["observed_min"])
        self.assertIsNone(snapshot["nights"][0]["points"][0]["observed"])

    def test_missing_or_duplicate_hour_rejected(self):
        self.rows[1]["valid_at_utc"] = self.rows[0]["valid_at_utc"]
        self.save_rows()
        with self.assertRaisesRegex(ValueError,"duplicate"):
            build_snapshot(self.root / "data",self.config_path)

    def test_mixed_run_rejected(self):
        self.rows[0]["forecast_run_at_utc"] = "2022-03-01T12:00:00+00:00"
        self.save_rows()
        with self.assertRaisesRegex(ValueError,"Mixed"):
            build_snapshot(self.root / "data",self.config_path)

    def test_standalone_html_has_embedded_assets_and_escaped_source_data(self):
        self.rows[0]["target_source"] = "</script><script>alert(1)</script>"
        self.save_rows()
        target = build_ui(self.root / "data",self.root / "output",self.config_path)
        html = target.read_text(encoding="utf-8")
        self.assertNotIn("<!-- PILOT_",html)
        self.assertIn('id="pilot-data"',html)
        self.assertIn("\\u003c/script>",html)
        self.assertNotIn('<script src=',html)
        self.assertNotIn('fetch(',html)


class DemoCaseTests(unittest.TestCase):
    def examples(self):
        return [{"date": day, "morning_date": morning, "split": "test", "points":
                 [{"base": base, "corrected": corrected, "observed": observed} for _ in range(14)]}
                for day,morning,base,corrected,observed in [
                    ("2025-03-02","2025-03-03",-4.3,-5.0,-4.0),
                    ("2025-03-07","2025-03-08",2.9,2.1,-4.0),
                    ("2025-03-04","2025-03-05",0.2,-0.5,2.0)]]

    def test_examples_verify_hit_miss_and_correction_only_false_alarm(self):
        cases = build_demo_cases(self.examples())
        self.assertTrue(all(c["available"] for c in cases))
        self.assertEqual(cases[2]["outcomes"], {"base":"correct_negative","corrected":"false_alarm"})

    def test_changed_outcome_cannot_keep_old_demo_title(self):
        nights = self.examples()
        for point in nights[2]["points"]:
            point["corrected"] = 1
        with self.assertRaisesRegex(ValueError,"outcome changed"):
            build_demo_cases(nights)

    def test_missing_or_partial_night_is_disabled_not_fabricated(self):
        self.assertTrue(all(not c["available"] for c in build_demo_cases([])))
        nights = self.examples()
        nights[0]["points"][2]["observed"] = None
        self.assertFalse(build_demo_cases(nights)[0]["available"])

    def test_training_night_cannot_be_presented_as_held_out_example(self):
        nights = self.examples()
        nights[0]["split"] = "development"
        with self.assertRaisesRegex(ValueError,"held-out"):
            build_demo_cases(nights)
