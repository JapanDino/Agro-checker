"""Exact conditional cold-sequence model with a frozen night detector, season-forward."""

from datetime import date, datetime, timedelta
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import math

from .frost_study import (DURATION_INDEXES, HOUR_NAMES, NIGHT_NAMES, evaluate_head, fit_cold, load_frost_protocol,
                          load_rows, night_samples, paired_uncertainty, predict_cold, score_cold, sha, validate_saved_model)
from .hourly_study import compare_timing_cohorts, timed_frost
from .model_study import load_protocol
from .pipeline import ROOT, UTC, write_csv, write_json

REFERENCE = {"name": "event_duration_weather@0.25", "family": "event_duration_weather", "threshold": .25}
STRUCTURE_NAMES = ["cold_fraction", "cold_fraction_squared", "episode_count_over_7", "transitions_over_13",
                   "first_cold_over_13", "last_cold_over_13", "cold_at_left_edge", "cold_at_right_edge",
                   "sum_run_lengths_squared_over_196", "all_14_cold"]
FEATURE_NAMES = [f"cold_unary_{n}_sum_over_sqrt14" for n in HOUR_NAMES]+STRUCTURE_NAMES+[
    f"duration_squared_x_{NIGHT_NAMES[i]}" for i in DURATION_INDEXES]
SELECT_WEIGHTS = {"miss_rate": .25, "false_alarm_rate": .15, "cold_duration_mae_over_14": .2,
                  "warm_duration_mae_over_14": .1, "cold_mask_mismatch_over_14": .15,
                  "cold_first_sample_error_with_miss_penalty_over_14": .15}
DECODE_WEIGHTS = {"hour_mask_fraction": 1., "duration_absolute_over_14": 1., "first_sample_absolute_over_13": 1.,
                  "episode_count_absolute_over_7": .25}


def load_episode_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if (p["schema_version"] != 1 or p["fixed_detector"] != REFERENCE or p["regularization"] != [1., 10.]
            or p["decoders"] != ["map", "minimum_expected_loss"] or p["selection_weights"] != SELECT_WEIGHTS
            or p["decoder_loss_weights"] != DECODE_WEIGHTS or p["weather_threshold_c"] != 0
            or p["source_config"] != "single_run.json" or p["base_model_protocol"] != "model_study.json"
            or p["detector_protocol"] != "frost_study.json"
            or p["optimizer"] != {"method": "L-BFGS-B", "maxiter": 300, "ftol": 1e-9, "gtol": 1e-6}):
        raise ValueError("Unexpected fixed episode protocol")
    return p


def candidates(p):
    return [{"name": "previous_detector_duration", "kind": "reference"}]+[
        {"name": f"joint_l2_{a:g}_{d}", "kind": "joint", "regularization": a, "decoder": d}
        for a in p["regularization"] for d in p["decoders"]]


@lru_cache(maxsize=1)
def mask_space():
    import numpy as np
    codes = np.arange(1, 1 << 14, dtype=np.int64)
    masks = ((codes[:, None] >> np.arange(14)) & 1).astype(np.float64)
    count = masks.sum(axis=1).astype(int)
    first = masks.argmax(axis=1)
    last = 13-masks[:, ::-1].argmax(axis=1)
    starts = masks*(1-np.column_stack([np.zeros(len(codes)), masks[:, :-1]]))
    episodes = starts.sum(axis=1).astype(int)
    transitions = np.abs(np.diff(masks, axis=1)).sum(axis=1)
    squared_runs = []
    for mask in masks:
        lengths = []; current = 0
        for v in mask:
            if v:
                current += 1
            elif current:
                lengths.append(current); current = 0
        lengths.append(current)
        squared_runs.append(sum(k*k for k in lengths))
    structure = np.column_stack([count/14, (count/14)**2, episodes/7, transitions/13,
                                 first/13, last/13, masks[:, 0], masks[:, -1], np.array(squared_runs)/196, count == 14])
    result = {"masks": masks, "count": count, "first": first, "last": last, "episodes": episodes, "structure": structure}
    for value in result.values(): value.flags.writeable = False
    return result


def scaler(x):
    import numpy as np
    a = np.asarray(x, dtype=float)
    mean, scale = a.mean(axis=0), a.std(axis=0)
    scale[scale == 0] = 1.
    return {"mean": mean.tolist(), "scale": scale.tolist()}


def sequence_features(sample, model):
    import numpy as np
    space = mask_space()
    x = (np.asarray(sample["hour_features"])-model["hour_scaler"]["mean"])/model["hour_scaler"]["scale"]
    duration_x = (np.asarray(sample["duration_features"])-model["duration_scaler"]["mean"])/model["duration_scaler"]["scale"]
    unary = space["masks"]@x/math.sqrt(14)
    duration_interactions = (space["count"][:, None]/14)**2*duration_x
    return np.column_stack([unary, space["structure"], duration_interactions])


def sequence_objective(weights, design, truth_indexes, regularization):
    """Exact negative conditional log likelihood and analytic gradient for all nonempty masks."""
    import numpy as np
    from scipy.special import logsumexp
    logits = design@weights
    normalizer = logsumexp(logits, axis=1)
    probabilities = np.exp(logits-normalizer[:, None])
    targets = design[np.arange(len(design)), truth_indexes]
    loss = (normalizer-logits[np.arange(len(design)), truth_indexes]).sum()+.5*regularization*(weights@weights)
    gradient = probabilities.reshape(-1)@design.reshape(-1, design.shape[-1])-targets.sum(axis=0)+regularization*weights
    return float(loss), gradient


