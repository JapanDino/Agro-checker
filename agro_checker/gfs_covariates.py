"""Acquire same-object instantaneous wind, dewpoint and total cloud from archived GFS."""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, datetime, timedelta
import hashlib
import json
import math

from .gfs_archive import aws_fetch, issued_at, object_url, range_download, run_at
from .pipeline import UTC, cached_download, night_hours, season_dates, write_json

FIELDS = {
    "u10": {"index_parameter": "UGRD", "index_level": "10 m above ground", "shortName": "10u", "typeOfLevel": "heightAboveGround", "level": 10, "units": "m s**-1", "parameterCategory": 2, "parameterNumber": 2},
    "v10": {"index_parameter": "VGRD", "index_level": "10 m above ground", "shortName": "10v", "typeOfLevel": "heightAboveGround", "level": 10, "units": "m s**-1", "parameterCategory": 2, "parameterNumber": 3},
    "dewpoint": {"index_parameter": "DPT", "index_level": "2 m above ground", "shortName": "2d", "typeOfLevel": "heightAboveGround", "level": 2, "units": "K", "parameterCategory": 0, "parameterNumber": 6},
    "cloud": {"index_parameter": "TCDC", "index_level": "entire atmosphere", "shortName": "tcc", "typeOfLevel": "atmosphere", "level": 0, "units": "%", "parameterCategory": 6, "parameterNumber": 1},
}


def field_range(index, run, lead, key, field_specs=None):
    spec = (FIELDS if field_specs is None else field_specs)[key]
    lines = index.decode("utf-8").splitlines()
    matches = []
    for i, line in enumerate(lines):
        fields = line.split(":")
        if len(fields) >= 6 and fields[3:6] == [spec["index_parameter"], spec["index_level"], f"{lead} hour fcst"]:
            matches.append(i)
    if len(matches) != 1:
        raise ValueError(f"Expected one instantaneous {key} message, got {len(matches)}")
    i = matches[0]
    if lines[i].split(":")[2] != f"d={run:%Y%m%d%H}" or i + 1 >= len(lines):
        raise ValueError("Covariate index run/next offset mismatch")
    start, end = int(lines[i].split(":")[1]), int(lines[i + 1].split(":")[1]) - 1
    if not (0 <= start < end and end - start < 10_000_000):
        raise ValueError("Invalid covariate byte range")
    return start, end


def decode_field(content, config, run, valid, key, field_specs=None):
    import eccodes as ec
    h = ec.codes_new_from_message(content)
    try:
        spec = (FIELDS if field_specs is None else field_specs)[key]
        lead = int((valid - run).total_seconds() / 3600)
        expected = {k: v for k, v in spec.items() if not k.startswith("index_")}
        expected.update(centre="kwbc", gridType="regular_ll", stepType="instant", discipline=0,
                        startStep=lead, endStep=lead, dataDate=int(run.strftime("%Y%m%d")), dataTime=run.hour * 100,
                        validityDate=int(valid.strftime("%Y%m%d")), validityTime=valid.hour * 100,
                        iDirectionIncrementInDegrees=.25, jDirectionIncrementInDegrees=.25)
        if key in ("u10", "v10"):
            expected["uvRelativeToGrid"] = 0
        for k, value in expected.items():
            actual = ec.codes_get(h, k)
            if actual != value:
                raise ValueError(f"{key}: GRIB {k} mismatch {actual} != {value}")
        point = ec.codes_grib_find_nearest(h, config["latitude"], config["longitude"], npoints=1)[0]
        if ec.codes_get(h, "bitmapPresent") and not ec.codes_get_element(h, "bitmap", point["index"]):
            raise ValueError("Missing covariate grid value")
        value = float(point["value"]) - (273.15 if key == "dewpoint" else 0)
        if not math.isfinite(value):
            raise ValueError("Nonfinite covariate value")
        if key == "dewpoint" and not -100 < value < 65:
            raise ValueError("Invalid dewpoint")
        if key == "cloud" and not 0 <= value <= 100:
            raise ValueError("Cloud cover must be 0–100 percent")
        if key == "rh" and not 0 <= value <= 110:
            raise ValueError("Invalid relative humidity")
        if key in ("u10", "v10") and abs(value) > 150:
            raise ValueError("Invalid wind component")
        return {"value": value, "grid_latitude": float(point["lat"]), "grid_longitude": float(point["lon"]),
                "grid_distance_km": float(point["distance"]), "step_type": "instant", "units": "degC" if key == "dewpoint" else spec["units"]}
    finally:
        ec.codes_release(h)


def check_object_version(meta, reference):
    if any(meta[k] != reference[k] for k in ["url", "etag", "archive_last_modified_at_utc"]):
        raise ValueError("Covariate and temperature originate from different object versions")


