"""Episode caps and short-gap priors, selected by forward seasons with an unchanged detector."""

from collections import Counter
from datetime import date, datetime, timedelta
from functools import lru_cache
import hashlib
import json

from .episode_study import (DECODE_WEIGHTS, REFERENCE, SELECT_WEIGHTS, assert_same_alerts, expected_sequence_loss, fit_joint,
                            load_episode_protocol, mask_space, pool_episode_scores, predict_joint, score_episodes,
                            sequence_features, validate_joint_model)
from .frost_study import evaluate_head, fit_cold, load_frost_protocol, load_rows, night_samples, paired_uncertainty, predict_cold, sha, validate_saved_model
from .hourly_study import compare_timing_cohorts, timed_frost
from .model_study import load_protocol
from .pipeline import ROOT, UTC, write_csv, write_json

SOURCE_JOINT = {"name": "joint_l2_10_minimum_expected_loss", "kind": "joint", "regularization": 10., "decoder": "minimum_expected_loss"}
GAP_COST = {"internal_warm_gap_1h": 1., "internal_warm_gap_2h": .5, "other_gaps": 0.}


def load_limits_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if (p["schema_version"] != 1 or p["fixed_detector"] != REFERENCE or p["source_joint_candidate"] != SOURCE_JOINT
            or p["max_episodes"] != [1, 2] or p["gap_penalties"] != [0., 2., 5.] or p["short_gap_cost"] != GAP_COST
            or p["decoder"] != "minimum_expected_loss" or p["decoder_loss_weights"] != DECODE_WEIGHTS
            or p["selection_weights"] != SELECT_WEIGHTS or p["weather_threshold_c"] != 0
            or p["source_config"] != "single_run.json" or p["base_model_protocol"] != "model_study.json"
            or p["episode_protocol"] != "episode_study.json" or p["detector_protocol"] != "frost_study.json"):
        raise ValueError("Unexpected fixed episode-limits protocol")
    return p


def candidates(p):
    return [{"name": "previous_detector_duration", "kind": "detector_reference"},
            {"name": "unlimited_joint", "kind": "joint_reference"}]+[
        {"name": f"cap_{cap}_gap_{penalty:g}", "kind": "limited", "max_episodes": cap, "gap_penalty": penalty}
        for cap in p["max_episodes"] for penalty in (p["gap_penalties"] if cap > 1 else [0.])]


def mask_shape(mask):
    if len(mask) != 14 or any(v not in [0, 1] for v in mask):
        raise ValueError("Expected a 14-hour binary cold mask")
    runs = []
    for i, v in enumerate(mask):
        if v:
            if not runs or runs[-1][1] != i: runs.append([i, i+1])
            else: runs[-1][1] = i+1
    gaps = [right[0]-left[1] for left, right in zip(runs, runs[1:])]
    return {"episodes": len(runs), "cold_bins_h": int(sum(mask)), "internal_warm_gaps_h": gaps,
            "short_gap_cost": sum(1. if n == 1 else .5 if n == 2 else 0. for n in gaps),
            "short_gap_count": sum(n <= 2 for n in gaps)}


@lru_cache(maxsize=1)
def shape_space():
    import numpy as np
    values = [mask_shape(mask) for mask in mask_space()["masks"]]
    result = {"short_gap_cost": np.array([v["short_gap_cost"] for v in values]),
              "short_gap_count": np.array([v["short_gap_count"] for v in values])}
    for a in result.values(): a.flags.writeable = False
    return result


def limited_distribution(features, weights, spec):
    import numpy as np
    from scipy.special import logsumexp
    if spec["max_episodes"] not in [1, 2] or spec["gap_penalty"] not in [0., 2., 5.]:
        raise ValueError("Unknown fixed episode cap/gap penalty")
    energies = features@np.asarray(weights)
    if energies.shape != (16383,) or not np.isfinite(energies).all():
        raise ValueError("Nonfinite/invalid limited energies")
    allowed = mask_space()["episodes"] <= spec["max_episodes"]
    # The prior is applied to the whole sequence before normalization; no observed gap is filled.
    energies = np.where(allowed, energies-spec["gap_penalty"]*shape_space()["short_gap_cost"], -np.inf)
    return np.exp(energies-logsumexp(energies))


