"""Forward-season, coupled weather scenarios and additional verified meteorological targets."""

from collections import Counter, defaultdict
from datetime import datetime
import csv
import gzip
import hashlib
import io
import json
import math

import numpy as np

from .gfs_covariates import load_covariates
from .humidity_archive import load_humidity
from .hourly_study import build_hourly_rows, fit_hourly, frost_timing, load_hourly_model, parse_humidity_observations, predict_hourly, save_hourly_model
from .model_study import load_protocol
from .pipeline import ROOT, UTC, cached_download, number, parse_observations, write_json
from .single_run import build_tables, load_points, load_single_config

TARGETS = ["temperature", "humidity", "wind"]
GROUP_INDEX = {"temperature": 0, "humidity": 1, "dewpoint_spread": 3, "wind": 4, "cloud": 5}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def load_scenario_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    expected = [("gfs", "gfs"), ("previous_hourly", "previous_hourly"), ("smooth_ridge_10", "smooth"),
                ("smooth_ridge_100", "smooth"), ("smooth_ridge_100_cold", "smooth"), ("analog_temperature_16", "analog"),
                ("analog_weather_8", "analog"), ("analog_weather_16", "analog"), ("analog_weather_32", "analog")]
    if p["schema_version"] != 1 or [(s["name"], s["kind"]) for s in p["candidates"]] != expected:
        raise ValueError("Unexpected fixed scenario registry")
    if p["targets"] != TARGETS or p["weather_threshold_c"] != 0 or p["high_humidity_threshold_pct"] != 90 or p["low_wind_threshold_ms"] != 2:
        raise ValueError("Unexpected target definitions")
    if p["historical_year"] != 2025 or p["fresh_year"] != 2026 or p["final_train_years"] != [2022, 2023]: raise ValueError("Unexpected scenario chronology")
    if p["folds"] != [{"train_years": [2022], "validation_year": 2023}, {"train_years": [2022, 2023], "validation_year": 2024}]: raise ValueError("Unexpected scenario folds")
    if not math.isclose(sum(p["selection_weights"].values()), 1): raise ValueError("Invalid selection weights")
    return p


def parse_wind(content, allowed):
    result = {}; audit = Counter()
    for r in csv.DictReader(io.StringIO(gzip.decompress(content).decode("utf-8-sig"))):
        time = datetime(*(int(r[k]) for k in ["year", "month", "day", "hour"]), tzinfo=UTC).isoformat()
        if time in result: raise ValueError("Duplicate wind target time")
        value = number(r["wspd"]); source = r["wspd_source"]
        valid = value is not None and 0 <= value <= 288 and source in allowed
        status = "accepted" if valid else "invalid_range" if value is not None and not 0 <= value <= 288 else "missing_or_excluded_source"
        result[time] = {"value": value/3.6 if valid else None, "source": source, "status": status, "raw_kmh": value}
        audit[status] += 1
    return result, dict(audit)


def load_rows(data_dir, years, config=None):
    config = config or load_single_config(ROOT/"config/single_run.json")
    if not years or not set(years) <= set(config["years"]): raise ValueError("Unsupported scenario years")
    points, failures, _, tmeta = load_points(data_dir, config)
    cov, cfail, _, cmeta = load_covariates(data_dir, config)
    rh, rfail, _, rmeta = load_humidity(data_dir, config)
    if failures or cfail or rfail: raise ValueError("Scenario forecast archive incomplete")
    temperatures, humidity, wind, provenance, audit = {}, {}, {}, {}, {}
    for year in years:
        content, meta = cached_download(f"https://data.meteostat.net/hourly/{year}/{config['station_id']}.csv.gz",
                                        data_dir/"raw"/f"meteostat-{config['station_id']}-{year}.csv.gz", True)
        temperatures.update(parse_observations(content, config["observation_sources"]))
        humidity.update(parse_humidity_observations(content, config["observation_sources"]))
        w, a = parse_wind(content, config["observation_sources"]); wind.update(w)
        provenance[str(year)] = meta; audit[str(year)] = a
    subset = lambda records: [r for r in records if int(r["night_date_local"][:4]) in years]
    hourly, _ = build_tables({**config, "years": years}, subset(points), temperatures)
    rows = build_hourly_rows(hourly, subset(cov), subset(rh), humidity, config)
    for r in rows:
        obs = wind.get(r["valid_at_utc"], {"value": None, "source": "", "status": "missing"})
        r["base"]["wind"] = r["features_weather"][4]
        r["target"]["wind"] = obs["value"]; r["source"]["wind"] = obs["source"]; r["wind_target_status"] = obs["status"]
        # Independent range screening leaves old studies and their targets unchanged.
        if r["target"]["temperature"] is not None and not -60 <= r["target"]["temperature"] <= 60: r["target"]["temperature"] = None
    return rows, {"temperature": tmeta, "covariates": cmeta, "humidity": rmeta, "observations": provenance, "annual_wind_audit": audit}


