from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agro_checker.field_live import capture_forecast, candidate_runs
from agro_checker.field_store import FieldStore, readiness
from agro_checker.field_worker import collect_due
from test_field_pilot import profile, forecast, ISSUE, START


class FieldAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = FieldStore(Path(self.temp.name)); self.p = profile()
        self.pr = self.store.save_profile(self.p, now=ISSUE)

    def hour(self, directory, run, valid):
        meta = {"retrieved_at_utc": (ISSUE-timedelta(minutes=1)).isoformat(), "sha256": "fixture",
                "archive_last_modified_at_utc": (run+timedelta(hours=1)).isoformat()}
        return {"valid": valid, "temp": b"fixture", "temp_meta": meta, "index_meta": meta,
                "fields": {k: (b"fixture", meta) for k in ["u10", "v10", "dewpoint", "cloud", "rh"]}}

    def decode(self, content, config, run, valid, key=None, specs=None):
        return {"grid_latitude": 45., "grid_longitude": 39.25, "grid_distance_km": 8.,
                "forecast_temperature_c": 2., "value": {"u10": 3., "v10": 4., "dewpoint": 0., "cloud": 30., "rh": 80.}.get(key, 2.)}

    def test_fallback_retrieves_entire_previous_run_without_mixing(self):
        first = candidate_runs(ISSUE)[0]; requested = []
        def fetch(directory, run, valid):
            requested.append((run, valid))
            if run == first and valid == START+timedelta(hours=4): raise OSError("unpublished hour")
            return self.hour(directory, run, valid)
        with patch("agro_checker.field_live.fetch_hour", side_effect=fetch), patch("agro_checker.field_live.decode_temperature", side_effect=self.decode), patch("agro_checker.field_live.decode_field", side_effect=self.decode), patch("agro_checker.field_live.utc_now", return_value=ISSUE):
            r = capture_forecast(self.store, self.p["id"], "z1", "2026-10-02", ISSUE)
        p = r["payload"]; chosen = candidate_runs(ISSUE)[1]
        self.assertEqual(p["run_at_utc"], chosen.isoformat()); self.assertEqual(len(p["hours"]), 14)
        self.assertEqual(len(p["manifest"]), 98); self.assertEqual(len(p["attempted_runs"]), 1)
        self.assertEqual(sum(run == chosen for run, _ in requested), 14)
        self.assertTrue(all(h["lead_hours"] == int((START+timedelta(hours=i)-chosen).total_seconds()/3600) for i, h in enumerate(p["hours"])))
        self.assertEqual(p["hours"][0]["wind_ms"], 5.)

    def test_late_completion_never_becomes_a_shadow_issue(self):
        with patch("agro_checker.field_live.fetch_hour", side_effect=self.hour), patch("agro_checker.field_live.decode_temperature", side_effect=self.decode), patch("agro_checker.field_live.decode_field", side_effect=self.decode), patch("agro_checker.field_live.utc_now", return_value=START):
            with self.assertRaisesRegex(ValueError, "после начала ночи"):
                capture_forecast(self.store, self.p["id"], "z1", "2026-10-02", ISSUE)
        self.assertEqual(self.store.records("forecast"), [])

    def test_daily_worker_is_pre_night_field_only_and_idempotent(self):
        demo = deepcopy(self.p); demo.update(id="demo-fixture", mode="demo")
        self.store.save_profile(demo, now=ISSUE)
        draft = deepcopy(self.p); draft["id"] = "blank-fixture"; draft["zones"][0].update(latitude=None, longitude=None)
        self.store.save_profile(draft, now=ISSUE)
        now = ISSUE+timedelta(hours=2)  # 17:00 MSK
        calls = []
        def capture(store, pid, zid, night, now):
            calls.append((pid, zid, night)); f = forecast(self.pr["id"], issued=now)
            return store.issue(pid, zid, f, now)
        self.assertEqual(collect_due(self.store, ISSUE, capture), [])
        self.assertEqual(collect_due(self.store, START, capture), [])
        self.assertEqual(collect_due(self.store, now, capture)[0]["status"], "saved")
        self.assertEqual(calls, [(self.p["id"], "z1", "2026-10-02")])
        self.assertEqual(collect_due(self.store, now, capture), [])

    def test_daily_failure_is_audited_and_retry_stays_before_night(self):
        def fail(*args, **kwargs): raise OSError("fixture outage")
        now = ISSUE+timedelta(hours=2)
        self.assertEqual(collect_due(self.store, now, fail)[0]["status"], "failed")
        self.assertEqual(len(self.store.records("collection_failure")), 1)
        self.assertEqual(self.store.records("forecast"), [])

    def test_future_review_rejected_and_available_equipment_requires_list(self):
        p = deepcopy(self.p); p["decision"]["reviewed_at_utc"] = (ISSUE+timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "будущем"): self.store.save_profile(p, self.pr["id"], ISSUE)
        p = deepcopy(self.p); p["equipment_status"] = "available"
        self.assertFalse(readiness(p)["ready_for_field_decision_review"])

    def test_inspection_records_samples_without_causal_savings(self):
        f = self.store.issue(self.p["id"], "z1", forecast(self.pr["id"]), ISSUE)
        when = START+timedelta(hours=20)
        body = {"forecast_id": f["id"], "actor": "Test", "observed_at_utc": when.isoformat(),
                "examined_units": 40, "damaged_units": 5, "sampling_method": "Fixture blossom transect"}
        r = self.store.inspection(self.p["id"], body, when)
        self.assertEqual(r["payload"]["sample_damage_pct"], 12.5)
        self.assertIsNone(r["payload"]["causal_attribution"])
        self.assertIsNone(self.store.state(when)["field_metrics"]["prevented_loss_rub"])
        body["damaged_units"] = 41
        with self.assertRaises(ValueError): self.store.inspection(self.p["id"], body, when)
        body["damaged_units"] = 0; body["observed_at_utc"] = ISSUE.isoformat()
        with self.assertRaises(ValueError): self.store.inspection(self.p["id"], body, when)


if __name__ == "__main__": unittest.main()