def decode_limited(probabilities, spec, p):
    import numpy as np
    risk = expected_sequence_loss(probabilities, p["decoder_loss_weights"])
    allowed = mask_space()["episodes"] <= spec["max_episodes"]
    if np.any(probabilities[~allowed] != 0.): raise ValueError("Posterior violates episode cap")
    index = int(np.argmin(np.where(allowed, risk, np.inf)))
    return [bool(v) for v in mask_space()["masks"][index]]


def predict_limited(model, detector, rows, spec, p):
    from threadpoolctl import threadpool_limits
    predictions = []
    with threadpool_limits(limits=2):
        for s in night_samples(rows):
            event_score = evaluate_head(detector["event_head"], s["night_features"])
            probabilities = limited_distribution(sequence_features(s, model), model["weights"], spec)
            marginal = probabilities@mask_space()["masks"]
            mask = decode_limited(probabilities, spec, p) if event_score >= p["fixed_detector"]["threshold"] else [False]*14
            timing = timed_frost([-1. if c else 1. for c in mask], [r["valid_at_utc"] for r in s["rows"]])
            predictions.append({"date": s["date"], "cold_mask": mask, "cold_hour_model_scores": (event_score*marginal).tolist(),
                                "night_event_model_score": event_score, "frost": timing,
                                "conditional_expected_cold_bins_h": float(probabilities@mask_space()["count"]),
                                "conditional_expected_episodes": float(probabilities@mask_space()["episodes"]),
                                "conditional_expected_short_gap_cost": float(probabilities@shape_space()["short_gap_cost"]),
                                "predicted_shape": mask_shape(mask)})
    return predictions


def score_limits(rows, predictions):
    score = score_episodes(rows, predictions)
    by_date = {p["date"]: p for p in predictions}
    stats = []
    for n in score["nights"]:
        predicted = mask_shape(by_date[n["date"]]["cold_mask"])
        stats.append({"date": n["date"], "actual_event": n["observed"]["event"], **predicted})
    cold = [n for n in stats if n["actual_event"]]
    score["predicted_short_gap_count_total"] = sum(n["short_gap_count"] for n in stats)
    score["predicted_short_gap_cost_cold_mean"] = sum(n["short_gap_cost"] for n in cold)/len(cold)
    score["predicted_max_episodes"] = max(n["episodes"] for n in stats)
    return score


def target_audit(rows):
    result = {}
    for year in sorted({r["year"] for r in rows}):
        samples = [s for s in night_samples(rows) if s["year"] == year]
        cold = [s for s in samples if s["complete"] and s["duration"] > 0]
        counts = Counter(mask_shape(s["cold_mask"])["episodes"] for s in cold)
        result[str(year)] = {"forecast_nights": len(samples), "complete_nights": sum(s["complete"] for s in samples),
                             "observed_cold_nights": len(cold), "observed_episode_counts": {str(k): v for k, v in sorted(counts.items())},
                             "observed_nights_exceeding_cap": {str(cap): sum(mask_shape(s["cold_mask"])["episodes"] > cap for s in cold) for cap in [1, 2]},
                             "policy": "no_observed_mask_modified_or_excluded_for_exceeding_cap"}
    return result


def prediction_registry(model, detector, rows, p, jp):
    predictions = {"previous_detector_duration": predict_cold(detector, rows, REFERENCE["threshold"]),
                   "unlimited_joint": predict_joint(model, detector, rows, SOURCE_JOINT["decoder"], jp),
                   "gfs": predict_cold({"family": "gfs"}, rows, None)}
    reference = predictions["previous_detector_duration"]
    assert_same_alerts(reference, predictions["unlimited_joint"])
    for spec in candidates(p):
        if spec["kind"] == "limited":
            predictions[spec["name"]] = predict_limited(model, detector, rows, spec, p)
            assert_same_alerts(reference, predictions[spec["name"]])
    return predictions


