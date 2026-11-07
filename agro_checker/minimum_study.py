"""Select a minimum head on 2023, calibrate on 2024, freeze before viewed controls."""

from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path

import numpy as np

from .minimum_model import calibrate, fit_minimum, point_minimum, predict_minimum, target_minimum
from .pipeline import ROOT, UTC, write_json
from .scenario_study import load_rows, nights, sha


def protocol(path):
    p = json.loads(path.read_text(encoding="utf-8"))
    if p["schema_version"] != 1 or p["selection_train_years"] != [2022] or p["selection_year"] != 2023 or p["final_train_years"] != [2022, 2023] or p["calibration_year"] != 2024 or p["evaluation_years"] != [2025, 2026]:
        raise ValueError("Invalid minimum chronology")
    if p["interval_alpha"] != .1 or p["minimum_calibration_nights"] != 30 or p["ood_max_abs_z"] != 4 or p["weather_threshold_c"] != 0:
        raise ValueError("Invalid locked calibration/guard settings")
    if [s["name"] for s in p["candidates"]] != ["gfs", "median_bias", "ridge_10", "ridge_100", "huber_1", "ridge_temperature_10"]:
        raise ValueError("Invalid fixed minimum registry")
    return p


def point_score(samples, model):
    pairs = [(target_minimum(n), point_minimum(model, n)[0]) for n in samples if target_minimum(n) is not None]
    cold = [abs(y-f) for y, f in pairs if y < 0]
    return {"nights": len(pairs), "excluded_nights": len(samples)-len(pairs),
            "cold_nights": len(cold), "mae_c": float(np.mean([abs(y-f) for y, f in pairs])) if pairs else None,
            "cold_minimum_mae_c": float(np.mean(cold)) if cold else None,
            "bias_c": float(np.mean([f-y for y, f in pairs])) if pairs else None}


def select_minimum(path, data_dir):
    p = protocol(path); directory = data_dir.parent / "models/research-minimum"
    selection_path = directory / "selection-before-controls.json"
    source_files = ["minimum_model.py", "minimum_study.py", "scenario_study.py", "hourly_study.py", "single_run.py"]
    source_hashes = {name: sha(ROOT / "agro_checker" / name) for name in source_files}
    if selection_path.exists():
        result = json.loads(selection_path.read_text(encoding="utf-8"))
        if result["protocol_sha256"] != sha(path) or result["source_sha256"] != source_hashes:
            raise ValueError("Frozen minimum protocol/source changed; do not refit in place")
        for name, expected in result["model_artifact_sha256"].items():
            if sha(directory / (name+".json")) != expected: raise ValueError("Minimum model hash mismatch")
        for years, key in [([2022, 2023], "selection_provenance"), ([2024], "calibration_provenance")]:
            _, provenance = load_rows(data_dir, years)
            if result[key] != provenance: raise ValueError("Frozen minimum inputs changed")
        return result
    write_json(directory / "protocol-lock.json", {"protocol": p, "protocol_sha256": sha(path), "source_sha256": source_hashes,
               "saved_at_utc": datetime.now(UTC).isoformat()})
    rows, provenance = load_rows(data_dir, [2022, 2023])
    samples = nights(rows); train = [n for n in samples if n["year"] == 2022]; valid = [n for n in samples if n["year"] == 2023]
    scores = {spec["name"]: point_score(valid, fit_minimum(spec, train)) for spec in p["candidates"]}
    if any(v["mae_c"] is None for v in scores.values()): raise ValueError("Missing minimum selection cases")
    winner = min(p["candidates"], key=lambda s: scores[s["name"]]["mae_c"])["name"]
    # Choice precedes opening 2024 labels; the held season cannot select a different point head.
    write_json(directory / "point-choice-before-calibration.json", {"winner": winner, "scores": scores,
               "observations_loaded_years": [2022, 2023], "saved_at_utc": datetime.now(UTC).isoformat()})
    calibrating_rows, calibration_provenance = load_rows(data_dir, [2024])
    calibrating_samples = nights(calibrating_rows)
    artifacts = {}; held_scores = {}
    for name in dict.fromkeys(["gfs", winner]):
        spec = next(s for s in p["candidates"] if s["name"] == name)
        model = fit_minimum(spec, samples)
        model["calibration"] = calibrate(model, calibrating_samples, p)
        held_scores[name] = point_score(calibrating_samples, model)
        write_json(directory / (name+".json"), model)
        artifacts[name] = sha(directory / (name+".json"))
    result = {"protocol": p, "protocol_sha256": sha(path), "source_sha256": source_hashes,
              "selection_saved_at_utc": datetime.now(UTC).isoformat(), "winner": winner, "selection_2023": scores,
              "held_2024_point_scores": held_scores, "selection_provenance": provenance,
              "calibration_provenance": calibration_provenance, "observations_loaded_years": [2022, 2023, 2024],
              "model_artifact_sha256": artifacts, "point_choice_sha256": sha(directory / "point-choice-before-calibration.json")}
    write_json(selection_path, result)
    return result


