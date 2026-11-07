"""Prior-year LOO interval selection with immutable minimum and cold detector."""

from datetime import datetime
import json
from collections import Counter
import numpy as np

from .cold_interval import calibrate_envelope, forecast_envelope, residual_pool
from .minimum_model import fit_minimum, target_minimum
from .pipeline import ROOT, UTC, write_json
from .scenario_study import load_rows, nights, sha


def load_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if p["schema_version"] != 1 or p["folds"] != [{"train_years": [2022], "validation_year": 2023}, {"train_years": [2022, 2023], "validation_year": 2024}]:
        raise ValueError("Invalid cold-envelope chronology")
    if p["final_calibration_year"] != 2024 or p["evaluation_years"] != [2025, 2026] or p["base_coverage"] != .9 or p["minimum_pool_nights"] != 30:
        raise ValueError("Invalid calibration settings")
    if [s["name"] for s in p["candidates"]] != ["legacy_symmetric", "global_symmetric_95", "risk_2_symmetric_95", "risk_5_symmetric_95", "risk_5_symmetric_975", "risk_5_lower_95", "risk_5_signed_95"]:
        raise ValueError("Invalid fixed interval registry")
    return p


def parent_assets(data_dir):
    models = data_dir.parent/"models"
    selection_path = models/"research-minimum/selection-before-controls.json"
    parent = json.loads(selection_path.read_text(encoding="utf-8"))
    if parent["winner"] != "huber_1": raise ValueError("Expected frozen Huber head")
    point_path = models/"research-minimum/huber_1.json"
    if sha(point_path) != parent["model_artifact_sha256"]["huber_1"]: raise ValueError("Frozen point head changed")
    detector_selection = models/"research-frost/selection-before-2025.json"
    detector_parent = json.loads(detector_selection.read_text(encoding="utf-8"))
    detector_path = models/"research-frost/event_duration_weather/model.json"
    if sha(detector_path) != detector_parent["model_artifact_sha256"]["event_duration_weather"]: raise ValueError("Frozen detector changed")
    return {"point_model": json.loads(point_path.read_text(encoding="utf-8")),
            "detector": json.loads(detector_path.read_text(encoding="utf-8")),
            "hashes": {"point_model": sha(point_path), "point_selection": sha(selection_path),
                       "detector": sha(detector_path), "detector_selection": sha(detector_selection)}}


def interval_score(f, actual):
    width = f["upper_c"]-f["lower_c"]
    return width+20*max(0., f["lower_c"]-actual, actual-f["upper_c"])


def summarize(records):
    scored = [r for r in records if r["actual_minimum_c"] is not None]
    cold = [r for r in scored if r["actual_minimum_c"] < 0]
    warm = [r for r in scored if r["actual_minimum_c"] >= 0]
    mean = lambda group, fn: float(np.mean([fn(r) for r in group])) if group else None
    covered = lambda r: r["forecast"]["lower_c"] <= r["actual_minimum_c"] <= r["forecast"]["upper_c"]
    width = lambda r: r["forecast"]["upper_c"]-r["forecast"]["lower_c"]
    iscore = lambda r: interval_score(r["forecast"], r["actual_minimum_c"])
    lower_miss = lambda r: r["actual_minimum_c"] < r["forecast"]["lower_c"]
    return {"nights": len(scored), "excluded_nights": len(records)-len(scored), "cold_nights": len(cold),
            "covered": sum(covered(r) for r in scored), "cold_covered": sum(covered(r) for r in cold),
            "coverage": mean(scored, covered), "cold_coverage": mean(cold, covered),
            "lower_cold_misses": sum(lower_miss(r) for r in cold),
            "upper_cold_misses": sum(r["actual_minimum_c"] > r["forecast"]["upper_c"] for r in cold),
            "mae_c": mean(scored, lambda r: abs(r["forecast"]["point_c"]-r["actual_minimum_c"])),
            "mean_width_c": mean(scored, width), "cold_mean_width_c": mean(cold, width),
            "warm_mean_width_c": mean(warm, width), "interval_score": mean(scored, iscore),
            "cold_interval_score": mean(cold, iscore), "warm_interval_score": mean(warm, iscore),
            "crosses_zero_nights": sum(r["forecast"]["lower_c"] < 0 <= r["forecast"]["upper_c"] for r in scored),
            "cold_fully_warm_range": sum(r["forecast"]["lower_c"] >= 0 for r in cold),
            "fallback_nights": sum(r["forecast"].get("calibration_fallback", False) for r in scored),
            "extrapolation_nights": sum(r["forecast"].get("extrapolation", False) for r in scored)}