def validate_limits(rows, p, m, dp, jp):
    if any(r["year"] >= m["historical_check_year"] for r in rows):
        raise ValueError("2025 rows cannot enter episode-limits selection")
    folds = []
    for fold in m["folds"]:
        print(f"Episode limits: {fold['train_years']} -> {fold['validation_year']}", flush=True)
        train = [r for r in rows if r["year"] in fold["train_years"]]
        valid = [r for r in rows if r["year"] == fold["validation_year"]]
        detector = fit_cold(REFERENCE["family"], train, dp, m)
        model = fit_joint(train, SOURCE_JOINT["regularization"], jp, m)
        predictions = prediction_registry(model, detector, valid, p, jp)
        folds.append({**fold, "scores": {n: score_limits(valid, ps) for n, ps in predictions.items()},
                      "training_cold_nights": model["training_cold_nights"], "optimizer": model["optimizer"]})
    specs = candidates(p)
    pooled = {s["name"]: pool_episode_scores([f["scores"][s["name"]] for f in folds]) for s in specs}
    winner = min(specs, key=lambda s: (pooled[s["name"]]["episode_selection_loss"], specs.index(s)))
    return folds, winner, pooled


def parent_models(data_dir, p, m, jp):
    directory = data_dir.parent / "models/research-episodes"
    path = directory / "selection-before-2025.json"
    selection = json.loads(path.read_text(encoding="utf-8"))
    if selection["selected_candidate"] != SOURCE_JOINT or selection["protocol"] != jp or selection["model_protocol"] != m:
        raise ValueError("Previous joint selection/protocol mismatch")
    models = {}
    for label, name in [("joint", "l2_10"), ("detector", "detector")]:
        file = directory / name / "model.json"
        if sha(file) != selection["model_artifact_sha256"][name]: raise ValueError("Previous joint/detector hash mismatch")
        model = json.loads(file.read_text(encoding="utf-8"))
        models[label] = validate_joint_model(model) if label == "joint" else validate_saved_model(model)
    return models, sha(path)


def select_limits(protocol_path, data_dir):
    p = load_limits_protocol(protocol_path)
    m = load_protocol(ROOT / "config" / p["base_model_protocol"])
    jp = load_episode_protocol(ROOT / "config" / p["episode_protocol"])
    dp = load_frost_protocol(ROOT / "config" / p["detector_protocol"])
    parent, parent_sha = parent_models(data_dir, p, m, jp)
    years = sorted({y for f in m["folds"] for y in f["train_years"]+[f["validation_year"]]})
    rows, provenance = load_rows(data_dir, years)
    directory = data_dir.parent / "models/research-episode-limits"
    path = directory / "selection-before-2025.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if old and (old["protocol_sha256"] != sha(protocol_path) or old["parent_selection_sha256"] != parent_sha
                or old["input_provenance"] != provenance or old["model_protocol"] != m or old["episode_protocol"] != jp or old["detector_protocol"] != dp):
        raise ValueError("Frozen episode-limits protocol/source changed; no historical tuning")
    write_json(directory / "protocol-lock.json", {"protocol": p, "protocol_sha256": sha(protocol_path),
                                                "written_at_utc": datetime.now(UTC).isoformat(), "observations_loaded_years": years})
    folds, winner, pooled = validate_limits(rows, p, m, dp, jp)
    train = [r for r in rows if r["year"] in m["final_train_years"]]
    models = {"joint": fit_joint(train, SOURCE_JOINT["regularization"], jp, m), "detector": fit_cold(REFERENCE["family"], train, dp, m)}
    if models != parent: raise ValueError("Unchanged final joint/detector fit differs from parent frozen models")
    hashes = {n: hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest() for n, model in models.items()}
    if old:
        if old["selected_candidate"] != winner or old["pooled_validation"] != pooled or old["model_content_sha256"] != hashes:
            raise ValueError("Frozen episode-limits selection/weights changed; no historical tuning")
        return old
    for n, model in models.items(): write_json(directory / n / "model.json", model)
    selected = {"protocol": p, "protocol_sha256": sha(protocol_path), "model_protocol": m, "episode_protocol": jp, "detector_protocol": dp,
                "selection_saved_at_utc": datetime.now(UTC).isoformat(), "observations_loaded_years": years, "parent_selection_sha256": parent_sha,
                "selected_candidate": winner, "folds": folds, "pooled_validation": pooled, "input_provenance": provenance,
                "development_target_audit": target_audit(rows), "model_content_sha256": hashes,
                "model_artifact_sha256": {n: sha(directory / n / "model.json") for n in models},
                "source_sha256": {f: sha(ROOT / "agro_checker" / f) for f in ["episode_limits.py", "episode_study.py", "frost_study.py", "hourly_study.py", "single_run.py", "model_study.py"]}}
    write_json(path, selected)
    return selected


