"""Fixed forward-season ablations for same-run GFS weather covariates."""

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import importlib.metadata
import json
import math

from .gfs_covariates import FIELDS, collect_covariates, load_covariates
from .model_study import FEATURE_NAMES, assemble_samples, cold_sequences, fit_model, load_protocol, paired_block_bootstrap, predict_model, score, select_model
from .pipeline import ROOT, UTC, cached_download, parse_observations, write_csv, write_json
from .single_run import build_tables, load_points, load_single_config

EXTRA_FEATURES = {
    "wind": ["wind_mean_ms", "wind_min_ms", "wind_at_gfs_min_ms"],
    "dewpoint": ["dewpoint_mean_c", "dewpoint_min_c", "dewpoint_at_gfs_min_c", "dewpoint_spread_mean_c", "dewpoint_spread_at_gfs_min_c"],
    "cloud": ["cloud_mean_pct", "cloud_min_pct", "cloud_at_gfs_min_pct"],
}
GROUPS = {"temperature": [], "temperature_wind": ["wind"], "temperature_dewpoint": ["dewpoint"],
          "temperature_cloud": ["cloud"], "temperature_all": ["wind", "dewpoint", "cloud"]}
ALL_FEATURE_NAMES = FEATURE_NAMES + sum(EXTRA_FEATURES.values(), [])


def load_covariate_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if p["schema_version"] != 1 or p["field_keys"] != list(FIELDS) or p["field_step_type"] != "instant":
        raise ValueError("Unexpected covariate source protocol")
    if p["features"] != EXTRA_FEATURES or p["feature_groups"] != list(GROUPS) or p["algorithms"] != ["ridge", "catboost"]:
        raise ValueError("Unexpected fixed feature/model groups")
    if p["baselines"] != ["identity", "night_min_median", "lead_hour_median"]:
        raise ValueError("Unexpected baseline list")
    if p["weather_threshold_c"] != 0 or p["selection_metric"] != "pooled_validation_night_min_mae" or p["missing_policy"] != "complete_nights_all_fields_no_imputation":
        raise ValueError("Unexpected evaluation protocol")
    if p["source_config"] != "single_run.json" or p["base_model_protocol"] != "model_study.json":
        raise ValueError("Unexpected source configs")
    return p


def group_indexes(group):
    names = FEATURE_NAMES + sum((EXTRA_FEATURES[g] for g in GROUPS[group]), [])
    return [ALL_FEATURE_NAMES.index(n) for n in names]


def covariate_features(curve, fields):
    """All hour alignment uses the predicted minimum, never the observed minimum."""
    if len(curve) != 14 or len(fields) != 14:
        raise ValueError("Covariate features require all 14 hours")
    index = curve.index(min(curve))
    wind = [math.hypot(f["u10"]["value"], f["v10"]["value"]) for f in fields]
    dew = [f["dewpoint"]["value"] for f in fields]
    cloud = [f["cloud"]["value"] for f in fields]
    spread = [t - d for t, d in zip(curve, dew)]
    values = [sum(wind)/14, min(wind), wind[index], sum(dew)/14, min(dew), dew[index], sum(spread)/14,
              spread[index], sum(cloud)/14, min(cloud), cloud[index]]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Nonfinite covariate feature")
    return values


