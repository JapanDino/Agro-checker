"""Data audit and fixed-lead diagnostic. No operational warning or crop-loss model."""

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["timezone"] != "Europe/Moscow" or config["utc_offset_hours"] != 3:
        raise ValueError("This pilot supports Europe/Moscow (UTC+3) only")
    if not config["years"] or len(set(config["years"])) != len(config["years"]):
        raise ValueError("Years must be nonempty and unique")
    if config["forecast_mode"] != "fixed_lead_24h_diagnostic" or config["forecast_variable"] != "temperature_2m_previous_day1":
        raise ValueError("This pipeline only supports the 24-hour diagnostic")
    start, end = config["night_start_hour"], config["night_end_hour_exclusive"]
    if not (0 <= end < start <= 23):
        raise ValueError("Night must cross midnight")
    if not math.isfinite(config["weather_threshold_c"]):
        raise ValueError("Threshold must be finite")
    splits = sum((config[f"{name}_years"] for name in ("development", "validation", "test")), [])
    if len(set(splits)) != len(splits) or set(splits) != set(config["years"]):
        raise ValueError("Split years must partition the configured years")
    if any(not config[f"{name}_years"] for name in ("development", "validation", "test")):
        raise ValueError("All temporal splits must be nonempty")
    if not (max(config["development_years"]) < min(config["validation_years"]) and
            max(config["validation_years"]) < min(config["test_years"])):
        raise ValueError("Temporal splits must be ordered development, validation, test")
    for year in config["years"]:
        if year < 2021 or year > 2025:
            raise ValueError("This completed-season experiment supports 2021–2025")
        if date.fromisoformat(f"{year}-{config['season_start']}") > date.fromisoformat(f"{year}-{config['season_end']}"):
            raise ValueError("Season must stay within one calendar year")
    return config


