"""Chronological calibration and evaluation of a common GFS run for each night."""

from datetime import date, datetime, timedelta
import hashlib
import json
from statistics import median

from .gfs_archive import collect, issued_at, retry_failed, run_at
from .pipeline import (
    ROOT, UTC, cached_download, metrics, night_hours, number, parse_observations,
    season_dates, split_for, write_csv, write_json,
)


def load_single_config(path):
    c = json.loads(path.read_text(encoding="utf-8"))
    if c["timezone"] != "Europe/Moscow" or c["utc_offset_hours"] != 3:
        raise ValueError("Single-run pilot requires Europe/Moscow UTC+3")
    if c["run_hour_utc"] not in (0, 6, 12, 18):
        raise ValueError("Invalid GFS cycle")
    if not (0 <= c["night_end_hour_exclusive"] < c["night_start_hour"] <= 23):
        raise ValueError("Night must cross midnight")
    if not 0 <= c["warning_hour_local"] < c["night_start_hour"]:
        raise ValueError("Warning must precede the night")
    if c["archive_availability_buffer_minutes"] < 10:
        raise ValueError("Archive availability buffer must be at least 10 minutes")
    splits = [c[f"{name}_years"] for name in ("development", "validation", "test")]
    if any(not s for s in splits):
        raise ValueError("Empty temporal split")
    flattened = sum(splits, [])
    if len(set(flattened)) != len(flattened) or set(flattened) != set(c["years"]) or len(set(c["years"])) != len(c["years"]):
        raise ValueError("Split years must partition years")
    if not max(splits[0]) < min(splits[1]) or not max(splits[1]) < min(splits[2]):
        raise ValueError("Splits must be ordered in time")
    if min(c["years"]) < 2022 or max(c["years"]) > 2025:
        raise ValueError("Archive pilot supports completed seasons 2022–2025")
    if c["correction_candidates"] != ["identity", "night_min_median", "lead_hour_median"]:
        raise ValueError("Unexpected correction candidates")
    if c["selection_metric"] != "validation_night_min_mae_c":
        raise ValueError("Unexpected selection metric")
    threshold = number(c["weather_threshold_c"])
    if threshold is None:
        raise ValueError("Threshold must be finite")
    for year in c["years"]:
        dates = list(season_dates(c, year))
        if not dates or dates[-1].month == 12:
            raise ValueError("Season must be nonempty and cannot cross the observation year")
        if run_at(c, dates[0]) >= issued_at(c, dates[0]):
            raise ValueError("Run initialization must precede warning time")
    return c


def seal_points(data_dir, config, rows, failures):
    """Local extraction cache. Raw range files retain separate SHA-256 provenance."""
    path = data_dir / "raw" / "single-run-points.json"
    write_json(path, {"config": config, "rows": rows, "failures": failures})
    write_json(path.with_name(path.name + ".meta.json"),
               {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "decoder": "eccodes-2.49.0",
                "manifest_sha256": hashlib.sha256((data_dir / "raw" / "single-run-manifest.json").read_bytes()).hexdigest()})


def load_points(data_dir, config):
    path = data_dir / "raw" / "single-run-points.json"
    content = path.read_bytes()
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text(encoding="utf-8"))
    manifest_bytes = (data_dir / "raw" / "single-run-manifest.json").read_bytes()
    if hashlib.sha256(content).hexdigest() != meta["sha256"] or hashlib.sha256(manifest_bytes).hexdigest() != meta["manifest_sha256"]:
        raise ValueError("Single-run extraction cache hash mismatch")
    points = json.loads(content)
    if points["config"] != config:
        raise ValueError("Single-run extraction config mismatch; use a new --data-dir")
    manifest = json.loads(manifest_bytes)
    return points["rows"], points["failures"], manifest["downloads"], meta


