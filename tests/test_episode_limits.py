from copy import deepcopy
from pathlib import Path
import json
import math
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from agro_checker.episode_limits import (
    SOURCE_JOINT, candidates, decode_limited, limited_distribution, load_limits_protocol, mask_shape, predict_limited,
    replay_limits, run_limits_study, select_limits, shape_space, target_audit, validate_limits,
)
from agro_checker.episode_study import assert_same_alerts, fit_joint, load_episode_protocol, mask_space, sequence_features
from agro_checker.frost_study import fit_cold, load_frost_protocol, night_samples, predict_cold, sha
from agro_checker.model_study import load_protocol
from agro_checker.pipeline import ROOT, write_json
from tests.test_frost_study import sample_rows


class EpisodeLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = load_limits_protocol(ROOT/"config/episode_limits.json")
        cls.jp = load_episode_protocol(ROOT/"config/episode_study.json")
        cls.dp = load_frost_protocol(ROOT/"config/frost_study.json")
        cls.m = load_protocol(ROOT/"config/model_study.json")
        cls.rows = sample_rows(2022)
        cls.model = fit_joint(cls.rows, 10., cls.jp, cls.m)
        cls.detector = fit_cold("event_duration_weather", cls.rows, cls.dp, cls.m)

    def test_short_gaps_only_inside_cold_not_warm_window_edges(self):
        m = [False]*14
        for i in [2, 3, 5, 8, 12, 13]: m[i] = True
        stats = mask_shape(m)
        self.assertEqual(stats["episodes"], 4)
        self.assertEqual(stats["internal_warm_gaps_h"], [1, 2, 3])
        self.assertEqual(stats["short_gap_cost"], 1.5)
        self.assertEqual(stats["short_gap_count"], 2)
        self.assertEqual(mask_shape([False]*2+[True]*3+[False]*9)["short_gap_cost"], 0.)
        self.assertEqual(mask_shape([False]*14)["episodes"], 0)

    def test_supported_spaces_have_exact_counts_and_cap_one_no_internal_gaps(self):
        space = mask_space()
        self.assertEqual(int((space["episodes"] <= 1).sum()), 105)
        self.assertEqual(int((space["episodes"] <= 2).sum()), 1470)
        self.assertTrue((shape_space()["short_gap_cost"][space["episodes"] == 1] == 0).all())
        self.assertEqual(len(candidates(self.p)), 6)
        limited = [s for s in candidates(self.p) if s["kind"] == "limited"]
        self.assertEqual(len(limited), 4)

    def test_energy_penalty_has_expected_odds_ratio_without_filling_truth(self):
        features = np.zeros((16383, 1))
        spec = {"max_episodes": 2, "gap_penalty": 2.}
        distribution = limited_distribution(features, [0.], spec)
        # Cold at 0/2 has 1h gap; cold at 0/3 has 2h; cold at 0/4 has 3h.
        one = (1+(1 << 2))-1; two = (1+(1 << 3))-1; long = (1+(1 << 4))-1
        self.assertAlmostEqual(distribution[one]/distribution[long], math.exp(-2.))
        self.assertAlmostEqual(distribution[two]/distribution[long], math.exp(-1.))
        self.assertAlmostEqual(distribution.sum(), 1.)
        self.assertTrue((distribution[mask_space()["episodes"] > 2] == 0).all())

    def test_increasing_gap_penalty_reduces_expected_gap_cost(self):
        s = night_samples(self.rows)[0]
        f = sequence_features(s, self.model)
        values = [limited_distribution(f, self.model["weights"], {"max_episodes": 2, "gap_penalty": a})@shape_space()["short_gap_cost"] for a in [0., 2., 5.]]
        self.assertGreaterEqual(values[0], values[1])
        self.assertGreaterEqual(values[1], values[2])

    def test_mbr_enforces_cap_in_action_space_not_only_posterior(self):
        for cap in [1, 2]:
            spec = {"max_episodes": cap, "gap_penalty": 0.}
            distribution = limited_distribution(np.zeros((16383, 1)), [0.], spec)
            action = decode_limited(distribution, spec, self.p)
            self.assertLessEqual(mask_shape(action)["episodes"], cap)
            self.assertGreater(sum(action), 0)
        bad = np.zeros(16383); bad[((1 << 0)+(1 << 2)+(1 << 4))-1] = 1.
        with self.assertRaisesRegex(ValueError, "violates episode cap"):
            decode_limited(bad, {"max_episodes": 1, "gap_penalty": 0.}, self.p)

    def test_target_poisoning_does_not_change_forecast_or_frozen_alerts(self):
        original = sample_rows(2025)
        poisoned = deepcopy(original)
        for r in poisoned: r["target"]["temperature"] = None
        reference = predict_cold(self.detector, original, .25)
        for spec in candidates(self.p):
            if spec["kind"] != "limited": continue
            a = predict_limited(self.model, self.detector, original, spec, self.p)
            b = predict_limited(self.model, self.detector, poisoned, spec, self.p)
            self.assertEqual(a, b)
            assert_same_alerts(reference, a)
            self.assertTrue(all(mask_shape(n["cold_mask"])["episodes"] <= spec["max_episodes"] for n in a))

    def test_audit_retains_observed_multiple_episodes_and_zero_temperature(self):
        rows = deepcopy(self.rows)
        audit = target_audit(rows)["2022"]
        self.assertEqual(audit["observed_cold_nights"], 2)
        self.assertEqual(audit["observed_nights_exceeding_cap"]["1"], 1)
        self.assertEqual(audit["complete_nights"], 8)
        self.assertEqual(rows, self.rows)
        # Exactly zero between two cold samples remains an observed warm gap.
        rows[14+9]["target"]["temperature"] = 0.
        audit = target_audit(rows)["2022"]
        self.assertEqual(audit["observed_nights_exceeding_cap"]["1"], 1)

    def test_historical_selection_and_protocol_tuning_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "2025"):
            validate_limits(sample_rows(2025), self.p, self.m, self.dp, self.jp)
        with tempfile.TemporaryDirectory() as d:
            p = deepcopy(self.p); p["gap_penalties"] = [0., 3.]
            path = Path(d)/"protocol.json"; write_json(path, p)
            with self.assertRaises(ValueError): load_limits_protocol(path)

    def test_empty_gate_and_censored_edges_preserved(self):
        detector = deepcopy(self.detector)
        detector["event_head"]["coefficients"] = [0.]*14
        detector["event_head"]["intercept"] = -10.
        spec = {"max_episodes": 1, "gap_penalty": 0.}
        p = predict_limited(self.model, detector, sample_rows(2025)[:14], spec, self.p)[0]
        self.assertEqual(sum(p["cold_mask"]), 0)
        detector["event_head"]["intercept"] = 10.
        model = deepcopy(self.model); model["weights"] = [0.]*len(model["weights"])
        from agro_checker.episode_study import FEATURE_NAMES
        model["weights"][FEATURE_NAMES.index("all_14_cold")] = 100.
        p = predict_limited(model, detector, sample_rows(2025)[:14], spec, self.p)[0]
        self.assertEqual(sum(p["cold_mask"]), 14)
        self.assertTrue(p["frost"]["left_censored"])
        self.assertTrue(p["frost"]["right_censored"])

    def test_frozen_choice_avoids_2025_and_tampered_weights_fail_before_history(self):
        rows = sample_rows(2022)+sample_rows(2023)+sample_rows(2024)
        opened = []
        def loader(data, years):
            opened.append(years)
            self.assertNotIn(2025, years)
            return [r for r in rows if r["year"] in years], {"synthetic": "same"}
        with tempfile.TemporaryDirectory() as d, patch("agro_checker.episode_limits.load_rows", side_effect=loader):
            data = Path(d)/"data"; directory = Path(d)/"models/research-episodes"
            train = [r for r in rows if r["year"] in [2022, 2023]]
            model = fit_joint(train, 10., self.jp, self.m)
            detector = fit_cold("event_duration_weather", train, self.dp, self.m)
            write_json(directory/"l2_10/model.json", model); write_json(directory/"detector/model.json", detector)
            write_json(directory/"selection-before-2025.json", {"selected_candidate": SOURCE_JOINT, "protocol": self.jp, "model_protocol": self.m,
                       "model_artifact_sha256": {"l2_10": sha(directory/"l2_10/model.json"), "detector": sha(directory/"detector/model.json")}})
            first = select_limits(ROOT/"config/episode_limits.json", data)
            frozen_path = Path(d)/"models/research-episode-limits/selection-before-2025.json"
            frozen = frozen_path.read_bytes()
            self.assertEqual(first, select_limits(ROOT/"config/episode_limits.json", data))
            self.assertEqual(frozen_path.read_bytes(), frozen)
            self.assertTrue(all(years == [2022, 2023, 2024] for years in opened))
            (Path(d)/"models/research-episode-limits/joint/model.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
                run_limits_study(ROOT/"config/episode_limits.json", data, Path(d)/"docs")
            self.assertEqual(frozen_path.read_bytes(), frozen)

    def test_replay_hash_and_forecast_fact_separation(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d); path = data/"processed/episode-limits/forecast-snapshot.json"
            n = {"date": "2025-03-07", "forecast": [{"selected_cold": True}], "hindsight_observations": [{"observed_temperature_c": -1.}]}
            write_json(path, {"nights": [n]})
            write_json(path.with_name(path.name+".meta.json"), {"sha256": sha(path)})
            replay = replay_limits(data, n["date"])
            self.assertNotIn("observed_temperature_c", replay["forecast"][0])
            self.assertIsNone(replay["forecast_available_at_utc"])
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"): replay_limits(data, n["date"])


if __name__ == "__main__": unittest.main()