def score(samples, model, p):
    result = point_score(samples, model)
    rows = []; counters = defaultdict(int); widths = []; interval_scores = []
    for n in samples:
        f = predict_minimum(model, model["calibration"], n, p); actual = target_minimum(n)
        row = {"date": n["date"], "forecast": f, "actual_minimum_c": actual}
        if actual is not None:
            covered = f["lower_c"] <= actual <= f["upper_c"]
            counters["covered"] += covered
            counters["cold_covered"] += actual < 0 and covered
            counters["warm_range_cold_misses"] += actual < 0 and f["threshold_state"] == "warm_range"
            counters["crosses_zero"] += f["threshold_state"] == "crosses_zero"
            counters["cold_range"] += f["threshold_state"] == "cold_range"
            counters["warm_range"] += f["threshold_state"] == "warm_range"
            counters["extrapolation_nights"] += f["extrapolation"]
            width = f["upper_c"]-f["lower_c"]; widths.append(width)
            interval_scores.append(width+2/p["interval_alpha"]*max(0, f["lower_c"]-actual, actual-f["upper_c"]))
            row["covered"] = covered
        rows.append(row)
    result.update(**dict(counters), coverage=counters["covered"]/result["nights"] if result["nights"] else None,
                  cold_coverage=counters["cold_covered"]/result["cold_nights"] if result["cold_nights"] else None,
                  mean_interval_width_c=float(np.mean(widths)) if widths else None,
                  interval_score=float(np.mean(interval_scores)) if interval_scores else None,
                  nominal_coverage=1-p["interval_alpha"], night_records=rows)
    return result