def fit_joint(rows, regularization, p, model_protocol):
    import numpy as np
    from scipy.optimize import minimize
    from threadpoolctl import threadpool_limits
    if not rows or any(r["year"] >= model_protocol["historical_check_year"] for r in rows):
        raise ValueError("2025 targets cannot enter joint episode fit")
    if regularization not in p["regularization"]:
        raise ValueError("Unknown fixed joint regularization")
    samples = [s for s in night_samples(rows) if s["complete"]]
    cold = [s for s in samples if s["duration"] > 0]
    if not cold:
        raise ValueError("Joint episode fit needs observed cold nights")
    model = {"schema_version": 1, "regularization": regularization, "feature_names": FEATURE_NAMES,
             "hour_feature_names": HOUR_NAMES, "duration_feature_names": [NIGHT_NAMES[i] for i in DURATION_INDEXES],
             "training_years": sorted({s["year"] for s in samples}), "training_nights": len(samples), "training_cold_nights": len(cold),
             "last_training_night": samples[-1]["date"],
             "hour_scaler": scaler([x for s in samples for x in s["hour_features"]]),
             "duration_scaler": scaler([s["duration_features"] for s in samples])}
    indexes = np.array([sum(int(v) << i for i, v in enumerate(s["cold_mask"]))-1 for s in cold])
    with threadpool_limits(limits=2):
        design = np.stack([sequence_features(s, model) for s in cold])
        result = minimize(sequence_objective, np.zeros(len(FEATURE_NAMES)), args=(design, indexes, regularization), jac=True,
                          method=p["optimizer"]["method"], options={k: v for k, v in p["optimizer"].items() if k != "method"})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError(f"Joint episode optimizer did not converge: {result.message}")
    model["weights"] = result.x.tolist()
    model["optimizer"] = {"converged": bool(result.success), "iterations": int(result.nit), "objective": float(result.fun),
                           "gradient_max_abs": float(np.abs(result.jac).max()), "message": str(result.message)}
    return model


def sequence_distribution(features, weights):
    import numpy as np
    from scipy.special import logsumexp
    logits = features@np.asarray(weights)
    if not np.isfinite(logits).all(): raise ValueError("Nonfinite sequence energy")
    return np.exp(logits-logsumexp(logits))


def expected_sequence_loss(probabilities, loss_weights):
    import numpy as np
    space = mask_space()
    if (len(probabilities) != len(space["masks"]) or np.any(probabilities < 0) or not np.isfinite(probabilities).all()
            or not np.isclose(probabilities.sum(), 1., atol=1e-8)):
        raise ValueError("Invalid normalized sequence probabilities")
    marginal = probabilities@space["masks"]
    risk = loss_weights["hour_mask_fraction"]*(space["masks"]@(1-2*marginal)+marginal.sum())/14
    for key, denom, weight_key, length in [("count", 14, "duration_absolute_over_14", 15),
                                           ("first", 13, "first_sample_absolute_over_13", 14),
                                           ("episodes", 7, "episode_count_absolute_over_7", 8)]:
        distribution = np.bincount(space[key], weights=probabilities, minlength=length)
        losses = np.abs(np.arange(length)[:, None]-np.arange(length))@distribution
        risk += loss_weights[weight_key]*losses[space[key]]/denom
    return risk


def decode_distribution(probabilities, decoder, p):
    import numpy as np
    if decoder not in p["decoders"]: raise ValueError("Unknown joint decoder")
    if (len(probabilities) != 16383 or not np.isfinite(probabilities).all() or np.any(probabilities < 0)
            or not np.isclose(probabilities.sum(), 1., atol=1e-8)):
        raise ValueError("Invalid normalized sequence probabilities")
    index = int(np.argmax(probabilities)) if decoder == "map" else int(np.argmin(expected_sequence_loss(probabilities, p["decoder_loss_weights"])))
    # Numeric mask code order resolves ties. Every possible nonempty mask, including multiple episodes, is supported.
    return [bool(v) for v in mask_space()["masks"][index]]


def predict_joint(model, detector, rows, decoder, p):
    import numpy as np
    from threadpoolctl import threadpool_limits
    predictions = []
    with threadpool_limits(limits=2):
        for s in night_samples(rows):
            event_score = evaluate_head(detector["event_head"], s["night_features"])
            probabilities = sequence_distribution(sequence_features(s, model), model["weights"])
            conditional_marginal = probabilities@mask_space()["masks"]
            mask = decode_distribution(probabilities, decoder, p) if event_score >= p["fixed_detector"]["threshold"] else [False]*14
            timing = timed_frost([-1. if v else 1. for v in mask], [r["valid_at_utc"] for r in s["rows"]])
            predictions.append({"date": s["date"], "cold_mask": mask, "cold_hour_model_scores": (event_score*conditional_marginal).tolist(),
                                "conditional_cold_hour_scores": conditional_marginal.tolist(), "night_event_model_score": event_score,
                                "conditional_expected_cold_bins_h": float(probabilities@mask_space()["count"]),
                                "posterior_diagnostics": {"expected_episode_count": float(probabilities@mask_space()["episodes"]),
                                                          "single_episode_mass": float(probabilities[mask_space()["episodes"] == 1].sum()),
                                                          "first_hour_cold_mass": float(conditional_marginal[0]),
                                                          "maximum_mask_mass": float(probabilities.max()),
                                                          "entropy_nats": float(-np.sum(probabilities*np.log(np.maximum(probabilities, 1e-300))))},
                                "frost": timing})
    return predictions