def nights(rows):
    groups = defaultdict(list)
    for r in rows: groups[r["date"]].append(r)
    result = []
    for date, hours in sorted(groups.items()):
        hours = sorted(hours, key=lambda r: r["hour_index"])
        if len(hours) != 14 or [r["hour_index"] for r in hours] != list(range(14)): raise ValueError("Need 14 ordered forecast hours")
        if len({r["forecast_run_at_utc"] for r in hours}) != 1 or len({r["issued_at_utc"] for r in hours}) != 1: raise ValueError("Mixed scenario issue")
        result.append({"date": date, "year": int(date[:4]), "rows": hours,
                       "base": {t: [r["base"][t] for r in hours] for t in TARGETS},
                       "target": {t: [r["target"][t] for r in hours] for t in TARGETS}})
    return result


def smooth_features(r):
    w = r["features_weather"]; z = 2*r["hour_index"]/13-1
    weather = [w[i] for i in [0, 1, 3, 4, 5, 6, 7, 9]]
    return weather+[z, z*z, max(0., z)]+[z*v for v in weather]+[z*z*v for v in weather]


def analog_features(night, groups):
    return [r["features_weather"][GROUP_INDEX[g]] for g in groups for r in night["rows"]]


def fit_candidate(spec, rows, p):
    if not rows or any(r["year"] >= p["historical_year"] for r in rows): raise ValueError("2025/2026 targets cannot enter scenario fitting")
    model = {"spec": spec, "training_years": sorted({r["year"] for r in rows}), "last_training_time": max(r["valid_at_utc"] for r in rows)}
    if spec["kind"] == "gfs": return model
    if spec["kind"] == "previous_hourly":
        m = load_protocol(ROOT/"config/model_study.json")
        model["reference"] = {t: fit_hourly(name, t, rows, m) for t, name in [("temperature", "ridge_temperature"), ("humidity", "catboost_weather")]}
        return model
    if spec["kind"] == "smooth":
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
        model["heads"] = {}
        for t in TARGETS:
            usable = [r for r in rows if r["target"][t] is not None]
            x = np.array([smooth_features(r) for r in usable]); scaler = StandardScaler().fit(x)
            weights = [1+(spec["near_zero_weight"]-1)*math.exp(-abs(r["base"]["temperature"])/3) for r in usable]
            estimator = Ridge(alpha=spec["alpha"], solver="svd").fit(scaler.transform(x), [r["target"][t]-r["base"][t] for r in usable], sample_weight=weights)
            model["heads"][t] = {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(), "coef": estimator.coef_.tolist(),
                                   "intercept": float(estimator.intercept_), "paired_training_hours": len(usable)}
        return model
    usable = [n for n in nights(rows) if all(v is not None for t in TARGETS for v in n["target"][t])]
    if len(usable) < spec["neighbors"]: raise ValueError("Too few complete joint analog nights")
    x = np.array([analog_features(n, spec["feature_groups"]) for n in usable]); scale = x.std(axis=0); scale[scale == 0] = 1
    model.update(mean=x.mean(axis=0).tolist(), scale=scale.tolist(), library=[{
        "date": n["date"], "forecast_features": analog_features(n, spec["feature_groups"]),
        "residual": {t: [a-b for a, b in zip(n["target"][t], n["base"][t])] for t in TARGETS}} for n in usable],
        complete_library_nights=len(usable), excluded_joint_nights=len(nights(rows))-len(usable))
    return model


