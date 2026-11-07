from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from agro_checker.episode_study import (
    FEATURE_NAMES, assert_same_alerts, candidates, decode_distribution, expected_sequence_loss, fit_joint,
    load_episode_protocol, mask_space, pool_episode_scores, predict_joint, replay_episodes, score_episodes,
    run_episode_study, select_episodes, sequence_distribution, sequence_features, sequence_objective, validate_episodes, validate_joint_model,
)
from agro_checker.frost_study import fit_cold, load_frost_protocol, night_samples, predict_cold, sha
from agro_checker.model_study import load_protocol
from agro_checker.pipeline import ROOT, write_json
from tests.test_frost_study import sample_rows


class EpisodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = load_episode_protocol(ROOT/"config/episode_study.json")
        cls.m = load_protocol(ROOT/"config/model_study.json")
        cls.dp = load_frost_protocol(ROOT/"config/frost_study.json")
        cls.rows = sample_rows(2022)
        cls.model = fit_joint(cls.rows, 1., cls.p, cls.m)
        cls.detector = fit_cold("event_duration_weather", cls.rows, cls.dp, cls.m)

    def test_exact_space_contains_every_nonempty_mask_and_multiple_episodes(self):
        space = mask_space()
        self.assertEqual(space["masks"].shape, (16383, 14))
        self.assertEqual(len(np.unique(space["masks"], axis=0)), 16383)
        self.assertTrue((space["count"] >= 1).all())
        self.assertEqual(space["count"][-1], 14)
        # Bits 0 and 13: two separated censored episodes.
        index = (1+(1 << 13))-1
        self.assertEqual(space["episodes"][index], 2)
        self.assertEqual(space["first"][index], 0)
        self.assertEqual(space["last"][index], 13)

    def test_joint_gradient_matches_finite_difference(self):
        # Tiny arbitrary design verifies the actual exact likelihood, including regularization.
        rng = np.random.default_rng(83)
        design = rng.normal(size=(2, 7, 4)); weights = rng.normal(size=4); targets = np.array([2, 5])
        _, analytic = sequence_objective(weights, design, targets, 1.)
        numeric = []
        for i in range(4):
            delta = np.zeros(4); delta[i] = 1e-6
            numeric.append((sequence_objective(weights+delta, design, targets, 1.)[0]-sequence_objective(weights-delta, design, targets, 1.)[0])/2e-6)
        np.testing.assert_allclose(analytic, numeric, atol=1e-6)

    def test_expected_loss_equals_direct_loss_on_degenerate_and_mixed_posteriors(self):
        space = mask_space()
        weights = self.p["decoder_loss_weights"]
        probability = np.zeros(16383)
        indexes = [19, 101, 3000]
        probability[indexes] = [.2, .5, .3]
        risk = expected_sequence_loss(probability, weights)
        for candidate in [0, 45, 1039, 16382]:
            expected = 0.
            for index, weight in zip(indexes, [.2, .5, .3]):
                loss = np.abs(space["masks"][candidate]-space["masks"][index]).sum()/14
                loss += abs(space["count"][candidate]-space["count"][index])/14
                loss += abs(space["first"][candidate]-space["first"][index])/13
                loss += .25*abs(space["episodes"][candidate]-space["episodes"][index])/7
                expected += weight*loss
            self.assertAlmostEqual(risk[candidate], expected)
        probability[:] = 0.; probability[101] = 1.
        for decoder in self.p["decoders"]:
            self.assertEqual(decode_distribution(probability, decoder, self.p), [bool(v) for v in space["masks"][101]])

    def test_normalization_is_stable_for_large_energies(self):
        distribution = sequence_distribution(np.array([[10000.], [10001.]]), [1.])
        self.assertAlmostEqual(distribution.sum(), 1.)
        self.assertTrue(np.isfinite(distribution).all())
        with self.assertRaisesRegex(ValueError, "Invalid normalized"):
            expected_sequence_loss(np.zeros(16383), self.p["decoder_loss_weights"])

    def test_fit_selection_reject_historical_and_missing_cold_training(self):
        with self.assertRaisesRegex(ValueError, "2025"):
            fit_joint(sample_rows(2025), 1., self.p, self.m)
        with self.assertRaisesRegex(ValueError, "2025"):
            validate_episodes(sample_rows(2022)+sample_rows(2025), self.p, self.m, self.dp)
        missing = deepcopy(self.rows)
        missing[0]["target"]["temperature"] = None
        m = fit_joint(missing, 1., self.p, self.m)
        self.assertEqual(m["training_nights"], 7)
        self.assertEqual(m["training_cold_nights"], 1)

    def test_frozen_gate_survives_every_decoder_and_target_poisoning(self):
        original = sample_rows(2025)
        missing = deepcopy(original)
        for r in missing: r["target"]["temperature"] = None
        reference = predict_cold(self.detector, original, .25)
        for decoder in self.p["decoders"]:
            predicted = predict_joint(self.model, self.detector, original, decoder, self.p)
            assert_same_alerts(reference, predicted)
            self.assertEqual(predicted, predict_joint(self.model, self.detector, missing, decoder, self.p))

    def test_gate_forces_empty_or_nonempty_and_preserves_censoring(self):
        detector = deepcopy(self.detector)
        detector["event_head"]["coefficients"] = [0.]*14
        rows = sample_rows(2025)[:14]
        model = deepcopy(self.model); model["weights"] = [0.]*len(FEATURE_NAMES)
        model["weights"][FEATURE_NAMES.index("cold_at_left_edge")] = 100.
        model["weights"][FEATURE_NAMES.index("cold_at_right_edge")] = 100.
        model["weights"][FEATURE_NAMES.index("cold_fraction")] = -10.
        detector["event_head"]["intercept"] = 10.
        p = predict_joint(model, detector, rows, "map", self.p)[0]
        self.assertEqual(sum(p["cold_mask"]), 2)
        self.assertEqual(len(p["frost"]["episodes"]), 2)
        self.assertTrue(p["frost"]["left_censored"])
        self.assertTrue(p["frost"]["right_censored"])
        detector["event_head"]["intercept"] = -10.
        p = predict_joint(model, detector, rows, "map", self.p)[0]
        self.assertEqual(sum(p["cold_mask"]), 0)

    def test_start_miss_penalty_and_episode_count_separate_from_hit_onset(self):
        rows = sample_rows(2025)
        predictions = [{"date": s["date"], "cold_mask": [False]*14, "cold_hour_model_scores": None, "night_event_model_score": None} for s in night_samples(rows)]
        score = score_episodes(rows, predictions)
        self.assertEqual(score["first_sample_pairs"], 0)
        self.assertIsNone(score["first_cold_sample_mae_hits_h"])
        self.assertEqual(score["cold_first_sample_error_with_miss_penalty_h"], 14.)
        self.assertEqual(score["episode_count_mae_cold"], 1.5)
        pooled = pool_episode_scores([score, score])
        self.assertEqual(pooled["cold_nights"], 4)
        self.assertAlmostEqual(pooled["episode_selection_loss"], score["episode_selection_loss"])

    def test_saved_joint_model_reproduces_distribution_and_schema_guard(self):
        saved = validate_joint_model(json.loads(json.dumps(self.model)))
        s = night_samples(self.rows)[0]
        a = sequence_distribution(sequence_features(s, self.model), self.model["weights"])
        b = sequence_distribution(sequence_features(s, saved), saved["weights"])
        np.testing.assert_array_equal(a, b)
        bad = deepcopy(saved); bad["training_years"] = [2025]
        with self.assertRaises(ValueError): validate_joint_model(bad)
        bad = deepcopy(saved); bad["weights"][0] = float("nan")
        with self.assertRaises(ValueError): validate_joint_model(bad)

    def test_registry_and_no_calendar_or_targets_in_features(self):
        self.assertEqual(len(candidates(self.p)), 5)
        self.assertTrue(all("calendar" not in n and "observed" not in n for n in FEATURE_NAMES))
        a = night_samples(self.rows)[0]
        changed = deepcopy(self.rows)
        for r in changed: r["target"]["temperature"] = -999.
        b = night_samples(changed)[0]
        np.testing.assert_array_equal(sequence_features(a, self.model), sequence_features(b, self.model))

    def test_replay_hash_and_forecast_fact_separation(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d); path = data/"processed/episode-study/forecast-snapshot.json"
            n = {"date": "2025-03-07", "forecast": [{"selected_cold": True}], "hindsight_observations": [{"observed_temperature_c": -1.}]}
            write_json(path, {"nights": [n]})
            write_json(path.with_name(path.name+".meta.json"), {"sha256": sha(path)})
            replay = replay_episodes(data, n["date"])
            self.assertNotIn("observed_temperature_c", replay["forecast"][0])
            self.assertIsNone(replay["forecast_available_at_utc"])
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"): replay_episodes(data, n["date"])

    def test_alert_preservation_detects_even_one_changed_night(self):
        p = [{"date": "2025-03-01", "cold_mask": [False]*14}]
        q = deepcopy(p); q[0]["cold_mask"][0] = True
        with self.assertRaisesRegex(ValueError, "changed the frozen"): assert_same_alerts(p, q)

    def test_frozen_selection_avoids_2025_and_tampered_weights_fail_before_history(self):
        rows = sample_rows(2022)+sample_rows(2023)+sample_rows(2024)
        opened = []
        def loader(data, years):
            opened.append(years)
            self.assertNotIn(2025, years)
            return [r for r in rows if r["year"] in years], {"synthetic": "unchanged"}
        with tempfile.TemporaryDirectory() as d, patch("agro_checker.episode_study.load_rows", side_effect=loader):
            data = Path(d)/"data"
            previous_dir = Path(d)/"models/research-frost"
            detector = fit_cold("event_duration_weather", [r for r in rows if r["year"] in [2022, 2023]], self.dp, self.m)
            detector_path = previous_dir/"event_duration_weather/model.json"
            write_json(detector_path, detector)
            write_json(previous_dir/"selection-before-2025.json", {
                "selected_candidate": self.p["fixed_detector"], "model_protocol": self.m, "protocol": self.dp,
                "model_artifact_sha256": {"event_duration_weather": sha(detector_path)}})
            first = select_episodes(ROOT/"config/episode_study.json", data)
            path = Path(d)/"models/research-episodes/selection-before-2025.json"
            frozen = path.read_bytes()
            self.assertEqual(first, select_episodes(ROOT/"config/episode_study.json", data))
            self.assertEqual(path.read_bytes(), frozen)
            self.assertTrue(all(years == [2022, 2023, 2024] for years in opened))
            model_path = Path(d)/"models/research-episodes/l2_1/model.json"
            model_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
                run_episode_study(ROOT/"config/episode_study.json", data, Path(d)/"docs")
            self.assertEqual(path.read_bytes(), frozen)


if __name__ == "__main__": unittest.main()
