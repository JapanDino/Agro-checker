from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from agro_checker.field_decision import forecast_brief
from agro_checker.field_store import FieldStore
from test_field_pilot import profile, forecast, measurements, ISSUE, START, END


class FieldQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = FieldStore(Path(self.temp.name)); self.p = profile()
        self.pr = self.store.save_profile(self.p, now=ISSUE)

    def issue(self, curve=None, issued=ISSUE):
        return self.store.issue(self.p["id"], "z1", forecast(self.pr["id"], curve, issued), issued)

    def journal(self, f, when=ISSUE+timedelta(minutes=10), recorded=None):
        return self.store.journal(self.p["id"], {"forecast_id": f["id"], "actor": "Fixture", "action": "observe",
                                  "protection_status": "not_used", "decision_at_utc": when.isoformat()}, recorded or when)

    def test_brief_preserves_multiple_episodes_and_hourly_crossing_uncertainty(self):
        f = self.issue([3, 2, 1, -1, -2, 1, 2, -1, -1, 2, 2, 2, 2, 2])
        b = forecast_brief(f["payload"], ISSUE)
        self.assertEqual(len(b["episodes"]), 2); self.assertEqual(b["cold_bins_hours"], 4)
        self.assertEqual(b["onset_bracket_at_utc"], [(START+timedelta(hours=i)).isoformat() for i in [2, 3]])
        self.assertEqual(b["prepare_by_utc"], START.isoformat())
        self.assertTrue(b["lead_sufficient_at_issue"]); self.assertIsNone(b["crop_damage_probability"])

    def test_brief_is_unchanged_by_poisoned_later_truth_or_journal(self):
        f = self.issue(); before = forecast_brief(f["payload"], ISSUE)
        self.store.import_measurements(self.p["id"], measurements([-20.]*14), now=END)
        self.journal(f, recorded=END)
        self.assertEqual(before, forecast_brief(self.store.records("forecast")[0]["payload"], ISSUE))
        self.assertEqual(self.store.records("forecast")[0]["sha256"], f["sha256"])

    def test_left_censored_episode_does_not_invent_prepare_deadline(self):
        b = forecast_brief(self.issue([-1.]*14)["payload"], ISSUE)
        self.assertTrue(b["episodes"][0]["left_censored"]); self.assertTrue(b["episodes"][0]["right_censored"])
        self.assertIsNone(b["prepare_by_utc"]); self.assertIn("onset_left_censored", b["limitations"])

    def test_nonzero_threshold_is_strict_and_no_cold_has_no_deadline(self):
        self.p["decision"]["weather_threshold_c"] = 1.
        self.pr = self.store.save_profile(self.p, self.pr["id"], ISSUE)
        b = forecast_brief(self.issue([1.]*14)["payload"], END)
        self.assertFalse(b["weather_event"]); self.assertEqual(b["window_state"], "past")
        self.assertIsNone(b["prepare_by_utc"])
        self.assertTrue(forecast_brief(self.issue([1.]*2+[.9]*12)["payload"], ISSUE)["weather_event"])

    def test_missing_lead_and_late_issue_explained_without_false_precision(self):
        self.p["decision"]["required_lead_hours"] = None
        self.pr = self.store.save_profile(self.p, self.pr["id"], ISSUE)
        b = forecast_brief(self.issue()["payload"], ISSUE)
        self.assertIsNone(b["prepare_by_utc"]); self.assertIn("lead_unknown", b["limitations"])
        self.p["decision"]["required_lead_hours"] = 4
        self.pr = self.store.save_profile(self.p, self.pr["id"], ISSUE)
        b = forecast_brief(self.issue([2]*3+[-1]*11, START-timedelta(minutes=30))["payload"], ISSUE)
        self.assertFalse(b["lead_sufficient_at_issue"]); self.assertIn("issue_after_prepare_deadline", b["limitations"])

    def test_latest_unscorable_issue_cannot_fall_back_to_older_hit(self):
        f = self.issue(); self.journal(f); self.store.import_measurements(self.p["id"], measurements(), now=END)
        p = deepcopy(self.p); p["sensors"][0]["measurement_type"] = "canopy"
        pr = self.store.save_profile(p, self.pr["id"], ISSUE+timedelta(minutes=20))
        later = self.store.issue(self.p["id"], "z1", forecast(pr["id"], issued=ISSUE+timedelta(hours=1)), ISSUE+timedelta(hours=1))
        s = self.store.state(END); m = s["field_metrics"]
        self.assertEqual(m["candidate_site_nights"], 1); self.assertEqual(m["unique_site_nights"], 0)
        self.assertIsNone(m["hourly_temperature_mae_c"]); self.assertEqual(m["excluded_completed_site_nights"], 1)
        self.assertEqual(s["field_coverage"][0]["forecast_id"], later["id"])
        old = next(a for a in s["assessments"] if a["forecast_id"] == f["id"])
        self.assertEqual(old["outcome"], "hit"); self.assertFalse(old["selected_for_field_metrics"])
        self.assertIn("superseded_issue", old["exclusion_reasons"])

    def test_follow_up_entries_do_not_replace_first_recorded_decision(self):
        f = self.issue(); j = self.journal(f); self.store.import_measurements(self.p["id"], measurements(), now=END)
        before = self.store.assess(f, END)
        self.journal(f, END-timedelta(minutes=1), END)
        after = self.store.assess(f, END)
        self.assertEqual(after["lead_decision_id"], j["id"])
        self.assertEqual(after["decision_lead_to_observed_cold_h"], before["decision_lead_to_observed_cold_h"])
        self.assertTrue(after["lead_decision_recorded_before_night"])

    def test_retrospective_declared_time_does_not_prove_operational_lead(self):
        f = self.issue(); self.journal(f, recorded=END)
        self.store.import_measurements(self.p["id"], measurements(), now=END)
        a = self.store.assess(f, END)
        self.assertIsNone(a["decision_lead_to_observed_cold_h"])
        self.assertIsNone(a["readiness_lead_met"]); self.assertEqual(a["retrospective_decision_entries"], 1)

    def test_readiness_uses_lower_crossing_bound_not_later_cold_sample(self):
        self.p["decision"]["required_lead_hours"] = 1
        self.pr = self.store.save_profile(self.p, self.pr["id"], ISSUE)
        f = self.issue([2]+[-1]*13, START-timedelta(hours=1))
        self.journal(f, START-timedelta(minutes=50))
        self.store.import_measurements(self.p["id"], measurements([2]+[-1]*13), now=END)
        a = self.store.assess(f, END)
        self.assertGreater(a["decision_lead_to_observed_cold_h"], 1)
        self.assertLess(a["decision_lead_to_onset_lower_bound_h"], 1); self.assertFalse(a["readiness_lead_met"])

    def test_temperature_and_humidity_match_independently_without_imputation(self):
        f = self.issue(); self.journal(f)
        self.store.import_measurements(self.p["id"], measurements().replace(",80", ","), now=END)
        rh = "sensor_id,observed_at,temperature_c,rh_pct\n"+"".join(f"t1,{(START+timedelta(hours=i,minutes=2)).isoformat()},,70\n" for i in range(13))
        self.store.import_measurements(self.p["id"], rh, now=END)
        a = self.store.assess(f, END); m = self.store.state(END)["field_metrics"]
        self.assertEqual(a["matched_hours"], 14); self.assertEqual(a["matched_rh_hours"], 13)
        self.assertEqual(m["paired_rh_hours"], 13); self.assertEqual(m["hourly_rh_mae_pp"], 10.)
        self.assertIsNone(a["observations"][-1]["rh_pct"])
        self.assertNotEqual(a["observations"][0]["measurement_id"], a["observations"][0]["rh_measurement_id"])

    def test_full_hourly_points_wait_until_end_of_night_window(self):
        f = self.issue(); self.journal(f); self.store.import_measurements(self.p["id"], measurements(), now=END)
        early = START+timedelta(hours=13, minutes=10)
        a = self.store.assess(f, early)
        self.assertEqual(a["matched_hours"], 14); self.assertEqual(a["status"], "awaiting_night")
        self.assertIn("night_pending", a["exclusion_reasons"])
        m = self.store.state(early)["field_metrics"]
        self.assertEqual(m["pending_site_nights"], 1); self.assertEqual(m["unique_site_nights"], 0)
        self.assertEqual(self.store.assess(f, START+timedelta(hours=14))["status"], "scored")


if __name__ == "__main__": unittest.main()