def derived(curves, p):
    def complete(t): return len(curves[t]) == 14 and all(v is not None and math.isfinite(v) for v in curves[t])
    temp_ok, rh_ok, wind_ok = [complete(t) for t in TARGETS]
    cold = frost_timing(curves["temperature"]) if temp_ok else None
    return {"minimum_c": min(curves["temperature"]) if temp_ok else None,
            "cold_degree_hours": sum(max(0., -v) for v in curves["temperature"]) if temp_ok else None,
            "cold_bins_hours": cold["cold_bins_hours"] if cold else None,
            "first_cold_index": cold["first_cold_sample_index"] if cold else None,
            "cold_mask": [v < 0 for v in curves["temperature"]] if temp_ok else None,
            "left_censored": cold["left_censored"] if cold else None, "right_censored": cold["right_censored"] if cold else None,
            "high_humidity_hours": sum(v >= p["high_humidity_threshold_pct"] for v in curves["humidity"]) if rh_ok else None,
            "low_wind_hours": sum(v < p["low_wind_threshold_ms"] for v in curves["wind"]) if wind_ok else None,
            "cold_high_humidity_hours": sum(t < 0 and rh >= p["high_humidity_threshold_pct"] for t, rh in zip(curves["temperature"], curves["humidity"])) if temp_ok and rh_ok else None}


def predict_candidate(model, night, p):
    spec = model["spec"]; base = night["base"]
    if spec["kind"] == "gfs": members = [{t: list(base[t]) for t in TARGETS}]; analog_dates = []
    elif spec["kind"] == "previous_hourly":
        members = [{**base, **{t: predict_hourly(m, night["rows"]) for t, m in model["reference"].items()}}]; analog_dates = []
    elif spec["kind"] == "smooth":
        members = [{t: [r["base"][t]+head["intercept"]+sum((x-m)/s*c for x, m, s, c in zip(smooth_features(r), head["mean"], head["scale"], head["coef"])) for r in night["rows"]] for t, head in model["heads"].items()}]; analog_dates = []
    else:
        x = np.array(analog_features(night, spec["feature_groups"])); scale = np.array(model["scale"])
        distances = [float(np.mean(((x-np.array(a["forecast_features"]))/scale)**2)) for a in model["library"]]
        indexes = sorted(range(len(distances)), key=lambda i: (distances[i], model["library"][i]["date"]))[:spec["neighbors"]]
        members = [{t: [b+e for b, e in zip(base[t], model["library"][i]["residual"][t])] for t in TARGETS} for i in indexes]
        analog_dates = [model["library"][i]["date"] for i in indexes]
    for member in members:
        member["humidity"] = [max(0., min(100., v)) for v in member["humidity"]]
        member["wind"] = [max(0., v) for v in member["wind"]]
        if any(not math.isfinite(v) for t in TARGETS for v in member[t]): raise ValueError("Nonfinite scenario prediction")
    point = {t: np.median([m[t] for m in members], axis=0).tolist() for t in TARGETS}
    values = [derived(m, p) for m in members]; score = sum(v["cold_bins_hours"] > 0 for v in values)/len(values)
    event = score >= spec.get("event_threshold", .5)
    allowed = [i for i, v in enumerate(values) if (v["cold_bins_hours"] > 0) == event]
    def loss(i, j):
        a, b = values[i], values[j]
        onset = abs(a["first_cold_index"]-b["first_cold_index"])/14 if a["first_cold_index"] is not None and b["first_cold_index"] is not None else 0.
        return abs(a["cold_bins_hours"]-b["cold_bins_hours"])/14+onset+abs(a["cold_degree_hours"]-b["cold_degree_hours"])/28+sum(x != y for x, y in zip(a["cold_mask"], b["cold_mask"]))/14
    chosen = min(allowed, key=lambda i: (sum(loss(i, j) for j in allowed)/len(allowed), i))
    intervals = {t: {"p10": np.quantile([m[t] for m in members], .1, axis=0).tolist(), "p90": np.quantile([m[t] for m in members], .9, axis=0).tolist()} for t in TARGETS}
    return {"point": point, "warning_curve": members[chosen]["temperature"], "warning": values[chosen], "event_score": score,
            "members": members, "member_count": len(members), "analog_dates": analog_dates, "intervals_80": intervals,
            "aggregate_quantiles": {k: {"p10": float(np.quantile([v[k] for v in values], .1)), "median": float(np.median([v[k] for v in values])), "p90": float(np.quantile([v[k] for v in values], .9))} for k in ["minimum_c", "cold_degree_hours", "high_humidity_hours", "low_wind_hours", "cold_high_humidity_hours"]}}