def fetch_hour(config, data_dir, evening, stamp, offline):
    run = run_at(config, evening)
    lead = int((stamp - run).total_seconds() / 3600)
    url = object_url(run, lead)
    original = data_dir / "raw" / "gfs-single" / f"{run:%Y%m%d%H}" / f"f{lead:03}"
    index, index_meta = cached_download(url + ".idx", original.with_suffix(".idx"), True)
    reference = json.loads(original.with_name(original.name + ".grib2.meta.json").read_text(encoding="utf-8"))
    fields = {}
    for key in FIELDS:
        start, end = field_range(index, run, lead, key)
        path = data_dir / "raw" / "gfs-covariates" / f"{run:%Y%m%d%H}" / f"f{lead:03}_{key}.grib2"
        content, meta = range_download(url, path, start, end, offline)
        check_object_version(meta, reference)
        fields[key] = (content, meta)
    return fields, index_meta


def decode_hour(config, evening, stamp, fields, temperature_point, field_specs=None):
    run, issue = run_at(config, evening), issued_at(config, evening)
    row = {"night_date_local": evening.isoformat(), "valid_at_utc": stamp.isoformat(),
           "forecast_run_at_utc": run.isoformat(), "issued_at_utc": issue.isoformat(),
           "lead_hours": int((stamp - run).total_seconds() / 3600), "fields": {}}
    for key, (content, meta) in fields.items():
        decoded = decode_field(content, config, run, stamp, key, field_specs)
        if any(abs(decoded[k] - temperature_point[k]) > 1e-6 for k in ["grid_latitude", "grid_longitude", "grid_distance_km"]):
            raise ValueError("Covariates must use the temperature grid point")
        modified = datetime.fromisoformat(meta["archive_last_modified_at_utc"])
        gate = modified + timedelta(minutes=config["archive_availability_buffer_minutes"])
        if modified < run:
            raise ValueError("Covariate archive timestamp predates initialization")
        row["fields"][key] = {**decoded, "archive_last_modified_at_utc": modified.isoformat(),
                              "availability_gate_at_utc": gate.isoformat(), "available_by_issue_proxy": gate <= issue,
                              "forecast_available_at_utc": None, "source_sha256": meta["sha256"]}
    return row


def seal_covariates(data_dir, config, rows, failures, downloads):
    raw = data_dir / "raw"
    path = raw / "covariate-points.json"
    manifest = raw / "covariate-manifest.json"
    write_json(manifest, {"downloads": sorted(downloads, key=lambda m: (m["url"], str(m["range"]))), "failures": failures})
    write_json(path, {"config": config, "field_schema": FIELDS, "rows": sorted(rows, key=lambda r: r["valid_at_utc"]), "failures": failures})
    write_json(path.with_name(path.name + ".meta.json"), {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "decoder": "eccodes"})


def load_covariates(data_dir, config):
    raw = data_dir / "raw"
    content = (raw / "covariate-points.json").read_bytes()
    manifest = (raw / "covariate-manifest.json").read_bytes()
    meta = json.loads((raw / "covariate-points.json.meta.json").read_text(encoding="utf-8"))
    if hashlib.sha256(content).hexdigest() != meta["sha256"] or hashlib.sha256(manifest).hexdigest() != meta["manifest_sha256"]:
        raise ValueError("Covariate extraction cache hash mismatch")
    payload = json.loads(content)
    if payload["config"] != config or payload["field_schema"] != FIELDS:
        raise ValueError("Covariate cache schema/config mismatch")
    return payload["rows"], payload["failures"], json.loads(manifest)["downloads"], meta


def collect_covariates(config, data_dir, temperature_points, offline=False, workers=8):
    if not 1 <= workers <= 12:
        raise ValueError("workers must be 1–12")
    # Import before downloading. Decoder stays on the calling thread.
    import eccodes  # noqa: F401
    points = {r["valid_at_utc"]: r for r in temperature_points}
    cached_rows, downloads = [], []
    if (data_dir / "raw" / "covariate-points.json").exists():
        cached_rows, _, downloads, _ = load_covariates(data_dir, config)
    done = {r["valid_at_utc"] for r in cached_rows}
    jobs = [(d, t) for year in config["years"] for d in season_dates(config, year)
            for t in night_hours(config, d) if t.isoformat() not in done]
    if not jobs:
        return load_covariates(data_dir, config)
    if offline:
        raise ValueError(f"Covariate cache incomplete: {len(jobs)} hours; run covariate-fetch online")
    rows, failures = list(cached_rows), []
    attempted = 0
    iterator = iter(jobs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}
        def submit_next():
            job = next(iterator, None)
            if job:
                pending[pool.submit(fetch_hour, config, data_dir, *job, offline)] = job
        for _ in range(workers * 2):
            submit_next()
        while pending:
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                evening, stamp = pending.pop(future)
                try:
                    fields, _ = future.result()
                    row = decode_hour(config, evening, stamp, fields, points[stamp.isoformat()])
                    rows.append(row)
                    downloads.extend(meta for _, meta in fields.values())
                except (ValueError, OSError, KeyError) as exc:
                    failures.append({"night_date_local": evening.isoformat(), "valid_at_utc": stamp.isoformat(), "error": str(exc)})
                attempted += 1
                if attempted % 140 == 0 or attempted == len(jobs):
                    seal_covariates(data_dir, config, rows, failures, downloads)
                    print(f"GFS covariates: {len(rows)}/{len(points)} hours; failures={len(failures)}; bytes={sum(m['bytes'] for m in downloads)/1024**3:.2f} GiB", flush=True)
                submit_next()
    return load_covariates(data_dir, config)