def score_episodes(rows, predictions):
    score = score_cold(rows, predictions)
    mean = lambda x: sum(x)/len(x) if x else None
    cold = [n for n in score["nights"] if n["observed"]["event"]]
    first = [abs(n["observed"]["first_cold_sample_index"]-n["predicted"]["first_cold_sample_index"]) if n["predicted"]["event"] else 14 for n in cold]
    score["cold_first_sample_error_with_miss_penalty_h"] = mean(first)
    score["episode_count_mae_cold"] = mean([abs(len(n["observed"]["episodes"])-len(n["predicted"]["episodes"])) for n in cold])
    score["longest_episode_mae_cold_h"] = mean([abs(n["observed"]["longest_cold_bins_hours"]-n["predicted"]["longest_cold_bins_hours"]) for n in cold])
    score["episode_selection_components"] = {
        "miss_rate": score["misses"]/score["cold_nights"], "false_alarm_rate": score["false_alarms"]/(score["complete_nights"]-score["cold_nights"]),
        "cold_duration_mae_over_14": score["cold_bin_duration_mae_cold_h"]/14, "warm_duration_mae_over_14": score["cold_bin_duration_mae_warm_h"]/14,
        "cold_mask_mismatch_over_14": score["cold_mask_mismatch_mae_cold_h"]/14,
        "cold_first_sample_error_with_miss_penalty_over_14": score["cold_first_sample_error_with_miss_penalty_h"]/14}
    score["episode_selection_loss"] = sum(SELECT_WEIGHTS[k]*v for k, v in score["episode_selection_components"].items())
    return score


def pool_episode_scores(scores):
    out = {k: sum(s[k] for s in scores) for k in ["complete_nights", "cold_nights", "hits", "misses", "false_alarms", "correct_negatives"]}
    cold_keys = ["cold_bin_duration_mae_cold_h", "cold_mask_mismatch_mae_cold_h", "cold_first_sample_error_with_miss_penalty_h"]
    for k in cold_keys:
        out[k] = sum(s[k]*s["cold_nights"] for s in scores)/out["cold_nights"]
    out["cold_bin_duration_mae_warm_h"] = sum(s["cold_bin_duration_mae_warm_h"]*(s["complete_nights"]-s["cold_nights"]) for s in scores)/(out["complete_nights"]-out["cold_nights"])
    out["episode_selection_components"] = {
        "miss_rate": out["misses"]/out["cold_nights"], "false_alarm_rate": out["false_alarms"]/(out["complete_nights"]-out["cold_nights"]),
        "cold_duration_mae_over_14": out["cold_bin_duration_mae_cold_h"]/14, "warm_duration_mae_over_14": out["cold_bin_duration_mae_warm_h"]/14,
        "cold_mask_mismatch_over_14": out["cold_mask_mismatch_mae_cold_h"]/14,
        "cold_first_sample_error_with_miss_penalty_over_14": out["cold_first_sample_error_with_miss_penalty_h"]/14}
    out["episode_selection_loss"] = sum(SELECT_WEIGHTS[k]*v for k, v in out["episode_selection_components"].items())
    return out


def assert_same_alerts(reference, predicted):
    by_date = {p["date"]: p for p in reference}
    if set(by_date) != {p["date"] for p in predicted} or len(by_date) != len(reference) or len(predicted) != len(reference):
        raise ValueError("Joint prediction date cohort mismatch")
    if any(any(p["cold_mask"]) != any(by_date[p["date"]]["cold_mask"]) for p in predicted):
        raise ValueError("Joint model changed the frozen detector alerts")


def validate_episodes(rows, p, model_protocol, detector_protocol):
    if any(r["year"] >= model_protocol["historical_check_year"] for r in rows):
        raise ValueError("2025 rows cannot enter joint selection")
    registry, folds = candidates(p), []
    for fold in model_protocol["folds"]:
        print(f"Episode selection: {fold['train_years']} -> {fold['validation_year']}", flush=True)
        train = [r for r in rows if r["year"] in fold["train_years"]]
        valid = [r for r in rows if r["year"] == fold["validation_year"]]
        detector = fit_cold(REFERENCE["family"], train, detector_protocol, model_protocol)
        reference = predict_cold(detector, valid, REFERENCE["threshold"])
        predictions = {"previous_detector_duration": reference,
                       "gfs": predict_cold(fit_cold("gfs", train, detector_protocol, model_protocol), valid, None)}
        diagnostics = {}
        for a in p["regularization"]:
            model = fit_joint(train, a, p, model_protocol)
            diagnostics[str(a)] = model["optimizer"]
            for d in p["decoders"]:
                name = f"joint_l2_{a:g}_{d}"
                predictions[name] = predict_joint(model, detector, valid, d, p)
                assert_same_alerts(reference, predictions[name])
        scores = {n: score_episodes(valid, ps) for n, ps in predictions.items()}
        folds.append({**fold, "scores": scores, "optimizer": diagnostics})
    pooled = {s["name"]: pool_episode_scores([f["scores"][s["name"]] for f in folds]) for s in registry}
    selected = min(registry, key=lambda s: (pooled[s["name"]]["episode_selection_loss"], registry.index(s)))
    return folds, selected, pooled