def score_predictions(samples, predictions, p):
    if len(samples) != len(predictions): raise ValueError("Scenario score length mismatch")
    errors = defaultdict(list); counts = Counter(hits=0, misses=0, false_alarms=0, correct_negatives=0)
    calibration = [{"count": 0, "forecast_sum": 0., "events": 0} for _ in range(5)]
    paired = {t: {"count": 0, "covered_80": 0, "width_sum": 0., "crps_sum": 0.} for t in TARGETS}
    records = []; complete_t = 0
    for n, forecast in zip(samples, predictions):
        truth = derived(n["target"], p); point = derived(forecast["point"], p)
        for t in TARGETS:
            members = np.array([m[t] for m in forecast["members"]])
            spread_term = np.mean(np.abs(members[:, None, :]-members[None, :, :]), axis=(0, 1))/2
            for i, actual in enumerate(n["target"][t]):
                if actual is None: continue
                errors[t].append(abs(forecast["point"][t][i]-actual)); row = paired[t]
                row["count"] += 1
                lo, hi = [forecast["intervals_80"][t][q][i] for q in ["p10", "p90"]]
                row["covered_80"] += lo <= actual <= hi; row["width_sum"] += hi-lo
                row["crps_sum"] += float(np.mean(np.abs(members[:, i]-actual))-spread_term[i])
        for target, label in [("minimum_c", "minimum"), ("cold_degree_hours", "cold_dose"), ("high_humidity_hours", "high_humidity"),
                              ("low_wind_hours", "low_wind"), ("cold_high_humidity_hours", "cold_high_humidity")]:
            if truth[target] is not None:
                estimate = forecast["aggregate_quantiles"][target]["median"]
                errors[label].append(abs(estimate-truth[target]))
                if target == "cold_degree_hours": errors["cold_dose_cold" if truth["cold_bins_hours"] else "cold_dose_warm"].append(abs(estimate-truth[target]))
        outcome = None
        if truth["cold_bins_hours"] is not None:
            complete_t += 1; actual_event = truth["cold_bins_hours"] > 0; event = forecast["warning"]["cold_bins_hours"] > 0
            outcome = "hits" if actual_event and event else "misses" if actual_event else "false_alarms" if event else "correct_negatives"
            counts[outcome] += 1
            errors["brier"].append((forecast["event_score"]-int(actual_event))**2)
            cell = calibration[min(4, int(forecast["event_score"]*5))]; cell["count"] += 1; cell["forecast_sum"] += forecast["event_score"]; cell["events"] += actual_event
            err = abs(forecast["warning"]["cold_bins_hours"]-truth["cold_bins_hours"])
            errors["cold_duration" if actual_event else "warm_duration"].append(err)
            errors["mask_cold" if actual_event else "mask_warm"].append(sum(a != b for a, b in zip(forecast["warning"]["cold_mask"], truth["cold_mask"])))
            if actual_event:
                onset = abs(forecast["warning"]["first_cold_index"]-truth["first_cold_index"]) if event else 14.
                errors["onset_with_miss"].append(onset)
                if event: errors["onset_hits"].append(onset)
        records.append({"date": n["date"], "outcome": outcome, "truth": truth, "point": point, "warning": forecast["warning"],
                        "event_score": forecast["event_score"], "minimum_prediction": forecast["aggregate_quantiles"]["minimum_c"]["median"]})
    avg = lambda key: float(np.mean(errors[key])) if errors[key] else None
    return {"complete_temperature_nights": complete_t, "excluded_temperature_nights": len(samples)-complete_t, **dict(counts),
            "cold_nights": counts["hits"]+counts["misses"], "mae": {t: avg(t) for t in TARGETS},
            "paired_hours": {t: len(errors[t]) for t in TARGETS},
            "minimum_mae_c": avg("minimum"), "cold_duration_mae_h": avg("cold_duration"), "warm_duration_mae_h": avg("warm_duration"),
            "onset_mae_hits_h": avg("onset_hits"), "onset_pairs": len(errors["onset_hits"]), "onset_with_miss_penalty_h": avg("onset_with_miss"),
            "cold_mask_mae_cold_h": avg("mask_cold"), "cold_degree_hours_mae": avg("cold_dose"), "cold_degree_hours_mae_cold": avg("cold_dose_cold"),
            "cold_degree_hours_mae_warm": avg("cold_dose_warm"), "high_humidity_hours_mae": avg("high_humidity"),
            "low_wind_hours_mae": avg("low_wind"), "cold_high_humidity_hours_mae": avg("cold_high_humidity"),
            "aggregate_pairs": {k: len(errors[k]) for k in ["minimum", "cold_dose", "high_humidity", "low_wind", "cold_high_humidity"]},
            "night_event_brier": avg("brier"),
            "intervals": {t: {"paired_hours": r["count"], "coverage_80": r["covered_80"]/r["count"] if r["count"] else None,
                                "mean_width": r["width_sum"]/r["count"] if r["count"] else None, "crps": r["crps_sum"]/r["count"] if r["count"] else None} for t, r in paired.items()},
            "reliability_bins": calibration, "night_records": records}


