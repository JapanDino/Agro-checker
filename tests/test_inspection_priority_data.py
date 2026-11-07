"""Synthetic technical fixtures only; no farm observations or files are created."""
from copy import deepcopy
import unittest
from agro_checker.inspection_priority_data import check_collection, pool_hash


def fixture():
    candidates = [dict(place_id=p, field_id="test-field", geometry_version="test-v1",
                       boundary_status="confirmed", eligible=True, source_ids=["test-source"])
                  for p in ("test-a", "test-b", "test-c", "test-d")]
    round_data = dict(round_id="synthetic-test", decision_at_utc="2024-05-01T09:00:00Z",
                      snapshot_saved_at_utc="2024-05-01T08:59:00Z",
                      routine_list_saved_at_utc="2024-05-01T08:58:00Z",
                      routine_list_before_system_seen=True, rule_version="test-only",
                      candidates=candidates, pool_sha256=pool_hash(candidates), random_seed=7,
                      system_top_k=["test-a"], routine_top_k=["test-b"], selected_controls=["test-c"],
                      sources=[dict(source_id="test-source", sha256="a" * 64,
                                    available_at_utc="2024-05-01T08:00:00Z",
                                    acquired_or_issued_at_utc="2024-05-01T07:00:00Z")],
                      assignments=[dict(visit_id=p, place_id=p, inclusion_probability=1 if i < 2 else .5)
                                   for i, p in enumerate(("test-a", "test-b", "test-c"))])
    results = [dict(visit_id=p, field_id="test-field", observed_at_utc="2024-05-01T10:00:00Z",
                    recorded_at_utc="2024-05-01T11:00:00Z", label_available_at_utc="2024-05-01T12:00:00Z",
                    actual_lat=45., actual_lon=38., position_match=True, completion="full",
                    observer_code="technical-fixture", reviewer_code="technical-reviewer",
                    notes="Synthetic fixture, not an observation", usefulness_reason="Test predicate",
                    review_status="reviewed", usefulness="useful" if i == 0 else "no_new_information",
                    new_information=i == 0, followup_required=i == 0)
               for i, p in enumerate(("test-a", "test-b", "test-c"))]
    return round_data, dict(results=results)


class InspectionCollectionTests(unittest.TestCase):
    def test_empty_metrics_unknown(self):
        report = check_collection({}, {"results": []})
        self.assertEqual(report["status"], "no_data")
        self.assertIsNone(report["system_precision_at_k"])
        self.assertFalse(report["training_allowed"])

    def test_complete_paired_comparison(self):
        r, y = fixture()
        originals = deepcopy((r, y))
        report = check_collection(r, y)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["precision_difference"], 1)
        self.assertEqual((r, y), originals)
        self.assertFalse(report["automatic_admission"])

    def test_missing_result_not_negative(self):
        r, y = fixture()
        y["results"].pop(1)
        report = check_collection(r, y)
        self.assertIsNone(report["system_precision_at_k"])
        self.assertIsNone(report["routine_precision_at_k"])
        self.assertNotIn("test-b", report["provisional_labels"])

    def test_unknown_partial_late_unverified_not_labels(self):
        for patch in ({"usefulness": "uncertain"}, {"completion": "partial"},
                      {"position_match": False}, {"review_status": "disputed"},
                      {"observed_at_utc": "2024-05-04T10:00:00Z", "recorded_at_utc": "2024-05-04T11:00:00Z",
                       "label_available_at_utc": "2024-05-04T12:00:00Z"}):
            with self.subTest(patch=patch):
                r, y = fixture()
                y["results"][0].update(patch)
                report = check_collection(r, y)
                self.assertNotIn("test-a", report["provisional_labels"])
                self.assertIsNone(report["precision_difference"])

    def test_future_acquisition_and_availability_block_scores(self):
        for key in ("available_at_utc", "acquired_or_issued_at_utc"):
            r, y = fixture()
            r["sources"][0][key] = "2024-05-02T00:00:00Z"
            report = check_collection(r, y)
            self.assertEqual(report["status"], "invalid")
            self.assertIsNone(report["precision_difference"])
            self.assertEqual(report["provisional_labels"], {})

    def test_pool_tampering(self):
        r, y = fixture()
        r["candidates"].pop()
        self.assertIn("pool_sha256 mismatch", check_collection(r, y)["errors"])

    def test_duplicate_result_and_assignment(self):
        for target in ("results", "assignments"):
            r, y = fixture()
            rows = y["results"] if target == "results" else r["assignments"]
            rows.append(deepcopy(rows[0]))
            self.assertEqual(check_collection(r, y)["status"], "invalid")

    def test_bad_random_probability(self):
        r, y = fixture()
        r["assignments"][-1]["inclusion_probability"] = 1
        self.assertEqual(check_collection(r, y)["status"], "invalid")

    def test_unblinded_baseline(self):
        r, y = fixture()
        r["routine_list_before_system_seen"] = False
        self.assertEqual(check_collection(r, y)["status"], "invalid")

    def test_timezone_and_label_chronology(self):
        for patch in ({"observed_at_utc": "2024-05-01T10:00:00"},
                      {"label_available_at_utc": "2024-04-30T10:00:00Z"}):
            r, y = fixture()
            y["results"][0].update(patch)
            self.assertEqual(check_collection(r, y)["status"], "invalid")

    def test_conflicting_negative_not_used(self):
        r, y = fixture()
        y["results"][1]["followup_required"] = True
        self.assertNotIn("test-b", check_collection(r, y)["provisional_labels"])

    def test_cancelled_has_no_invented_observation_or_label_time(self):
        r, y = fixture()
        y["results"][0].update(completion="cancelled", observed_at_utc=None,
                                review_status="not_reviewed", label_available_at_utc=None,
                                usefulness="not_assessed", actual_lat=None, actual_lon=None)
        report = check_collection(r, y)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["errors"], [])
        self.assertNotIn("test-a", report["provisional_labels"])


if __name__ == "__main__":
    unittest.main()