def validation_key(score):
    if not score["cold_nights"] or score["cold_interval_score"] is None or score["warm_interval_score"] is None:
        raise ValueError("Selection needs cold and warm validation nights")
    return ((score["cold_nights"]-score["cold_covered"])/score["cold_nights"],
            (score["cold_interval_score"]+score["warm_interval_score"])/2, score["mean_width_c"])


def select_intervals(path, data_dir):
    p = load_protocol(path); directory = data_dir.parent/"models/research-cold-intervals"
    selection_path = directory/"selection-before-controls.json"
    parent = parent_assets(data_dir)
    sources = {name: sha(ROOT/"agro_checker"/name) for name in [
        "cold_interval.py", "cold_interval_study.py", "minimum_model.py", "scenario_study.py", "frost_study.py"]}
    if selection_path.exists():
        old = json.loads(selection_path.read_text(encoding="utf-8"))
        if old["protocol_sha256"] != sha(path) or old["source_sha256"] != sources or old["parent_hashes"] != parent["hashes"]:
            raise ValueError("Frozen interval protocol/source/parent changed")
        if sha(directory/"envelope.json") != old["envelope_sha256"]: raise ValueError("Envelope hash mismatch")
        _, provenance = load_rows(data_dir, [2022, 2023, 2024])
        if old["input_provenance"] != provenance: raise ValueError("Frozen interval inputs changed")
        return old
    write_json(directory/"protocol-lock.json", {"protocol": p, "protocol_sha256": sha(path),
               "source_sha256": sources, "saved_at_utc": datetime.now(UTC).isoformat()})
    rows, provenance = load_rows(data_dir, [2022, 2023, 2024]); samples = nights(rows)
    records = {s["name"]: [] for s in p["candidates"]}; fold_reports = []
    for fold in p["folds"]:
        training = [n for n in samples if n["year"] in fold["train_years"]]
        valid = [n for n in samples if n["year"] == fold["validation_year"]]
        model = fit_minimum(parent["point_model"]["spec"], training)
        pool = residual_pool(model, valid, [fold["validation_year"]])
        fold_records = {s["name"]: [] for s in p["candidates"]}
        for n in valid:
            if target_minimum(n) is None: continue
            for spec in p["candidates"]:
                envelope = calibrate_envelope(pool, spec, p, exclude_date=n["date"])
                forecast = forecast_envelope(model, envelope, n, p)
                r = {"date": n["date"], "forecast": forecast, "actual_minimum_c": target_minimum(n)}
                records[spec["name"]].append(r); fold_records[spec["name"]].append(r)
        fold_reports.append({**fold, "calibration_available_nights": len(pool),
                             "scores": {name: summarize(r) for name, r in fold_records.items()}})
    scores = {name: summarize(r) for name, r in records.items()}
    winner = min(p["candidates"], key=lambda spec: validation_key(scores[spec["name"]]))["name"]
    spec = next(s for s in p["candidates"] if s["name"] == winner)
    final_samples = [n for n in samples if n["year"] == p["final_calibration_year"]]
    pool = residual_pool(parent["point_model"], final_samples, [2024])
    envelope = calibrate_envelope(pool, spec, p)
    if envelope["base_radius_c"] != parent["point_model"]["calibration"]["radius_c"]:
        raise ValueError("Legacy envelope mismatch; preserve original baseline")
    write_json(directory/"envelope.json", envelope)
    result = {"protocol": p, "protocol_sha256": sha(path), "source_sha256": sources,
              "selection_saved_at_utc": datetime.now(UTC).isoformat(), "winner": winner,
              "observations_loaded_years": [2022, 2023, 2024], "parent_hashes": parent["hashes"],
              "folds": fold_reports, "pooled_validation": scores, "input_provenance": provenance,
              "envelope_sha256": sha(directory/"envelope.json")}
    write_json(selection_path, result)
    return result