def join_covariates(samples, hourly, covariates, config):
    by_time = {}
    for row in covariates:
        if row["valid_at_utc"] in by_time:
            raise ValueError("Duplicate covariate valid time")
        by_time[row["valid_at_utc"]] = row
    hours = defaultdict(list)
    for row in hourly:
        hours[row["night_date_local"]].append(row)
    result, excluded = [], []
    for s in samples:
        aligned, reason = [], None
        for h in sorted(hours[s["date"]], key=lambda r: r["lead_hours"]):
            row = by_time.get(h["valid_at_utc"])
            if row is None:
                reason = "missing_covariate_hour"
                break
            for key in ["night_date_local", "forecast_run_at_utc", "issued_at_utc", "lead_hours"]:
                if row[key] != h[key]:
                    raise ValueError(f"Covariate join {key} mismatch")
            if set(row["fields"]) != set(FIELDS):
                reason = "missing_covariate_field"
                break
            for key, field in row["fields"].items():
                modified = datetime.fromisoformat(field["archive_last_modified_at_utc"])
                gate = modified + timedelta(minutes=config["archive_availability_buffer_minutes"])
                issue = datetime.fromisoformat(row["issued_at_utc"])
                if modified != datetime.fromisoformat(h["archive_last_modified_at_utc"]):
                    raise ValueError("Covariate archive version timestamp mismatch")
                if field["availability_gate_at_utc"] != gate.isoformat() or field["available_by_issue_proxy"] != (gate <= issue):
                    raise ValueError("Covariate availability gate mismatch")
                if gate > issue:
                    reason = "late_covariate_by_proxy"
                if field["step_type"] != "instant" or field["units"] != ("degC" if key == "dewpoint" else FIELDS[key]["units"]):
                    raise ValueError("Covariate step/units mismatch")
                if not math.isfinite(field["value"]):
                    raise ValueError("Nonfinite covariate input")
            if reason:
                break
            aligned.append(row["fields"])
        if reason:
            excluded.append({"date": s["date"], "reason": reason})
        else:
            result.append({**s, "features": s["features"] + covariate_features(s["curve"], aligned)})
    return result, excluded


def candidates(protocol):
    return protocol["baselines"] + [f"{a}_{g}" for a in protocol["algorithms"] for g in protocol["feature_groups"]]


def prepare_features(samples, indexes):
    return [{**s, "features": [s["features"][i] for i in indexes]} for s in samples]


def fit_candidate(label, train, model_protocol):
    if label in ["identity", "night_min_median", "lead_hour_median"]:
        return {"model": fit_model(label, train, model_protocol), "indexes": list(range(12)), "label": label}
    algorithm, group = label.split("_", 1)
    indexes = group_indexes(group)
    model = fit_model("ridge_min" if algorithm == "ridge" else "catboost_min", prepare_features(train, indexes), model_protocol)
    return {"model": model, "indexes": indexes, "label": label}


def predict_candidate(candidate, samples):
    return predict_model(candidate["model"], prepare_features(samples, candidate["indexes"]))


def validate_candidates(samples, protocol, model_protocol):
    names = candidates(protocol)
    folds = []
    for f in model_protocol["folds"]:
        print(f"Covariate study: train {f['train_years']} -> validate {f['validation_year']}", flush=True)
        train = [s for s in samples if s["year"] in f["train_years"]]
        valid = [s for s in samples if s["year"] == f["validation_year"]]
        fitted = {n: fit_candidate(n, train, model_protocol) for n in names}
        predictions = {n: predict_candidate(m, valid) for n, m in fitted.items()}
        folds.append({**f, "role": "validation", "training_nights": len(train),
                      "scores": {n: score(valid, p) for n, p in predictions.items()}})
    selected, pooled = select_model(folds, names)
    return folds, selected, pooled


def save_candidate(candidate, directory):
    model = candidate["model"]
    meta = {**model["meta"], "label": candidate["label"], "feature_names": [ALL_FEATURE_NAMES[i] for i in candidate["indexes"]],
            "indexes": candidate["indexes"]}
    path = directory / candidate["label"]
    path.mkdir(parents=True, exist_ok=True)
    if "offset" in model:
        meta["offset_c"] = model["offset"]
    elif "offsets" in model:
        meta["offsets_c"] = model["offsets"]
    elif model["meta"]["name"] == "ridge_min":
        scale, ridge = [s[1] for s in model["estimator"].steps]
        meta.update(scaler_mean=scale.mean_.tolist(), scaler_scale=scale.scale_.tolist(), coefficients=ridge.coef_.tolist(), intercept=float(ridge.intercept_))
    else:
        model["estimator"].save_model(str(path / "model.cbm"))
        meta["resolved_parameters"] = model["estimator"].get_all_params()
    write_json(path / "model.json", meta)
    return meta