def validate_joint_model(model):
    if (model["schema_version"] != 1 or model["feature_names"] != FEATURE_NAMES or max(model["training_years"]) >= 2025
            or model["hour_feature_names"] != HOUR_NAMES or model["duration_feature_names"] != [NIGHT_NAMES[i] for i in DURATION_INDEXES]
            or model["regularization"] not in [1., 10.] or len(model["weights"]) != len(FEATURE_NAMES)
            or not all(math.isfinite(v) for v in model["weights"]) or not model["optimizer"]["converged"]):
        raise ValueError("Joint model schema/training mismatch")
    for key, length in [("hour_scaler", len(HOUR_NAMES)), ("duration_scaler", len(DURATION_INDEXES))]:
        sc = model[key]
        if len(sc["mean"]) != length or len(sc["scale"]) != length or not all(math.isfinite(v) for v in sc["mean"]) or not all(math.isfinite(v) and v > 0 for v in sc["scale"]):
            raise ValueError("Joint scaler schema/finite mismatch")
    return model


def select_episodes(protocol_path, data_dir):
    p = load_episode_protocol(protocol_path)
    m = load_protocol(ROOT / "config" / p["base_model_protocol"])
    dp = load_frost_protocol(ROOT / "config" / p["detector_protocol"])
    previous_dir = data_dir.parent / "models/research-frost"
    previous = json.loads((previous_dir / "selection-before-2025.json").read_text(encoding="utf-8"))
    if previous["selected_candidate"] != REFERENCE or previous["model_protocol"] != m or previous["protocol"] != dp:
        raise ValueError("Frozen previous detector mismatch; run frost-select with its original protocol")
    previous_model_path = previous_dir / REFERENCE["family"] / "model.json"
    if sha(previous_model_path) != previous["model_artifact_sha256"][REFERENCE["family"]]:
        raise ValueError("Frozen previous detector hash mismatch")
    years = sorted({y for f in m["folds"] for y in f["train_years"]+[f["validation_year"]]})
    rows, provenance = load_rows(data_dir, years)
    directory = data_dir.parent / "models/research-episodes"
    choice_path = directory / "selection-before-2025.json"
    old = json.loads(choice_path.read_text(encoding="utf-8")) if choice_path.exists() else None
    previous_sha = sha(previous_dir / "selection-before-2025.json")
    if old and (old["protocol_sha256"] != sha(protocol_path) or old["model_protocol"] != m or old["input_provenance"] != provenance
                or old["previous_selection_sha256"] != previous_sha):
        raise ValueError("Frozen episode protocol/input changed; no historical tuning")
    write_json(directory / "protocol-lock.json", {"protocol": p, "protocol_sha256": sha(protocol_path),
                                                "written_at_utc": datetime.now(UTC).isoformat(), "observations_loaded_years": years})
    folds, selected, pooled = validate_episodes(rows, p, m, dp)
    train = [r for r in rows if r["year"] in m["final_train_years"]]
    detector = fit_cold(REFERENCE["family"], train, dp, m)
    if detector != json.loads(previous_model_path.read_text(encoding="utf-8")):
        raise ValueError("Final detector differs from previous frozen model")
    models = {f"l2_{a:g}": fit_joint(train, a, p, m) for a in p["regularization"]}
    models["detector"] = detector
    content_hashes = {n: hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest() for n, model in models.items()}
    if old:
        if old["selected_candidate"] != selected or old["pooled_validation"] != pooled or old["model_content_sha256"] != content_hashes:
            raise ValueError("Frozen episode selection/weights changed; no historical tuning")
        return old
    for n, model in models.items(): write_json(directory / n / "model.json", model)
    selection = {"protocol": p, "protocol_sha256": sha(protocol_path), "model_protocol": m,
                 "selection_saved_at_utc": datetime.now(UTC).isoformat(), "observations_loaded_years": years,
                 "selected_candidate": selected, "folds": folds, "pooled_validation": pooled, "input_provenance": provenance,
                 "previous_selection_sha256": previous_sha, "model_content_sha256": content_hashes,
                 "model_artifact_sha256": {n: sha(directory / n / "model.json") for n in models},
                 "source_sha256": {f: sha(ROOT / "agro_checker" / f) for f in ["episode_study.py", "frost_study.py", "hourly_study.py", "single_run.py", "model_study.py"]},
                 "packages": {n: importlib.metadata.version(n) for n in ["numpy", "scipy", "scikit-learn", "threadpoolctl"]}}
    write_json(choice_path, selection)
    return selection