def selection_loss(score, p):
    cold, warm = score["cold_nights"], score["complete_temperature_nights"]-score["cold_nights"]
    if not cold or not warm: raise ValueError("Selection needs cold and warm validation cases")
    v = {"miss_rate": score["misses"]/cold, "false_alarm_rate": score["false_alarms"]/warm,
         "cold_duration_mae_over_14": score["cold_duration_mae_h"]/14,
         "balanced_cold_dose_mae_over_28": (score["cold_degree_hours_mae_cold"]+score["cold_degree_hours_mae_warm"])/56,
         "cold_onset_error_with_miss_penalty_over_14": score["onset_with_miss_penalty_h"]/14,
         "temperature_mae_over_3": score["mae"]["temperature"]/3, "humidity_mae_over_20": score["mae"]["humidity"]/20,
         "wind_mae_over_5": score["mae"]["wind"]/5, "high_humidity_duration_mae_over_14": score["high_humidity_hours_mae"]/14}
    return sum(p["selection_weights"][k]*value for k, value in v.items()), v


def save_model(model, directory):
    value = {k: v for k, v in model.items() if k != "reference"}
    if "reference" in model:
        for m in model["reference"].values(): save_hourly_model(m, directory/"reference")
        value["reference_sha256"] = {str(path.relative_to(directory)): sha(path) for path in sorted((directory/"reference").rglob("*")) if path.is_file()}
    write_json(directory/"model.json", value)
    return sha(directory/"model.json")


def load_model(directory, expected):
    if sha(directory/"model.json") != expected: raise ValueError("Scenario model hash mismatch")
    m = json.loads((directory/"model.json").read_text(encoding="utf-8"))
    if m["spec"]["kind"] == "previous_hourly":
        for path, expected_hash in m["reference_sha256"].items():
            if sha(directory/path) != expected_hash: raise ValueError("Scenario reference artifact hash mismatch")
        m["reference"] = {t: load_hourly_model(directory/"reference", t, name) for t, name in [("temperature", "ridge_temperature"), ("humidity", "catboost_weather")]}
    return m