def build_tables(config, points, observations):
    by_time = {}
    for row in points:
        stamp = datetime.fromisoformat(row["valid_at_utc"])
        if stamp in by_time:
            raise ValueError("Duplicate single-run valid time")
        by_time[stamp] = row
    hourly, nights = [], []
    for year in config["years"]:
        for evening in season_dates(config, year):
            rows = []
            for stamp in night_hours(config, evening):
                point = by_time.get(stamp)
                obs = observations.get(stamp, {})
                run, issue = run_at(config, evening), issued_at(config, evening)
                expected_lead = int((stamp - run).total_seconds() / 3600)
                if point:
                    if (point["forecast_run_at_utc"] != run.isoformat() or point["issued_at_utc"] != issue.isoformat()
                            or point["night_date_local"] != evening.isoformat() or point["lead_hours"] != expected_lead):
                        raise ValueError("Mixed or incorrect run in night")
                    modified = datetime.fromisoformat(point["archive_last_modified_at_utc"])
                    gate = modified + timedelta(minutes=config["archive_availability_buffer_minutes"])
                    if point["availability_gate_at_utc"] != gate.isoformat() or point["available_by_issue_proxy"] != (gate <= issue):
                        raise ValueError("Incorrect archive availability gate")
                    if modified < run:
                        raise ValueError("Archive timestamp precedes GFS initialization")
                forecast = point["forecast_temperature_c"] if point and point["available_by_issue_proxy"] else None
                rows.append({"night_date_local": evening.isoformat(), "split": split_for(config, year),
                             "valid_at_utc": stamp.isoformat(), "forecast_run_at_utc": run.isoformat(),
                             "issued_at_utc": issue.isoformat(), "lead_hours": expected_lead,
                             "forecast_temperature_c": forecast, "target_temperature_c": obs.get("temperature_c"),
                             "target_source": obs.get("source", ""),
                             "archive_last_modified_at_utc": point["archive_last_modified_at_utc"] if point else None,
                             "availability_gate_at_utc": point["availability_gate_at_utc"] if point else None,
                             "forecast_available_at_utc": None,
                             "status": "missing_forecast" if point is None else "available_by_proxy" if forecast is not None else "too_late_by_proxy"})
            observed = [r["target_temperature_c"] for r in rows if r["target_temperature_c"] is not None]
            predicted = [r["forecast_temperature_c"] for r in rows if r["forecast_temperature_c"] is not None]
            obs_complete, fc_complete = len(observed) == len(rows), len(predicted) == len(rows)
            nights.append({"night_date_local": evening.isoformat(), "year": year, "split": split_for(config, year),
                           "forecast_run_at_utc": run_at(config, evening).isoformat(), "issued_at_utc": issued_at(config, evening).isoformat(),
                           "expected_hours": len(rows), "observed_hours": len(observed), "forecast_hours": len(predicted),
                           "late_forecast_hours": sum(r["status"] == "too_late_by_proxy" for r in rows),
                           "observed_min_c": min(observed) if obs_complete else None,
                           "forecast_min_c": min(predicted) if fc_complete else None,
                           "eligible": obs_complete and fc_complete})
            hourly.extend(rows)
    return hourly, nights


def fit_correction(name, train_hours, train_nights):
    if not train_nights or not train_hours or any(r["split"] != "development" for r in train_nights + train_hours):
        raise ValueError("Correction can fit on development data only")
    if any(not n["eligible"] for n in train_nights):
        raise ValueError("Training requires full paired nights")
    model = {"name": name, "trained_nights": len(train_nights), "trained_hours": len(train_hours),
             "training_last_valid_at_utc": max(r["valid_at_utc"] for r in train_hours)}
    if name == "identity":
        model["offset_c"] = 0.0
    elif name == "night_min_median":
        model["offset_c"] = median(n["observed_min_c"] - n["forecast_min_c"] for n in train_nights)
    elif name == "lead_hour_median":
        leads = sorted(set(r["lead_hours"] for r in train_hours))
        model["offset_by_lead_c"] = {str(lead): median(r["target_temperature_c"] - r["forecast_temperature_c"]
                                                     for r in train_hours if r["lead_hours"] == lead) for lead in leads}
    else:
        raise ValueError("Unknown correction")
    return model


def corrected_temperature(model, row):
    # Prediction depends only on forecast and its lead, never on the later target.
    if row["forecast_temperature_c"] is None:
        return None
    offset = model.get("offset_c")
    if offset is None:
        offset = model["offset_by_lead_c"][str(row["lead_hours"])]
    return row["forecast_temperature_c"] + offset


def evaluate_model(config, model, hourly, nights):
    corrected = [{**r, "forecast_temperature_c": corrected_temperature(model, r)} for r in hourly]
    by_night = {}
    for row in corrected:
        if row["forecast_temperature_c"] is not None:
            by_night.setdefault(row["night_date_local"], []).append(row["forecast_temperature_c"])
    predicted_nights = [{**n, "forecast_min_c": min(by_night[n["night_date_local"]])
                        if n["forecast_hours"] == n["expected_hours"] else None} for n in nights]
    result = {}
    for split in ("development", "validation", "test"):
        selected_nights = [n for n in predicted_nights if n["split"] == split]
        paired_dates = {n["night_date_local"] for n in selected_nights if n["eligible"]}
        selected_hours = [r for r in corrected if r["night_date_local"] in paired_dates]
        result[split] = metrics(selected_nights, selected_hours, config["weather_threshold_c"])
    return result, corrected, predicted_nights