def selected_prediction(models, rows, selected, p, jp):
    if selected["kind"] == "detector_reference": return predict_cold(models["detector"], rows, REFERENCE["threshold"])
    if selected["kind"] == "joint_reference": return predict_joint(models["joint"], models["detector"], rows, SOURCE_JOINT["decoder"], jp)
    return predict_limited(models["joint"], models["detector"], rows, selected, p)


def run_limits_study(protocol_path, data_dir, report_dir):
    selection = select_limits(protocol_path, data_dir)
    directory = data_dir.parent / "models/research-episode-limits"
    models = {}
    for n, digest in selection["model_artifact_sha256"].items():
        path = directory / n / "model.json"
        if sha(path) != digest: raise ValueError("Frozen episode-limits artifact hash mismatch")
        model = json.loads(path.read_text(encoding="utf-8"))
        models[n] = validate_joint_model(model) if n == "joint" else validate_saved_model(model)
    year = selection["model_protocol"]["historical_check_year"]
    # The historical station file is first opened only after selection and model verification.
    rows, provenance = load_rows(data_dir, [year])
    predictions = prediction_registry(models["joint"], models["detector"], rows, selection["protocol"], selection["episode_protocol"])
    scores = {n: score_limits(rows, ps) for n, ps in predictions.items()}
    name = selection["selected_candidate"]["name"]
    summary = {**selection, "generated_at_utc": datetime.now(UTC).isoformat(), "historical_input_provenance": provenance,
               "historical_scores": scores, "historical_target_audit": target_audit(rows), "detector_alerts_preserved": True,
               "paired_timing": {n: compare_timing_cohorts(scores[n], scores[name]) for n in ["gfs", "previous_detector_duration", "unlimited_joint"]},
               "paired_uncertainty": {n: paired_uncertainty(scores[n], scores[name], selection["model_protocol"]["bootstrap"]) for n in ["gfs", "previous_detector_duration", "unlimited_joint"]},
               "selection_artifact_sha256": sha(directory / "selection-before-2025.json"),
               "execution_source_sha256": {f: sha(ROOT / "agro_checker" / f) for f in selection["source_sha256"]}}
    all_rows, _ = load_rows(data_dir, [2022, 2023, 2024, 2025])
    reference = predict_cold(models["detector"], all_rows, REFERENCE["threshold"])
    unlimited = predict_joint(models["joint"], models["detector"], all_rows, SOURCE_JOINT["decoder"], selection["episode_protocol"])
    chosen = selected_prediction(models, all_rows, selection["selected_candidate"], selection["protocol"], selection["episode_protocol"])
    assert_same_alerts(reference, chosen)
    by_date = {n["date"]: n for n in chosen}; old_by_date = {n["date"]: n for n in reference}; joint_by_date = {n["date"]: n for n in unlimited}
    flat, snapshot = [], []
    for s in night_samples(all_rows):
        pred, previous, joint = by_date[s["date"]], old_by_date[s["date"]], joint_by_date[s["date"]]
        role = "historical_check_already_viewed" if s["year"] == year else "model_selection_replay" if s["year"] == 2024 else "in_sample_training_replay"
        forecast, truth = [], []
        for i, r in enumerate(s["rows"]):
            f = {"valid_at_utc": r["valid_at_utc"], "gfs_temperature_c": r["base"]["temperature"], "gfs_cold": r["base"]["temperature"] < 0,
                 "previous_detector_cold": previous["cold_mask"][i], "unlimited_joint_cold": joint["cold_mask"][i], "selected_cold": pred["cold_mask"][i],
                 "cold_hour_model_score": pred["cold_hour_model_scores"][i]}
            forecast.append(f)
            truth.append({"valid_at_utc": r["valid_at_utc"], "observed_temperature_c": r["target"]["temperature"], "temperature_source": r["source"]["temperature"]})
            flat.append({"night_date_local": s["date"], "evaluation_status": role, "forecast_run_at_utc": r["forecast_run_at_utc"], "issued_at_utc": r["issued_at_utc"],
                         **f, **{k: v for k, v in truth[-1].items() if k != "valid_at_utc"}})
        stamps = [r["valid_at_utc"] for r in s["rows"]]
        snapshot.append({"date": s["date"], "evaluation_status": role, "selected_candidate": selection["selected_candidate"],
                         "issued_at_utc": s["rows"][0]["issued_at_utc"], "forecast_run_at_utc": s["rows"][0]["forecast_run_at_utc"],
                         "night_event_model_score": previous["night_event_model_score"], "gfs_frost": timed_frost(s["curve"], stamps),
                         "previous_detector_frost": previous["frost"], "unlimited_joint_frost": joint["frost"], "selected_frost": pred["frost"],
                         "selected_shape": mask_shape(pred["cold_mask"]), "forecast": forecast, "hindsight_observations": truth,
                         "hindsight_frost": timed_frost([r["target"]["temperature"] for r in s["rows"]], stamps)})
    processed = data_dir / "processed/episode-limits"
    write_csv(processed / "forecasts.csv", flat)
    write_json(processed / "forecast-snapshot.json", {"selected_candidate": selection["selected_candidate"], "nights": snapshot})
    write_json(processed / "forecast-snapshot.json.meta.json", {"sha256": sha(processed / "forecast-snapshot.json")})
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "EPISODE_LIMITS_SUMMARY.json", summary)
    plot_limits(summary, snapshot, report_dir)
    (report_dir / "EPISODE_LIMITS_REPORT.md").write_text(report_limits(summary), encoding="utf-8")
    return summary