def select_scenarios(protocol_path, data_dir):
    p = load_scenario_protocol(protocol_path); directory = data_dir.parent/"models/research-scenarios"
    path = directory/"selection-before-history.json"
    source_files = ["scenario_study.py", "hourly_study.py", "single_run.py", "gfs_covariates.py", "model_study.py"]
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old["protocol_sha256"] != sha(protocol_path): raise ValueError("Frozen scenario protocol changed")
        # Verify prior-season inputs too; never refit from modified labels under an existing selection.
        _, provenance = load_rows(data_dir, [2022, 2023, 2024])
        if old["input_provenance"] != provenance: raise ValueError("Frozen scenario inputs changed")
        for name, h in old["model_artifact_sha256"].items(): load_model(directory/name, h)
        return old
    write_json(directory/"protocol-lock.json", {"protocol": p, "protocol_sha256": sha(protocol_path), "saved_at_utc": datetime.now(UTC).isoformat()})
    rows, provenance = load_rows(data_dir, [2022, 2023, 2024]); pooled_samples = []; pooled_predictions = defaultdict(list); folds = []
    for fold in p["folds"]:
        print(f"Scenario validation: {fold['train_years']} -> {fold['validation_year']}", flush=True)
        train = [r for r in rows if r["year"] in fold["train_years"]]; valid = nights([r for r in rows if r["year"] == fold["validation_year"]])
        scores = {}
        for spec in p["candidates"]:
            model = fit_candidate(spec, train, p); pred = [predict_candidate(model, n, p) for n in valid]
            score = score_predictions(valid, pred, p); score.pop("night_records")
            scores[spec["name"]] = score; pooled_predictions[spec["name"]].extend(pred)
        pooled_samples.extend(valid); folds.append({**fold, "scores": scores})
    pooled = {}
    for spec in p["candidates"]:
        score = score_predictions(pooled_samples, pooled_predictions[spec["name"]], p); score.pop("night_records")
        score["selection_loss"], score["selection_components"] = selection_loss(score, p); pooled[spec["name"]] = score
    names = [s["name"] for s in p["candidates"]]
    winner = min(names, key=lambda name: (pooled[name]["selection_loss"], names.index(name)))
    point_winners = {t: min(names, key=lambda name: (pooled[name]["mae"][t], names.index(name))) for t in TARGETS}
    train = [r for r in rows if r["year"] in p["final_train_years"]]; hashes = {}; training_audit = {}
    for spec in p["candidates"]:
        model = fit_candidate(spec, train, p); hashes[spec["name"]] = save_model(model, directory/spec["name"])
        training_audit[spec["name"]] = {k: model[k] for k in ["training_years", "last_training_time", "complete_library_nights", "excluded_joint_nights"] if k in model}
    selected = {"protocol": p, "protocol_sha256": sha(protocol_path), "selection_saved_at_utc": datetime.now(UTC).isoformat(),
                "observations_loaded_years": [2022, 2023, 2024], "selected_decision_model": winner, "selected_point_models": point_winners,
                "folds": folds, "pooled_validation": pooled, "input_provenance": provenance, "training_audit": training_audit,
                "model_artifact_sha256": hashes, "source_sha256": {f: sha(ROOT/"agro_checker"/f) for f in source_files}}
    write_json(path, selected)
    return selected


# Post-selection reporting helpers. The preceding learner/selector bytes remain unchanged.
def point_bundle(predictions, selected, p):
    point = {t: list(predictions[selected[t]]["point"][t]) for t in TARGETS}
    value = derived(point, p); event = float(value["cold_bins_hours"] > 0)
    return {"point": point, "warning_curve": point["temperature"], "warning": value, "event_score": event,
            "members": [point], "member_count": 1, "analog_dates": [],
            "intervals_80": {t: {"p10": point[t], "p90": point[t]} for t in TARGETS},
            "aggregate_quantiles": {k: {"p10": value[k], "median": value[k], "p90": value[k]} for k in ["minimum_c", "cold_degree_hours", "high_humidity_hours", "low_wind_hours", "cold_high_humidity_hours"]}}