def cached_download(url, path, offline=False, fetcher=None):
    """Cache immutable bytes with provenance; never silently trust an altered file."""
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists():
        content = path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta["url"] != url or meta["sha256"] != hashlib.sha256(content).hexdigest():
            raise ValueError(f"Cache mismatch: {path}. Use a new --data-dir to download again.")
        return content, meta
    if offline:
        raise ValueError(f"Missing cached download: {path}")
    for attempt in range(3):
        try:
            if fetcher is None:
                request = Request(url, headers={"User-Agent": "Agro-checker-research/0.1"})
                with urlopen(request, timeout=60) as response:
                    content = response.read()
                    content_type = response.headers.get("Content-Type", "")
            else:
                content, headers = fetcher(url)
                content_type = headers.get("Content-Type", "")
            break
        except (HTTPError, URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    meta = {"url": url, "sha256": hashlib.sha256(content).hexdigest(),
            "retrieved_at_utc": datetime.now(UTC).isoformat(), "bytes": len(content),
            "content_type": content_type}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(content)
    temp.replace(path)
    write_json(meta_path, meta)
    return content, meta


def number(value):
    if value is None or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        return None
    return result


def parse_observations(content, allowed_sources):
    reader = csv.DictReader(io.StringIO(gzip.decompress(content).decode("utf-8-sig")))
    required = {"year", "month", "day", "hour", "temp", "temp_source"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Meteostat CSV schema has changed")
    records = {}
    for row in reader:
        stamp = datetime(*(int(row[key]) for key in ("year", "month", "day", "hour")), tzinfo=UTC)
        if stamp in records:
            raise ValueError(f"Duplicate observation timestamp: {stamp}")
        source = row["temp_source"]
        value = number(row["temp"])
        records[stamp] = {"raw_temperature_c": value, "source": source,
                          "temperature_c": value if source in allowed_sources else None}
    return records


def parse_forecast(content, variable):
    payload = json.loads(content)
    if payload.get("error"):
        raise ValueError(payload.get("reason", "Forecast API error"))
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("Forecast timestamps must be UTC")
    if payload["hourly_units"][variable] != "°C":
        raise ValueError("Forecast temperature unit must be Celsius")
    stamps, values = payload["hourly"]["time"], payload["hourly"][variable]
    if len(stamps) != len(values):
        raise ValueError("Forecast arrays differ in length")
    records = {}
    for stamp, value in zip(stamps, values):
        stamp = datetime.fromisoformat(stamp)
        stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
        if stamp in records:
            raise ValueError(f"Duplicate forecast timestamp: {stamp}")
        records[stamp] = number(value)
    grid = {key: payload.get(key) for key in ("latitude", "longitude", "elevation", "timezone", "utc_offset_seconds")}
    return records, grid


def season_dates(config, year):
    current = date.fromisoformat(f"{year}-{config['season_start']}")
    end = date.fromisoformat(f"{year}-{config['season_end']}")
    while current <= end:
        yield current
        current += timedelta(days=1)


def night_hours(config, evening):
    local = timezone(timedelta(hours=config["utc_offset_hours"]))
    start = datetime.combine(evening, datetime.min.time(), local) + timedelta(hours=config["night_start_hour"])
    count = 24 - config["night_start_hour"] + config["night_end_hour_exclusive"]
    return [(start + timedelta(hours=i)).astimezone(UTC) for i in range(count)]


def split_for(config, year):
    return next(name for name in ("development", "validation", "test") if year in config[f"{name}_years"])


def build_nights(config, year, observations, forecasts):
    hourly, nights = [], []
    for evening in season_dates(config, year):
        stamps = night_hours(config, evening)
        observed, predicted = [], []
        for stamp in stamps:
            obs = observations.get(stamp, {})
            target, forecast = obs.get("temperature_c"), forecasts.get(stamp)
            if target is not None:
                observed.append(target)
            if forecast is not None:
                predicted.append(forecast)
            hourly.append({"night_date_local": evening.isoformat(), "split": split_for(config, year),
                           "valid_at_utc": stamp.isoformat(), "forecast_temperature_c": forecast,
                           "target_temperature_c": target, "target_source": obs.get("source", ""),
                           "lead_hours_nominal": 24, "forecast_run_at_utc": None,
                           "forecast_available_at_utc": None, "issued_at_utc": None,
                           "mode": config["forecast_mode"]})
        complete_obs, complete_fc = len(observed) == len(stamps), len(predicted) == len(stamps)
        nights.append({"night_date_local": evening.isoformat(), "year": year, "split": split_for(config, year),
                       "expected_hours": len(stamps), "observed_hours": len(observed), "forecast_hours": len(predicted),
                       "observed_min_c": min(observed) if complete_obs else None,
                       "forecast_min_c": min(predicted) if complete_fc else None,
                       "eligible": complete_obs and complete_fc})
    return hourly, nights


def episode_count(nights, threshold):
    """Consecutive cold nights; missing night breaks continuity, not independence."""
    cold = sorted(date.fromisoformat(n["night_date_local"]) for n in nights
                  if n["observed_min_c"] is not None and n["observed_min_c"] < threshold)
    return sum(i == 0 or d - cold[i - 1] != timedelta(days=1) for i, d in enumerate(cold))


def metrics(nights, hourly, threshold):
    paired = [n for n in nights if n["eligible"]]
    errors = [n["forecast_min_c"] - n["observed_min_c"] for n in paired]
    cold_errors = [n["forecast_min_c"] - n["observed_min_c"] for n in paired if n["observed_min_c"] < threshold]
    confusion = Counter()
    for n in paired:
        actual, predicted = n["observed_min_c"] < threshold, n["forecast_min_c"] < threshold
        confusion["hits" if actual and predicted else "misses" if actual else "false_alarms" if predicted else "correct_negatives"] += 1
    matched_hours = [h for h in hourly if h["forecast_temperature_c"] is not None and h["target_temperature_c"] is not None]
    mean = lambda values: round(sum(values) / len(values), 4) if values else None
    return {"expected_nights": len(nights), "complete_observation_nights": sum(n["observed_min_c"] is not None for n in nights),
            "paired_nights": len(paired), "excluded_nights": len(nights) - len(paired),
            "observed_cold_nights": sum(n["observed_min_c"] is not None and n["observed_min_c"] < threshold for n in nights),
            "paired_cold_nights": len(cold_errors), "cold_night_sequences": episode_count(nights, threshold),
            "night_min_mae_c": mean([abs(e) for e in errors]), "night_min_bias_c": mean(errors),
            "cold_night_min_mae_c": mean([abs(e) for e in cold_errors]),
            "hourly_pairs": len(matched_hours),
            "hourly_mae_c": mean([abs(h["forecast_temperature_c"] - h["target_temperature_c"]) for h in matched_hours]),
            **{key: confusion[key] for key in ("hits", "misses", "false_alarms", "correct_negatives")}}


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build(config, data_dir, offline):
    all_hourly, all_nights, audits, manifest = [], [], [], []
    for year in config["years"]:
        print(f"{year}: observations and GFS forecast", flush=True)
        obs_url = f"https://data.meteostat.net/hourly/{year}/{config['station_id']}.csv.gz"
        content, meta = cached_download(obs_url, data_dir / "raw" / f"meteostat-{config['station_id']}-{year}.csv.gz", offline)
        manifest.append(meta)
        observations = parse_observations(content, config["observation_sources"])
        # Include the morning after the final evening, and preserve UTC boundaries.
        start = f"{year}-{config['season_start']}"
        end = (date.fromisoformat(f"{year}-{config['season_end']}") + timedelta(days=1)).isoformat()
        params = {"latitude": config["latitude"], "longitude": config["longitude"], "elevation": config["elevation_m"],
                  "start_date": start, "end_date": end, "timezone": "GMT", "models": config["forecast_model"],
                  "hourly": config["forecast_variable"], "temperature_unit": "celsius"}
        fc_url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + urlencode(params)
        content, meta = cached_download(fc_url, data_dir / "raw" / f"gfs-day1-{year}.json", offline)
        manifest.append(meta)
        forecasts, grid = parse_forecast(content, config["forecast_variable"])
        hourly, nights = build_nights(config, year, observations, forecasts)
        all_hourly.extend(hourly)
        all_nights.extend(nights)
        selected = [observations[s] for evening in season_dates(config, year) for s in night_hours(config, evening) if s in observations]
        audits.append({"year": year, "annual_rows": len(observations), "night_rows_present": len(selected),
                       "night_source_counts": dict(Counter(r["source"] or "missing" for r in selected)),
                       "night_unusable_temperature_rows": sum(r["temperature_c"] is None for r in selected),
                       "missing_observation_timestamps": sum(n["expected_hours"] for n in nights) - len(selected),
                       "excluded_nights": [{"night_date_local": n["night_date_local"],
                                           "observed_hours": n["observed_hours"], "forecast_hours": n["forecast_hours"]}
                                          for n in nights if not n["eligible"]],
                       "paired_cold_nights_by_month": dict(Counter(n["night_date_local"][5:7] for n in nights
                                                                  if n["eligible"] and n["observed_min_c"] < config["weather_threshold_c"])),
                       "grid": grid, "metrics": metrics(nights, hourly, config["weather_threshold_c"])})
    processed = data_dir / "processed"
    write_csv(processed / "hourly.csv", all_hourly)
    write_csv(processed / "nights.csv", all_nights)
    summary = {"generated_at_utc": datetime.now(UTC).isoformat(), "config": config,
               "method": "Fixed 24-hour lead GFS diagnostic; no common evening issuance time",
               "audits": audits, "splits": {name: metrics([n for n in all_nights if n["split"] == name],
                                                        [h for h in all_hourly if h["split"] == name], config["weather_threshold_c"])
                                              for name in ("development", "validation", "test")},
               "total": metrics(all_nights, all_hourly, config["weather_threshold_c"]), "downloads": manifest}
    write_json(processed / "summary.json", summary)
    return summary


def report(summary):
    c = summary["config"]
    lines = ["# Первый эксперимент: данные и базовый прогноз", "",
             f"Сформировано: {summary['generated_at_utc']}. Яблоня, заморозки, Краснодар; станция {c['station_id']}.", "",
             "Это диагностический эксперимент GFS с номинальной заблаговременностью 24 часа для каждого часа. "
             "Он не воспроизводит единое вечернее предупреждение. ML ещё не обучалась.", "",
             f"Ночь: {c['night_start_hour']:02}:00–{c['night_end_hour_exclusive']:02}:00 следующего дня, конец исключён, Europe/Moscow (UTC+3). "
             f"Сезон: {c['season_start']}–{c['season_end']}, дата ночи — дата вечера.", "",
             f"Погодное событие: минимум воздуха < {c['weather_threshold_c']} °C. Это не порог повреждения яблони. "
             "Фаза развития не определена; повреждения, длительность экспозиции и температура цветков не оцениваются.", "",
             "Цели: только `isd_lite` и `metar`; неизвестные источники и модельные заполнения исключены. "
             "Для ночного минимума нужны все часы и наблюдений, и прогноза; частичные ночи не входят в метрики ночей.", "",
             "## Покрытие и качество по сезонам", "",
             "| Год | Ночей | Полных наблюдений | Парных ночей | Холодных парных | Серий холодных ночей¹ | MAE минимума, °C | MAE холодных, °C | Пропуски | Ложные тревоги |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def display(value):
        return "—" if value is None else str(value)
    for audit in summary["audits"]:
        m = audit["metrics"]
        keys = ("expected_nights", "complete_observation_nights", "paired_nights", "paired_cold_nights", "cold_night_sequences", "night_min_mae_c", "cold_night_min_mae_c", "misses", "false_alarms")
        lines.append("| " + " | ".join([str(audit["year"])] + [display(m[k]) for k in keys]) + " |")
    lines += ["", "¹ Последовательные холодные ночи по полным наблюдениям — описательная группировка, "
              "не доказанное число независимых метеособытий. Пропуск наблюдений разрывает серию.", "",
              "## Потери событий из-за неполноты", ""]
    for a in summary["audits"]:
        m = a["metrics"]
        lines.append(f"- {a['year']}: {m['excluded_nights']} неполных пар; "
                     f"{m['observed_cold_nights'] - m['paired_cold_nights']} холодных ночей по полным наблюдениям "
                     "не вошли в парную оценку. Даты и доступные часы сохранены в JSON.")
    lines += ["", "Ноль парных холодных ночей не доказывает отсутствие заморозков. "
              "Все метрики ниже относятся к доступным парам, а не ко всем ожидаемым ночам.", "",
              "## Временные части", "", "| Часть | Парных ночей | Холодных | MAE минимума, °C | Попадания | Пропуски | Ложные тревоги | Верные отрицательные |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, m in summary["splits"].items():
        keys = ("paired_nights", "paired_cold_nights", "night_min_mae_c", "hits", "misses", "false_alarms", "correct_negatives")
        lines.append("| " + " | ".join([name] + [display(m[k]) for k in keys]) + " |")
    lines += ["", f"Разработка: {c['development_years']}; валидация: {c['validation_years']}; отложенная проверка: {c['test_years']}. "
              "Настройка на отложенном периоде не выполнялась. База пока только исходный GFS.", "", "## Источники температуры в ночные часы", ""]
    for a in summary["audits"]:
        lines.append(f"- {a['year']}: {a['night_source_counts']}; непригодных значений: {a['night_unusable_temperature_rows']}; ячейка прогноза: {a['grid']}.")
    months = Counter()
    for a in summary["audits"]:
        months.update(a["paired_cold_nights_by_month"])
    lines += ["", "## Ограничения и следующий шаг", "",
              f"- Холодные парные ночи по месяцам: {dict(months)}. Реальная история фаз яблони отсутствует; "
              "эта выборка не подтверждает достаточное число заморозков именно в цветение. Для полевой проверки нужен журнал фаз и событий сада.",
              "- Станция аэропорта и погодная ячейка не подтверждают температуру сада. Высота запроса задана 33 м; параметры API и возвращённая ячейка сохранены.",
              "- Архив агрегирован и может пересматриваться; история фактической доступности наблюдений неизвестна. Пока они используются только как цели, а не как оперативные признаки.",
              "- Previous Runs не даёт точного выпуска и времени публикации. Соответствующие поля таблицы оставлены пустыми; заблаговременность — номинальная по спецификации API.",
              "- Ранний архив Single Runs ECMWF описан как IFS Cycle 49R1 hindcasts. До проверки происхождения нельзя использовать его как свидетельство реально выпущенного тогда предупреждения.",
              "- Для следующего шага нужен один проверенный оперативный выпуск на всю ночь и время его доступности; затем сравнение небольшой модели с базой на тех же ночах.",
              "- Фаза яблони и рекомендации требуют отдельного сценария и просмотра агрономом. Процент ущерба и вероятность повреждения не рассчитываются.", "",
              "## Воспроизведение", "", "```powershell", "python -m agro_checker run", "python -m agro_checker run --offline", "python -m unittest discover -s tests -v", "```", "",
              "Исходные байты, URL, время загрузки и SHA-256 — `data/raw/`; таблицы — `data/processed/`. "
              "Сводка и метаданные без сырых рядов: [BASELINE_SUMMARY.json](BASELINE_SUMMARY.json).", "",
              "Источники: [Meteostat hourly](https://dev.meteostat.net/data/timeseries/hourly), "
              "[время и единицы](https://dev.meteostat.net/formats), "
              "[провайдеры](https://dev.meteostat.net/providers), "
              "[Previous Runs](https://open-meteo.com/en/docs/previous-runs-api), "
              "[Single Runs](https://open-meteo.com/en/docs/single-runs-api).", ""]
    return "\n".join(lines)


def replay(config, data_dir, night_date):
    """Retrospective replay with forecast and later truth deliberately separated."""
    date.fromisoformat(night_date)
    with (data_dir / "processed" / "hourly.csv").open(encoding="utf-8", newline="") as input_file:
        rows = [r for r in csv.DictReader(input_file) if r["night_date_local"] == night_date]
    if not rows:
        raise ValueError("Night not found. Run the pipeline first or choose a configured spring night.")
    if len(rows) != len(night_hours(config, date.fromisoformat(night_date))):
        raise ValueError("Unexpected replay night length")
    values = [number(r["forecast_temperature_c"]) for r in rows]
    complete = all(v is not None for v in values)
    return {"site_id": config["site_id"], "crop": config["crop"], "phase": config["phase"],
            "night_date_local": night_date, "mode": "retrospective_fixed_lead_diagnostic",
            "warning_issued_at_utc": None, "forecast_min_c": min(values) if complete else None,
            "weather_threshold_c": config["weather_threshold_c"],
            "weather_event_predicted": min(values) < config["weather_threshold_c"] if complete else None,
            "crop_damage_risk": "not_assessed", "forecast_complete": complete,
            "forecast": [{"valid_at_utc": r["valid_at_utc"], "temperature_c": number(r["forecast_temperature_c"])} for r in rows],
            "hindsight_observations": [{"valid_at_utc": r["valid_at_utc"], "temperature_c": number(r["target_temperature_c"]), "source": r["target_source"]} for r in rows]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "replay", "single-run", "single-replay", "ui", "model-study", "covariate-fetch", "covariate-study", "humidity-fetch", "hourly-study", "hourly-replay", "frost-select", "frost-study", "frost-replay", "episode-select", "episode-study", "episode-replay", "episode-limits-select", "episode-limits-study", "episode-limits-replay", "field-init", "field-demo", "field-serve", "field-fetch", "field-import", "field-journal", "field-report", "user-test-prepare", "cold-interval-select", "cold-interval-study", "minimum-select", "minimum-study", "scenario-select", "scenario-fetch", "scenario-study", "scenario-replay"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--night", help="Local evening date for replay, YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=6, help="Parallel archive downloads (1–12)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "pilot", help="Standalone UI output")
    parser.add_argument("--serve", action="store_true", help="Serve the UI on localhost after building")
    parser.add_argument("--port", type=int)
    parser.add_argument("--state-dir", type=Path, default=ROOT/"data/field")
    parser.add_argument("--test-dir", type=Path, help="user-test-prepare: new directory for five isolated usability slots")
    parser.add_argument("--profile-id", default="orchard-draft")
    parser.add_argument("--zone-id", default="z1")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--daily-shadow", action="store_true", help="field-serve: collect once per zone/night at 17–19 MSK while this process runs")
    parser.add_argument("--include-fresh", action="store_true", help="scenario-study: evaluate sealed spring 2026 after the frozen choice")
    args = parser.parse_args()
    try:
        if args.command in ["cold-interval-select", "cold-interval-study"]:
            from .cold_interval_study import select_intervals, run_interval_study
            path = args.config or ROOT/"config/cold_interval.json"
            if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
            s = select_intervals(path, args.data_dir) if args.command == "cold-interval-select" else run_interval_study(path, args.data_dir, args.report_dir)
            choice = s if args.command == "cold-interval-select" else s["selection"]
            if args.command == "cold-interval-study":
                from .cold_interval_report import write_report, build_viewer
                write_report(s, args.report_dir)
                build_viewer(s, args.data_dir)
            print(json.dumps({"winner": choice["winner"], "frozen_at_utc": choice["selection_saved_at_utc"],
                              "scores": {y: {"intervals": v["scores"], "detector": v["detector"]} for y, v in s.get("seasons", {}).items()}}, ensure_ascii=False, indent=2))
            return
        if args.command in ["minimum-select", "minimum-study"]:
            from .minimum_study import select_minimum, run_minimum_study
            path = args.config or ROOT/"config/minimum_study.json"
            if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
            s = select_minimum(path, args.data_dir) if args.command == "minimum-select" else run_minimum_study(path, args.data_dir, args.report_dir)
            choice = s if args.command == "minimum-select" else s["selection"]
            if args.command == "minimum-study":
                from .minimum_report import format_minimum_report
                format_minimum_report(s, args.report_dir)
            print(json.dumps({"winner": choice["winner"], "frozen_at_utc": choice["selection_saved_at_utc"],
                              "seasons": s.get("seasons", {}), "deployment": choice["protocol"]["deployment"]}, ensure_ascii=False, indent=2))
            return
        if args.command == "user-test-prepare":
            if not args.test_dir: parser.error("user-test-prepare requires --test-dir pointing to a new directory")
            from .user_test_kit import prepare_user_test
            if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
            print(json.dumps(prepare_user_test(args.test_dir), ensure_ascii=False, indent=2)); return
        if args.command.startswith("scenario-"):
            from .scenario_study import select_scenarios, run_scenario_study, replay_scenario
            path = args.config or ROOT/"config/scenario_study.json"
            if args.command == "scenario-select":
                s = select_scenarios(path, args.data_dir)
                output = {"decision_model": s["selected_decision_model"], "point_models": s["selected_point_models"], "saved_at_utc": s["selection_saved_at_utc"]}
            elif args.command == "scenario-fetch":
                from .scenario_fresh import collect_fresh
                from .scenario_observation_audit import audit_fresh_metar
                fresh = collect_fresh(args.data_dir, path, args.workers, args.offline)
                audit = audit_fresh_metar(fresh, args.offline)
                output = {"fresh_directory": str(fresh), "primary_wind_pairs": audit["wind"]["pairs"]}
            elif args.command == "scenario-replay":
                if not args.night: parser.error("scenario-replay requires --night")
                output = replay_scenario(args.data_dir, args.night)
            else:
                s = run_scenario_study(path, args.data_dir, args.report_dir, args.include_fresh)
                from .scenario_report import write_report, build_viewer
                write_report(s, args.report_dir)
                viewer = build_viewer(s, args.data_dir)
                output = {"decision_model": s["selection"]["selected_decision_model"], "point_models": s["selection"]["selected_point_models"], "fresh_status": s["fresh_status"], "reports": str(args.report_dir/"SCENARIO_STUDY_REPORT.md"), "viewer": str(viewer)}
            print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)); return
        if args.command.startswith("field-"):
            if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
            from .field_store import FieldStore, seed_demo
            store = FieldStore(args.state_dir).init()
            if args.command == "field-serve":
                from .field_server import serve_field
                serve_field(args.state_dir, args.port or 8770, args.daily_shadow)
                return
            if args.command == "field-demo": output = {"profile_id": seed_demo(store), "demo_only": True}
            elif args.command == "field-fetch":
                from .field_live import capture_forecast
                output = capture_forecast(store, args.profile_id, args.zone_id, args.night, progress=lambda s: print(s, flush=True))
            elif args.command in ["field-import", "field-journal"]:
                if not args.input: parser.error(args.command+" requires --input")
                content = args.input.read_text(encoding="utf-8-sig")
                output = store.import_measurements(args.profile_id, content, args.input.name) if args.command == "field-import" else store.journal(args.profile_id, json.loads(content))
            else: output = store.state()
            print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
            return
        if args.command in ["episode-limits-select", "episode-limits-study"]:
            from .episode_limits import select_limits, run_limits_study
            path = args.config or ROOT / "config/episode_limits.json"
            if args.command == "episode-limits-select":
                summary = select_limits(path, args.data_dir)
                output = {"selected_candidate": summary["selected_candidate"], "pooled_validation": summary["pooled_validation"],
                          "observations_loaded_years": summary["observations_loaded_years"]}
            else:
                summary = run_limits_study(path, args.data_dir, args.report_dir)
                output = {"selected_candidate": summary["selected_candidate"], "detector_alerts_preserved": summary["detector_alerts_preserved"],
                          "historical_scores": {n: {k: v for k, v in s.items() if k != "nights"} for n, s in summary["historical_scores"].items()}}
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        if args.command == "episode-limits-replay":
            from .episode_limits import replay_limits
            if not args.night:
                parser.error("episode-limits-replay requires --night")
            print(json.dumps(replay_limits(args.data_dir, args.night), ensure_ascii=False, indent=2, allow_nan=False))
            return
        if args.command in ["episode-select", "episode-study"]:
            from .episode_study import select_episodes, run_episode_study
            path = args.config or ROOT / "config/episode_study.json"
            if args.command == "episode-select":
                summary = select_episodes(path, args.data_dir)
                output = {"selected_candidate": summary["selected_candidate"], "pooled_validation": summary["pooled_validation"],
                          "observations_loaded_years": summary["observations_loaded_years"]}
            else:
                summary = run_episode_study(path, args.data_dir, args.report_dir)
                output = {"selected_candidate": summary["selected_candidate"], "detector_alerts_preserved": summary["detector_alerts_preserved"],
                          "historical_scores": {n: {k: v for k, v in s.items() if k != "nights"} for n, s in summary["historical_scores"].items()}}
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        if args.command == "episode-replay":
            from .episode_study import replay_episodes
            if not args.night:
                parser.error("episode-replay requires --night")
            print(json.dumps(replay_episodes(args.data_dir, args.night), ensure_ascii=False, indent=2, allow_nan=False))
            return
        if args.command in ["frost-select", "frost-study"]:
            from .frost_study import select_frost, run_frost_study
            path = args.config or ROOT / "config/frost_study.json"
            if args.command == "frost-select":
                summary = select_frost(path, args.data_dir)
                print(json.dumps({"selected_candidate": summary["selected_candidate"], "pooled_validation": summary["pooled_validation"],
                                  "observations_loaded_years": summary["observations_loaded_years"]}, ensure_ascii=False, indent=2))
            else:
                summary = run_frost_study(path, args.data_dir, args.report_dir)
                print(json.dumps({"selected_candidate": summary["selected_candidate"], "historical_scores": {
                    n: {k: v for k, v in s.items() if k != "nights"} for n, s in summary["historical_scores"].items()}}, ensure_ascii=False, indent=2))
            return
        if args.command == "frost-replay":
            from .frost_study import replay_frost
            if not args.night:
                parser.error("frost-replay requires --night")
            print(json.dumps(replay_frost(args.data_dir, args.night), ensure_ascii=False, indent=2, allow_nan=False))
            return
        if args.command == "humidity-fetch":
            from .single_run import load_single_config, load_points
            from .humidity_archive import collect_humidity
            config = load_single_config(args.config or ROOT / "config" / "single_run.json")
            points, failures, _, _ = load_points(args.data_dir, config)
            if failures:
                raise ValueError("Resolve temperature acquisition failures first")
            rows, failures, downloads, _ = collect_humidity(config, args.data_dir, points, args.offline, args.workers)
            print(json.dumps({"extracted_hours": len(rows), "failed_hours": len(failures), "message_count": len(downloads)}, indent=2))
            if failures:
                raise ValueError("Humidity acquisition incomplete; rerun to retry")
            return
        if args.command == "hourly-study":
            from .hourly_study import run_hourly_study
            summary = run_hourly_study(args.config or ROOT / "config" / "hourly_study.json", args.data_dir, args.report_dir)
            print(json.dumps({"selected_models": summary["selected_models"], "pooled_validation_mae": summary["pooled_validation_mae"],
                              "historical_scores": summary["historical_scores"]}, ensure_ascii=False, indent=2))
            return
        if args.command == "hourly-replay":
            from .hourly_study import replay_hourly
            if not args.night:
                parser.error("hourly-replay requires --night")
            print(json.dumps(replay_hourly(args.data_dir, args.night), ensure_ascii=False, indent=2, allow_nan=False))
            return
        if args.command == "covariate-fetch":
            from .single_run import load_single_config, load_points
            from .gfs_covariates import collect_covariates
            config = load_single_config(args.config or ROOT / "config" / "single_run.json")
            points, failures, _, _ = load_points(args.data_dir, config)
            if failures:
                raise ValueError("Resolve temperature acquisition failures first")
            rows, failures, downloads, _ = collect_covariates(config, args.data_dir, points, args.offline, args.workers)
            print(json.dumps({"extracted_hours": len(rows), "failed_hours": len(failures), "message_count": len(downloads)}, indent=2))
            if failures:
                raise ValueError("Covariate acquisition incomplete; rerun to retry")
            return
        if args.command == "covariate-study":
            from .covariate_study import run_covariate_study
            summary = run_covariate_study(args.config or ROOT / "config" / "covariate_study.json", args.data_dir, args.report_dir)
            print(json.dumps({"selected_model": summary["selected_model"], "pooled_validation_mae_c": summary["pooled_validation_mae_c"],
                              "historical_scores": summary["historical_scores"]}, ensure_ascii=False, indent=2))
            return
        if args.command == "model-study":
            from .model_study import run_study
            summary = run_study(args.config or ROOT / "config" / "model_study.json", args.data_dir, args.report_dir)
            print(json.dumps({"selected_model": summary["selected_model"], "pooled_validation_mae_c": summary["pooled_validation_mae_c"],
                              "historical_scores": summary["historical_scores"]}, ensure_ascii=False, indent=2))
            return
        if args.command == "ui":
            from .pilot_ui import build_ui, serve_ui
            target = build_ui(args.data_dir, args.output_dir, args.config)
            print(f"Standalone pilot: {target}", flush=True)
            if args.serve:
                serve_ui(target, args.port or 8765)
            return
        if args.command.startswith("single-"):
            from .single_run import load_single_config, replay_single, run_single
            config = load_single_config(args.config or ROOT / "config" / "single_run.json")
            if not 1 <= args.workers <= 12:
                parser.error("--workers must be 1–12")
            if args.command == "single-run":
                summary = run_single(config, args.data_dir, args.report_dir, args.offline, args.workers)
                print(json.dumps({"selected_model": summary["selected_model"], "scores": summary["scores"]}, ensure_ascii=False, indent=2))
            else:
                if not args.night:
                    parser.error("single-replay requires --night")
                print(json.dumps(replay_single(config, args.data_dir, args.night), ensure_ascii=False, indent=2, allow_nan=False))
            return
        config = load_config(args.config or ROOT / "config" / "pilot.json")
        if args.command == "run":
            summary = build(config, args.data_dir, args.offline)
            args.report_dir.mkdir(parents=True, exist_ok=True)
            write_json(args.report_dir / "BASELINE_SUMMARY.json", summary)
            (args.report_dir / "BASELINE_REPORT.md").write_text(report(summary), encoding="utf-8")
            print(json.dumps(summary["total"], ensure_ascii=False, indent=2))
        else:
            if not args.night:
                parser.error("replay requires --night")
            # Ensure outputs correspond to the selected configuration.
            previous = json.loads((args.data_dir / "processed" / "summary.json").read_text(encoding="utf-8"))
            if previous["config"] != config:
                raise ValueError("Processed data config mismatch; run again with this config")
            print(json.dumps(replay(config, args.data_dir, args.night), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, OSError, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