def load_candidate(directory, label):
    if label not in ["identity", "night_min_median", "lead_hour_median"] + [f"{a}_{g}" for a in ["ridge", "catboost"] for g in GROUPS]:
        raise ValueError("Unknown candidate")
    meta = json.loads((directory / label / "model.json").read_text(encoding="utf-8"))
    indexes = list(range(12)) if label in ["identity", "night_min_median", "lead_hour_median"] else group_indexes(label.split("_", 1)[1])
    if meta["label"] != label or meta["indexes"] != indexes or meta["feature_names"] != [ALL_FEATURE_NAMES[i] for i in indexes]:
        raise ValueError("Saved covariate feature schema mismatch")
    model = {"meta": meta}
    if "offset_c" in meta:
        model["offset"] = meta["offset_c"]
    elif "offsets_c" in meta:
        model["offsets"] = meta["offsets_c"]
    elif meta["name"] == "ridge_min":
        model["linear"] = meta
    else:
        from catboost import CatBoostRegressor
        model["estimator"] = CatBoostRegressor()
        model["estimator"].load_model(str(directory / label / "model.cbm"))
    return {"label": label, "model": model, "indexes": indexes}


def pooled_counts(folds, name):
    return {k: sum(f["scores"][name][k] for f in folds) for k in ["nights", "cold_nights", "hits", "misses", "false_alarms", "correct_negatives"]}


def input_audit(cov, hourly, train, check):
    temperatures = {r["valid_at_utc"]: r["forecast_temperature_c"] for r in hourly}
    audit = {}
    for year in sorted({r["night_date_local"][:4] for r in cov}):
        rows = [r for r in cov if r["night_date_local"][:4] == year]
        wind = [math.hypot(r["fields"]["u10"]["value"], r["fields"]["v10"]["value"]) for r in rows]
        cloud = [r["fields"]["cloud"]["value"] for r in rows]
        spreads = [temperatures[r["valid_at_utc"]] - r["fields"]["dewpoint"]["value"] for r in rows if temperatures[r["valid_at_utc"]] is not None]
        audit[year] = {"forecast_hours": len(rows), "wind_range_ms": [min(wind), max(wind)],
                       "cloud_range_pct": [min(cloud), max(cloud)], "cloud_zero_fraction": sum(v == 0 for v in cloud)/len(cloud),
                       "cloud_100_fraction": sum(v == 100 for v in cloud)/len(cloud),
                       "dewpoint_spread_range_c": [min(spreads), max(spreads)], "negative_spread_hours": sum(v < 0 for v in spreads)}
    ranges = {name: {"min": min(s["features"][i] for s in train), "max": max(s["features"][i] for s in train)}
              for i, name in enumerate(ALL_FEATURE_NAMES)}
    outside = [{"date": s["date"], "outside_features": {n: s["features"][i] for i, n in enumerate(ALL_FEATURE_NAMES)
                                                        if not ranges[n]["min"] <= s["features"][i] <= ranges[n]["max"]}}
               for s in check]
    return {"hourly_by_year": audit, "training_feature_ranges": ranges,
            "historical_outside_range_nights": [r for r in outside if r["outside_features"]],
            "note": "Raw forecast spreads retained without clipping; univariate ranges are diagnostic only"}


def load_inputs(data_dir, config):
    points, failures, _, temp_meta = load_points(data_dir, config)
    if failures:
        raise ValueError("Temperature acquisition failures must be resolved first")
    observations, provenance = {}, []
    for year in config["years"]:
        content, meta = cached_download(f"https://data.meteostat.net/hourly/{year}/{config['station_id']}.csv.gz",
                                       data_dir / "raw" / f"meteostat-{config['station_id']}-{year}.csv.gz", True)
        observations.update(parse_observations(content, config["observation_sources"]))
        provenance.append(meta)
    hourly, nights = build_tables(config, points, observations)
    return hourly, nights, temp_meta, provenance


