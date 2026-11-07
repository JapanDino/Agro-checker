"""Offline, season-forward model experiments; never promote a research fit to the UI."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import hashlib
import importlib.metadata
import json
import math
import platform
from statistics import median

from .pipeline import ROOT, UTC, cached_download, parse_observations, write_csv, write_json
from .single_run import build_tables, load_points, load_single_config

NAMES = ["identity", "night_min_median", "lead_hour_median", "ridge_min", "catboost_min", "ridge_hourly"]
FEATURE_NAMES = ["gfs_min", "gfs_max", "gfs_mean", "gfs_range", "evening_temperature",
                 "morning_temperature", "overnight_change", "early_cooling", "late_warming",
                 "min_hour_index", "season_sin", "season_cos"]
HOURLY_FEATURE_NAMES = ["gfs_temperature", "gfs_min", "gfs_range", "overnight_change", "season_sin", "season_cos"] + [f"lead_{i}" for i in range(10, 24)]


def load_protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if p["schema_version"] != 1 or p["candidates"] != NAMES:
        raise ValueError("Unexpected model study candidates/schema")
    if p["selection_metric"] != "pooled_validation_night_min_mae":
        raise ValueError("Unsupported selection metric")
    test = p["historical_check_year"]
    if not p["folds"] or len({f["validation_year"] for f in p["folds"]}) != len(p["folds"]):
        raise ValueError("Validation seasons must be nonempty and distinct")
    for f in p["folds"]:
        if not f["train_years"] or len(set(f["train_years"])) != len(f["train_years"]):
            raise ValueError("Invalid training seasons")
        if not max(f["train_years"]) < f["validation_year"] < test:
            raise ValueError("Folds must precede the historical check")
    if not p["final_train_years"] or max(p["final_train_years"]) >= max(f["validation_year"] for f in p["folds"]):
        raise ValueError("Final fit must leave the last validation season unused")
    if p["final_train_years"] != p["folds"][-1]["train_years"]:
        raise ValueError("Final training must match the last forward-validation fit")
    if not math.isfinite(p["ridge_alpha"]) or p["ridge_alpha"] <= 0 or p["bootstrap"]["replicates"] < 100:
        raise ValueError("Invalid regularization/bootstrap settings")
    if any(b <= 0 for b in p["bootstrap"]["block_days"]):
        raise ValueError("Block size must be positive")
    if p["catboost"]["loss_function"] != "MAE" or p["catboost"]["allow_writing_files"]:
        raise ValueError("Expected fixed MAE CatBoost without auxiliary writes")
    if any(not math.isfinite(v) or v < 0 for v in p["warning_margins_c"]) or any(not math.isfinite(v) for v in p["event_thresholds_c"]):
        raise ValueError("Invalid diagnostic thresholds")
    for names in p["diagnostic_ridge_feature_groups"].values():
        if not names or len(set(names)) != len(names) or any(n not in FEATURE_NAMES for n in names):
            raise ValueError("Invalid ablation feature group")
    return p


def forecast_features(evening, curve):
    """Only the common issued GFS run and the known date; no observation argument."""
    if len(curve) != 14 or not all(math.isfinite(v) for v in curve):
        raise ValueError("Features require 14 finite forecast hours")
    low, high = min(curve), max(curve)
    # Month/day calendar coordinate avoids a leap-year offset in spring.
    seasonal_day = (date(2001, evening.month, evening.day) - date(2001, 1, 1)).days
    angle = 2 * math.pi * seasonal_day / 365
    return [low, high, sum(curve) / 14, high - low, curve[0], curve[-1], curve[-1] - curve[0],
            curve[4] - curve[0], curve[-1] - low, float(curve.index(low)), math.sin(angle), math.cos(angle)]


def assemble_samples(hourly, nights):
    grouped = defaultdict(list)
    for row in hourly:
        grouped[row["night_date_local"]].append(row)
    samples = []
    for n in nights:
        if not n["eligible"]:
            continue
        rows = sorted(grouped[n["night_date_local"]], key=lambda r: r["lead_hours"])
        if len(rows) != 14 or [r["lead_hours"] for r in rows] != list(range(10, 24)):
            raise ValueError("Study requires exactly leads 10–23")
        if len({r["forecast_run_at_utc"] for r in rows}) != 1:
            raise ValueError("Mixed forecast runs")
        curve = [r["forecast_temperature_c"] for r in rows]
        target_curve = [r["target_temperature_c"] for r in rows]
        evening = date.fromisoformat(n["night_date_local"])
        samples.append({"date": evening.isoformat(), "year": evening.year,
                        "features": forecast_features(evening, curve), "curve": curve,
                        "target": min(target_curve), "target_curve": target_curve})
    if len({s["date"] for s in samples}) != len(samples):
        raise ValueError("Duplicate study night")
    return sorted(samples, key=lambda s: s["date"])


def hourly_features(sample):
    f = sample["features"]
    return [[t, f[0], f[3], f[6], f[10], f[11]] + [float(i == j) for j in range(14)]
            for i, t in enumerate(sample["curve"])]


def fit_model(name, train, protocol):
    if not train or any(s["year"] >= protocol["historical_check_year"] for s in train):
        raise ValueError("No historical-check targets may enter fit")
    meta = {"name": name, "training_nights": len(train), "training_years": sorted({s["year"] for s in train}),
            "training_last_night": max(s["date"] for s in train)}
    if name == "identity":
        return {"meta": meta, "offset": 0.0}
    if name == "night_min_median":
        return {"meta": meta, "offset": median(s["target"] - min(s["curve"]) for s in train)}
    if name == "lead_hour_median":
        return {"meta": meta, "offsets": [median(s["target_curve"][i] - s["curve"][i] for s in train) for i in range(14)]}
    if name == "ridge_hourly":
        x = [row for s in train for row in hourly_features(s)]
        y = [t - f for s in train for t, f in zip(s["target_curve"], s["curve"])]
    elif name in ("ridge_min", "catboost_min"):
        x = [s["features"] for s in train]
        y = [s["target"] - min(s["curve"]) for s in train]
    else:
        raise ValueError("Unknown study model")
    if name.startswith("ridge"):
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        estimator = make_pipeline(StandardScaler(), Ridge(alpha=protocol["ridge_alpha"], solver="svd"))
    else:
        from catboost import CatBoostRegressor
        estimator = CatBoostRegressor(**protocol["catboost"])
    # No eval_set, early stopping, or test-dependent parameter search.
    estimator.fit(x, y)
    return {"meta": meta, "estimator": estimator}


def fit_ablation(train, indexes, protocol):
    """Diagnostic only; these models are not additional selection candidates."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if any(s["year"] >= protocol["historical_check_year"] for s in train):
        raise ValueError("No historical-check targets may enter fit")
    estimator = make_pipeline(StandardScaler(), Ridge(alpha=protocol["ridge_alpha"], solver="svd"))
    estimator.fit([[s["features"][i] for i in indexes] for s in train],
                  [s["target"] - min(s["curve"]) for s in train])
    return {"meta": {"name": "ridge_min"}, "estimator": estimator, "indexes": indexes}