def prediction_registry(models, rows, p):
    detector = models["detector"]
    reference = predict_cold(detector, rows, REFERENCE["threshold"])
    predictions = {"previous_detector_duration": reference,
                   "gfs": predict_cold({"family": "gfs"}, rows, None)}
    for a in p["regularization"]:
        for d in p["decoders"]:
            name = f"joint_l2_{a:g}_{d}"
            predictions[name] = predict_joint(models[f"l2_{a:g}"], detector, rows, d, p)
            assert_same_alerts(reference, predictions[name])
    return predictions


def run_episode_study(protocol_path, data_dir, report_dir):
    selection = select_episodes(protocol_path, data_dir)
    directory = data_dir.parent / "models/research-episodes"
    models = {}
    for n, digest in selection["model_artifact_sha256"].items():
        path = directory / n / "model.json"
        if sha(path) != digest: raise ValueError("Frozen joint artifact hash mismatch")
        model = json.loads(path.read_text(encoding="utf-8"))
        models[n] = validate_saved_model(model) if n == "detector" else validate_joint_model(model)
    # Freeze and verify all weights BEFORE loading the previously viewed historical station file.
    year = selection["model_protocol"]["historical_check_year"]
    rows, provenance = load_rows(data_dir, [year])
    predictions = prediction_registry(models, rows, selection["protocol"])
    scores = {n: score_episodes(rows, ps) for n, ps in predictions.items()}
    name = selection["selected_candidate"]["name"]
    summary = {**selection, "generated_at_utc": datetime.now(UTC).isoformat(), "historical_input_provenance": provenance,
               "historical_scores": scores, "detector_alerts_preserved": True,
               "paired_timing_vs_gfs": compare_timing_cohorts(scores["gfs"], scores[name]),
               "paired_timing_vs_previous": compare_timing_cohorts(scores["previous_detector_duration"], scores[name]),
               "paired_uncertainty_vs_gfs": paired_uncertainty(scores["gfs"], scores[name], selection["model_protocol"]["bootstrap"]),
               "paired_uncertainty_vs_previous": paired_uncertainty(scores["previous_detector_duration"], scores[name], selection["model_protocol"]["bootstrap"]),
               "selection_artifact_sha256": sha(directory / "selection-before-2025.json"),
               "execution_source_sha256": {f: sha(ROOT / "agro_checker" / f) for f in selection["source_sha256"]},
               "training_cold_nights": models["detector"]["training_cold_nights"],
               "optimizer": {n: m["optimizer"] for n, m in models.items() if n != "detector"}}
    observed = {n["date"]: n for n in scores[name]["nights"]}
    summary["selected_posterior_diagnostics"] = [
        {"date": p["date"], "outcome": observed[p["date"]]["outcome"],
         "observed_episode_count": len(observed[p["date"]]["observed"]["episodes"]),
         "predicted_episode_count": len(observed[p["date"]]["predicted"]["episodes"]),
         "conditional_expected_cold_bins_h": p.get("conditional_expected_cold_bins_h"), **p.get("posterior_diagnostics", {})}
        for p in predictions[name] if p["date"] in observed and (observed[p["date"]]["observed"]["event"] or any(p["cold_mask"]))]
    summary["uniform_nonempty_mask_prior"] = {
        "mean_cold_bins_h": float(mask_space()["count"].mean()), "mean_episode_count": float(mask_space()["episodes"].mean()),
        "first_hour_cold_mass": float(mask_space()["masks"][:, 0].mean())}
    all_rows, _ = load_rows(data_dir, [2022, 2023, 2024, 2025])
    reference = predict_cold(models["detector"], all_rows, REFERENCE["threshold"])
    chosen = selection["selected_candidate"]
    if chosen["kind"] == "reference": selected = reference
    else: selected = predict_joint(models[f"l2_{chosen['regularization']:g}"], models["detector"], all_rows, chosen["decoder"], selection["protocol"])
    assert_same_alerts(reference, selected)
    by_date, old_by_date = {n["date"]: n for n in selected}, {n["date"]: n for n in reference}
    snapshot, flat = [], []
    for s in night_samples(all_rows):
        p, old = by_date[s["date"]], old_by_date[s["date"]]
        role = "historical_check_already_viewed" if s["year"] == year else "model_selection_replay" if s["year"] == 2024 else "in_sample_training_replay"
        forecast, truth = [], []
        for i, r in enumerate(s["rows"]):
            f = {"valid_at_utc": r["valid_at_utc"], "gfs_temperature_c": r["base"]["temperature"], "gfs_cold": r["base"]["temperature"] < 0,
                 "previous_cold": old["cold_mask"][i], "selected_cold": p["cold_mask"][i], "cold_hour_model_score": p["cold_hour_model_scores"][i]}
            forecast.append(f)
            truth.append({"valid_at_utc": r["valid_at_utc"], "observed_temperature_c": r["target"]["temperature"], "temperature_source": r["source"]["temperature"]})
            flat.append({"night_date_local": s["date"], "evaluation_status": role, "issued_at_utc": r["issued_at_utc"],
                         "forecast_run_at_utc": r["forecast_run_at_utc"], **f, **{k: v for k, v in truth[-1].items() if k != "valid_at_utc"}})
        stamps = [r["valid_at_utc"] for r in s["rows"]]
        snapshot.append({"date": s["date"], "evaluation_status": role, "selected_candidate": chosen,
                         "issued_at_utc": s["rows"][0]["issued_at_utc"], "forecast_run_at_utc": s["rows"][0]["forecast_run_at_utc"],
                         "night_event_model_score": old["night_event_model_score"],
                         "gfs_frost": timed_frost(s["curve"], stamps), "previous_frost": old["frost"], "selected_frost": p["frost"],
                         "conditional_expected_cold_bins_h": p.get("conditional_expected_cold_bins_h"),
                         "forecast": forecast, "hindsight_observations": truth,
                         "hindsight_frost": timed_frost([r["target"]["temperature"] for r in s["rows"]], stamps)})
    processed = data_dir / "processed/episode-study"
    write_csv(processed / "forecasts.csv", flat)
    write_json(processed / "forecast-snapshot.json", {"selected_candidate": chosen, "nights": snapshot})
    write_json(processed / "forecast-snapshot.json.meta.json", {"sha256": sha(processed / "forecast-snapshot.json")})
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "EPISODE_STUDY_SUMMARY.json", summary)
    plot_episodes(summary, snapshot, report_dir)
    (report_dir / "EPISODE_STUDY_REPORT.md").write_text(report_episodes(summary), encoding="utf-8")
    return summary