def run_covariate_study(protocol_path, data_dir, report_dir):
    protocol = load_covariate_protocol(protocol_path)
    source_path = ROOT / "config" / protocol["source_config"]
    model_path = ROOT / "config" / protocol["base_model_protocol"]
    config = load_single_config(source_path)
    model_protocol = load_protocol(model_path)
    cov, failures, downloads, cov_meta = load_covariates(data_dir, config)
    if failures:
        raise ValueError("Resolve covariate acquisition failures before model evaluation")
    hourly, nights, temp_meta, obs_meta = load_inputs(data_dir, config)
    if {r["valid_at_utc"] for r in cov} != {r["valid_at_utc"] for r in hourly}:
        raise ValueError("Covariate acquisition incomplete; run covariate-fetch before model evaluation")
    base_samples = assemble_samples(hourly, nights)
    samples, excluded = join_covariates(base_samples, hourly, cov, config)
    names = candidates(protocol)
    directory = data_dir.parent / "models" / "research-covariates"
    lock = {"protocol": protocol, "model_protocol": model_protocol, "source_config": config,
            "protocol_saved_at_utc": datetime.now(UTC).isoformat(),
            "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
            "model_protocol_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "source_sha256": {f: hashlib.sha256((ROOT / "agro_checker" / f).read_bytes()).hexdigest()
                              for f in ["gfs_covariates.py", "covariate_study.py", "model_study.py", "single_run.py", "gfs_archive.py", "pipeline.py"]},
            "temperature_cache": temp_meta, "covariate_cache": cov_meta, "observation_provenance": obs_meta,
            "feature_names": ALL_FEATURE_NAMES, "covariate_excluded_nights": excluded}
    write_json(directory / "protocol-lock.json", lock)
    folds, selected, pooled = validate_candidates(samples, protocol, model_protocol)
    preselection_path = directory / "validation-selection-pre-2025.json"
    preselection = None
    if preselection_path.exists():
        preselection = json.loads(preselection_path.read_text(encoding="utf-8"))
        if (preselection["protocol_sha256"] != lock["protocol_sha256"]
                or preselection["model_protocol_sha256"] != lock["model_protocol_sha256"]
                or preselection["selected_model"] != selected or preselection["pooled_validation_mae_c"] != pooled):
            raise ValueError("Frozen pre-2025 validation selection changed; do not tune this experiment on 2025")
    train = [s for s in samples if s["year"] in model_protocol["final_train_years"]]
    fitted = {n: fit_candidate(n, train, model_protocol) for n in names}
    models = {n: save_candidate(m, directory) for n, m in fitted.items()}
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
              for label in names for p in (directory / label).iterdir() if p.suffix in [".json", ".cbm"]}
    # Only validation, protocol and training artifacts exist at this point. Test labels are never a fit/selection input.
    selection = {**lock, "selection_saved_at_utc": datetime.now(UTC).isoformat(),
                 "folds": folds, "selected_model": selected, "pooled_validation_mae_c": pooled,
                 "models": models, "model_artifact_sha256": hashes, "independent_preselection": preselection}
    write_json(directory / "selection-before-2025.json", selection)
    check = [s for s in samples if s["year"] == model_protocol["historical_check_year"]]
    predictions = {n: predict_candidate(m, check) for n, m in fitted.items()}
    historical_scores = {n: score(check, p) for n, p in predictions.items()}
    within_algorithm = {}
    for algorithm in protocol["algorithms"]:
        base = f"{algorithm}_temperature"
        within_algorithm[algorithm] = {g: paired_block_bootstrap(check, predictions[base], predictions[f"{algorithm}_{g}"], model_protocol["bootstrap"])
                                       for g in protocol["feature_groups"]}
    details = []
    for i, s in enumerate(check):
        if s["target"] < 0 or any(p[i]["prediction"] < 0 for p in predictions.values()):
            details.append({"date": s["date"], "observed_min_c": s["target"],
                            "predictions": {n: p[i]["prediction"] for n, p in predictions.items()},
                            "covariate_features": dict(zip(ALL_FEATURE_NAMES[12:], s["features"][12:]))})
    grids = sorted({(field["grid_latitude"], field["grid_longitude"], field["grid_distance_km"]) for r in cov for field in r["fields"].values()})
    summary = {**selection, "generated_at_utc": datetime.now(UTC).isoformat(),
               "packages": {n: importlib.metadata.version(n) for n in ["eccodes", "numpy", "scikit-learn", "catboost", "matplotlib"]},
               "archive": {"expected_hours": len(hourly), "extracted_hours": len(cov), "message_count": len(downloads),
                           "download_bytes": sum(m["bytes"] for m in downloads), "failed_hours": len(failures),
                           "late_field_hours": {k: sum(not r["fields"][k]["available_by_issue_proxy"] for r in cov) for k in FIELDS}, "grids": grids},
               "original_paired_nights": len(base_samples), "common_paired_nights": len(samples),
               "year_counts": {str(y): sum(s["year"] == y for s in samples) for y in config["years"]},
               "pooled_validation_counts": {n: pooled_counts(folds, n) for n in names}, "historical_scores": historical_scores,
               "training_scores": {n: score(train, predict_candidate(m, train)) for n, m in fitted.items()},
               "bootstrap_added_features": within_algorithm,
               "bootstrap_vs_gfs": {n: paired_block_bootstrap(check, predictions["identity"], p, model_protocol["bootstrap"]) for n, p in predictions.items()},
               "cold_sequences": {n: cold_sequences(check, p) for n, p in predictions.items()}, "cold_or_alert_nights": details}
    summary["input_audit"] = input_audit(cov, hourly, train, check)
    processed = data_dir / "processed" / "covariate-study"
    write_csv(processed / "features.csv", [{"date": s["date"], "year": s["year"], **dict(zip(ALL_FEATURE_NAMES, s["features"])), "observed_min_c": s["target"]} for s in samples])
    write_csv(processed / "historical_predictions.csv", [{"date": s["date"], "observed_min_c": s["target"], **{n: p[i]["prediction"] for n, p in predictions.items()}} for i, s in enumerate(check)])
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "COVARIATE_STUDY_SUMMARY.json", summary)
    plot_covariates(summary, report_dir)
    (report_dir / "COVARIATE_STUDY_REPORT.md").write_text(report_covariates(summary), encoding="utf-8")
    return summary


