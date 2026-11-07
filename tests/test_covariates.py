from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agro_checker.covariate_study import (
    ALL_FEATURE_NAMES, candidates, covariate_features, fit_candidate, group_indexes,
    join_covariates, load_candidate, load_covariate_protocol, predict_candidate,
    save_candidate, validate_candidates,
)
from agro_checker.gfs_covariates import FIELDS, check_object_version, decode_field, field_range, load_covariates, seal_covariates
from agro_checker.model_study import forecast_features, load_protocol
from agro_checker.pipeline import ROOT
from agro_checker.single_run import load_single_config

try:
    import eccodes as ec
except ImportError:
    ec = None
SCIENCE = all(importlib.util.find_spec(p) for p in ["numpy", "sklearn", "catboost"])


def field_values(day=1):
    return [{"u10": {"value": 3.0 + day / 10}, "v10": {"value": 4.0},
             "dewpoint": {"value": 1.0 + i / 10}, "cloud": {"value": 50.0 + i}} for i in range(14)]


def sample(year, day):
    d = date(year, 3, day)
    curve = [8 + day * .2 - i * .4 for i in range(14)]
    target = [v - .8 + day * .03 + (i % 3) * .1 for i, v in enumerate(curve)]
    return {"date": d.isoformat(), "year": year, "curve": curve, "target": min(target), "target_curve": target,
            "features": forecast_features(d, curve) + covariate_features(curve, field_values(day))}