def compare(config, hourly, nights):
    train_nights = [n for n in nights if n["split"] == "development" and n["eligible"]]
    train_dates = {n["night_date_local"] for n in train_nights}
    train_hours = [r for r in hourly if r["night_date_local"] in train_dates]
    models = [fit_correction(name, train_hours, train_nights) for name in config["correction_candidates"]]
    evaluations = {m["name"]: evaluate_model(config, m, hourly, nights) for m in models}
    if not evaluations["identity"][0]["validation"]["paired_nights"] or not evaluations["identity"][0]["test"]["paired_nights"]:
        raise ValueError("Validation and test require full paired nights")
    # Select without consulting test scores. Fixed complexity order breaks ties.
    winner = min(enumerate(models), key=lambda pair: (evaluations[pair[1]["name"]][0]["validation"]["night_min_mae_c"], pair[0]))[1]
    return {"models": models, "selected_model": winner,
            "selection_target": "night_minimum_temperature_only",
            "operational_status": "experimental; no demonstrated improvement in frost detection",
            "selection_rule": "Minimum validation night-minimum MAE (rounded to 4 decimals); ties: identity, global, by-lead. No test fitting or refitting.",
            "scores": {name: evaluation[0] for name, evaluation in evaluations.items()}}, evaluations


def run_single(config, data_dir, report_dir, offline=False, workers=6):
    points_path = data_dir / "raw" / "single-run-points.json"
    if not points_path.exists():
        if offline:
            raise ValueError("No extracted single-run cache; run online first")
        points, failures, _ = collect(config, data_dir, offline, workers)
        seal_points(data_dir, config, points, failures)
    points, failures, downloads, cache_meta = load_points(data_dir, config)
    if failures and not offline:
        print(f"Retrying {len(failures)} failed archive hours before comparison", flush=True)
        points, failures, downloads = retry_failed(config, data_dir, points, failures, downloads, workers)
        seal_points(data_dir, config, points, failures)
        points, failures, downloads, cache_meta = load_points(data_dir, config)
    observations, observation_manifest = {}, []
    for year in config["years"]:
        content, meta = cached_download(f"https://data.meteostat.net/hourly/{year}/{config['station_id']}.csv.gz",
                                        data_dir / "raw" / f"meteostat-{config['station_id']}-{year}.csv.gz", offline)
        observations.update(parse_observations(content, config["observation_sources"]))
        observation_manifest.append(meta)
    hourly, nights = build_tables(config, points, observations)
    comparison, evaluations = compare(config, hourly, nights)
    processed = data_dir / "processed" / "single-run"
    write_csv(processed / "hourly.csv", hourly)
    write_csv(processed / "nights.csv", nights)
    model = comparison["selected_model"]
    _, selected_hours, selected_nights = evaluations[model["name"]]
    write_csv(processed / "corrected_hourly.csv", selected_hours)
    write_csv(processed / "corrected_nights.csv", selected_nights)
    write_json(ROOT / "models" / "single-run-correction.json", {"config": config, **comparison})
    grids = sorted({(p["grid_latitude"], p["grid_longitude"], p["grid_distance_km"]) for p in points})
    summary = {"generated_at_utc": datetime.now(UTC).isoformat(), "config": config, **comparison,
               "archive": {"requested_hours": sum(n["expected_hours"] for n in nights), "extracted_hours": len(points),
                           "failed_hours": len(failures), "failures": failures,
                           "late_hours_by_proxy": sum(r["status"] == "too_late_by_proxy" for r in hourly),
                           "download_count": len(downloads), "download_bytes": sum(d["bytes"] for d in downloads),
                           "extraction_cache": cache_meta, "example_grib_provenance": next((d for d in downloads if "range" in d), None)},
               "observation_downloads": observation_manifest, "grids": grids,
               "excluded_nights": [n for n in nights if not n["eligible"]],
               "per_year_baseline": {str(year): metrics([n for n in nights if n["year"] == year],
                                                        [r for r in hourly if date.fromisoformat(r["night_date_local"]).year == year
                                                         and any(n["night_date_local"] == r["night_date_local"] and n["eligible"] for n in nights)],
                                                        config["weather_threshold_c"]) for year in config["years"]}}
    write_json(processed / "summary.json", summary)
    write_json(report_dir / "SINGLE_RUN_SUMMARY.json", summary)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "SINGLE_RUN_REPORT.md").write_text(report(summary), encoding="utf-8")
    return summary