def paired_uncertainty(samples, baseline, corrected, settings):
    output = {}
    for target in TARGETS:
        result = {}
        for width in settings["block_days"]:
            groups = defaultdict(list)
            for n, b, c in zip(samples, baseline, corrected):
                day = datetime.fromisoformat(n["date"]); origin = datetime(day.year, 3, 1)
                key = (day.year, (day-origin).days//width)
                for i, y in enumerate(n["target"][target]):
                    if y is not None: groups[key].append(abs(b["point"][target][i]-y)-abs(c["point"][target][i]-y))
            values = list(groups.values())
            if not values: result[str(width)] = {"blocks": 0, "improvement": None, "interval_95": None}; continue
            totals = np.array([sum(v) for v in values]); sizes = np.array([len(v) for v in values])
            rng = np.random.default_rng(settings["seed"]); draws = rng.integers(0, len(values), (settings["replicates"], len(values)))
            estimates = totals[draws].sum(axis=1)/sizes[draws].sum(axis=1)
            result[str(width)] = {"blocks": len(values), "replicates": settings["replicates"], "paired_hours": int(sum(sizes)),
                                  "improvement": float(sum(totals)/sum(sizes)), "interval_95": np.quantile(estimates, [.025, .975]).tolist()}
        output[target] = result
    return output


def evaluate_year(rows, models, selection, p):
    samples = nights(rows); predictions = {name: [predict_candidate(model, n, p) for n in samples] for name, model in models.items()}
    predictions["selected_points"] = [point_bundle({name: fore[i] for name, fore in predictions.items()}, selection["selected_point_models"], p) for i in range(len(samples))]
    scores = {name: score_predictions(samples, fore, p) for name, fore in predictions.items()}
    for s in scores.values(): s.pop("night_records")
    decision = selection["selected_decision_model"]
    from .frost_study import predict_cold, score_cold
    directory = ROOT/"models/research-frost"; previous = json.loads((directory/"selection-before-2025.json").read_text(encoding="utf-8"))
    artifact = directory/"event_duration_weather/model.json"
    if sha(artifact) != previous["model_artifact_sha256"]["event_duration_weather"]: raise ValueError("Previous detector hash mismatch")
    detector = json.loads(artifact.read_text(encoding="utf-8")); masks = predict_cold(detector, rows, .25)
    if scores["gfs"]["cold_nights"]:
        old_score = score_cold(rows, masks); old_score.pop("nights")
    else:
        from .hourly_study import frost_score
        old_score = frost_score(rows, [-1. if b else 1. for m in masks for b in m["cold_mask"]]); old_score.pop("nights")
    common = []
    for i, n in enumerate(samples):
        actual = derived(n["target"], p)
        if actual["cold_bins_hours"] is None or not actual["cold_bins_hours"]: continue
        if predictions["gfs"][i]["warning"]["cold_bins_hours"] and predictions[decision][i]["warning"]["cold_bins_hours"]:
            common.append({"date": n["date"], "gfs_onset_error_h": abs(predictions["gfs"][i]["warning"]["first_cold_index"]-actual["first_cold_index"]),
                           "selected_onset_error_h": abs(predictions[decision][i]["warning"]["first_cold_index"]-actual["first_cold_index"]),
                           "left_censored": actual["left_censored"]})
    frames = []
    for i, n in enumerate(samples):
        stamp = [r["valid_at_utc"] for r in n["rows"]]
        frames.append({"date": n["date"], "year": n["year"], "forecast_run_at_utc": n["rows"][0]["forecast_run_at_utc"],
                       "issued_at_utc": n["rows"][0]["issued_at_utc"], "timestamps": stamp,
                       "baseline": predictions["gfs"][i]["point"], "selected_points": predictions["selected_points"][i]["point"],
                       "decision_model": decision, "decision_forecast": {k: v for k, v in predictions[decision][i].items() if k != "members"},
                       "previous_detector_cold_mask": masks[i]["cold_mask"],
                       "hindsight": n["target"], "target_sources": [r["source"] for r in n["rows"]],
                       "hindsight_derived": derived(n["target"], p), "point_derived": derived(predictions["selected_points"][i]["point"], p),
                       "role": "historical previously viewed" if n["year"] == p["historical_year"] else "new season after frozen choice"})
    return {"scores": scores, "previous_detector": old_score, "common_onset_pairs": common,
            "paired_bootstrap_point_vs_gfs": paired_uncertainty(samples, predictions["gfs"], predictions["selected_points"], p["bootstrap"]),
            "complete_target_nights": {t: sum(all(v is not None for v in n["target"][t]) for n in samples) for t in TARGETS}}, frames


def run_scenario_study(protocol_path, data_dir, report_dir, include_fresh=False):
    selection = select_scenarios(protocol_path, data_dir); p = selection["protocol"]
    source = (ROOT/"agro_checker/scenario_study.py").read_bytes()
    prefix = source.split(b"\n\n# Post-selection reporting helpers.")[0]
    # Legacy choice was saved before report helpers existed; later choices record the full file.
    learner_matches = selection["source_sha256"]["scenario_study.py"] in [hashlib.sha256(source).hexdigest(), hashlib.sha256(prefix).hexdigest()]
    if not learner_matches: raise ValueError("Scenario learner/selector changed after frozen selection")
    directory = data_dir.parent/"models/research-scenarios"; choice = directory/"selection-before-history.json"
    models = {name: load_model(directory/name, h) for name, h in selection["model_artifact_sha256"].items()}
    rows, provenance = load_rows(data_dir, [p["historical_year"]])
    historical, frames = evaluate_year(rows, models, selection, p)
    result = {"protocol": p, "selection": selection, "selection_artifact_sha256": sha(choice),
              "generated_at_utc": datetime.now(UTC).isoformat(), "historical": historical, "historical_provenance": provenance,
              "fresh_status": "not requested", "fresh": None, "deployment": "research only; field GFS unchanged"}
    if include_fresh:
        fresh_dir = data_dir/"scenario-2026"
        try:
            marker = json.loads((fresh_dir/"new-season-open.json").read_text(encoding="utf-8"))
            if marker["selection_sha256"] != sha(choice): raise ValueError("Fresh selection boundary mismatch")
            config = json.loads((fresh_dir/"source-config.json").read_text(encoding="utf-8"))
            fresh_rows, fresh_provenance = load_rows(fresh_dir, [p["fresh_year"]], config)
            as_downloaded, _ = evaluate_year(fresh_rows, models, selection, p)
            audit_path = fresh_dir/"metar-cross-check.json"
            if not audit_path.exists(): raise ValueError("Run primary METAR wind cross-check before publishing fresh wind metrics")
            from .scenario_observation_audit import audit_fresh_metar
            # Reconstruct labels from hash-verified primary bytes, never trust an editable audit JSON.
            audit = audit_fresh_metar(fresh_dir, offline=True)
            primary_wind = audit["primary_hourly_wind_targets"]
            for row in fresh_rows:
                row["target"]["wind"] = primary_wind.get(row["valid_at_utc"])
                row["source"]["wind"] = "raw_METAR_MPS_IEM" if row["target"]["wind"] is not None else "missing_primary_METAR"
                row["wind_target_status"] = "primary_crosschecked" if row["target"]["wind"] is not None else "missing"
            result["fresh"], fresh_frames = evaluate_year(fresh_rows, models, selection, p)
            result["fresh_as_downloaded_meteostat"] = as_downloaded
            result["fresh_wind_crosscheck"] = {k: v for k, v in audit.items() if k not in ["comparisons", "primary_hourly_wind_targets"]}
            result["fresh_wind_audit_sha256"] = sha(audit_path)
            result["fresh_wind_target_note"] = "After source-scale discrepancy diagnosis, verify frozen models on raw METAR wind at exact target hours; T/RH unchanged. Preserve initial metrics separately. No model refit or selection change. This QC is not another unseen test."
            result.update(fresh_status="evaluated", fresh_provenance=fresh_provenance, fresh_boundary=marker)
            frames.extend(fresh_frames)
        except (ValueError, OSError, KeyError) as exc:
            result.update(fresh_status="unavailable", fresh_error=str(exc))
    # Check again after evaluation: these files are never rewritten by the study.
    for name, h in selection["model_artifact_sha256"].items(): load_model(directory/name, h)
    result["execution_source_sha256"] = sha(ROOT/"agro_checker/scenario_study.py")
    result["learner_source_unchanged_since_selection"] = learner_matches
    output = data_dir/"processed/scenario-study"
    write_json(output/"night-forecasts.json", frames)
    result["forecast_artifact_sha256"] = sha(output/"night-forecasts.json")
    write_json(report_dir/"SCENARIO_STUDY_SUMMARY.json", result)
    return result


def replay_scenario(data_dir, evening):
    path = data_dir/"processed/scenario-study/night-forecasts.json"
    summary = json.loads((ROOT/"docs/SCENARIO_STUDY_SUMMARY.json").read_text(encoding="utf-8"))
    if sha(path) != summary["forecast_artifact_sha256"]: raise ValueError("Scenario forecast snapshot hash mismatch")
    frame = next((n for n in json.loads(path.read_text(encoding="utf-8")) if n["date"] == evening), None)
    if frame is None: raise ValueError("Scenario night not found")
    return {"site": "public Krasnodar station 34929, not orchard", "date": frame["date"], "role": frame["role"],
            "forecast": {k: v for k, v in frame.items() if not k.startswith("hindsight") and k != "target_sources"},
            "hindsight": {"observations": frame["hindsight"], "derived": frame["hindsight_derived"], "sources": frame["target_sources"]},
            "warning_score": "uncalibrated member fraction, not crop damage probability", "selection_sha256": summary["selection_artifact_sha256"]}