def predict_model(model, samples):
    name = model["meta"]["name"]
    offsets = None
    if name in ("ridge_min", "catboost_min", "ridge_hourly"):
        x = [row for s in samples for row in hourly_features(s)] if name == "ridge_hourly" else [s["features"] for s in samples]
        if "indexes" in model:
            x = [[row[i] for i in model["indexes"]] for row in x]
        if "linear" in model:
            m = model["linear"]
            offsets = [m["intercept"] + sum((v - mean) / scale * coef for v, mean, scale, coef in
                        zip(row, m["scaler_mean"], m["scaler_scale"], m["coefficients"])) for row in x]
        elif name == "catboost_min":
            offsets = model["estimator"].predict(x, thread_count=2)
        else:
            offsets = model["estimator"].predict(x)
    result = []
    for index, s in enumerate(samples):
        curve = None
        if name == "identity":
            curve = list(s["curve"])
        elif name == "night_min_median":
            # Uniform shift is retained only to quantify the old diagnostic curve.
            curve = [t + model["offset"] for t in s["curve"]]
        elif name == "lead_hour_median":
            curve = [t + d for t, d in zip(s["curve"], model["offsets"])]
        elif name == "ridge_hourly":
            curve = [float(t + d) for t, d in zip(s["curve"], offsets[index * 14:(index + 1) * 14])]
        else:
            # A minimum-only fit never fabricates a corrected hourly trajectory.
            value = min(s["curve"]) + float(offsets[index])
        if curve is not None:
            value = min(curve)
        result.append({"date": s["date"], "prediction": value, "curve": curve})
    return result


def score(samples, predictions, threshold=0.0, warning_margin=0.0):
    if len(samples) != len(predictions) or not samples:
        raise ValueError("Need nonempty paired predictions")
    if any(s["date"] != p["date"] for s, p in zip(samples, predictions)):
        raise ValueError("Prediction dates do not match targets")
    errors, hourly, cold_errors = [], [], []
    counts = Counter(hits=0, misses=0, false_alarms=0, correct_negatives=0)
    for s, p in zip(samples, predictions):
        error = p["prediction"] - s["target"]
        errors.append(error)
        actual, alert = s["target"] < threshold, p["prediction"] < threshold + warning_margin
        key = "hits" if actual and alert else "misses" if actual else "false_alarms" if alert else "correct_negatives"
        counts[key] += 1
        if actual:
            cold_errors.append(abs(error))
        if p["curve"] is not None:
            hourly.extend(abs(a - b) for a, b in zip(p["curve"], s["target_curve"]))
    cold = counts["hits"] + counts["misses"]
    alerts = counts["hits"] + counts["false_alarms"]
    negatives = counts["false_alarms"] + counts["correct_negatives"]
    recall = counts["hits"] / cold if cold else None
    specificity = counts["correct_negatives"] / negatives if negatives else None
    return {"nights": len(samples), "cold_nights": cold, "mae_c": sum(map(abs, errors)) / len(errors),
            "rmse_c": math.sqrt(sum(e * e for e in errors) / len(errors)), "bias_c": sum(errors) / len(errors),
            "cold_mae_c": sum(cold_errors) / len(cold_errors) if cold_errors else None,
            "hourly_mae_c": sum(hourly) / len(hourly) if hourly else None,
            **counts, "recall": recall, "precision": counts["hits"] / alerts if alerts else None,
            "balanced_accuracy": (recall + specificity) / 2 if recall is not None and specificity is not None else None}