def plot_covariates(summary, report_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), layout="constrained")
    groups = summary["protocol"]["feature_groups"]
    labels = ["Температура", "+ Ветер", "+ Точка росы", "+ Облачность", "+ Всё"]
    x = np.arange(len(groups))
    for algorithm, offset in [("ridge", -.18), ("catboost", .18)]:
        axes[0].bar(x + offset, [summary["pooled_validation_mae_c"][f"{algorithm}_{g}"] for g in groups], width=.34, label=algorithm)
    axes[0].set(xticks=x, xticklabels=labels, ylabel="MAE минимума, °C", title="Выбор только по 2023–2024")
    axes[0].tick_params(axis="x", labelrotation=20)
    axes[0].legend()
    ordered = [f"{a}_{g}" for a in ["ridge", "catboost"] for g in groups]
    y = np.arange(len(ordered))
    axes[1].barh(y-.16, [summary["historical_scores"][n]["misses"] for n in ordered], height=.30, label="Пропуски", color="#b94338")
    axes[1].barh(y+.16, [summary["historical_scores"][n]["false_alarms"] for n in ordered], height=.30, label="Ложные тревоги", color="#63819a")
    axes[1].set(yticks=y, yticklabels=[f"{a}: {l}" for a in ["ridge", "catboost"] for l in labels],
                xlabel="Число ночей", title="2025: исторический контроль, порог <0 °C")
    axes[1].invert_yaxis()
    max_count = max(max(summary["historical_scores"][n]["misses"], summary["historical_scores"][n]["false_alarms"]) for n in ordered)
    axes[1].set_xticks(list(range(max_count + 1)))
    axes[1].legend(fontsize=9)
    fig.suptitle("GFS одного выпуска · ветер 10 м, точка росы 2 м, общая мгновенная облачность", fontsize=12)
    target = report_dir / "assets" / "covariate-study.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=160)
    plt.close(fig)