def run_interval_study(path, data_dir, report_dir):
    selection = select_intervals(path, data_dir); p = selection["protocol"]; parent = parent_assets(data_dir)
    directory = data_dir.parent/"models/research-cold-intervals"
    envelope = json.loads((directory/"envelope.json").read_text(encoding="utf-8"))
    # Reference controls are opened only after frozen interval selection.
    old_summary = json.loads((report_dir/"MINIMUM_STUDY_SUMMARY.json").read_text(encoding="utf-8"))
    old_path = data_dir/"processed/minimum-study/night-forecasts.json"
    if sha(old_path) != old_summary["forecast_sha256"]: raise ValueError("Original forecast snapshot changed")
    reference = {r["date"]: r for r in json.loads(old_path.read_text(encoding="utf-8"))}
    scenario_summary = json.loads((report_dir/"SCENARIO_STUDY_SUMMARY.json").read_text(encoding="utf-8"))
    scenario_path = data_dir/"processed/scenario-study/night-forecasts.json"
    if sha(scenario_path) != scenario_summary["forecast_artifact_sha256"]: raise ValueError("Original cold-mask snapshot changed")
    reference_masks = {r["date"]: r["previous_detector_cold_mask"] for r in json.loads(scenario_path.read_text(encoding="utf-8"))}
    from .frost_study import predict_cold
    seasons = {}; frames = []
    for year in p["evaluation_years"]:
        if year == 2025: rows, provenance = load_rows(data_dir, [year])
        else:
            fresh = data_dir/"scenario-2026"
            rows, provenance = load_rows(fresh, [year], json.loads((fresh/"source-config.json").read_text(encoding="utf-8")))
        samples = nights(rows); masks = predict_cold(parent["detector"], rows, .25)
        records = {"gfs": [], "legacy_huber": [], "cold_aware": []}; counts = Counter()
        for n, mask in zip(samples, masks):
            previous = reference[n["date"]]
            forecast = forecast_envelope(parent["point_model"], envelope, n, p)
            if forecast["point_c"] != previous["selected"]["point_c"]: raise ValueError("Point prediction changed")
            if forecast["legacy_lower_c"] != previous["selected"]["lower_c"] or forecast["legacy_upper_c"] != previous["selected"]["upper_c"]:
                raise ValueError("Legacy interval changed")
            if mask["cold_mask"] != reference_masks[n["date"]] or mask["frost"] != previous["previous_detector"]["frost"]:
                raise ValueError("Detector mask/timing changed")
            if forecast["lower_c"] > previous["selected"]["lower_c"] or forecast["upper_c"] < previous["selected"]["upper_c"]:
                raise ValueError("New interval does not contain legacy interval")
            actual = target_minimum(n)
            if actual is not None:
                event = any(mask["cold_mask"]); cold = actual < 0
                counts["hits" if cold and event else "misses" if cold else "false_alarms" if event else "correct_negatives"] += 1
            for name, f in [("gfs", previous["gfs"]), ("legacy_huber", previous["selected"]), ("cold_aware", forecast)]:
                records[name].append({"date": n["date"], "forecast": f, "actual_minimum_c": actual})
            frames.append({**previous, "legacy": previous["selected"], "selected": forecast, "interval_method": selection["winner"],
                           "cold_mask": mask["cold_mask"], "detector_unchanged": True})
        seasons[str(year)] = {"scores": {name: summarize(r) for name, r in records.items()},
                             "detector": dict(counts), "detector_masks_verified": len(samples), "input_provenance": provenance}
    if parent_assets(data_dir)["hashes"] != selection["parent_hashes"] or sha(directory/"envelope.json") != selection["envelope_sha256"]:
        raise ValueError("Frozen parent/envelope changed during evaluation")
    result = {"selection": selection, "selection_sha256": sha(directory/"selection-before-controls.json"),
              "envelope": envelope, "seasons": seasons, "generated_at_utc": datetime.now(UTC).isoformat(),
              "legacy_forecast_sha256": sha(old_path), "legacy_mask_sha256": sha(scenario_path),
              "deployment": p["deployment"]}
    output = data_dir/"processed/cold-interval-study/night-forecasts.json"; write_json(output, frames)
    result["forecast_sha256"] = sha(output)
    write_json(report_dir/"COLD_INTERVAL_SUMMARY.json", result)
    return result