def select_model(validation, candidates):
    """Selection takes validation metrics only; no test scores in its interface."""
    if any(f["role"] != "validation" for f in validation):
        raise ValueError("Selection only accepts validation folds")
    pooled = {name: sum(f["scores"][name]["mae_c"] * f["scores"][name]["nights"] for f in validation)
              / sum(f["scores"][name]["nights"] for f in validation) for name in candidates}
    winner = min(candidates, key=lambda name: (pooled[name], candidates.index(name)))
    return winner, pooled


def forward_validation(samples, protocol):
    folds = []
    for f in protocol["folds"]:
        print(f"Model study: train {f['train_years']} -> validate {f['validation_year']}", flush=True)
        train = [s for s in samples if s["year"] in f["train_years"]]
        valid = [s for s in samples if s["year"] == f["validation_year"]]
        models = {name: fit_model(name, train, protocol) for name in protocol["candidates"]}
        predictions = {name: predict_model(model, valid) for name, model in models.items()}
        folds.append({**f, "role": "validation", "training_nights": len(train),
                      "scores": {name: score(valid, p) for name, p in predictions.items()},
                      "warning_margins": {str(margin): {name: score(valid, p, warning_margin=margin) for name, p in predictions.items()}
                                          for margin in protocol["warning_margins_c"]}})
    winner, pooled = select_model(folds, protocol["candidates"])
    return folds, winner, pooled