def report(summary):
    c = summary["config"]
    archive = summary["archive"]
    lines = ["# Один выпуск GFS на ночь: коррекция температуры", "",
             f"Сформировано: {summary['generated_at_utc']}. Станция Краснодар {c['station_id']}; яблоня, заморозки.", "",
             f"На каждую ночь выбран исходный выпуск NOAA GFS 0.25° в {c['run_hour_utc']:02}:00 UTC. "
             f"Сценарное предупреждение: {c['warning_hour_local']:02}:00 по Москве; ночь {c['night_start_hour']:02}:00–{c['night_end_hour_exclusive']:02}:00 следующего дня, конец исключён. "
             "Все 14 температур каждой ночи взяты из одного выпуска; это обычная температура 2 м, не минимум за накопительный интервал.", "",
             "## Источник и доступность", "",
             f"Извлечено {archive['extracted_hours']} из {archive['requested_hours']} часов; ошибок загрузки {archive['failed_hours']}; "
             f"часов с запоздалым архивным timestamp {archive['late_hours_by_proxy']}. "
             f"Получено {archive['download_bytes'] / 1024**3:.2f} GiB индексов и температурных GRIB-сообщений вместо полных файлов.", "",
             f"Ячейка/расстояние до станции (широта, долгота, км): {summary['grids']}. "
             "Выбрана ближайшая точка исходной сетки 0.25°, без корректировки высоты. Эта база отличается от предыдущей базы Open-Meteo.", "",
             "Загрузка HTTP Range подтверждается `206`, точным `Content-Range`, размером и SHA-256. "
             "В GRIB проверяются центр NOAA, параметр/высота/единицы, сетка, время выпуска и действия. Индексы и метаданные сохранены локально.", "",
             f"Допуск в исторический расчёт: `S3 Last-Modified + {c['archive_availability_buffer_minutes']} минут <= issued_at`. "
             "Это свидетельство времени записи текущей версии объекта в архив, с предположением о публичном доступе в тот момент. "
             "Оно не является журналом первой фактической доступности у NOAA/в саду. Поле `forecast_available_at_utc` оставлено пустым; "
             "время архива и допуск сохраняются отдельно. Поздние версии или пропуски исключаются, другой выпуск не подставляется.", "",
             "Наблюдения: только `isd_lite`/`metar`; нужны все 14 часов прогноза и целей. "
             "Наблюдения не являются входом коррекции. Риск повреждения яблони и фаза развития не оценены. "
             f"Погодное событие — минимум < {c['weather_threshold_c']} °C.", "",
             "## Обучение и выбор", "",
             f"Обучение: {c['development_years']}; выбор метода: {c['validation_years']}; отложенная проверка: {c['test_years']}.", "",
             "Кандидаты заданы до оценки: исходный GFS; единое медианное смещение ночного минимума; "
             "медианное смещение для каждого часа заблаговременности. Смещения обучены только на полных ночах первой части. "
             "Выбор — минимальная MAE ночного минимума на валидации; при равенстве предпочтение более простому методу. "
             "Отложенные цели не участвуют ни в подборе коэффициентов, ни в выборе. После выбора переобучение не выполнялось.", "",
             f"Выбран: **{summary['selected_model']['name']}**. Параметры: `{json.dumps(summary['selected_model'], ensure_ascii=False)}`.", "",
             "## Сравнение на одинаковых ночах", "",
             "| Период | Метод | Парных ночей | Холодных | MAE минимума, °C | MAE холодных, °C | MAE часа, °C | Пропуски | Ложные тревоги |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for split in ("development", "validation", "test"):
        for name, scores in summary["scores"].items():
            m = scores[split]
            keys = ("paired_nights", "paired_cold_nights", "night_min_mae_c", "cold_night_min_mae_c", "hourly_mae_c", "misses", "false_alarms")
            lines.append("| " + " | ".join([split, name] + ["—" if m[k] is None else str(m[k]) for k in keys]) + " |")
    base = summary["scores"]["identity"]["test"]
    selected = summary["scores"][summary["selected_model"]["name"]]["test"]
    difference = base["night_min_mae_c"] - selected["night_min_mae_c"]
    lines += ["", f"На отложенном периоде изменение MAE минимума (база минус выбранный метод): {difference:.4f} °C. "
              "Положительное число означает снижение ошибки; отрицательное — ухудшение. "
              "Показываем отдельно изменения пропусков и ложных тревог: улучшение средней ошибки не гарантирует лучшую защиту.", "",
              f"Пропуски на тесте: {base['misses']} → {selected['misses']}; ложные тревоги: {base['false_alarms']} → {selected['false_alarms']}; "
              f"почасовая MAE: {base['hourly_mae_c']} → {selected['hourly_mae_c']} °C. "
              "Сдвиг, выбранный для ночного минимума, применяется к кривой только для диагностики; он не оптимизирует каждый час. "
              "Результат не подтверждает улучшение обнаружения заморозков. Перед оперативным применением необходимы проверка по холодным событиям и данные сада.", "",
              "## Границы результата", "",
              "- Сравниваются база и коррекция одного источника, выпуска, ячейки и ночей. Результаты первой проверки фиксированных 24 часов не объединяются с этими.",
              "- Холодные события редки и могут быть связаны между собой. Число ночей не равно числу независимых заморозков; 0 °C не является порогом повреждения яблони.",
              "- Коррекция проверена у станции аэропорта; температура цветков и сада не измеряется. Диапазоны неопределённости и вероятности не рассчитаны.",
              "- Обучение использует пересматриваемый станционный архив как цель. Время доступности наблюдений не восстановлено; оперативные лаги не используются.",
              "- Публичная доступность архива в исторический момент предполагается. Для строгой оперативной проверки сохранять реальные выдачи и ответы при получении.", "",
              "## Воспроизведение", "", "```powershell", ".\\.venv\\Scripts\\python.exe -m pip install -r requirements-archive.txt",
              ".\\.venv\\Scripts\\python.exe -m agro_checker single-run", ".\\.venv\\Scripts\\python.exe -m agro_checker single-run --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker single-replay --night 2024-03-09", "```", "",
              "Таблицы: `data/processed/single-run/`; исходные сообщения: `data/raw/gfs-single/`; "
              "полный манифест: `data/raw/single-run-manifest.json`; коэффициенты: `models/single-run-correction.json`. "
              "[Сводка и параметры](SINGLE_RUN_SUMMARY.json).", "",
              "Источники: [NOAA GFS/AWS](https://registry.opendata.aws/noaa-gfs-bdp-pds/), "
              "[S3 metadata](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html), "
              "[ecCodes](https://github.com/ecmwf/eccodes-python).", ""]
    return "\n".join(lines)


def replay_single(config, data_dir, night):
    date.fromisoformat(night)
    summary = json.loads((data_dir / "processed" / "single-run" / "summary.json").read_text(encoding="utf-8"))
    if summary["config"] != config:
        raise ValueError("Single-run summary configuration mismatch")
    import csv
    with (data_dir / "processed" / "single-run" / "hourly.csv").open(encoding="utf-8", newline="") as file:
        rows = [r for r in csv.DictReader(file) if r["night_date_local"] == night]
    if not rows:
        raise ValueError("Night not found; run single-run first")
    model = summary["selected_model"]
    # Training-period replays are fitted, validation is model-selection data.
    split = rows[0]["split"]
    if split == "development":
        evaluation_status = "in_sample_training_replay"
    elif split == "validation":
        evaluation_status = "model_selection_replay"
    else:
        evaluation_status = "held_out_test_replay"
    result = []
    for r in rows:
        forecast = number(r["forecast_temperature_c"])
        corrected = corrected_temperature(model, {"forecast_temperature_c": forecast, "lead_hours": int(r["lead_hours"])})
        result.append({"valid_at_utc": r["valid_at_utc"], "base_temperature_c": forecast,
                       "corrected_temperature_c": corrected, "availability_status": r["status"]})
    complete = all(r["base_temperature_c"] is not None for r in result)
    return {"night_date_local": night, "split": split, "evaluation_status": evaluation_status,
            "correction_target": "night_minimum_only; hourly curve shift is diagnostic",
            "forecast_run_at_utc": rows[0]["forecast_run_at_utc"], "scenario_issued_at_utc": rows[0]["issued_at_utc"],
            "forecast_available_at_utc": None, "availability_evidence": "archive_timestamp_proxy",
            "phase": config["phase"], "crop_damage_risk": "not_assessed", "model": model,
            "forecast_complete": complete,
            "base_min_c": min(r["base_temperature_c"] for r in result) if complete else None,
            "corrected_min_c": min(r["corrected_temperature_c"] for r in result) if complete else None,
            "forecast": result,
            "hindsight_observations": [{"valid_at_utc": r["valid_at_utc"], "temperature_c": number(r["target_temperature_c"]),
                                       "source": r["target_source"]} for r in rows]}
