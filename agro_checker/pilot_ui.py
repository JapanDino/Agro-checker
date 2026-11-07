"""Build an offline standalone archive explorer from the validated pilot tables."""

from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from .gfs_archive import issued_at, run_at
from .pipeline import ROOT, night_hours, number, season_dates
from .single_run import corrected_temperature, load_single_config


def build_demo_cases(nights, cases_path=None):
    catalog = json.loads((cases_path or ROOT / "config" / "demo_cases.json").read_text(encoding="utf-8"))
    threshold = catalog["weather_threshold_c"]
    if threshold != 0.0:
        raise ValueError("Demo outcomes must use the weather threshold 0 C")
    by_date = {night["date"]: night for night in nights}
    output = []
    for case in catalog["cases"]:
        night = by_date.get(case["date"])
        if night is None:
            output.append({**case, "available": False, "reason": "date_not_in_archive"})
            continue
        if night["split"] != "test":
            raise ValueError(f"Demo must use held-out data: {case['date']}")
        points = night["points"]
        if not points or any(p[key] is None for p in points for key in ("base", "corrected", "observed")):
            output.append({**case, "available": False, "reason": "incomplete_data"})
            continue
        minima = {key: min(p[key] for p in points) for key in ("base", "corrected", "observed")}
        event = minima["observed"] < threshold
        def outcome(value):
            predicted = value < threshold
            return ("hit" if predicted else "miss") if event else ("false_alarm" if predicted else "correct_negative")
        actual = {"base": outcome(minima["base"]), "corrected": outcome(minima["corrected"])}
        if actual != {"base": case["expected_base"], "corrected": case["expected_corrected"]}:
            raise ValueError(f"Demo outcome changed for {case['date']}; review its label before rebuilding")
        output.append({**case, "available": True, "morning_date": night["morning_date"],
                       "minima": minima, "outcomes": actual})
    return output


def build_snapshot(data_dir, config_path=None):
    config = load_single_config(config_path or ROOT / "config" / "single_run.json")
    processed = data_dir / "processed" / "single-run"
    summary_path = processed / "summary.json"
    if not summary_path.exists():
        raise ValueError("No single-run results. Run 'python -m agro_checker single-run' first.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["config"] != config:
        raise ValueError("UI configuration differs from the computed single-run results")
    model = summary["selected_model"]
    thresholds = json.loads((ROOT / "config" / "apple_thresholds.json").read_text(encoding="utf-8"))
    for stage in thresholds["stages"]:
        stage["threshold_c"] = (stage["threshold_f"] - 32) * 5 / 9
    grouped = defaultdict(list)
    hourly_path = processed / "hourly.csv"
    with hourly_path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            grouped[row["night_date_local"]].append(row)
    expected_dates = [d for year in config["years"] for d in season_dates(config, year)]
    if set(grouped) != {d.isoformat() for d in expected_dates}:
        raise ValueError("UI table does not contain exactly the configured archive nights")
    local = timezone(timedelta(hours=config["utc_offset_hours"]))
    nights = []
    for evening in expected_dates:
        rows = sorted(grouped[evening.isoformat()], key=lambda r: r["valid_at_utc"])
        expected = [s.isoformat() for s in night_hours(config, evening)]
        if [r["valid_at_utc"] for r in rows] != expected:
            raise ValueError(f"Missing or duplicate hourly timestamps for {evening}")
        if any(r["forecast_run_at_utc"] != run_at(config, evening).isoformat() or
               r["issued_at_utc"] != issued_at(config, evening).isoformat() for r in rows):
            raise ValueError(f"Mixed run or issuance time for {evening}")
        points = []
        for row in rows:
            base = number(row["forecast_temperature_c"])
            if row["status"] != "available_by_proxy" and base is not None:
                raise ValueError("Unqualified forecast value in UI export")
            stamp = datetime.fromisoformat(row["valid_at_utc"])
            points.append({"time": stamp.isoformat(), "local_time": stamp.astimezone(local).strftime("%H:%M"),
                           "base": base, "corrected": corrected_temperature(model, {"forecast_temperature_c": base, "lead_hours": int(row["lead_hours"])}),
                           "observed": number(row["target_temperature_c"]), "source": row["target_source"],
                           "status": row["status"], "lead_hours": int(row["lead_hours"]),
                           "archive_modified": row["archive_last_modified_at_utc"], "availability_gate": row["availability_gate_at_utc"]})
        observed = [p["observed"] for p in points if p["observed"] is not None]
        forecast = [p["base"] for p in points if p["base"] is not None]
        nights.append({"date": evening.isoformat(), "year": evening.year, "split": rows[0]["split"],
                       "morning_date": (evening + timedelta(days=1)).isoformat(),
                       "run_at": rows[0]["forecast_run_at_utc"], "scenario_issued_at": rows[0]["issued_at_utc"],
                       "forecast_complete": len(forecast) == len(points), "observations_complete": len(observed) == len(points),
                       "observed_min": min(observed) if len(observed) == len(points) else None,
                       "points": points})
    return {"version": 1, "config": config, "model": model, "scores": summary["scores"],
            "thresholds": thresholds, "nights": nights, "demo_cases": build_demo_cases(nights),
            "provenance": {"summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                           "hourly_sha256": hashlib.sha256(hourly_path.read_bytes()).hexdigest(),
                           "computed_at_utc": summary["generated_at_utc"], "grids": summary["grids"],
                           "availability_evidence": "S3 Last-Modified + 10 minutes; public availability assumed"}}


def build_ui(data_dir, output_dir, config_path=None):
    snapshot = build_snapshot(data_dir, config_path)
    source = ROOT / "web" / "pilot"
    page = (source / "index.html").read_text(encoding="utf-8")
    # Escape '<' so source data cannot terminate the embedded JSON script tag.
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    page = page.replace("<!-- PILOT_STYLE -->", "<style>" + (source / "styles.css").read_text(encoding="utf-8") + "</style>")
    page = page.replace("<!-- PILOT_DATA -->", '<script id="pilot-data" type="application/json">' + encoded + "</script>")
    scripts = (source / "risk.js").read_text(encoding="utf-8") + "\n" + (source / "app.js").read_text(encoding="utf-8")
    page = page.replace("<!-- PILOT_SCRIPT -->", "<script>" + scripts + "</script>")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "index.html"
    target.write_text(page, encoding="utf-8")
    return target


def serve_ui(path, port):
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a port between 1024 and 65535")
    handler = partial(SimpleHTTPRequestHandler, directory=str(path.parent))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Local pilot: http://127.0.0.1:{port}/", flush=True)
        print("Ctrl+C stops the server. The standalone HTML also works without a server.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