def bootstrap_minimum(samples, base, selected, settings):
    result = {}
    for width in settings["block_days"]:
        blocks = defaultdict(list)
        for n in samples:
            y = target_minimum(n)
            if y is None: continue
            day = datetime.fromisoformat(n["date"])
            blocks[(day.year, (day-datetime(day.year, 3, 1)).days//width)].append(
                abs(y-point_minimum(base, n)[0])-abs(y-point_minimum(selected, n)[0]))
        values = list(blocks.values())
        if not values: result[str(width)] = None; continue
        totals = np.array([sum(v) for v in values]); sizes = np.array([len(v) for v in values])
        rng = np.random.default_rng(settings["seed"])
        draws = rng.integers(0, len(values), (settings["replicates"], len(values)))
        estimates = totals[draws].sum(axis=1)/sizes[draws].sum(axis=1)
        result[str(width)] = {"blocks": len(values), "paired_nights": int(sum(sizes)),
                             "improvement_c": float(sum(totals)/sum(sizes)),
                             "interval_95": np.quantile(estimates, [.025, .975]).tolist()}
    return result


def run_minimum_study(path, data_dir, report_dir):
    selection = select_minimum(path, data_dir); p = selection["protocol"]
    directory = data_dir.parent / "models/research-minimum"
    models = {name: json.loads((directory / (name+".json")).read_text(encoding="utf-8"))
              for name in selection["model_artifact_sha256"]}
    winner = selection["winner"]; seasons = {}; frames = []
    from .frost_study import predict_cold
    detector_dir = data_dir.parent / "models/research-frost"
    parent = json.loads((detector_dir / "selection-before-2025.json").read_text(encoding="utf-8"))
    detector_path = detector_dir / "event_duration_weather/model.json"
    detector_hash = sha(detector_path)
    if detector_hash != parent["model_artifact_sha256"]["event_duration_weather"]: raise ValueError("Frozen detector changed")
    detector = json.loads(detector_path.read_text(encoding="utf-8"))
    for year in p["evaluation_years"]:
        if year == 2025: rows, provenance = load_rows(data_dir, [year])
        else:
            fresh = data_dir / "scenario-2026"
            source = json.loads((fresh / "source-config.json").read_text(encoding="utf-8"))
            rows, provenance = load_rows(fresh, [year], source)
        samples = nights(rows); scores = {name: score(samples, model, p) for name, model in models.items()}
        masks = predict_cold(detector, rows, .25)
        for i, n in enumerate(samples):
            frames.append({"date": n["date"], "gfs": scores["gfs"]["night_records"][i]["forecast"],
                           "selected": scores[winner]["night_records"][i]["forecast"],
                           "actual_minimum_c": target_minimum(n),
                           "run_at_utc": n["rows"][0]["forecast_run_at_utc"], "issued_at_utc": n["rows"][0]["issued_at_utc"],
                           "previous_detector": {"event": any(masks[i]["cold_mask"]), "frost": masks[i]["frost"]},
                           "role": "previously viewed control; no fitting or calibration"})
        for s in scores.values(): s.pop("night_records")
        seasons[str(year)] = {"scores": scores, "input_provenance": provenance,
                             "minimum_improvement_block_bootstrap": bootstrap_minimum(samples, models["gfs"], models[winner], p["bootstrap"])}
    for name, expected in selection["model_artifact_sha256"].items():
        if sha(directory / (name+".json")) != expected: raise ValueError("Model changed during evaluation")
    if sha(detector_path) != detector_hash: raise ValueError("Detector changed during evaluation")
    result = {"selection": selection, "selection_sha256": sha(directory / "selection-before-controls.json"),
              "generated_at_utc": datetime.now(UTC).isoformat(), "seasons": seasons,
              "detector_sha256": detector_hash, "deployment": p["deployment"]}
    write_json(data_dir / "processed/minimum-study/night-forecasts.json", frames)
    result["forecast_sha256"] = sha(data_dir / "processed/minimum-study/night-forecasts.json")
    write_json(report_dir / "MINIMUM_STUDY_SUMMARY.json", result)
    write_report(result, report_dir)
    build_viewer(result, frames)
    return result


def write_report(s, directory):
    choice = s["selection"]; winner = choice["winner"]
    lines = ["# Модель минимума и диапазон ошибки", "",
             "Станция Краснодар34929, не сад. Модель выбрана только по2023, обучена на2022–2023; диапазон откалиброван на отдельном2024. Годы2025/2026 уже просмотрены и не являются новым слепым тестом.", "",
             "Выбранная модель: "+winner+". Цель — минимум14 часовых отсчётов воздуха2м, не непрерывный минимум и не повреждение яблони.",
             "Прежний детектор холода сохранён; новый минимум не подменяет обнаружение, начало или длительность. Полевая модель остаётся GFS.", "",
             "## Выбор2023", "", "| Модель | Ночей | MAE минимума, °C | MAE холодных ночей, °C |", "|---|---:|---:|---:|"]
    for name, row in choice["selection_2023"].items():
        lines.append(f"| {name} | {row['nights']} | {row['mae_c']:.3f} | {row['cold_minimum_mae_c']:.3f} |")
    lines += ["", "## Ранее просмотренные контрольные сезоны", "",
              "| Год | Метод | Ночей | MAE минимума, °C | Холодных ночей | Покрытие диапазона | Покрытие холодных | Ширина, °C | Interval score |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for year, season in s["seasons"].items():
        for name in dict.fromkeys(["gfs", winner]):
            r = season["scores"][name]
            lines.append(f"| {year} | {name} | {r['nights']} | {r['mae_c']:.3f} | {r['cold_nights']} | {r['coverage']:.1%} | {r['cold_coverage']:.1%} | {r['mean_interval_width_c']:.3f} | {r['interval_score']:.3f} |")
        b = season["minimum_improvement_block_bootstrap"]["7"]
        lines += ["", f"{year}: уменьшение MAE {b['improvement_c']:.3f}°C; парный95% интервал с блоком7дней {b['interval_95'][0]:.3f}…{b['interval_95'][1]:.3f}°C.", ""]
    lines += ["## Ограничения и применение", "",
              "Номинальный диапазон90% построен по абсолютным ошибкам отдельного сезона2024 с конечновыборочным рангом. Обычная гарантия требует обменности; сезонная зависимая погода её не подтверждает. Публикуем эмпирическое покрытие, ширину и interval score; это не вероятность повреждений.",
              "Попадание диапазона в0 означает неопределённость, а не отсутствие холода. Число таких ночей, ложные тёплые диапазоны и экстраполяции сохранены в JSON. Широкий диапазон честен, но может быть мало полезен для решения.",
              "Защита инференса отвергает другой сайт, сезон, горизонт, смешанные выпуски, неполные/некорректные признаки; при |z|>4 скорректированный ответ не выпускается. Исследовательские оценки сохраняют все ночи, включая экстраполяции: трудные случаи не вырезаны из MAE.",
              "Нельзя подключать станционную коррекцию к неизвестному саду, октябрю или другим выпускам GFS. Для переноса нужны измерения сада и новая перспективная проверка. Финальный fit не обновляется на2024: это разрушило бы независимую калибровку.",
              "Методологический источник об ограничениях обычного CP для временных рядов: [Xu и Xie, ICML2023](https://proceedings.mlr.press/v202/xu23r/xu23r.pdf). Здесь реализован простой split residual envelope, не алгоритм SPCI из статьи.", "",
              "## Воспроизведение", "",
              "python -m agro_checker minimum-select --offline",
              "python -m agro_checker minimum-study --offline", "",
              "Выбор, модели, исходные данные и source SHA проверяются при повторном запуске; существующий выбор не перезаписывается."]
    (directory / "MINIMUM_STUDY_REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def build_viewer(summary, frames):
    content = (ROOT / "web/model/index.html").read_text(encoding="utf-8")
    payload = {"frames": frames, "summary": summary}
    output = ROOT / "dist/model"; output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(content.replace("__MODEL_DATA__", json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")), encoding="utf-8")