def paired_block_bootstrap(samples, baseline, corrected, settings):
    """Paired calendar blocks within each season; keep the target/prediction pair together."""
    import numpy as np
    diffs = [abs(b["prediction"] - s["target"]) - abs(c["prediction"] - s["target"])
             for s, b, c in zip(samples, baseline, corrected)]
    output = {}
    for width in settings["block_days"]:
        groups = defaultdict(list)
        for s, delta in zip(samples, diffs):
            d = date.fromisoformat(s["date"])
            groups[(d.year, (d - date(d.year, 3, 1)).days // width)].append(delta)
        blocks = list(groups.values())
        sums = np.array([sum(b) for b in blocks])
        sizes = np.array([len(b) for b in blocks])
        rng = np.random.default_rng(settings["seed"])
        indexes = rng.integers(0, len(blocks), size=(settings["replicates"], len(blocks)))
        means = sums[indexes].sum(axis=1) / sizes[indexes].sum(axis=1)
        low, high = np.quantile(means, [0.025, 0.975])
        output[str(width)] = {"block_days": width, "blocks": len(blocks), "replicates": settings["replicates"],
                              "improvement_c": sum(diffs) / len(diffs), "interval_95_c": [float(low), float(high)]}
    return output


def cold_sequences(samples, predictions):
    groups = []
    for s, p in zip(samples, predictions):
        if s["target"] >= 0:
            continue
        d = date.fromisoformat(s["date"])
        if not groups or d != date.fromisoformat(groups[-1][-1]["date"]) + timedelta(days=1):
            groups.append([])
        groups[-1].append({"date": s["date"], "observed_min_c": s["target"], "forecast_min_c": p["prediction"],
                           "hit": p["prediction"] < 0})
    return [{"start": g[0]["date"], "end": g[-1]["date"], "nights": g,
             "any_detected": any(n["hit"] for n in g), "all_detected": all(n["hit"] for n in g)} for g in groups]


def export_model(model, directory):
    directory.mkdir(parents=True, exist_ok=True)
    name = model["meta"]["name"]
    meta = dict(model["meta"])
    meta["feature_names"] = HOURLY_FEATURE_NAMES if name == "ridge_hourly" else FEATURE_NAMES
    if "offset" in model:
        meta["offset_c"] = model["offset"]
    elif "offsets" in model:
        meta["offsets_c"] = model["offsets"]
    elif name.startswith("ridge"):
        scale, ridge = model["estimator"].steps[0][1], model["estimator"].steps[1][1]
        meta.update(scaler_mean=scale.mean_.tolist(), scaler_scale=scale.scale_.tolist(),
                    coefficients=ridge.coef_.tolist(), intercept=float(ridge.intercept_))
    else:
        model["estimator"].save_model(str(directory / f"{name}.cbm"))
        meta["training_feature_importance"] = dict(zip(FEATURE_NAMES, map(float, model["estimator"].feature_importances_)))
        meta["resolved_parameters"] = model["estimator"].get_all_params()
    write_json(directory / f"{name}.json", meta)
    return meta


def load_research_model(directory, name):
    if name not in NAMES:
        raise ValueError("Unknown research model")
    meta = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    expected = HOURLY_FEATURE_NAMES if name == "ridge_hourly" else FEATURE_NAMES
    if meta["name"] != name or meta["feature_names"] != expected:
        raise ValueError("Research model feature schema mismatch")
    model = {"meta": meta}
    if name in ("identity", "night_min_median"):
        model["offset"] = meta["offset_c"]
    elif name == "lead_hour_median":
        model["offsets"] = meta["offsets_c"]
    elif name.startswith("ridge"):
        model["linear"] = meta
    else:
        from catboost import CatBoostRegressor
        model["estimator"] = CatBoostRegressor()
        model["estimator"].load_model(str(directory / f"{name}.cbm"))
    return model


def audit(samples, hourly, nights, predictions):
    by_year = {}
    for year in sorted({s["year"] for s in samples}):
        subset = [s for s in samples if s["year"] == year]
        vals = [t for s in subset for t in s["target_curve"]]
        by_year[str(year)] = {"paired_nights": len(subset), "cold_nights": sum(s["target"] < 0 for s in subset),
                              "zero_min_nights": sum(s["target"] == 0 for s in subset),
                              "integer_temperature_fraction": sum(t == round(t) for t in vals) / len(vals),
                              "source_counts": dict(Counter(r["target_source"] for r in hourly if r["night_date_local"][:4] == str(year))),
                              "cold_months": dict(Counter(s["date"][5:7] for s in subset if s["target"] < 0))}
    check_year = max(s["year"] for s in samples)
    check = [s for s in samples if s["year"] == check_year]
    details = []
    for i, s in enumerate(check):
        details.append({"date": s["date"], "observed_min_c": s["target"],
                        "observed_min_first_local_hour": (19 + s["target_curve"].index(s["target"])) % 24,
                        "gfs_min_local_hour": (19 + s["curve"].index(min(s["curve"]))) % 24,
                        "forecast_cooling_c": s["curve"][-1] - s["curve"][0],
                        "observed_cooling_c": s["target_curve"][-1] - s["target_curve"][0],
                        "predictions": {name: p[i]["prediction"] for name, p in predictions.items()}})
    return {"by_year": by_year, "excluded_nights": [n for n in nights if not n["eligible"]],
            "historical_cold_or_false_alarm_nights": [r for r in details if r["observed_min_c"] < 0 or any(v < 0 for v in r["predictions"].values())],
            "largest_baseline_errors": sorted(details, key=lambda r: abs(r["predictions"]["identity"] - r["observed_min_c"]), reverse=True)[:10]}


def feature_shift(train, check):
    ranges = {name: {"min": min(s["features"][i] for s in train), "max": max(s["features"][i] for s in train)}
              for i, name in enumerate(FEATURE_NAMES)}
    outside = []
    for s in check:
        violations = {n: s["features"][i] for i, n in enumerate(FEATURE_NAMES)
                      if s["features"][i] < ranges[n]["min"] or s["features"][i] > ranges[n]["max"]}
        if violations:
            outside.append({"date": s["date"], "outside_features": violations})
    return {"training_feature_ranges": ranges, "outside_training_range_nights": outside,
            "note": "Univariate range diagnostic only; no clipping or model/threshold selection"}


def plot_study(summary, check, predictions, report_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    names = summary["protocol"]["candidates"]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), layout="constrained")
    periods = [f["scores"] for f in summary["folds"]] + [summary["historical_scores"]]
    for name in names:
        axes[0].plot([2023, 2024, 2025], [p[name]["mae_c"] for p in periods], "o-", label=name)
    axes[0].set(title="MAE минимума по сезонам", xlabel="Год проверки", ylabel="°C", xticks=[2023, 2024, 2025])
    axes[0].legend(fontsize=8)
    positions = np.arange(len(names))
    for shift, width in zip([-0.15, 0, 0.15], [3, 7, 14]):
        bands = [summary["bootstrap_vs_gfs"][n][str(width)] for n in names]
        values = [b["improvement_c"] for b in bands]
        errors = [[v - b["interval_95_c"][0] for v, b in zip(values, bands)],
                  [b["interval_95_c"][1] - v for v, b in zip(values, bands)]]
        axes[1].errorbar(values, positions + shift, xerr=errors, fmt="o", capsize=3, label=f"Блок {width} дн.")
    axes[1].axvline(0, color="grey", ls="--")
    axes[1].set(yticks=positions, yticklabels=names, xlabel="MAE GFS − MAE метода, °C", title="2025: 95% блочный bootstrap")
    axes[1].legend(fontsize=8)
    selected = summary["selected_model"]
    observed = [s["target"] for s in check]
    for name in ["identity", selected] if selected != "identity" else ["identity"]:
        axes[2].scatter(observed, [p["prediction"] for p in predictions[name]], s=18, alpha=.65, label=name)
    lo = min(observed) - 1
    hi = max(observed) + 1
    axes[2].plot([lo, hi], [lo, hi], color="grey", ls="--")
    axes[2].axhline(0, color="grey", lw=.7)
    axes[2].axvline(0, color="grey", lw=.7)
    axes[2].set(xlabel="Наблюдаемый минимум, °C", ylabel="Прогноз минимума, °C", title=f"2025: GFS и {selected}")
    axes[2].legend(fontsize=8)
    fig.suptitle("Краснодар · 366 парных ночей · 2025 — уже просмотренный исторический контроль", fontsize=12)
    out = report_dir / "assets" / "model-study.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)


def report(summary):
    def fmt(v):
        return "—" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)
    selected = summary["selected_model"]
    base, chosen = summary["historical_scores"]["identity"], summary["historical_scores"][selected]
    band = summary["bootstrap_vs_gfs"][selected]["7"]["interval_95_c"]
    lines = ["# Построение модели и качество: Краснодар, яблоня, заморозки", "",
             f"Сформировано {summary['generated_at_utc']}. Исследовательский расчёт не меняет модель интерфейса.", "",
             "## Основной результат", "",
             f"Выбор по прошлым сезонам: **{selected}**. В историческом контроле 2025 MAE минимума "
             f"{base['mae_c']:.3f} → {chosen['mae_c']:.3f} °C ({(1-chosen['mae_c']/base['mae_c']):.1%} снижения); "
             f"пропуски {base['misses']} → {chosen['misses']} из {chosen['cold_nights']}, ложные тревоги "
             f"{base['false_alarms']} → {chosen['false_alarms']}. MAE холодных ночей "
             f"{base['cold_mae_c']:.3f} → {chosen['cold_mae_c']:.3f} °C. "
             f"95% блочный диапазон выигрыша за 7 дней [{band[0]:.3f}, {band[1]:.3f}] °C; "
             "по этой малой выборке нет основания считать метод готовым к полевому применению.", "",
             "CatBoost имеет меньшую среднюю ошибку на 2025, но он не выиграл заданный выбор на прошлых сезонах; "
             "переключение на него по историческому контролю означало бы подбор по уже просмотренному году. "
             "Средняя MAE, температура холодных ночей и обнаружение заморозка дают разные оценки полезности.", "",
             "## Постановка и честность проверки", "",
             "Цель — температура 2 м у станции 34929. Прогноз одного выпуска GFS 06 UTC на ночь 19–09 МСК; "
             "все методы получают одинаковые 14 часов. Ночные минимумы и почасовая температура — разные цели. "
             "Ridge/CatBoost для минимума предсказывают остаток `наблюдаемый минимум − минимум GFS`; "
             "они не сдвигают всю кривую. Отдельный Ridge обучен на почасовых остатках, его минимум получен из 14 прогнозов.", "",
             "**2025 уже просмотрен в предыдущих отчётах и демонстрации. Это исторический контроль, не новый слепой тест.** "
             "Новые параметры зафиксированы в конфигурации до запуска этой серии; дополнительного поиска по 2025 нет. "
             "Выбор метода использует только прогнозы следующих сезонов: 2022 → 2023 и 2022–2023 → 2024. "
             "Полные ночи и холодные серии не разделяются случайно между обучением и проверкой. "
             "Средняя MAE объединённых валидационных ночей — критерий выбора; при равенстве берётся более простой метод по порядку конфигурации. "
             "Для исторического контроля все методы обучены только на 2022–2023, без дообучения на 2024.", "",
             "## Признаки и параметры", "",
             "12 признаков минимума: минимум, максимум, среднее, диапазон GFS; температуры начала и конца ночи; "
             "изменение за ночь; раннее охлаждение; утренний подъём от минимума; индекс часа минимума; sin/cos календарного дня. "
             "Год не является признаком. Это прогнозные значения, известные в выбранном выпуске, а не будущие измерения. "
             "Почасовой Ridge: текущая прогнозная температура, минимум/диапазон/изменение ночи, sin/cos дня и 14 индикаторов часа. "
             "Стандартизация Ridge обучается только на тренировочной части.", "",
             f"Ridge alpha={summary['protocol']['ridge_alpha']}, SVD. "
             "CatBoost: 200 деревьев, глубина 3, learning_rate 0.03, L2=10, MAE, фиксированный seed; "
             "без early stopping, eval_set и поиска гиперпараметров. Шесть кандидатов включают три прежние базы. "
             "Минимальная сетка намеренно ограничивает число попыток при 184 тренировочных ночах. "
             "Статическая высота/координаты одной станции не варьируются; прошлые измерения не включены из-за неизвестных лагов публикации.", "",
             "## Проверка следующих сезонов", "",
             "| Год | Обучение | Метод | MAE минимума °C | MAE холодных °C | Пропуски/холодных | Ложные тревоги | MAE часа °C |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for f in summary["folds"]:
        for name, m in f["scores"].items():
            lines.append(f"| {f['validation_year']} | {f['train_years']} | {name} | {fmt(m['mae_c'])} | {fmt(m['cold_mae_c'])} | {m['misses']}/{m['cold_nights']} | {m['false_alarms']} | {fmt(m['hourly_mae_c'])} |")
    lines += ["", f"Выбран **{selected}** по объединённой валидации: " + "; ".join(f"{k}: {v:.3f} °C" for k, v in summary["pooled_validation_mae_c"].items()) + ".", "",
              "## Вклад признаков и переобучение", "",
              "Абляция Ridge с тем же alpha=10: каждая группа обучена только на прошлых сезонах. "
              "Это дополнительная исследовательская диагностика после ранее просмотренного 2025; группы не участвуют в выборе победителя.", "",
              "| Признаки остатка минимума | MAE 2023–2024 °C | MAE 2025 °C |", "|---|---:|---:|"]
    for name, values in summary["ridge_ablation"].items():
        lines.append(f"| {name} | {values['pooled_validation_mae_c']:.3f} | {values['historical']['mae_c']:.3f} |")
    lines += ["", "| Метод | MAE на обучении 2022–2023 °C | MAE 2024 °C |", "|---|---:|---:|"]
    for name in summary["protocol"]["candidates"]:
        lines.append(f"| {name} | {summary['training_scores'][name]['mae_c']:.3f} | {summary['folds'][-1]['scores'][name]['mae_c']:.3f} |")
    lines += ["", "Разрыв обучения и следующего сезона включает и подгонку, и изменение погоды; "
              "его нельзя целиком приписать переобучению. Признаки температуры зависимы между собой; "
              "коэффициенты Ridge и встроенные важности CatBoost не доказывают причинное влияние факторов.", "",
              "## Исторический контроль 2025", "",
              "91 полная ночь, 6 холодных ночей в 3 последовательных сериях. Серии — описательная группировка, не доказательство независимости.", "",
              "| Метод | MAE минимума °C | Bias °C | MAE холодных °C | MAE часа °C | Попадания | Пропуски | Ложные тревоги | Recall | Precision |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, m in summary["historical_scores"].items():
        lines.append("| " + " | ".join([name] + [fmt(m[k]) for k in ["mae_c", "bias_c", "cold_mae_c", "hourly_mae_c", "hits", "misses", "false_alarms", "recall", "precision"]]) + " |")
    lines += ["", "Прочерк MAE часа у моделей минимума означает отсутствие почасового прогноза. "
              "MAE часа у night_min_median описывает прежний диагностический сдвиг, не оптимизированный по часам. "
              "Recall/precision относятся к погодному порогу `<0 °C`; они не являются вероятностью повреждения яблони.", "",
              "## Устойчивость выигрыша", "",
              "Положительная разность MAE GFS − MAE метода означает улучшение. Парный bootstrap использует календарные блоки "
              "по 3/7/14 дней, 5000 повторов; пропущенная ночь не сжимает календарь. Пары остаются вместе. "
              "Это приближённая чувствительность к зависимости, не гарантия на новой станции/сезоне. Только один контрольный сезон, "
              "мало блоков и нет поправки на множественные исследовательские сравнения.", "",
              "| Метод | Выигрыш °C | 95% диапазон, 3 дня | 7 дней | 14 дней |", "|---|---:|---|---|---|"]
    for name, values in summary["bootstrap_vs_gfs"].items():
        bands = ["[" + ", ".join(f"{v:.3f}" for v in values[str(b)]["interval_95_c"]) + "]" for b in [3, 7, 14]]
        lines.append(f"| {name} | {values['7']['improvement_c']:.3f} | " + " | ".join(bands) + " |")
    lines += ["", "![Сравнение и устойчивость](assets/model-study.png)", "", "## Где модель ошибается", "",
              "| Ночь | Факт °C | GFS °C | Сдвиг минимума °C | Ridge min °C | CatBoost min °C |", "|---|---:|---:|---:|---:|---:|"]
    for r in summary["audit"]["historical_cold_or_false_alarm_nights"]:
        lines.append("| " + " | ".join([r["date"], fmt(r["observed_min_c"])] + [fmt(r["predictions"][n]) for n in ["identity", "night_min_median", "ridge_min", "catboost_min"]]) + " |")
    lines += ["", "По месяцам, диапазонам прогнозного минимума, холодным сериям, часам и 10 крупнейшим ошибкам "
              "результаты сохранены в JSON. Диагностика по фактическому охлаждению выполняется после ночи и не входит в признаки.", "",
              "Пропуск 7–8 марта сохраняется у всех шести методов: факт −4 °C, GFS +2.909 °C, Ridge +1.195 °C. "
              "Ridge распознаёт 20–21 марта (−0.343 °C вместо GFS +1.374 °C при факте −1 °C), "
              "но для экстремальной ночи 1–2 марта переохлаждает прогноз до −18.047 °C при факте −14 °C. "
              "Исходный минимум GFS −15.200 °C в эту ночь далеко за тренировочным диапазоном "
              f"[{summary['feature_shift']['training_feature_ranges']['gfs_min']['min']:.3f}, "
              f"{summary['feature_shift']['training_feature_ranges']['gfs_min']['max']:.3f}] °C. "
              "Это конкретный риск экстраполяции; однопараметрическая проверка диапазона ещё не является полноценным детектором сдвига данных.", "",
              "На уровне последовательных холодных серий GFS и Ridge обнаруживают хотя бы одну ночь в 2 из 3 серий. "
              "Ночное улучшение 4/6 → 5/6 не означает дополнительной обнаруженной серии заморозков.", "",
              "## Качество целей и чувствительность порога", ""]
    for year, a in summary["audit"]["by_year"].items():
        lines.append(f"- {year}: {a['paired_nights']} парных ночей, холодных {a['cold_nights']}, минимум ровно 0 °C у {a['zero_min_nights']}; "
                     f"{a['integer_temperature_fraction']:.1%} принятых почасовых целей целочисленны; источники всех ожидаемых часов {a['source_counts']} "
                     f"(модельные заполнения исключены); холодные месяцы {a['cold_months']}.")
    lines += ["", "Полнота и источник проверены, но исходные флаги качества независимого станционного ряда не восстановлены. "
              "Грубая дискретность у нуля делает разницу `<0` и `<=0` существенной. В JSON показана диагностика "
              "порогов −2/0/+1 °C с одинаковыми строгими условиями для факта и прогноза. Это проверка чувствительности, "
              "не выбор наиболее выгодного порога и не агрономические пороги.", "",
              "## Температура и правило тревоги", "",
              "Дополнительный запас в правиле тревоги не меняет прогноз температуры. Таблица ниже показывает для выбранного метода "
              "правило `прогноз < 0 + запас`, факт всегда `<0`. Запасы 0/1/2/3 °C заданы в конфигурации; "
              "их стоимость для хозяйства не установлена, оперативный запас не выбран.", "",
              "| Запас °C | Пропуски 2023–2024 | Ложные тревоги 2023–2024 | Пропуски 2025 | Ложные тревоги 2025 |", "|---|---:|---:|---:|---:|"]
    for margin, m in summary["warning_margin_diagnostic"][selected].items():
        lines.append(f"| {margin} | {m['validation']['misses']} | {m['validation']['false_alarms']} | {m['historical']['misses']} | {m['historical']['false_alarms']} |")
    lines += ["", "Вероятности заморозка и интервалы температуры не публикуются: валидация уже участвует в выборе, "
              "и 4 холодные ночи 2024 недостаточны для убедительной отдельной калибровки. Bootstrap выше описывает метрику, а не диапазон прогноза отдельной ночи.", "",
              "## Следующая версия модели", "",
              "1. Добавить из того же заранее доступного выпуска ветер 10 м, точку росы/влажность 2 м и облачность; "
              "проверять единицы, слой, момент действия и S3 gate каждого параметра. Сравнить с температурными признаками отдельной абляцией. "
              "Текущие ошибки сами по себе не доказывают радиационный механизм или полезность этих признаков.",
              "2. Сверить холодные ночи и нули с независимыми первичными наблюдениями/флагами QC. "
              "Записывать фактическую доступность измерений, прежде чем вводить вечерний станционный лаг.",
              "3. Заранее закрепить критерий принятия: выигрыш по минимуму вместе с ограничением пропусков/ложных тревог, "
              "стоимость ошибок согласовать с агрономом. Сравнивать также почасовой метод и исходный GFS.",
              "4. Собирать будущие выдачи и садовые измерения вместе с фазой яблони; новый сезон/другая станция должны стать свежей проверкой. "
              "2025 больше не использовать как слепой тест. Аэропорт и сетка в 8.69 км не заменяют измерения цветков в саду.", "",
              "## Воспроизведение", "", "```powershell", ".\\.venv\\Scripts\\python.exe -m pip install -r requirements-model.txt",
              ".\\.venv\\Scripts\\python.exe -m agro_checker model-study --offline", "```", "",
              "Конфигурация: `config/model_study.json`. Вход — проверенный кэш исходных извлечений и наблюдений; "
              "сетевых запросов в команде нет. Фичи/прогнозы — `data/processed/model-study/`, "
              "обученные модели и закреплённый выбор до расчёта 2025 — `models/research/`. "
              "Хэши протокола, входов, исходного кода и версии пакетов: [MODEL_STUDY_SUMMARY.json](MODEL_STUDY_SUMMARY.json). "
              "Основные таблицы интерфейса и его модель не перезаписываются.", "",
              "Документация: [CatBoostRegressor](https://catboost.ai/docs/en/concepts/python-reference_catboostregressor), "
              "[Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html), "
              "[временная проверка](https://scikit-learn.org/stable/modules/cross_validation.html#time-series-split).", ""]
    return "\n".join(lines)


def run_study(protocol_path, data_dir, report_dir):
    protocol = load_protocol(protocol_path)
    config = load_single_config(ROOT / "config" / "single_run.json")
    if protocol["historical_check_year"] != max(config["years"]):
        raise ValueError("Historical check must be the last configured season")
    points, failures, _, cache_meta = load_points(data_dir, config)
    if failures:
        raise ValueError("Resolve archive failures before the study")
    observations, provenance = {}, []
    for year in config["years"]:
        content, meta = cached_download(f"https://data.meteostat.net/hourly/{year}/{config['station_id']}.csv.gz",
                                       data_dir / "raw" / f"meteostat-{config['station_id']}-{year}.csv.gz", offline=True)
        observations.update(parse_observations(content, config["observation_sources"]))
        provenance.append(meta)
    hourly, nights = build_tables(config, points, observations)
    samples = assemble_samples(hourly, nights)
    folds, selected, pooled = forward_validation(samples, protocol)
    train = [s for s in samples if s["year"] in protocol["final_train_years"]]
    models = {name: fit_model(name, train, protocol) for name in protocol["candidates"]}
    directory = data_dir.parent / "models" / "research"
    exported = {name: export_model(model, directory) for name, model in models.items()}
    model_hashes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                   for f in [directory / f"{name}.json" for name in protocol["candidates"]] + [directory / "catboost_min.cbm"]}
    # Save selection before calculating any 2025 predictions/metrics.
    write_json(directory / "selection.json", {"protocol": protocol, "folds": folds, "selected_model": selected,
                                             "pooled_validation_mae_c": pooled, "models": exported, "model_artifact_sha256": model_hashes,
                                             "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(), "archive_cache": cache_meta})
    check = [s for s in samples if s["year"] == protocol["historical_check_year"]]
    predictions = {name: predict_model(model, check) for name, model in models.items()}
    scores = {name: score(check, values) for name, values in predictions.items()}
    ablation = {}
    groups = {label: [FEATURE_NAMES.index(n) for n in names] for label, names in protocol["diagnostic_ridge_feature_groups"].items()}
    for label, indexes in groups.items():
        validation_scores = []
        for f in protocol["folds"]:
            past = [s for s in samples if s["year"] in f["train_years"]]
            valid = [s for s in samples if s["year"] == f["validation_year"]]
            validation_scores.append(score(valid, predict_model(fit_ablation(past, indexes, protocol), valid)))
        ablation[label] = {"features": [FEATURE_NAMES[i] for i in indexes], "validation_scores": validation_scores,
                           "pooled_validation_mae_c": sum(m["mae_c"] * m["nights"] for m in validation_scores) / sum(m["nights"] for m in validation_scores),
                           "historical": score(check, predict_model(fit_ablation(train, indexes, protocol), check)),
                           "role": "diagnostic_only; excluded from selection"}
    margins = {name: {} for name in protocol["candidates"]}
    for margin in protocol["warning_margins_c"]:
        totals = {name: Counter(hits=0, misses=0, false_alarms=0, correct_negatives=0, cold_nights=0, nights=0) for name in protocol["candidates"]}
        for f in folds:
            for name in protocol["candidates"]:
                m = f["warning_margins"][str(margin)][name]
                totals[name].update({k: m[k] for k in totals[name]})
        for name in protocol["candidates"]:
            margins[name][str(margin)] = {"validation": dict(totals[name]), "historical": score(check, predictions[name], warning_margin=margin)}
    summary = {"generated_at_utc": datetime.now(UTC).isoformat(), "protocol": protocol,
               "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
               "source_sha256": {name: hashlib.sha256((ROOT / "agro_checker" / name).read_bytes()).hexdigest()
                                  for name in ["model_study.py", "single_run.py", "pipeline.py", "gfs_archive.py"]},
               "source_config": config, "source_config_sha256": hashlib.sha256((ROOT / "config" / "single_run.json").read_bytes()).hexdigest(),
               "model_artifact_sha256": model_hashes,
               "environment": {"python": platform.python_version(), "platform": platform.platform()},
               "packages": {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scipy", "scikit-learn", "catboost", "matplotlib"]},
               "archive_cache": cache_meta, "observation_provenance": provenance, "features": FEATURE_NAMES,
               "folds": folds, "selected_model": selected, "pooled_validation_mae_c": pooled, "models": exported,
               "historical_scores": scores, "training_scores": {n: score(train, predict_model(m, train)) for n, m in models.items()},
               "ridge_ablation": ablation, "feature_shift": feature_shift(train, check),
               "bootstrap_vs_gfs": {n: paired_block_bootstrap(check, predictions["identity"], p, protocol["bootstrap"]) for n, p in predictions.items()},
               "cold_sequences": {n: cold_sequences(check, p) for n, p in predictions.items()},
               "warning_margin_diagnostic": margins,
               "threshold_sensitivity": {str(t): {n: score(check, p, threshold=t) for n, p in predictions.items()} for t in protocol["event_thresholds_c"]},
               "audit": audit(samples, hourly, nights, predictions)}
    summary["historical_by_month"] = {month: {n: score([s for s in check if s["date"][5:7] == month], [v for v in p if v["date"][5:7] == month])
                                                   for n, p in predictions.items()} for month in ["03", "04", "05"]}
    summary["historical_by_lead"] = {}
    for i in range(14):
        summary["historical_by_lead"][str(10 + i)] = {}
        for name, values in predictions.items():
            if values[0]["curve"] is None:
                continue
            errors = [p["curve"][i] - s["target_curve"][i] for s, p in zip(check, values)]
            summary["historical_by_lead"][str(10 + i)][name] = {"local_hour": (19 + i) % 24,
                                                              "hours": len(errors), "mae_c": sum(map(abs, errors)) / len(errors),
                                                              "bias_c": sum(errors) / len(errors)}
    bins = [("below_0", -math.inf, 0), ("0_to_3", 0, 3), ("3_to_10", 3, 10), ("10_plus", 10, math.inf)]
    summary["historical_by_gfs_min"] = {}
    for label, low, high in bins:
        indexes = [i for i, s in enumerate(check) if low <= min(s["curve"]) < high]
        if indexes:
            summary["historical_by_gfs_min"][label] = {n: score([check[i] for i in indexes], [p[i] for i in indexes]) for n, p in predictions.items()}
    processed = data_dir / "processed" / "model-study"
    write_csv(processed / "features.csv", [{"date": s["date"], "year": s["year"], **dict(zip(FEATURE_NAMES, s["features"])), "observed_min_c": s["target"]} for s in samples])
    write_csv(processed / "historical_predictions.csv", [{"date": s["date"], "observed_min_c": s["target"], **{n: p[i]["prediction"] for n, p in predictions.items()}} for i, s in enumerate(check)])
    report_dir.mkdir(parents=True, exist_ok=True)
    plot_study(summary, check, predictions, report_dir)
    write_json(report_dir / "MODEL_STUDY_SUMMARY.json", summary)
    (report_dir / "MODEL_STUDY_REPORT.md").write_text(report(summary), encoding="utf-8")
    return summary