def report_covariates(s):
    def fmt(x):
        return "—" if x is None else f"{x:.3f}" if isinstance(x, float) else str(x)
    selected = s["selected_model"]
    b, m = s["historical_scores"]["identity"], s["historical_scores"][selected]
    lines = ["# Ветер, точка росы и облачность: проверка дополнительных признаков GFS", "",
             f"Расчёт {s['generated_at_utc']}. Выбор на 2023–2024: **{selected}**. "
             f"В историческом контроле 2025 MAE минимума {b['mae_c']:.3f} → {m['mae_c']:.3f} °C, "
             f"пропуски {b['misses']} → {m['misses']}, ложные тревоги {b['false_alarms']} → {m['false_alarms']}.", "",
             "## Ответ на вопрос о новых признаках", ""]
    for a in s["protocol"]["algorithms"]:
        base, full = f"{a}_temperature", f"{a}_temperature_all"
        before, after = s["historical_scores"][base], s["historical_scores"][full]
        band = s["bootstrap_added_features"][a]["temperature_all"]["7"]
        lines.append(f"- {a}: MAE 2023–2024 {s['pooled_validation_mae_c'][base]:.3f} → {s['pooled_validation_mae_c'][full]:.3f} °C; "
                     f"MAE 2025 {before['mae_c']:.3f} → {after['mae_c']:.3f} °C. Пропуски 2025 {before['misses']} → {after['misses']}, "
                     f"ложные тревоги {before['false_alarms']} → {after['false_alarms']}. 95% диапазон добавочного выигрыша "
                     f"[{band['interval_95_c'][0]:.3f}, {band['interval_95_c'][1]:.3f}] °C.")
    lines += ["", "Совместное добавление полей снижает среднюю ошибку в обеих проверках для обоих алгоритмов, "
              "но 95% диапазоны добавочного выигрыша на 2025 включают ноль. На валидации новые поля не уменьшили пропуски "
              "относительно температурных моделей. На 2025 у Ridge обнаружение не улучшилось и выросли ложные тревоги; "
              "у CatBoost улучшились ночные исходы. **Замена победителя на CatBoost после просмотра 2025 не выполняется.** "
              "Это перспективные исследовательские признаки, а не подтверждённое полевое преимущество.", "",
             "## Протокол до расчёта моделей", "",
             "Группы и настройки закреплены в `config/covariate_study.json` до оценки новых моделей. "
             "Два алгоритма с прежними настройками: Ridge alpha=10 (стандартизация только на обучении), "
             "CatBoost MAE, 200 деревьев глубиной 3, learning_rate=0.03, L2=10, фиксированный seed. "
             "Без поиска параметров и early stopping. Пять групп: температура; температура+ветер; +точка росы; +облачность; все признаки. "
             "Базы: GFS, медианный сдвиг минимума, поправка по часу. Все 13 методов сравниваются на одинаковых полных ночах.", "",
             "Временная проверка: 2022 → 2023 и 2022–2023 → 2024; выбор по MAE объединённых валидационных ночей. "
             "Порядок конфигурации разрешает равенство. Все итоговые модели обучены на 2022–2023 без дообучения на 2024. "
             "Выбор, хэши протокола/входов/моделей сохранены в `models/research-covariates/selection-before-2025.json` "
             "перед прогнозами и метриками 2025. **2025 уже просмотрен раньше; это исторический контроль, не свежий слепой тест.** "
             "Погодный порог всегда строгий `<0 °C`, запас тревоги не подбирался. MAE и обнаружение заморозков оцениваются отдельно.", "",
             "## Источник и полнота", "",
             f"{s['archive']['extracted_hours']}/{s['archive']['expected_hours']} часов, {s['archive']['message_count']} сообщений, "
             f"{s['archive']['download_bytes']/1024**3:.2f} GiB диапазонов; ошибок {s['archive']['failed_hours']}. "
             f"Запоздалые поля по архивному proxy: {s['archive']['late_field_hours']}. "
             f"Исходных парных ночей {s['original_paired_nights']}, общих полных {s['common_paired_nights']}; по годам {s['year_counts']}.", "",
             "Из исходных NOAA GFS 0.25° объектов берутся UGRD/VGRD на 10 м (м/с), DPT на 2 м (K → °C), "
             "TCDC для всей атмосферы (%). Выбраны мгновенные поля; усреднённый TCDC из соседней записи не используется. "
             "Проверены центр, параметр, слой, единицы, шаг, выпуск, время действия, сетка и точка. "
             "ETag и Last-Modified каждого диапазона совпадают с температурным объектом, исключая смешение версий. "
             "Каждый диапазон имеет SHA-256 и точный HTTP 206 Content-Range. Повторный запуск восстанавливает незавершённую загрузку.", "",
             "Доступность: S3 Last-Modified + 10 минут <= выдача 18:00 МСК. Это архивный proxy с предположением "
             "о публичной доступности тогда, а не журнал первой публикации. Фактическое время публикации оставлено пустым. "
             "Пропуски/опоздания исключают всю ночь из сравнения; другой выпуск и импутация не подставляются. "
             "Наблюдения только ISD-lite/METAR, все 14 часов; модельные заполнения исключены.", "",
             "## Признаки", "",
             "К прежним 12 температурным/календарным признакам добавлены 11: средняя и минимальная скорость ветра, "
             "скорость в час минимума GFS; средняя/минимальная точка росы и её значение в этот час; "
             "средняя разность температура−точка росы и разность в этот час; средняя/минимальная общая облачность и её значение в этот час. "
             "Скорость ветра рассчитывается как sqrt(U²+V²) для каждого часа до агрегации. "
             "Час минимума выбран по прогнозу GFS, а не по последующему факту. Используются только прогнозные значения этого выпуска.", "",
             "## Результаты выбора: 2023–2024", "",
             "| Метод | MAE минимума °C | Попадания | Пропуски/холодных | Ложные тревоги |", "|---|---:|---:|---:|---:|"]
    for n in candidates(s["protocol"]):
        c = s["pooled_validation_counts"][n]
        lines.append(f"| {n} | {s['pooled_validation_mae_c'][n]:.3f} | {c['hits']} | {c['misses']}/{c['cold_nights']} | {c['false_alarms']} |")
    lines += ["", "## Исторический контроль 2025", "",
              "| Метод | MAE минимума °C | MAE холодных °C | Bias °C | Попадания | Пропуски/холодных | Ложные тревоги | Recall | Precision |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n, v in s["historical_scores"].items():
        lines.append("| " + " | ".join([n, fmt(v["mae_c"]), fmt(v["cold_mae_c"]), fmt(v["bias_c"]), str(v["hits"]), f"{v['misses']}/{v['cold_nights']}", str(v["false_alarms"]), fmt(v["recall"]), fmt(v["precision"])]) + " |")
    lines += ["", "## Дают ли новые поля выигрыш относительно температурной модели?", "",
              "Положительное число — MAE температурной модели минус MAE модели с новыми полями, внутри того же алгоритма. "
              "95% парный bootstrap по календарным блокам 7 дней, 5000 повторов; варианты 3/14 дней также сохранены в JSON. "
              "Один контрольный сезон, редкие связанные заморозки, 13 сравнений; интервалы не доказывают переносимость и не скорректированы на множественные попытки.", "",
              "| Алгоритм | Добавленные поля | Выигрыш 2023–2024 °C | Выигрыш 2025 °C | 95% диапазон 2025 °C |", "|---|---|---:|---:|---|"]
    for a in s["protocol"]["algorithms"]:
        for g in s["protocol"]["feature_groups"][1:]:
            value = s["bootstrap_added_features"][a][g]["7"]
            val = s["pooled_validation_mae_c"][f"{a}_temperature"] - s["pooled_validation_mae_c"][f"{a}_{g}"]
            lines.append(f"| {a} | {g} | {val:.3f} | {value['improvement_c']:.3f} | [{value['interval_95_c'][0]:.3f}, {value['interval_95_c'][1]:.3f}] |")
    lines += ["", "![Сравнение групп признаков](assets/covariate-study.png)", "",
              "## Холодные ночи и тревоги", "",
              f"| Ночь | Факт °C | GFS °C | Ridge температура °C | Ridge все °C | CatBoost температура °C | CatBoost все °C | Выбранный: {selected} °C |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in s["cold_or_alert_nights"]:
        lines.append("| " + " | ".join([r["date"], fmt(r["observed_min_c"])] + [fmt(r["predictions"][n]) for n in ["identity", "ridge_temperature", "ridge_temperature_all", "catboost_temperature", "catboost_temperature_all", selected]]) + " |")
    lines += ["", "| Метод | Холодных последовательных серий | Хотя бы одна ночь обнаружена | Все ночи серии обнаружены |", "|---|---:|---:|---:|"]
    for n in dict.fromkeys(["identity", "ridge_temperature", "ridge_temperature_all", "catboost_temperature", "catboost_temperature_all", selected]):
        groups = s["cold_sequences"][n]
        lines.append(f"| {n} | {len(groups)} | {sum(g['any_detected'] for g in groups)} | {sum(g['all_detected'] for g in groups)} |")
    lines += ["", "Серии — описательная группировка соседних холодных ночей, не доказанное число независимых погодных событий. "
              "Полные метрики каждого сезона и значения новых признаков по ошибочным ночам сохранены в JSON. "
              "Температура станции аэропорта не является температурой цветков/сада; реальные фазы яблони отсутствуют. "
              "Добавление физических признаков не заменяет независимый QC целочисленных целей и проверку за пределами температур обучения. "
              "Выигрыш MAE сам по себе не подтверждает более полезные тревоги или предотвращение ущерба.", ""]
    details = {r["date"]: r for r in s["cold_or_alert_nights"]}
    if "2025-03-07" in details:
        r = details["2025-03-07"]
        lines += [f"Ночь 7–8 марта: факт {r['observed_min_c']:+.3f} °C, Ridge со всеми полями "
                  f"{r['predictions']['ridge_temperature_all']:+.3f} °C, CatBoost со всеми полями "
                  f"{r['predictions']['catboost_temperature_all']:+.3f} °C. "
                  f"Прогноз не пересёк 0 °C у {sum(v >= 0 for v in r['predictions'].values())} из {len(r['predictions'])} методов.", ""]
    for d, label, name in [("2025-03-20", "20–21 марта", "catboost_temperature_all"), ("2025-04-12", "12–13 апреля", "ridge_temperature_all")]:
        if d in details:
            r = details[d]
            value = r["predictions"][name]
            lines += [f"Ночь {label}: {name} {value:+.3f} °C при факте {r['observed_min_c']:+.3f} °C. "
                      + ("Прогноз близок к порогу; такой исход не является устойчивым доказательством обнаружения заморозка." if abs(value) < .25 else "Исход показан в ретроспективном разборе; по нему модель и порог не настраиваются."), ""]
    lines += ["Обнаружение хотя бы одной ночи в каждой серии не улучшается у моделей со всеми полями относительно GFS "
              "в этом контроле: см. отдельную таблицу серий выше.", "",
              "## Аудит новых входов", "",
              "| Год | Часов прогноза | Диапазон скорости ветра м/с | Диапазон T−Td °C | Часов с Td>T | Доля полностью ясных / облачных часов |",
              "|---|---:|---|---|---:|---|"]
    for year, a in s["input_audit"]["hourly_by_year"].items():
        lines.append(f"| {year} | {a['forecast_hours']} | {a['wind_range_ms'][0]:.3f}…{a['wind_range_ms'][1]:.3f} | "
                     f"{a['dewpoint_spread_range_c'][0]:.3f}…{a['dewpoint_spread_range_c'][1]:.3f} | {a['negative_spread_hours']} | "
                     f"{a['cloud_zero_fraction']:.1%} / {a['cloud_100_fraction']:.1%} |")
    lines += ["", "Исходная разность T−Td сохранена без отсечения; это аудит прогнозных полей, не наблюдаемой влажности. "
              "Диапазоны 23 признаков на обучении и выходы за них в 2025 сохранены в JSON, без изменения прогнозов по результатам диагностики.", "",
              "## Воспроизведение", "", "```powershell", ".\\.venv\\Scripts\\python.exe -m agro_checker covariate-fetch --workers 10",
              ".\\.venv\\Scripts\\python.exe -m agro_checker covariate-study --offline", "```", "",
              "Зависимости: `requirements-archive.txt` и `requirements-model.txt`. Сырые диапазоны — `data/raw/gfs-covariates/`; "
              "извлечения/манифест — `data/raw/covariate-*.json`; признаки/прогнозы — `data/processed/covariate-study/`; "
              "модели и закреплённый выбор — `models/research-covariates/`. Исследование не изменяет демонстрационную модель. "
              "[Численные результаты и происхождение](COVARIATE_STUDY_SUMMARY.json).", "",
              "Источники: [официальный каталог NOAA GFS](https://www.nco.ncep.noaa.gov/pmb/products/gfs/), "
              "[NOAA/AWS](https://registry.opendata.aws/noaa-gfs-bdp-pds/).", ""]
    return "\n".join(lines)