def replay_episodes(data_dir, night):
    date.fromisoformat(night)
    path = data_dir / "processed/episode-study/forecast-snapshot.json"
    meta = json.loads(path.with_name(path.name+".meta.json").read_text(encoding="utf-8"))
    if sha(path) != meta["sha256"]: raise ValueError("Episode snapshot hash mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    n = next((n for n in payload["nights"] if n["date"] == night), None)
    if n is None: raise ValueError("Night not found")
    return {**n, "night_date_local": night, "availability_evidence": "archive_timestamp_proxy; first publication unknown", "forecast_available_at_utc": None,
            "target": "station 2m T<0, not flower damage", "time_resolution": "hourly bins 19–09 MSK; edge censoring; not continuous duration",
            "model_score_status": "conditional structured scores and fixed detector scores are uncalibrated"}


def report_episodes(s):
    winner = s["selected_candidate"]["name"]
    f = lambda v: "—" if v is None else f"{v:.3f}"
    base, old, selected = [s["historical_scores"][n] for n in ["gfs", "previous_detector_duration", winner]]
    lines = ["# Совместная модель холодных эпизодов", "", f"Расчёт {s['generated_at_utc']}. Краснодар 34929, станционная T<0 °C, 14 часов 19–08 МСК.", "",
             f"Выбран **{winner}** по 2023–2024; финальное обучение 2022–2023, {s['training_cold_nights']} холодных ночей. 2025 ранее просмотрен и используется только как исторический контроль, не слепой тест.", "",
             "## Что совместно и что закреплено", "",
             "Ночной детектор `event_duration_weather@0.25` сохранён из предыдущего эксперимента. Его коэффициенты и порог проверены по SHA и повторному fit, его список тревог сохраняется точно. Новая модель совместно учит расположение холодных часов, длительность, число эпизодов и границы при условии, что ночь холодная. Это условная совместная модель формы эпизодов, а не заново обученное обнаружение события.", "",
             "Линейная CRF нормирует оценку по всем 2^14−1=16383 непустым маскам. Признаки: суммы погоды по холодным часам, холодная доля и квадрат, число эпизодов/переходов, первый/последний холодный час, отрицательные края окна, суммы квадратов длин эпизодов и взаимодействия квадрата длительности с прогнозом ночи. Разрывы допустимы; ни один физический эпизод не принудительно соединяется через тёплый час. 28 совместных коэффициентов, scaler только обучения, без календаря или фактических измерений во входах.", "",
             "Обучение — точная условная log-likelihood полной маски на фактически холодных обучающих ночах + L2. Все 14 пригодных температур обязательны. Ночные ошибки классификатора не меняют набор обучения формы: истинные холодные ночи используются целиком. У тёплых ночей входные прогнозы участвуют только в обучающем scaler.", "",
             "Четыре фиксированных варианта: L2=1/10 × MAP/минимум ожидаемой ошибки. MAP выбирает наиболее вероятную маску. Второй decoder минимизирует сумму ожидаемых ошибок: доля несовпавших часов + |длительность−факт|/14 + |первый холодный час−факт|/13 + 0,25·|число эпизодов−факт|/7. Считается точно по распределению всех масок. Для ночей без тревоги возвращается пустая маска, для ночей с тревогой — непустая. Состояния не превращаются в искусственные температуры; модельные оценки не откалиброваны как вероятности.", "",
             "## Как выбиралась модель", "",
             "Фолды 2022→2023 и 2022–2023→2024. Реестр: четыре совместных варианта и прежний метод как fallback; GFS отдельно как база. L = 0,25·miss-rate + 0,15·false-alarm-rate + 0,20·MAE длительности холодных/14 + 0,10·MAE длительности тёплых/14 + 0,15·несовпадение маски холодных/14 + 0,15·ошибка первого часа холодных/14. Для пропуска первого часа штраф 14; это заранее заданная исследовательская цена пропуска, не измеренная ошибка начала. Классы объединяются по своим знаменателям, не средним процентов сезонов. У совместных моделей список тревог совпадает с прежним, поэтому различия L определяются формой эпизода.", "",
             "`episode-select` читает наблюдения только 2022–2024, сохраняет выбор и модели до чтения 2025. `episode-study` проверяет frozen выбор, коэффициенты и SHA перед исторической оценкой. Нет подбора порога/регуляризации/decoder по 2025, early stopping по нему или повторного fit на 2024. Семь холодных ночей в выборе и 21 в обучении ограничивают устойчивость.", "",
             "| Вариант | L ↓ | Пропуски/7 | Ложные | MAE холодной длительности ч | Несовпавших часов холодной маски | Первый час с штрафом за пропуск ч |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for n, a in s["pooled_validation"].items():
        lines.append(f"| {n} | {f(a['episode_selection_loss'])} | {a['misses']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['cold_mask_mismatch_mae_cold_h'])} | {f(a['cold_first_sample_error_with_miss_penalty_h'])} |")
    lines += ["", "| Сезон | Метод | Пропуски/холодных | Ложные | MAE холодной длительности ч | Первый час MAE попаданий ч (n) |", "|---|---|---|---:|---:|---|"]
    for fold in s["folds"]:
        for n in ["gfs", "previous_detector_duration", winner]:
            a = fold["scores"][n]
            lines.append(f"| {fold['validation_year']} | {n} | {a['misses']}/{a['cold_nights']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['first_cold_sample_mae_hits_h'])} ({a['first_sample_pairs']}) |")
    lines += ["", "## Исторический 2025", "", "| Вариант | Пропуски/6 | Ложные | MAE длительности все / холодные ч | Первый час MAE попаданий ч (n) | Несовпавших часов холодной маски | Recall холодных часов |", "|---|---:|---:|---|---|---:|---:|"]
    for n, a in s["historical_scores"].items():
        lines.append(f"| {n} | {a['misses']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_all_h'])} / {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['first_cold_sample_mae_hits_h'])} ({a['first_sample_pairs']}) | {f(a['cold_mask_mismatch_mae_cold_h'])} | {f(a['cold_hour_recall'])} |")
    lines += ["", "Ошибка первого холодного отсчёта только на одинаковых попаданиях; границы окна показаны отдельно:", "", "| База | Определение | Пар | MAE базы ч | MAE выбранного ч |", "|---|---|---:|---:|---:|"]
    for key, label in [("paired_timing_vs_gfs", "GFS"), ("paired_timing_vs_previous", "Прежний детектор+длительность")]:
        for kind, text in [("first_sample_common_hits", "Первый холодный отсчёт"), ("onset_common_interior_hits", "Без границы начала у обоих")]:
            a = s[key][kind]
            lines.append(f"| {label} | {text} | {a['paired_nights']} | {f(a['baseline_mae_h'])} | {f(a['corrected_mae_h'])} |")
    lines += ["", "Парный bootstrap MAE длительности на фактически холодных ночах, 5000 повторов календарных блоков. Тёплые блоки сохраняются, повторения без холодных ночей исключены и посчитаны; положительная разность означает выигрыш выбранного метода. Один сезон/шесть событий и выбор по семи событиям не доказывают переносимость.", "", "| База | Блок дней | Выигрыш ч | 95% диапазон ч | Повторов без холодных ночей |", "|---|---:|---:|---|---:|"]
    for key, label in [("paired_uncertainty_vs_gfs", "GFS"), ("paired_uncertainty_vs_previous", "Прежний метод")]:
        for width, a in s[key]["duration_block_bootstrap"].items():
            lines.append(f"| {label} | {width} | {f(a['cold_duration_mae_improvement_h'])} | [{f(a['interval_95'][0])}, {f(a['interval_95'][1])}] | {a['replicates_without_cold_nights']} |")
    stamp = lambda v: (datetime.fromisoformat(v)+timedelta(hours=3)).strftime("%d.%m %H:%M") if v else "—"
    old_dates = {n["date"]: n for n in old["nights"]}; base_dates = {n["date"]: n for n in base["nights"]}
    lines += ["", "| Холодная ночь или тревога | Исход | Часов факт / GFS / прежний / выбранный | Первый холодный час факт / выбранный МСК | Эпизодов факт / выбранный |", "|---|---|---|---|---|"]
    for n in selected["nights"]:
        if n["observed"]["event"] or n["predicted"]["event"]:
            a, p = n["observed"], n["predicted"]
            lines.append(f"| {n['date']} | {n['outcome']} | {a['cold_bins_hours']} / {base_dates[n['date']]['predicted']['cold_bins_hours']} / {old_dates[n['date']]['predicted']['cold_bins_hours']} / {p['cold_bins_hours']} | {stamp(a['first_cold_sample_at_utc'])} / {stamp(p['first_cold_sample_at_utc'])} | {len(a['episodes'])} / {len(p['episodes'])} |")
    lines += ["", "## Диагностика совместного распределения", "",
              "Множество допустимых масок включает много раздробленных последовательностей. При нулевых коэффициентах модель задаёт равномерный закон по маскам, не по эпизодам: "
              f"средняя длительность {f(s['uniform_nonempty_mask_prior']['mean_cold_bins_h'])} ч, "
              f"число эпизодов {f(s['uniform_nonempty_mask_prior']['mean_episode_count'])}, "
              f"масса холода в первом часе {f(s['uniform_nonempty_mask_prior']['first_hour_cold_mass'])}. "
              "Регуляризация тянет коэффициенты к этому закону. Поэтому доступность всех разрывов сама по себе не означает правильный prior формы эпизода; совместный decoder тоже может выбирать разрозненные ранние часы.", "",
              "| Рискованная ночь | Эпизодов факт / выбранный | Ожидаемых эпизодов | Масса одного эпизода | Максимальная масса маски | Масса холода в первом часе |",
              "|---|---|---:|---:|---:|---:|"]
    for n in s["selected_posterior_diagnostics"]:
        lines.append(f"| {n['date']} | {n['observed_episode_count']} / {n['predicted_episode_count']} | {f(n.get('expected_episode_count'))} | {f(n.get('single_episode_mass'))} | {f(n.get('maximum_mask_mass'))} | {f(n.get('first_hour_cold_mass'))} |")
    lines += ["", "Таблица — диагностика фиксированной модели после исторической проверки, не новый подбор. "
              "Данных для совместной формы мало: 21 холодная обучающая ночь и семь в выборе. "
              "Сравнение decoder показывает разные компромиссы числа эпизодов/длительности/первого часа; "
              "по 2025 на другой вариант не переключались. Для следующего независимого эксперимента нужно заранее обосновать prior формы эпизодов и масштаб признаков при регуляризации."]
    previous_pair = s["paired_timing_vs_previous"]["first_sample_common_hits"]
    gfs_pair = s["paired_timing_vs_gfs"]["first_sample_common_hits"]
    lines += ["", "## Вывод", "",
              f"Обнаружение сохранено механически: у прежнего и выбранного метода {selected['misses']} пропусков и {selected['false_alarms']} ложных тревог, у GFS {base['misses']} и {base['false_alarms']}. "
              f"MAE длительности холодных ночей: GFS {f(base['cold_bin_duration_mae_cold_h'])}, прежний {f(old['cold_bin_duration_mae_cold_h'])}, выбранный {f(selected['cold_bin_duration_mae_cold_h'])} ч. "
              f"Первый холодный час на {previous_pair['paired_nights']} общих попаданиях прежний→выбранный {f(previous_pair['baseline_mae_h'])}→{f(previous_pair['corrected_mae_h'])} ч; "
              f"на {gfs_pair['paired_nights']} общих попаданиях GFS→выбранный {f(gfs_pair['baseline_mae_h'])}→{f(gfs_pair['corrected_mae_h'])} ч. "
              "Качество расписания и обнаружение нужно оценивать отдельно, включая ложные тревоги. Демонстрация не заменена.", "",
              "![Холодные эпизоды](assets/episode-timing.png)", "", "## Воспроизведение и границы", "",
              "```powershell", ".\\.venv\\Scripts\\python.exe -m agro_checker episode-select --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker episode-study --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker episode-replay --night 2025-03-07", "```", "",
              "Тот же исходный выпуск GFS 06 UTC, gate Last-Modified+10 минут к 18 МСК, nearest-point и источники ISD-lite/METAR. Первая публикация неизвестна. Предсказывается T воздуха на станции 2 м, не температура цветков/урожай. Начало — первый отрицательный часовой отсчёт; длительность — число холодных часовых интервалов в [19,09), не непрерывное измеренное время. При отрицательном первом/последнем отсчёте начало/конец за окном неизвестны; интерполяции и минутной точности нет.", "",
              "Модели/выбор — `models/research-episodes/`; все 368 выдач/CSV/SHA snapshot — `data/processed/episode-study/`. Replay отделяет прогноз от последующего факта, маркирует сезон и проверяет SHA. Неполные цели исключены из fit/score, но не мешают прогнозу. Старые исследовательские артефакты и UI сохранены. [Численная сводка и происхождение](EPISODE_STUDY_SUMMARY.json).", ""]
    return "\n".join(lines)


def plot_episodes(summary, snapshot, report_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    nights = {n["date"]: n for n in snapshot}
    examples = summary["protocol"]["report_examples"]
    fig, axes = plt.subplots(len(examples), 1, figsize=(12, 10))
    for ax, d in zip(axes, examples):
        n = nights[d]
        masks = [[r["observed_temperature_c"] < 0 for r in n["hindsight_observations"]],
                 [r["gfs_cold"] for r in n["forecast"]], [r["previous_cold"] for r in n["forecast"]], [r["selected_cold"] for r in n["forecast"]]]
        ax.imshow(np.array(masks), cmap="Blues", vmin=0, vmax=1, aspect="auto", interpolation="nearest")
        ax.set_yticks(range(4), ["Observed", "GFS", "Previous", "Selected episode model"])
        ax.set_xticks(range(14), [(19+i)%24 for i in range(14)])
        ax.set_title(d+" · cold bins "+" / ".join(str(sum(m)) for m in masks), fontsize=10)
        ax.set_xlabel("Hour MSK")
    fig.suptitle("Selected on 2023–2024: "+summary["selected_candidate"]["name"]+"\n2025 historical examples · frozen detector · hourly bins, not continuous duration", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, .93))
    assets = report_dir / "assets"; assets.mkdir(parents=True, exist_ok=True)
    fig.savefig(assets / "episode-timing.png", dpi=160); plt.close(fig)