def replay_limits(data_dir, night):
    date.fromisoformat(night)
    path = data_dir / "processed/episode-limits/forecast-snapshot.json"
    meta = json.loads(path.with_name(path.name+".meta.json").read_text(encoding="utf-8"))
    if sha(path) != meta["sha256"]: raise ValueError("Episode-limits snapshot hash mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    n = next((n for n in payload["nights"] if n["date"] == night), None)
    if n is None: raise ValueError("Night not found")
    return {**n, "night_date_local": night, "target": "station 2m T<0, not flower damage", "forecast_available_at_utc": None,
            "availability_evidence": "archive_timestamp_proxy; first publication unknown", "model_score_status": "uncalibrated constrained posterior and frozen detector",
            "time_resolution": "hourly cold bins 19–09 MSK; edge censoring; not continuous duration"}


def report_limits(s):
    winner = s["selected_candidate"]["name"]
    f = lambda v: "—" if v is None else f"{v:.3f}"
    base, old, unlimited, chosen = [s["historical_scores"][n] for n in ["gfs", "previous_detector_duration", "unlimited_joint", winner]]
    lines = ["# Ограничение числа холодных эпизодов и штраф коротких разрывов", "", f"Расчёт {s['generated_at_utc']}. Краснодар, станция 34929, погодный порог T<0 °C.", "",
             f"Выбран **{winner}** только по 2023–2024. 2025 ранее просмотрен и остаётся историческим контролем; по нему параметры не подбирались. Детектор и исходные коэффициенты CRF сохранены.", "",
             "## Постановка", "",
             "Повторно используется прежняя CRF L2=10 и decoder ожидаемой потери, выбранные по прошлым сезонам. Новый prior ограничивает поддержку и решение максимумом одного или двух эпизодов. Внутренний тёплый разрыв длиной один час получает стоимость1, два часа —0,5, длиннее —0; тёплые края окна не являются разрывом. Из логарифмической оценки всей маски вычитается λ·стоимость, затем распределение нормируется по допустимым маскам. λ=0/2/5 в единицах log-score; это модельный prior, не экономическая цена.", "",
             "Один эпизод допускает105 непустых масок, два —1470. Для cap=1 внутренних разрывов нет, все λ эквивалентны, поэтому оставлен один вариант cap1/λ0. Реестр: четыре ограниченных варианта плюс прежний detector+duration и unlimited joint как fallback. GFS отдельно как база. Ограничение применяется и к posterior, и к пространству действий decoder; разрешённый posterior сам по себе не гарантировал бы разрешённого решения.", "",
             "Штраф не заполняет измеренные разрывы и не меняет цели. Исходная CRF учится на всех истинных холодных обучающих ночах, включая маски с числом эпизодов больше cap. Новые ограничения задают prior прогнозной формы, а не отбор удобных целей. Неполные14 температур исключаются по прежнему правилу, без imputation. Порог строго T<0: факт0 °C не считается холодом.", "",
             "Фиксированный ночной detector event_duration_weather@0.25 сохранён. Без тревоги маска пустая, с тревогой непустая: список тревог у всех вариантов точно совпадает. Если cap слишком жёсткий, истинная форма нескольких эпизодов может быть недостижима; это ограничение показано в аудите и метриках.", "",
             "## Выбор и аудит", "",
             "Фолды2022→2023 и2022–2023→2024; scaler/28 коэффициентов обучаются только на прошлом. Настройки исходной CRF неизменны, final fit только2022–2023 и совпадает с предыдущими frozen моделями. `episode-limits-select` не открывает наблюдения2025; выбор и SHA моделей записаны до их чтения в study. Повторение проверяет неизменность выбора, коэффициентов, входов и родительского frozen выбора.", "",
             "Критерий прежний: L=0,25 miss-rate +0,15 false-alarm-rate +0,20 cold-duration-MAE/14 +0,10 warm-duration-MAE/14 +0,15 cold-mask-mismatch/14 +0,15 first-hour-error/14. За пропуск первого холодного часа заранее задан штраф14, он не выдаётся за измеренную ошибку начала. Классы объединяются по своим знаменателям. Пропуски/ложные совпадают у вариантов с прежним detector, поэтому выбор определяется формой и длительностью.", "",
             "| Год | Полных ночей | Холодных | Истинных ночей с >1 эпизодом | С >2 эпизодами |", "|---|---:|---:|---:|---:|"]
    for year, a in {**s["development_target_audit"], **s["historical_target_audit"]}.items():
        lines.append(f"| {year} | {a['complete_nights']} | {a['observed_cold_nights']} | {a['observed_nights_exceeding_cap']['1']} | {a['observed_nights_exceeding_cap']['2']} |")
    lines += ["", "Ни одна такая ночь не удалена и её наблюдения не сглажены. Всего7 холодных ночей в выборе и21 в final fit; устойчивость ограничена.", "",
              "| Вариант | L ↓ | Пропуски/7 | Ложные | MAE холодной длительности ч | Несовпавших часов холодной маски | Первый час с штрафом за пропуск ч |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for n, a in s["pooled_validation"].items():
        lines.append(f"| {n} | {f(a['episode_selection_loss'])} | {a['misses']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['cold_mask_mismatch_mae_cold_h'])} | {f(a['cold_first_sample_error_with_miss_penalty_h'])} |")
    lines += ["", "| Сезон | Метод | Пропуски/холодных | Ложные | MAE холодной длительности ч | Первый час MAE попаданий ч (n) |", "|---|---|---|---:|---:|---|"]
    for fold in s["folds"]:
        for n in ["gfs", "previous_detector_duration", "unlimited_joint", winner]:
            a = fold["scores"][n]
            lines.append(f"| {fold['validation_year']} | {n} | {a['misses']}/{a['cold_nights']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['first_cold_sample_mae_hits_h'])} ({a['first_sample_pairs']}) |")
    lines += ["", "## Исторический 2025", "",
              "| Вариант | Пропуски/6 | Ложные | MAE длительности все / холодные ч | Первый час MAE попаданий ч (n) | MAE числа эпизодов холодных | Коротких разрывов прогноза всего |",
              "|---|---:|---:|---|---|---:|---:|"]
    for n, a in s["historical_scores"].items():
        lines.append(f"| {n} | {a['misses']} | {a['false_alarms']} | {f(a['cold_bin_duration_mae_all_h'])} / {f(a['cold_bin_duration_mae_cold_h'])} | {f(a['first_cold_sample_mae_hits_h'])} ({a['first_sample_pairs']}) | {f(a['episode_count_mae_cold'])} | {a['predicted_short_gap_count_total']} |")
    lines += ["", "Первый холодный отсчёт только на одинаковых попаданиях; ошибка физического начала за краем окна неизвестна:", "",
              "| База | Определение | Пар | MAE базы ч | MAE выбранного ч |", "|---|---|---:|---:|---:|"]
    for label, cohorts in s["paired_timing"].items():
        for kind, text in [("first_sample_common_hits", "Первый холодный отсчёт"), ("onset_common_interior_hits", "Без границы начала у обоих")]:
            a = cohorts[kind]
            lines.append(f"| {label} | {text} | {a['paired_nights']} | {f(a['baseline_mae_h'])} | {f(a['corrected_mae_h'])} |")
    lines += ["", "Парный bootstrap MAE длительности холодных ночей:5000 повторов календарных блоков; положительная разность — выигрыш выбранного. Тёплые блоки не удаляются; повторения без холодных ночей посчитаны отдельно. Один просмотренный сезон с6 событиями не подтверждает переносимость.", "",
              "| База | Блок дней | Выигрыш ч | 95% диапазон ч | Повторов без холодных ночей |", "|---|---:|---:|---|---:|"]
    for label, values in s["paired_uncertainty"].items():
        for width, a in values["duration_block_bootstrap"].items():
            lines.append(f"| {label} | {width} | {f(a['cold_duration_mae_improvement_h'])} | [{f(a['interval_95'][0])}, {f(a['interval_95'][1])}] | {a['replicates_without_cold_nights']} |")
    by_base = {n["date"]: n for n in base["nights"]}; by_old = {n["date"]: n for n in old["nights"]}; by_joint = {n["date"]: n for n in unlimited["nights"]}
    stamp = lambda v: (datetime.fromisoformat(v)+timedelta(hours=3)).strftime("%d.%m %H:%M") if v else "—"
    lines += ["", "| Холодная ночь или тревога | Исход | Часов факт/GFS/прежний/unlimited/выбранный | Первый час факт/выбранный МСК | Эпизодов факт/выбранный |", "|---|---|---|---|---|"]
    for n in chosen["nights"]:
        if n["observed"]["event"] or n["predicted"]["event"]:
            a, pred = n["observed"], n["predicted"]
            lines.append(f"| {n['date']} | {n['outcome']} | {a['cold_bins_hours']}/{by_base[n['date']]['predicted']['cold_bins_hours']}/{by_old[n['date']]['predicted']['cold_bins_hours']}/{by_joint[n['date']]['predicted']['cold_bins_hours']}/{pred['cold_bins_hours']} | {stamp(a['first_cold_sample_at_utc'])}/{stamp(pred['first_cold_sample_at_utc'])} | {len(a['episodes'])}/{len(pred['episodes'])} |")
    gfs_pair = s["paired_timing"]["gfs"]["first_sample_common_hits"]
    lines += ["", "## Вывод", "",
              f"Список тревог прежнего detector сохранён: {chosen['hits']} попаданий, {chosen['misses']} пропусков, {chosen['false_alarms']} ложных. "
              f"MAE холодной длительности GFS/прежний/unlimited/выбранный: {f(base['cold_bin_duration_mae_cold_h'])}/{f(old['cold_bin_duration_mae_cold_h'])}/{f(unlimited['cold_bin_duration_mae_cold_h'])}/{f(chosen['cold_bin_duration_mae_cold_h'])} ч. "
              f"На {gfs_pair['paired_nights']} общих с GFS попаданиях MAE первого холодного часа {f(gfs_pair['baseline_mae_h'])}→{f(gfs_pair['corrected_mae_h'])} ч. "
              "По 2025 вариант не менялся; обнаружение и расписание оцениваются отдельно, с числом пропусков и ложных тревог. Модель демонстрации не заменена.", "",
              "![Эпизоды с ограничениями](assets/episode-limits.png)", "", "## Воспроизведение", "", "```powershell",
              ".\\.venv\\Scripts\\python.exe -m agro_checker episode-limits-select --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker episode-limits-study --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker episode-limits-replay --night 2025-03-07", "```", "",
              "Нужны исходные кэши и предыдущий `episode-select` с frozen моделями. Новых загрузок нет. Тот же выпуск GFS06UTC, gate Last-Modified+10мин к18МСК, ближайшая ячейка, источники ISD-lite/METAR. Первая публикация неизвестна. Станционная температура2м не равна температуре тканей яблони; фаза сада неизвестна. Начало — первый отрицательный часовой отсчёт, длительность — число холодных часовых интервалов в[19,09), не непрерывное измерение. На границах окна начало/конец неизвестны, интерполяции нет. Вероятности не откалиброваны.", "",
              "Frozen модели/выбор: `models/research-episode-limits/`; все368 выдач/CSV/SHA snapshot: `data/processed/episode-limits/`. Replay маркирует роль сезона и отделяет факт. Старые артефакты/UI сохранены. [Численная сводка и происхождение](EPISODE_LIMITS_SUMMARY.json).", ""]
    if s["selected_candidate"]["kind"] == "limited":
        setting = s["selected_candidate"]
        note = f"Настройка выбора: максимум {setting['max_episodes']} эпизода, дополнительный штраф λ={setting['gap_penalty']:g}. "
        if setting["gap_penalty"] == 0:
            note += "Механизм штрафа реализован и проверен, но положительные λ=2/5 не выиграли критерий 2023–2024. Нулевой штраф сохранён и в исторической проверке."
        lines[7:7] = [note, ""]
    return "\n".join(lines)


def plot_limits(summary, snapshot, report_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    nights = {n["date"]: n for n in snapshot}; examples = summary["protocol"]["report_examples"]
    fig, axes = plt.subplots(len(examples), 1, figsize=(12, 11))
    for ax, d in zip(axes, examples):
        n = nights[d]
        masks = [[r["observed_temperature_c"] < 0 for r in n["hindsight_observations"]]]+[
            [r[k] for r in n["forecast"]] for k in ["gfs_cold", "previous_detector_cold", "unlimited_joint_cold", "selected_cold"]]
        ax.imshow(np.array(masks), cmap="Blues", vmin=0, vmax=1, aspect="auto", interpolation="nearest")
        ax.set_yticks(range(5), ["Observed", "GFS", "Previous detector+duration", "Unlimited joint", "Selected limits"])
        ax.set_xticks(range(14), [(19+i)%24 for i in range(14)])
        ax.set_title(d+" · cold bins "+" / ".join(str(sum(m)) for m in masks), fontsize=10); ax.set_xlabel("Hour MSK")
    fig.suptitle("Selected on 2023–2024: "+summary["selected_candidate"]["name"]+"\n2025 historical examples · fixed detector · hourly bins, not continuous duration", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, .94))
    assets = report_dir / "assets"; assets.mkdir(parents=True, exist_ok=True)
    fig.savefig(assets / "episode-limits.png", dpi=160); plt.close(fig)
