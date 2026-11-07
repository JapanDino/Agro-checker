"""Season-forward cold-state models, selected before reading historical 2025 targets."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import hashlib
import importlib.metadata
import json
import math

from .gfs_covariates import load_covariates
from .humidity_archive import load_humidity
from .hourly_study import build_hourly_rows, compare_timing_cohorts, fit_hourly, frost_score, predict_hourly, timed_frost
from .model_study import load_protocol
from .pipeline import ROOT, UTC, cached_download, parse_observations, write_csv, write_json
from .single_run import build_tables, load_points, load_single_config

FAMILIES = ["gfs", "cold_hour_logistic", "event_duration_weather"]
HOUR_NAMES = ["temperature_c", "night_min_c", "night_range_c", "hour_change_c", "dewpoint_c", "dewpoint_spread_c",
              "rh_pct", "wind_ms", "cloud_pct", "hour_index_over_13"]
NIGHT_NAMES = ["min_c", "max_c", "mean_c", "range_c", "evening_c", "morning_c", "change_c", "min_index",
               "gfs_cold_bins", "gfs_near_zero_bins", "dewpoint_mean_c", "wind_mean_ms", "cloud_mean_pct", "rh_mean_pct"]
DURATION_INDEXES = [0, 3, 6, 7, 8, 10, 11, 12]
LOSS_WEIGHTS = {"miss_rate": .25, "false_alarm_rate": .25, "cold_duration_mae_over_14": .25, "warm_duration_mae_over_14": .25}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frost_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if (p["schema_version"] != 1 or p["families"] != FAMILIES or p["decision_thresholds"] != [.25, .5, .75]
            or p["selection"] != "pooled_balanced_event_duration_loss" or p["loss_weights"] != LOSS_WEIGHTS
            or p["weather_threshold_c"] != 0 or p["logistic_c"] != 1 or p["duration_ridge_alpha"] != 10
            or p["source_config"] != "single_run.json" or p["base_model_protocol"] != "model_study.json"):
        raise ValueError("Unexpected fixed frost protocol")
    return p


def candidate_specs(protocol):
    return [{"name": "gfs", "family": "gfs", "threshold": None}] + [
        {"name": f"{f}@{t:g}", "family": f, "threshold": t}
        for f in protocol["families"][1:] for t in protocol["decision_thresholds"]]


def load_rows(data_dir, years):
    """Load observation files ONLY for the requested years. Selection never opens 2025 observations."""
    config = load_single_config(ROOT / "config/single_run.json")
    if not years or any(y not in config["years"] for y in years):
        raise ValueError("Unsupported source years")
    points, failures, _, temp_meta = load_points(data_dir, config)
    cov, cov_failures, _, cov_meta = load_covariates(data_dir, config)
    rh, rh_failures, _, rh_meta = load_humidity(data_dir, config)
    if failures or cov_failures or rh_failures:
        raise ValueError("Resolve archive acquisition failures first")
    observations, provenance = {}, {}
    for y in years:
        content, meta = cached_download(f"https://data.meteostat.net/hourly/{y}/{config['station_id']}.csv.gz",
                                       data_dir / "raw" / f"meteostat-{config['station_id']}-{y}.csv.gz", True)
        observations.update(parse_observations(content, config["observation_sources"]))
        provenance[str(y)] = meta
    subset = {**config, "years": years}
    hourly, _ = build_tables(subset, [r for r in points if int(r["night_date_local"][:4]) in years], observations)
    cov = [r for r in cov if int(r["night_date_local"][:4]) in years]
    rh = [r for r in rh if int(r["night_date_local"][:4]) in years]
    rows = build_hourly_rows(hourly, cov, rh, {}, config)
    return rows, {"temperature": temp_meta, "covariates": cov_meta, "humidity": rh_meta, "observations": provenance}


def night_samples(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r["date"]].append(r)
    result = []
    for evening, hours in sorted(groups.items()):
        hours = sorted(hours, key=lambda r: r["hour_index"])
        if len(hours) != 14 or [r["hour_index"] for r in hours] != list(range(14)):
            raise ValueError("Cold models require 14 ordered forecast hours")
        curve = [r["base"]["temperature"] for r in hours]
        if any(not math.isfinite(t) for t in curve):
            raise ValueError("Nonfinite forecast")
        weather = [r["features_weather"] for r in hours]
        hour_x = [[w[0], w[7], w[9], w[6], w[2], w[3], w[1], w[4], w[5], i / 13] for i, w in enumerate(weather)]
        means = lambda i: sum(w[i] for w in weather) / 14
        x = [min(curve), max(curve), sum(curve)/14, max(curve)-min(curve), curve[0], curve[-1], curve[-1]-curve[0],
             float(curve.index(min(curve))), float(sum(t < 0 for t in curve)), float(sum(abs(t) <= 2 for t in curve)),
             means(2), means(4), means(5), means(1)]
        target = [r["target"]["temperature"] for r in hours]
        complete = all(t is not None and math.isfinite(t) for t in target)
        result.append({"date": evening, "year": int(evening[:4]), "rows": hours, "curve": curve,
                       "hour_features": hour_x, "night_features": x, "duration_features": [x[i] for i in DURATION_INDEXES],
                       "complete": complete, "cold_mask": [t < 0 for t in target] if complete else None,
                       "duration": sum(t < 0 for t in target) if complete else None})
    return result


def linear_head(x, y, kind, protocol):
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler().fit(x)
    if kind == "logistic":
        if len(set(y)) != 2:
            raise ValueError("Cold classifier training needs both classes")
        estimator = LogisticRegression(C=protocol["logistic_c"], solver="lbfgs", max_iter=2000)
    else:
        estimator = Ridge(alpha=protocol["duration_ridge_alpha"], solver="svd")
    estimator.fit(scaler.transform(x), y)
    coefficients = estimator.coef_.reshape(-1).tolist()
    intercept = float(estimator.intercept_[0]) if kind == "logistic" else float(estimator.intercept_)
    return {"kind": kind, "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
            "coefficients": coefficients, "intercept": intercept}


def evaluate_head(head, features):
    if len(features) != len(head["coefficients"]):
        raise ValueError("Cold feature schema length mismatch")
    if any(not math.isfinite(v) for v in features):
        raise ValueError("Nonfinite cold feature")
    v = head["intercept"] + sum((x-m)/s*c for x, m, s, c in zip(features, head["scaler_mean"], head["scaler_scale"], head["coefficients"]))
    return 1/(1+math.exp(-v)) if head["kind"] == "logistic" and v >= 0 else math.exp(v)/(1+math.exp(v)) if head["kind"] == "logistic" else v


def fit_cold(family, rows, protocol, model_protocol):
    if not rows or any(r["year"] >= model_protocol["historical_check_year"] for r in rows):
        raise ValueError("2025 targets cannot enter frost fit")
    samples = [s for s in night_samples(rows) if s["complete"]]
    if not samples:
        raise ValueError("No complete training nights")
    model = {"schema_version": 1, "family": family, "training_years": sorted({s["year"] for s in samples}),
             "training_nights": len(samples), "training_cold_nights": sum(s["duration"] > 0 for s in samples),
             "training_cold_hours": sum(s["duration"] for s in samples), "last_training_night": samples[-1]["date"],
             "feature_schema": {"hour": HOUR_NAMES, "night": NIGHT_NAMES, "duration": [NIGHT_NAMES[i] for i in DURATION_INDEXES]}}
    if family == "gfs":
        return model
    if family not in FAMILIES:
        raise ValueError("Unknown cold model")
    model["hour_head"] = linear_head([x for s in samples for x in s["hour_features"]],
                                     [int(v) for s in samples for v in s["cold_mask"]], "logistic", protocol)
    if family == "event_duration_weather":
        model["event_head"] = linear_head([s["night_features"] for s in samples], [int(s["duration"] > 0) for s in samples], "logistic", protocol)
        cold = [s for s in samples if s["duration"] > 0]
        model["duration_head"] = linear_head([s["duration_features"] for s in cold], [s["duration"] for s in cold], "ridge", protocol)
    return model


def predict_cold(model, rows, threshold):
    result = []
    for s in night_samples(rows):
        if model["family"] == "gfs":
            mask = [t < 0 for t in s["curve"]]
            p, event_p, duration_raw = None, None, None
        else:
            if threshold is None or not 0 < threshold < 1:
                raise ValueError("Missing cold decision threshold")
            p = [evaluate_head(model["hour_head"], x) for x in s["hour_features"]]
            if model["family"] == "cold_hour_logistic":
                mask = [v >= threshold for v in p]
                event_p, duration_raw = None, None
            else:
                event_p = evaluate_head(model["event_head"], s["night_features"])
                duration_raw = evaluate_head(model["duration_head"], s["duration_features"])
                k = max(1, min(14, math.floor(duration_raw+.5))) if event_p >= threshold else 0
                # Rank cold hours; ties prefer the earlier hour. Never bridge separate episodes.
                chosen = set(sorted(range(14), key=lambda i: (-p[i], i))[:k])
                mask = [i in chosen for i in range(14)]
        stamps = [r["valid_at_utc"] for r in s["rows"]]
        # Indicator encoding is INTERNAL ONLY: no fabricated temperature is returned or scored.
        timing = timed_frost([-1. if c else 1. for c in mask], stamps)
        result.append({"date": s["date"], "cold_mask": mask, "cold_hour_model_scores": p, "night_event_model_score": event_p,
                       "conditional_duration_raw_h": duration_raw, "frost": timing})
    return result


def score_cold(rows, predictions):
    samples = night_samples(rows)
    by_date = {p["date"]: p for p in predictions}
    if len(by_date) != len(predictions) or set(by_date) != {s["date"] for s in samples}:
        raise ValueError("Cold prediction dates mismatch")
    ordered_rows, indicators = [], []
    for s in samples:
        p = by_date[s["date"]]
        if len(p["cold_mask"]) != 14 or any(type(c) is not bool for c in p["cold_mask"]):
            raise ValueError("Expected boolean cold mask")
        ordered_rows.extend(s["rows"])
        indicators.extend(-1. if c else 1. for c in p["cold_mask"])
    score = frost_score(ordered_rows, indicators)
    del score["night_min_mae_c"]
    for n in score["nights"]:
        del n["predicted_min_c"]
    warm_errors, hours = [], Counter(hits=0, misses=0, false_alarms=0, correct_negatives=0)
    hour_brier, night_brier, cold_mismatch, warm_mismatch = [], [], [], []
    for s in samples:
        if not s["complete"]:
            continue
        p = by_date[s["date"]]
        for a, b in zip(s["cold_mask"], p["cold_mask"]):
            hours["hits" if a and b else "misses" if a else "false_alarms" if b else "correct_negatives"] += 1
        mismatch = sum(a != b for a, b in zip(s["cold_mask"], p["cold_mask"]))
        (cold_mismatch if s["duration"] > 0 else warm_mismatch).append(mismatch)
        if s["duration"] == 0:
            warm_errors.append(sum(p["cold_mask"]))
        if p["cold_hour_model_scores"] is not None:
            hour_brier.extend((v-int(a))**2 for v, a in zip(p["cold_hour_model_scores"], s["cold_mask"]))
        if p["night_event_model_score"] is not None:
            night_brier.append((p["night_event_model_score"]-int(s["duration"] > 0))**2)
    mean = lambda x: sum(x)/len(x) if x else None
    score.update(cold_bin_duration_mae_warm_h=mean(warm_errors), hourly_confusion=dict(hours),
                 cold_hour_recall=hours["hits"]/(hours["hits"]+hours["misses"]) if hours["hits"]+hours["misses"] else None,
                 cold_mask_mismatch_mae_cold_h=mean(cold_mismatch), cold_mask_mismatch_mae_warm_h=mean(warm_mismatch),
                 cold_hour_brier=mean(hour_brier), night_event_brier=mean(night_brier))
    score.update(selection_components=loss_components(score))
    score["selection_loss"] = sum(LOSS_WEIGHTS[k]*v for k, v in score["selection_components"].items())
    return score


def loss_components(score):
    warm = score["complete_nights"]-score["cold_nights"]
    if not score["cold_nights"] or not warm:
        raise ValueError("Balanced score needs both cold and warm nights")
    return {"miss_rate": score["misses"]/score["cold_nights"], "false_alarm_rate": score["false_alarms"]/warm,
            "cold_duration_mae_over_14": score["cold_bin_duration_mae_cold_h"]/14,
            "warm_duration_mae_over_14": score["cold_bin_duration_mae_warm_h"]/14}


def pool_scores(scores):
    combined = {k: sum(s[k] for s in scores) for k in ["complete_nights", "cold_nights", "hits", "misses", "false_alarms", "correct_negatives"]}
    for key, counts in [("cold_bin_duration_mae_cold_h", [s["cold_nights"] for s in scores]),
                        ("cold_bin_duration_mae_warm_h", [s["complete_nights"]-s["cold_nights"] for s in scores])]:
        combined[key] = sum(s[key]*n for s, n in zip(scores, counts))/sum(counts)
    combined["selection_components"] = loss_components(combined)
    combined["selection_loss"] = sum(LOSS_WEIGHTS[k]*v for k, v in combined["selection_components"].items())
    return combined


def validate_cold(rows, protocol, model_protocol):
    folds = []
    # The function accepts only development years, rather than silently dropping historical targets.
    if any(r["year"] >= model_protocol["historical_check_year"] for r in rows):
        raise ValueError("2025 rows cannot enter frost selection")
    specs = candidate_specs(protocol)
    for fold in model_protocol["folds"]:
        print(f"Frost selection: {fold['train_years']} -> {fold['validation_year']}", flush=True)
        train = [r for r in rows if r["year"] in fold["train_years"]]
        valid = [r for r in rows if r["year"] == fold["validation_year"]]
        fitted = {f: fit_cold(f, train, protocol, model_protocol) for f in protocol["families"]}
        scores = {s["name"]: score_cold(valid, predict_cold(fitted[s["family"]], valid, s["threshold"])) for s in specs}
        ridge = fit_hourly("ridge_temperature", "temperature", train, model_protocol)
        ridge_values = predict_hourly(ridge, valid)
        scores["hourly_ridge_reference"] = score_cold(valid, temperature_masks(valid, ridge_values))
        folds.append({**fold, "scores": scores, "training_cold_nights": fitted["gfs"]["training_cold_nights"]})
    pooled = {s["name"]: pool_scores([f["scores"][s["name"]] for f in folds]) for s in specs}
    # GFS can win. No forced promotion of a newly fitted model.
    winner = min(specs, key=lambda s: (pooled[s["name"]]["selection_loss"], specs.index(s)))
    return folds, winner, pooled


def temperature_masks(rows, values):
    if len(rows) != len(values):
        raise ValueError("Temperature length mismatch")
    grouped = defaultdict(list)
    for r, v in zip(rows, values):
        grouped[r["date"]].append((r["hour_index"], v))
    return [{"date": d, "cold_mask": [v < 0 for _, v in sorted(items)], "cold_hour_model_scores": None, "night_event_model_score": None}
            for d, items in sorted(grouped.items())]


def validate_saved_model(model):
    if (model["schema_version"] != 1 or model["family"] not in FAMILIES or max(model["training_years"]) >= 2025
            or model["feature_schema"] != {"hour": HOUR_NAMES, "night": NIGHT_NAMES, "duration": [NIGHT_NAMES[i] for i in DURATION_INDEXES]}):
        raise ValueError("Saved frost model schema/training mismatch")
    expected_heads = set() if model["family"] == "gfs" else {"hour_head"} if model["family"] == "cold_hour_logistic" else {"hour_head", "event_head", "duration_head"}
    if {k for k in model if k.endswith("_head")} != expected_heads:
        raise ValueError("Missing or unexpected saved frost heads")
    for key, names in [("hour_head", HOUR_NAMES), ("event_head", NIGHT_NAMES), ("duration_head", DURATION_INDEXES)]:
        if key not in model:
            continue
        head = model[key]
        if head["kind"] != ("ridge" if key == "duration_head" else "logistic") or not math.isfinite(head["intercept"]):
            raise ValueError("Invalid saved frost head kind/intercept")
        if any(len(head[k]) != len(names) for k in ["scaler_mean", "scaler_scale", "coefficients"]):
            raise ValueError("Saved frost coefficient schema mismatch")
        if any(not math.isfinite(v) for k in ["scaler_mean", "scaler_scale", "coefficients"] for v in head[k]) or any(s <= 0 for s in head["scaler_scale"]):
            raise ValueError("Invalid saved frost coefficients")
    return model


def select_frost(protocol_path, data_dir):
    protocol = load_frost_protocol(protocol_path)
    model_protocol = load_protocol(ROOT / "config" / protocol["base_model_protocol"])
    years = sorted({y for f in model_protocol["folds"] for y in f["train_years"]+[f["validation_year"]]})
    rows, provenance = load_rows(data_dir, years)
    directory = data_dir.parent / "models/research-frost"
    protocol_sha = sha(protocol_path)
    lock_path = directory / "selection-before-2025.json"
    old = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else None
    if old and old["protocol_sha256"] != protocol_sha:
        raise ValueError("Frozen frost protocol changed; no historical tuning")
    if old and (old["input_provenance"] != provenance or old["model_protocol"] != model_protocol):
        raise ValueError("Frozen frost source inputs or seasonal protocol changed")
    write_json(directory / "protocol-lock.json", {"protocol": protocol, "protocol_sha256": protocol_sha,
                                                "written_at_utc": datetime.now(UTC).isoformat(), "observations_loaded_years": years})
    folds, selected, pooled = validate_cold(rows, protocol, model_protocol)
    train = [r for r in rows if r["year"] in model_protocol["final_train_years"]]
    models = {f: fit_cold(f, train, protocol, model_protocol) for f in protocol["families"]}
    hashes = {f: hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest() for f, m in models.items()}
    if old:
        if old["selected_candidate"] != selected or old["pooled_validation"] != pooled or old["model_content_sha256"] != hashes:
            raise ValueError("Frozen frost selection/model changed; no historical tuning")
        return old
    for f, model in models.items():
        write_json(directory / f / "model.json", model)
    selection = {"protocol": protocol, "protocol_sha256": protocol_sha, "model_protocol": model_protocol,
                 "source_config": load_single_config(ROOT / "config/single_run.json"),
                 "selection_saved_at_utc": datetime.now(UTC).isoformat(), "observations_loaded_years": years,
                 "selected_candidate": selected, "folds": folds, "pooled_validation": pooled,
                 "model_content_sha256": hashes, "model_artifact_sha256": {f: sha(directory / f / "model.json") for f in models},
                 "input_provenance": provenance,
                 "source_sha256": {f: sha(ROOT / "agro_checker" / f) for f in ["frost_study.py", "hourly_study.py", "single_run.py", "model_study.py", "gfs_covariates.py", "humidity_archive.py"]},
                 "feature_schemas": models["gfs"]["feature_schema"],
                 "packages": {p: importlib.metadata.version(p) for p in ["numpy", "scikit-learn", "eccodes"]}}
    write_json(lock_path, selection)
    return selection


def diagnosis(rows, ridge_model, values):
    """Posthoc explanations of the fixed old Ridge, never an input to selection."""
    groups = {"current_gfs_temperature": [0], "night_profile": [1, 2, 3], "calendar": [4, 5], "lead_indicators": list(range(6, 20))}
    scaler, estimator = [step[1] for step in ridge_model["estimator"].steps]
    mean = lambda v: sum(v)/len(v) if v else None
    by_date, slices = defaultdict(list), defaultdict(list)
    for r, value in zip(rows, values):
        x = r["features_temperature"]
        contributions = {k: sum((x[i]-scaler.mean_[i])/scaler.scale_[i]*estimator.coef_[i] for i in indexes) for k, indexes in groups.items()}
        actual, base = r["target"]["temperature"], r["base"]["temperature"]
        item = {"date": r["date"], "valid_at_utc": r["valid_at_utc"], "hour_index": r["hour_index"], "observed_c": actual,
                "gfs_c": base, "ridge_c": value, "correction_c": value-base, "intercept_c": float(estimator.intercept_),
                "standardized_additive_contributions_c": contributions}
        by_date[r["date"]].append(item)
        if actual is not None:
            label = "T<0" if actual < 0 else "T=0" if actual == 0 else "0<T<=2" if actual <= 2 else "T>2"
            slices[label].append(item)
    sliced = {}
    for label, items in slices.items():
        sliced[label] = {"hours": len(items), "gfs_mae_c": mean([abs(r["gfs_c"]-r["observed_c"]) for r in items]),
                         "ridge_mae_c": mean([abs(r["ridge_c"]-r["observed_c"]) for r in items]),
                         "gfs_bias_c": mean([r["gfs_c"]-r["observed_c"] for r in items]),
                         "ridge_bias_c": mean([r["ridge_c"]-r["observed_c"] for r in items])}
    failures = []
    for d, items in sorted(by_date.items()):
        if any(r["observed_c"] is None for r in items):
            continue
        a = sum(r["observed_c"] < 0 for r in items)
        b = sum(r["gfs_c"] < 0 for r in items)
        c = sum(r["ridge_c"] < 0 for r in items)
        if a or b or c:
            failures.append({"date": d, "observed_cold_bins": a, "gfs_cold_bins": b, "ridge_cold_bins": c,
                             "crossed_into_cold": [r for r in items if r["gfs_c"] >= 0 and r["ridge_c"] < 0],
                             "crossed_out_of_cold": [r for r in items if r["gfs_c"] < 0 <= r["ridge_c"]],
                             "hours": items})
    return {"slices_by_observed_temperature": sliced, "risk_nights": failures,
            "interpretation": "algebraic_contributions_are_not_causal_attribution; correlated_features; diagnostic_only"}


def paired_uncertainty(baseline, selected, settings):
    """Calendar-block paired duration differences and exact night changes, not independent hourly resampling."""
    import numpy as np
    b = {n["date"]: n for n in baseline["nights"]}
    changes = []
    results = {}
    for n in selected["nights"]:
        old = b[n["date"]]
        if old["outcome"] != n["outcome"]:
            changes.append({"date": n["date"], "gfs": old["outcome"], "selected": n["outcome"]})
    for width in settings["block_days"]:
        blocks = defaultdict(list)
        for n in selected["nights"]:
            if not n["observed"]["event"]:
                continue
            d = date.fromisoformat(n["date"])
            key = (d.year, (d-date(d.year, 3, 1)).days//width)
            blocks[key].append(b[n["date"]]["duration_absolute_error_h"]-n["duration_absolute_error_h"])
        # Include empty calendar blocks, keeping the original chronology and warm spells.
        dates = [date.fromisoformat(n["date"]) for n in selected["nights"]]
        for d in dates:
            blocks.setdefault((d.year, (d-date(d.year, 3, 1)).days//width), [])
        values = list(blocks.values())
        sums, counts = np.array([sum(v) for v in values]), np.array([len(v) for v in values])
        rng = np.random.default_rng(settings["seed"])
        picks = rng.integers(0, len(values), size=(settings["replicates"], len(values)))
        total = counts[picks].sum(axis=1)
        valid = total > 0
        means = sums[picks].sum(axis=1)[valid]/total[valid]
        low, high = np.quantile(means, [.025, .975])
        results[str(width)] = {"blocks": len(values), "blocks_with_cold_nights": sum(bool(v) for v in values),
                               "usable_replicates": int(valid.sum()), "replicates_without_cold_nights": int((~valid).sum()),
                               "cold_duration_mae_improvement_h": float(sums.sum()/counts.sum()), "interval_95": [float(low), float(high)]}
    return {"paired_night_event_changes": changes, "duration_block_bootstrap": results}


def run_frost_study(protocol_path, data_dir, report_dir):
    selection = select_frost(protocol_path, data_dir)
    directory = data_dir.parent / "models/research-frost"
    models = {}
    for family in FAMILIES:
        path = directory / family / "model.json"
        if sha(path) != selection["model_artifact_sha256"][family]:
            raise ValueError("Frozen cold model hash mismatch")
        models[family] = validate_saved_model(json.loads(path.read_text(encoding="utf-8")))
    # Only AFTER frozen choice and model hash verification do we read the 2025 station file.
    year = selection["model_protocol"]["historical_check_year"]
    check, provenance = load_rows(data_dir, [year])
    specs = candidate_specs(selection["protocol"])
    predictions = {s["name"]: predict_cold(models[s["family"]], check, s["threshold"]) for s in specs}
    scores = {name: score_cold(check, p) for name, p in predictions.items()}
    train, _ = load_rows(data_dir, selection["model_protocol"]["final_train_years"])
    ridge = fit_hourly("ridge_temperature", "temperature", train, selection["model_protocol"])
    ridge_values = predict_hourly(ridge, check)
    scores["hourly_ridge_reference"] = score_cold(check, temperature_masks(check, ridge_values))
    winner = selection["selected_candidate"]["name"]
    summary = {**selection, "generated_at_utc": datetime.now(UTC).isoformat(), "historical_input_provenance": provenance,
               "historical_scores": scores, "old_ridge_diagnosis": diagnosis(check, ridge, ridge_values),
               "paired_timing_vs_gfs": compare_timing_cohorts(scores["gfs"], scores[winner]),
               "paired_uncertainty_vs_gfs": paired_uncertainty(scores["gfs"], scores[winner], selection["model_protocol"]["bootstrap"]),
               "training_target_counts": {"nights": models["gfs"]["training_nights"], "cold_nights": models["gfs"]["training_cold_nights"],
                                          "hours": 14*models["gfs"]["training_nights"], "cold_hours": models["gfs"]["training_cold_hours"]}}
    summary["selection_artifact_sha256"] = sha(directory / "selection-before-2025.json")
    summary["execution_source_sha256"] = {f: sha(ROOT / "agro_checker" / f) for f in selection["source_sha256"]}
    actual_by_date = {n["date"]: n for n in scores[winner]["nights"]}
    summary["selected_head_diagnostics"] = [
        {"date": p["date"], "night_event_model_score": p["night_event_model_score"], "conditional_duration_raw_h": p["conditional_duration_raw_h"],
         "predicted_cold_bins_h": sum(p["cold_mask"]), "observed_cold_bins_h": actual_by_date[p["date"]]["observed"]["cold_bins_hours"],
         "outcome": actual_by_date[p["date"]]["outcome"]}
        for p in predictions[winner] if p["date"] in actual_by_date and (any(p["cold_mask"]) or actual_by_date[p["date"]]["observed"]["event"])]
    all_rows, _ = load_rows(data_dir, selection["source_config"]["years"])
    selected_spec = selection["selected_candidate"]
    all_predictions = predict_cold(models[selected_spec["family"]], all_rows, selected_spec["threshold"])
    by_date = {p["date"]: p for p in all_predictions}
    flat, snapshot = [], []
    for s in night_samples(all_rows):
        p = by_date[s["date"]]
        role = "historical_check_already_viewed" if s["year"] == year else "model_selection_replay" if s["year"] == 2024 else "in_sample_training_replay"
        forecast, truth = [], []
        for i, r in enumerate(s["rows"]):
            f = {"valid_at_utc": r["valid_at_utc"], "gfs_temperature_c": r["base"]["temperature"], "gfs_cold": r["base"]["temperature"] < 0,
                 "predicted_cold": p["cold_mask"][i], "cold_hour_model_score": p["cold_hour_model_scores"][i] if p["cold_hour_model_scores"] is not None else None}
            forecast.append(f)
            truth.append({"valid_at_utc": r["valid_at_utc"], "observed_temperature_c": r["target"]["temperature"], "temperature_source": r["source"]["temperature"]})
            flat.append({"night_date_local": s["date"], "evaluation_status": role, "issued_at_utc": r["issued_at_utc"],
                         "forecast_run_at_utc": r["forecast_run_at_utc"], **f, **{k: v for k, v in truth[-1].items() if k != "valid_at_utc"}})
        stamps = [r["valid_at_utc"] for r in s["rows"]]
        snapshot.append({"date": s["date"], "evaluation_status": role, "issued_at_utc": s["rows"][0]["issued_at_utc"],
                         "forecast_run_at_utc": s["rows"][0]["forecast_run_at_utc"], "selected_candidate": selected_spec,
                         "night_event_model_score": p["night_event_model_score"], "conditional_duration_raw_h": p["conditional_duration_raw_h"],
                         "selected_frost": p["frost"], "gfs_frost": timed_frost(s["curve"], stamps), "forecast": forecast,
                         "hindsight_observations": truth, "hindsight_frost": timed_frost([r["target"]["temperature"] for r in s["rows"]], stamps)})
    processed = data_dir / "processed/frost-study"
    write_csv(processed / "forecasts.csv", flat)
    write_json(processed / "forecast-snapshot.json", {"selected_candidate": selected_spec, "nights": snapshot})
    write_json(processed / "forecast-snapshot.json.meta.json", {"sha256": sha(processed / "forecast-snapshot.json")})
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "FROST_STUDY_SUMMARY.json", summary)
    plot_frost(summary, snapshot, report_dir)
    (report_dir / "FROST_STUDY_REPORT.md").write_text(report_frost(summary), encoding="utf-8")
    return summary


def replay_frost(data_dir, night):
    date.fromisoformat(night)
    path = data_dir / "processed/frost-study/forecast-snapshot.json"
    meta = json.loads(path.with_name(path.name+".meta.json").read_text(encoding="utf-8"))
    if sha(path) != meta["sha256"]:
        raise ValueError("Frost snapshot hash mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    n = next((n for n in payload["nights"] if n["date"] == night), None)
    if n is None:
        raise ValueError("Night not found")
    return {**n, "night_date_local": night, "availability_evidence": "archive_timestamp_proxy; first publication unknown",
            "forecast_available_at_utc": None, "target": "station air 2m T<0; not flower damage",
            "time_resolution": "hourly bins within 19:00–09:00 MSK; edge censoring; not continuous duration",
            "model_score_status": "uncalibrated classifier scores; decision threshold selected on 2023–2024 only"}


def report_frost(s):
    winner = s["selected_candidate"]["name"]
    selected, base, old = [s["historical_scores"][n] for n in [winner, "gfs", "hourly_ridge_reference"]]
    f = lambda v: "—" if v is None else f"{v:.3f}"
    lines = ["# Обнаружение и длительность заморозка", "", f"Расчёт {s['generated_at_utc']}. Краснодар, станция 34929, яблоня как сценарий; погодная цель T<0 °C.", "",
             f"Выбран **{winner}** только по 2023–2024. Обучение 2022–2023; 2025 уже был просмотрен и остаётся исторической проверкой. Модель демонстрации не заменена.", "",
             "## Почему почасовой Ridge ухудшает время холода", "",
             "1. Старая модель выбиралась по средней почасовой MAE температуры; сам Ridge учит квадратичную ошибку остатка с регуляризацией. Ошибка знака около нуля и лишний холодный час не имели отдельного штрафа. Тёплые часы преобладают; средняя MAE скрывает качество редкого события.",
             f"2. В финальном обучении {s['training_target_counts']['cold_hours']} отрицательных часов из {s['training_target_counts']['hours']}, {s['training_target_counts']['cold_nights']} холодных ночей из {s['training_target_counts']['nights']}. В валидации всего семь холодных ночей: выбор нестабилен.",
             "3. Ridge обучен на остатке Tфакт−TGFS с признаками температуры, профиля, календаря и индикаторов часа. Его поправка меняет переход через ноль, хотя улучшение MAE не требует правильного времени перехода. Ниже показано точное аддитивное разложение поправки; это алгебра, не причинное влияние независимых признаков.",
             "4. Станционные цели целочисленные: ровно 0 °C — не заморозок по заданному строгому порогу. Небольшая отрицательная поправка превращает такой час в ложный холод. Минимум и общая MAE не проверяют форму холодного эпизода.", "",
             "| Фактическая T, диагностический срез 2025 | Часов | MAE GFS °C | MAE Ridge °C | Bias GFS °C | Bias Ridge °C |",
             "|---|---:|---:|---:|---:|---:|"]
    for label in ["T<0", "T=0", "0<T<=2", "T>2"]:
        a = s["old_ridge_diagnosis"]["slices_by_observed_temperature"][label]
        lines.append(f"| {label} | {a['hours']} | {f(a['gfs_mae_c'])} | {f(a['ridge_mae_c'])} | {f(a['gfs_bias_c'])} | {f(a['ridge_bias_c'])} |")
    lines += ["", "Срезы по факту — только объяснение прошлого результата, в выборе новой модели не участвуют.", "",
              "| Ночь | Холодных часов факт / GFS / Ridge | Лишних пересечений в холод |",
              "|---|---|---:|"]
    for n in s["old_ridge_diagnosis"]["risk_nights"]:
        lines.append(f"| {n['date']} | {n['observed_cold_bins']} / {n['gfs_cold_bins']} / {n['ridge_cold_bins']} | {len(n['crossed_into_cold'])} |")
    lines += ["", "| Первый добавленный холодный час, МСК | TGFS → TRidge °C | Факт °C | Intercept | Текущая T | Профиль | Календарь | Индикаторы часа |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for n in s["old_ridge_diagnosis"]["risk_nights"]:
        if not n["crossed_into_cold"]:
            continue
        r = n["crossed_into_cold"][0]; c = r["standardized_additive_contributions_c"]
        stamp = (datetime.fromisoformat(r["valid_at_utc"])+timedelta(hours=3)).strftime("%d.%m %H:%M")
        lines.append(f"| {stamp} | {r['gfs_c']:.2f} → {r['ridge_c']:.2f} | {r['observed_c']:.0f} | {r['intercept_c']:.2f} | {c['current_gfs_temperature']:.2f} | {c['night_profile']:.2f} | {c['calendar']:.2f} | {c['lead_indicators']:.2f} |")
    lines += ["", "## Новая постановка", "",
              "Реестр закреплён в `config/frost_study.json` до новой валидации: GFS и две семьи, по три фиксированных порога 0.25/0.50/0.75, всего семь вариантов. C=1, scaler только обучения, LogisticRegression без class weights; Ridge длительности alpha=10. Нет поиска настроек, календаря, измерений или каскада с исправленной температурой во входах.", "",
              "- `cold_hour_logistic`: напрямую учит знак T<0 на каждом часе; порог задаёт холодную маску.",
              "- `event_duration_weather`: отдельный классификатор ночного события, Ridge числа холодных часов только на холодных обучающих ночах и часовой классификатор для расположения этих часов. При событии длительность округляется и ограничивается 1–14, затем выбираются k часов с наибольшей оценкой холода. Разрывы не заполняются, отдельные эпизоды сохраняются. Ошибки этих трёх голов могут накапливаться.", "",
              "Выход — холодные часы и эпизоды; новая модель не выдаёт искусственную температуру. Оценки классификаторов не откалиброваны как вероятности. Температурный и RH прогнозы прежнего этапа сохранены отдельно.", "",
              "Выбор: L=¼·(доля пропущенных холодных ночей + доля ложных тревог среди тёплых + MAE длительности холодных/14 + MAE длительности тёплых/14). Так тёплое большинство не подавляет холодные ночи. Пропуски включены в длительность; ложные тревоги штрафуются и как событие, и как часы. Веса заданы до расчёта как исследовательский компромисс, не оценка стоимости ущерба. Начало не оптимизируется условной MAE попаданий: она могла бы улучшаться простым отказом от сложных ночей.", "",
              "Фолды: 2022→2023, 2022–2023→2024. Классы и ошибки длительности объединяются по ночам с собственными знаменателями, а не простым средним двух сезонов. GFS может выиграть; прежний Ridge — диагностическая база вне нового реестра. Финальный fit только 2022–2023. `frost-select` не открывает станционный файл 2025; модель/выбор и SHA записываются до его чтения в `frost-study`. Повторение проверяет неизменность выбора и коэффициентов.", "",
              "## Выбор на 2023–2024", "",
              "| Метод | L ↓ | Пропуски из 7 | Ложные | MAE длительности холодных ч | MAE длительности тёплых ч |",
              "|---|---:|---:|---:|---:|---:|"]
    for n, a in s["pooled_validation"].items():
        lines.append(f"| {n} | {f(a['selection_loss'])} | {a['misses']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['cold_bin_duration_mae_warm_h'])} |")
    lines += ["", "| Сезон | Метод | Пропуски/холодных | Ложные | MAE длительности холодных ч |",
              "|---|---|---|---:|---:|"]
    for fold in s["folds"]:
        for n in ["gfs", "hourly_ridge_reference", winner]:
            a = fold["scores"][n]
            lines.append(f"| {fold['validation_year']} | {n} | {a['misses']}/{a['cold_nights']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_cold_h'])} |")
    lines += ["", "Всего семь положительных ночей в выборе и зависимые холодные серии: преимущество в валидации не является независимым подтверждением. 2025 не слепой тест; нужна новая станция/сезон с заранее закреплённым протоколом.", "",
              "## Исторический 2025: без переключения на победителя этого года", "",
              "| Метод | L ↓ | Пропуски/6 | Ложные | MAE длительности все / холодные ч | Первый холодный час MAE ч (n) | Recall холодных часов |",
              "|---|---:|---:|---:|---|---|---:|"]
    for n, a in s["historical_scores"].items():
        lines.append(f"| {n} | {f(a['selection_loss'])} | {a['misses']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_all_h'])} / {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['first_cold_sample_mae_hits_h'])} ({a['first_sample_pairs']}) | {f(a['cold_hour_recall'])} |")
    lines += ["", "Сравнение первого холодного отсчёта на одинаковых попаданиях:", "",
              "| Определение | Общих ночей | GFS MAE ч | Выбранная MAE ч |", "|---|---:|---:|---:|"]
    for key, label in [("first_sample_common_hits", "Первый холодный отсчёт"), ("onset_common_interior_hits", "Начало без границы у обеих моделей")]:
        a = s["paired_timing_vs_gfs"][key]
        lines.append(f"| {label} | {a['paired_nights']} | {f(a['baseline_mae_h'])} | {f(a['corrected_mae_h'])} |")
    lines += ["", "Парная разность MAE длительности GFS−модель на фактически холодных ночах, календарные блоки; положительное значение — выигрыш. Пустые тёплые блоки сохраняются; повторения без холодных ночей исключены и посчитаны. Это чувствительность одного сезона с шестью событиями, не доказательство переносимости.", "",
              "| Блок, дней | Блоков с холодом / всех | Выигрыш ч | 95% диапазон ч | Повторов без холодных ночей |", "|---|---|---:|---|---:|"]
    for width, a in s["paired_uncertainty_vs_gfs"]["duration_block_bootstrap"].items():
        lines.append(f"| {width} | {a['blocks_with_cold_nights']}/{a['blocks']} | {f(a['cold_duration_mae_improvement_h'])} | [{f(a['interval_95'][0])}, {f(a['interval_95'][1])}] | {a['replicates_without_cold_nights']} |")
    lines += ["", "| Холодная ночь или тревога выбранного метода | Исход | Холодных часов факт / GFS / модель | Первый холодный отсчёт факт / модель МСК |",
              "|---|---|---|---|"]
    by_base = {n["date"]: n for n in base["nights"]}
    stamp = lambda v: (datetime.fromisoformat(v)+timedelta(hours=3)).strftime("%d.%m %H:%M") if v else "—"
    for n in selected["nights"]:
        if n["observed"]["event"] or n["predicted"]["event"]:
            a, p, b = n["observed"], n["predicted"], by_base[n["date"]]["predicted"]
            lines.append(f"| {n['date']} | {n['outcome']} | {a['cold_bins_hours']} / {b['cold_bins_hours']} / {p['cold_bins_hours']} | {stamp(a['first_cold_sample_at_utc'])} / {stamp(p['first_cold_sample_at_utc'])} |")
    paired = s["paired_timing_vs_gfs"]["first_sample_common_hits"]
    lines += ["", "## Что изменилось и где остаётся ошибка", "",
              f"Ночной детектор: пропуски {base['misses']} → {selected['misses']} из {base['cold_nights']}, ложные тревоги {base['false_alarms']} → {selected['false_alarms']}. "
              f"Но MAE длительности фактически холодных ночей {f(base['cold_bin_duration_mae_cold_h'])} → {f(selected['cold_bin_duration_mae_cold_h'])} ч; "
              f"первого холодного часа на {paired['paired_nights']} общих попаданиях {f(paired['baseline_mae_h'])} → {f(paired['corrected_mae_h'])} ч. "
              "Снижение выбранного составного L не означает улучшения каждого из его компонентов.", "",
              "| Ночь | Исход | Оценка события | Сырая условная длительность ч | Холодных часов модель / факт |",
              "|---|---|---:|---:|---|"]
    for n in s["selected_head_diagnostics"]:
        lines.append(f"| {n['date']} | {n['outcome']} | {f(n['night_event_model_score'])} | {f(n['conditional_duration_raw_h'])} | {n['predicted_cold_bins_h']} / {n['observed_cold_bins_h']} |")
    lines += ["", "Ночной классификатор решает, будет ли хоть один отрицательный отсчёт; он не гарантирует нужного числа часов. "
              "Длительность учится лишь на 21 холодной обучающей ночи, поэтому её переносимость ограничена. "
              "Ранжирование выбирает часы отдельно и может ставить разрозненные лишние отсчёты раньше факта. "
              "Это видимые ошибки отдельных голов и декодирования, не доказанная физическая причина. "
              "Модель полезна как исследовательский детектор; заменять ей GFS для расписания защиты пока оснований нет.", "",
              "![Маски холода и причины старой ошибки](assets/frost-timing.png)", "", "## Ограничения и воспроизведение", "",
              "Цель — T воздуха на станции 2 м, не температура тканей яблони и не потеря урожая. Начало — первый отрицательный часовой отсчёт, длительность — число холодных часовых интервалов в [19,09). За границами окна начало/конец неизвестны; минутная и непрерывная точность не заявляются. Неполные 14 температур исключаются из fit/score; прогноз всё равно возвращается без использования факта. Источник ISD-lite/METAR не заменяет QC. Те же ближайшая точка GFS и архивная доступность Last-Modified+10 минут; первая публикация неизвестна.", "",
              "```powershell", ".\\.venv\\Scripts\\python.exe -m agro_checker frost-select --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker frost-study --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker frost-replay --night 2025-03-07", "```", "",
              "Модели/выбор: `models/research-frost/`; выдачи всех 368 ночей: `data/processed/frost-study/`. Replay проверяет SHA, отделяет факт от прогноза, маркирует роль сезона. [Численные результаты и происхождение](FROST_STUDY_SUMMARY.json).", ""]
    return "\n".join(lines)


def plot_frost(summary, snapshot, report_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    winner = summary["selected_candidate"]["name"]
    old = {n["date"]: n for n in summary["old_ridge_diagnosis"]["risk_nights"]}
    nights = {n["date"]: n for n in snapshot}
    examples = summary["protocol"]["report_examples"]
    fig, axes = plt.subplots(len(examples), 2, figsize=(14, 11), gridspec_kw={"width_ratios": [1.1, 1]})
    for i, d in enumerate(examples):
        n = nights[d]; diag = old[d]; x = list(range(14)); hours = [(19+j)%24 for j in x]
        ax = axes[i, 0]
        for key, label, color in [("gfs_c", "GFS", "#3277a8"), ("ridge_c", "Old hourly Ridge", "#e28131"), ("observed_c", "Observed", "#398459")]:
            ax.plot(x, [r[key] for r in diag["hours"]], marker="o", ms=3, label=label, color=color)
        ax.axhline(0, color="#777", ls="--", lw=1)
        ax.set_title(d+" · temperature", fontsize=11); ax.set_ylabel("°C"); ax.grid(alpha=.18)
        ax.set_xticks(x[::2], hours[::2]); ax.set_xlabel("Hour MSK")
        if i == 0: ax.legend(fontsize=8)
        masks = [[r["observed_c"] < 0 for r in diag["hours"]], [r["gfs_c"] < 0 for r in diag["hours"]],
                 [r["ridge_c"] < 0 for r in diag["hours"]], [r["predicted_cold"] for r in n["forecast"]]]
        ax = axes[i, 1]
        ax.imshow(np.array(masks), cmap="Blues", vmin=0, vmax=1, aspect="auto", interpolation="nearest")
        ax.set_yticks(range(4), ["Observed", "GFS", "Old Ridge", "Selected cold model"])
        ax.set_xticks(x[::2], hours[::2]); ax.set_xlabel("Hour MSK")
        durations = [sum(m) for m in masks]
        ax.set_title(f"Cold bins: {durations[0]} / {durations[1]} / {durations[2]} / {durations[3]}", fontsize=10)
    fig.suptitle(f"Selected on 2023–2024: {winner}\n2025 historical examples; blue = cold; hourly bins, not continuous duration", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, .94))
    assets = report_dir / "assets"; assets.mkdir(parents=True, exist_ok=True)
    fig.savefig(assets / "frost-timing.png", dpi=160); plt.close(fig)