class CovariateTests(unittest.TestCase):
    def setUp(self):
        self.config = load_single_config(ROOT / "config" / "single_run.json")
        self.protocol = load_covariate_protocol(ROOT / "config" / "covariate_study.json")
        self.model_protocol = load_protocol(ROOT / "config" / "model_study.json")
        self.model_protocol["catboost"]["iterations"] = 4

    def test_cloud_index_excludes_average_and_wrong_layer(self):
        index = b"1:0:d=2022030106:TCDC:low cloud layer:10 hour fcst:\n2:100:d=2022030106:TCDC:entire atmosphere:6-10 hour ave fcst:\n3:200:d=2022030106:TCDC:entire atmosphere:10 hour fcst:\n4:300:d=2022030106:TMP:surface:10 hour fcst:\n"
        run = datetime(2022, 3, 1, 6, tzinfo=timezone.utc)
        self.assertEqual(field_range(index, run, 10, "cloud"), (200, 299))
        with self.assertRaisesRegex(ValueError, "one instantaneous"):
            field_range(index, run, 11, "cloud")
        with self.assertRaisesRegex(ValueError, "run"):
            field_range(index, run + timedelta(days=1), 10, "cloud")

    def test_covariates_cannot_mix_object_versions(self):
        ref = {"url": "https://noaa-gfs-bdp-pds.s3.amazonaws.com/object", "etag": "x", "archive_last_modified_at_utc": "2022-03-01T09:00:00+00:00"}
        check_object_version(ref, ref)
        for key in ref:
            changed = {**ref, key: "different"}
            with self.assertRaisesRegex(ValueError, "object versions"):
                check_object_version(changed, ref)

    def test_wind_index_cannot_confuse_10mb_with_10m(self):
        index = b"1:0:d=2022030106:UGRD:10 mb:10 hour fcst:\n2:100:d=2022030106:UGRD:10 m above ground:10 hour fcst:\n3:200:d=2022030106:VGRD:10 m above ground:10 hour fcst:\n"
        run = datetime(2022, 3, 1, 6, tzinfo=timezone.utc)
        self.assertEqual(field_range(index, run, 10, "u10"), (100, 199))

    def test_features_use_vector_speed_and_predicted_minimum_hour(self):
        curve = [10.0] * 14
        curve[2] = 0.0
        f = field_values()
        f[2]["u10"]["value"] = 3
        f[2]["v10"]["value"] = 4
        values = covariate_features(curve, f)
        self.assertEqual(len(values), 11)
        self.assertEqual(values[2], 5)
        self.assertAlmostEqual(values[5], 1.2)
        self.assertAlmostEqual(values[7], -1.2)  # Keep raw spread; no post-hoc clipping.
        self.assertEqual(values[-1], 52)
        with self.assertRaises(ValueError):
            covariate_features(curve, f[:-1])

    def test_fixed_groups_have_expected_dimensions(self):
        self.assertEqual(len(ALL_FEATURE_NAMES), 23)
        self.assertEqual([len(group_indexes(g)) for g in self.protocol["feature_groups"]], [12, 15, 17, 15, 23])
        self.assertEqual(len(candidates(self.protocol)), 13)

    def test_covariate_cache_checks_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            seal_covariates(data, self.config, [], [], [])
            self.assertEqual(load_covariates(data, self.config)[0], [])
            (data / "raw/covariate-points.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_covariates(data, self.config)

    def aligned_inputs(self):
        s = sample(2023, 1)
        s["features"] = s["features"][:12]
        run = datetime(2023, 3, 1, 6, tzinfo=timezone.utc)
        issue, modified = run + timedelta(hours=9), run + timedelta(hours=4)
        hours, cov = [], []
        for i, fields in enumerate(field_values()):
            h = {"night_date_local": s["date"], "forecast_run_at_utc": run.isoformat(), "issued_at_utc": issue.isoformat(),
                 "lead_hours": 10+i, "valid_at_utc": (run+timedelta(hours=10+i)).isoformat(), "archive_last_modified_at_utc": modified.isoformat()}
            hours.append(h)
            decoded = {k: {**f, "archive_last_modified_at_utc": modified.isoformat(),
                          "availability_gate_at_utc": (modified+timedelta(minutes=10)).isoformat(), "available_by_issue_proxy": True,
                          "step_type": "instant", "units": "degC" if k == "dewpoint" else FIELDS[k]["units"]} for k, f in fields.items()}
            cov.append({**h, "fields": decoded})
        return [s], hours, cov

    def test_missing_field_excludes_whole_night_without_imputation(self):
        samples, hours, cov = self.aligned_inputs()
        joined, excluded = join_covariates(samples, hours, cov, self.config)
        self.assertEqual(len(joined[0]["features"]), 23)
        del cov[3]["fields"]["cloud"]
        joined, excluded = join_covariates(samples, hours, cov, self.config)
        self.assertEqual(joined, [])
        self.assertEqual(excluded[0]["reason"], "missing_covariate_field")

    def test_bad_gate_or_forecast_run_is_rejected(self):
        samples, hours, cov = self.aligned_inputs()
        cov[0]["forecast_run_at_utc"] = "2023-03-01T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "forecast_run"):
            join_covariates(samples, hours, cov, self.config)
        samples, hours, cov = self.aligned_inputs()
        cov[0]["fields"]["cloud"]["availability_gate_at_utc"] = cov[0]["issued_at_utc"]
        with self.assertRaisesRegex(ValueError, "gate"):
            join_covariates(samples, hours, cov, self.config)

    def test_late_covariate_excludes_night_without_using_a_later_run(self):
        samples, hours, cov = self.aligned_inputs()
        for h, row in zip(hours, cov):
            issue = datetime.fromisoformat(row["forecast_run_at_utc"]) + timedelta(hours=4, minutes=5)
            row["issued_at_utc"] = h["issued_at_utc"] = issue.isoformat()
            for field in row["fields"].values():
                field["available_by_issue_proxy"] = False
        joined, excluded = join_covariates(samples, hours, cov, self.config)
        self.assertEqual(joined, [])
        self.assertEqual(excluded[0]["reason"], "late_covariate_by_proxy")

    @unittest.skipUnless(SCIENCE, "Install requirements-model.txt")
    def test_changed_2025_targets_cannot_change_fit_selection_or_prediction(self):
        samples = [sample(y, d) for y in range(2022, 2026) for d in range(1, 9)]
        changed = deepcopy(samples)
        for s in changed:
            if s["year"] == 2025:
                s["target"] = -1000
                s["target_curve"] = [-1000] * 14
        a = validate_candidates(samples, self.protocol, self.model_protocol)
        b = validate_candidates(changed, self.protocol, self.model_protocol)
        self.assertEqual(a, b)
        train = [s for s in samples if s["year"] < 2024]
        for name in candidates(self.protocol):
            model = fit_candidate(name, train, self.model_protocol)
            self.assertEqual(predict_candidate(model, samples[-8:]), predict_candidate(model, changed[-8:]))

    @unittest.skipUnless(SCIENCE, "Install requirements-model.txt")
    def test_saved_models_reproduce_all_feature_groups(self):
        train = [sample(2022, d) for d in range(1, 9)]
        check = [sample(2025, d) for d in range(1, 9)]
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name in candidates(self.protocol):
                model = fit_candidate(name, train, self.model_protocol)
                save_candidate(model, directory)
                replay = load_candidate(directory, name)
                for a, b in zip(predict_candidate(model, check), predict_candidate(replay, check)):
                    self.assertAlmostEqual(a["prediction"], b["prediction"], places=10)


@unittest.skipIf(ec is None, "Install requirements-archive.txt")
class NativeCovariateTests(unittest.TestCase):
    def message(self, key):
        handle = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
        try:
            keys = {"centre": "kwbc", "Ni": 2, "Nj": 2,
                    "latitudeOfFirstGridPointInDegrees": 45.0, "longitudeOfFirstGridPointInDegrees": 39.0,
                    "latitudeOfLastGridPointInDegrees": 44.75, "longitudeOfLastGridPointInDegrees": 39.25,
                    "iDirectionIncrementInDegrees": .25, "jDirectionIncrementInDegrees": .25,
                    "paramId": {"u10": 165, "v10": 166, "dewpoint": 168, "cloud": 228164}[key],
                    "dataDate": 20220301, "dataTime": 600, "stepUnits": 1, "forecastTime": 10}
            for k, v in keys.items():
                ec.codes_set(handle, k, v)
            if key == "cloud":
                ec.codes_set(handle, "typeOfLevel", "atmosphere")
            ec.codes_set_values(handle, [280, 281, 282, 283] if key == "dewpoint" else [10, 20, 30, 40])
            return ec.codes_get_message(handle)
        finally:
            ec.codes_release(handle)

    def test_real_decoder_checks_all_field_metadata_units_and_valid_time(self):
        config = load_single_config(ROOT / "config/single_run.json")
        config["latitude"] = 44.99
        run = datetime(2022, 3, 1, 6, tzinfo=timezone.utc)
        for key in FIELDS:
            content = self.message(key)
            result = decode_field(content, config, run, run+timedelta(hours=10), key)
            self.assertAlmostEqual(result["value"], 7.85 if key == "dewpoint" else 20)
            self.assertEqual((result["grid_latitude"], result["grid_longitude"]), (45, 39.25))
            with self.assertRaises(ValueError):
                decode_field(content, config, run, run+timedelta(hours=11), key)
        with self.assertRaisesRegex(ValueError, "shortName"):
            decode_field(self.message("u10"), config, run, run+timedelta(hours=10), "dewpoint")

    def test_native_decoder_rejects_averaged_cloud_field(self):
        handle = ec.codes_new_from_message(self.message("cloud"))
        try:
            ec.codes_set(handle, "stepType", "avg")
            ec.codes_set(handle, "startStep", 6)
            ec.codes_set(handle, "endStep", 10)
            content = ec.codes_get_message(handle)
        finally:
            ec.codes_release(handle)
        config = load_single_config(ROOT / "config/single_run.json")
        config["latitude"] = 44.99
        run = datetime(2022, 3, 1, 6, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "stepType"):
            decode_field(content, config, run, run+timedelta(hours=10), "cloud")


if __name__ == "__main__":
    unittest.main()
