"""Extract original single-run NOAA GFS forecasts without downloading full GRIB files."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
from http.client import HTTPSConnection, HTTPException
import json
from threading import local
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .pipeline import ROOT, UTC, cached_download, night_hours, season_dates, write_json

BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
CONNECTIONS = local()


def aws_fetch(url, byte_range=None):
    """Reuse one TLS connection per downloader thread for indexes and ranges."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "noaa-gfs-bdp-pds.s3.amazonaws.com":
        raise ValueError("Archive connection is restricted to the NOAA bucket")
    connection = getattr(CONNECTIONS, "connection", None)
    if connection is None:
        connection = HTTPSConnection(parsed.netloc, timeout=45)
        CONNECTIONS.connection = connection
    headers = {"User-Agent": "Agro-checker-research/0.2"}
    if byte_range:
        start, end = byte_range
        headers["Range"] = f"bytes={start}-{end}"
    try:
        connection.request("GET", parsed.path, headers=headers)
        response = connection.getresponse()
        if response.status >= 400:
            response.read()
            raise HTTPError(url, response.status, response.reason, response.headers, None)
        if byte_range:
            if response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
                raise ValueError("Server did not honor the exact byte range")
            limit = end - start + 1
        else:
            if response.status != 200:
                raise ValueError("Unexpected NOAA index response")
            limit = 2_000_000
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError("NOAA response exceeds requested size")
        return content, dict(response.headers)
    except (OSError, HTTPException, ValueError) as exc:
        connection.close()
        CONNECTIONS.connection = None
        if isinstance(exc, HTTPException) or (isinstance(exc, OSError) and not isinstance(exc, HTTPError)):
            raise URLError(str(exc)) from exc
        raise


def run_at(config, evening):
    return datetime(evening.year, evening.month, evening.day, config["run_hour_utc"], tzinfo=UTC)


def issued_at(config, evening):
    return datetime(evening.year, evening.month, evening.day, config["warning_hour_local"], tzinfo=UTC) - timedelta(hours=config["utc_offset_hours"])


def object_url(run, lead):
    return f"{BUCKET}/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{lead:03}"


def temperature_range(index, run, lead):
    lines = index.decode("utf-8").splitlines()
    matches = [i for i, line in enumerate(lines) if ":TMP:2 m above ground:" in line]
    if len(matches) != 1:
        raise ValueError("Expected exactly one 2m temperature message in NOAA index")
    i = matches[0]
    if i + 1 >= len(lines):
        raise ValueError("Missing next GRIB index offset")
    fields = lines[i].split(":")
    if fields[2] != f"d={run:%Y%m%d%H}" or fields[5] != f"{lead} hour fcst":
        raise ValueError("NOAA index run/lead mismatch")
    start, end = int(fields[1]), int(lines[i + 1].split(":")[1]) - 1
    if not (0 <= start < end and end - start < 10_000_000):
        raise ValueError("Invalid temperature byte range")
    return start, end


def range_download(url, path, start, end, offline=False):
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists():
        content = path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (meta["url"] != url or meta["range"] != [start, end] or
                meta["sha256"] != hashlib.sha256(content).hexdigest()):
            raise ValueError(f"Range cache mismatch: {path}")
        return content, meta
    if offline:
        raise ValueError(f"Missing cached GRIB message: {path}")
    for attempt in range(3):
        try:
            content, headers = aws_fetch(url, (start, end))
            modified = parsedate_to_datetime(headers["Last-Modified"]).astimezone(UTC)
            etag = headers.get("ETag")
            break
        except (HTTPError, URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    if len(content) != end - start + 1 or content[:4] != b"GRIB" or content[-4:] != b"7777":
        raise ValueError("Invalid or truncated GRIB range response")
    if content[7] != 2 or int.from_bytes(content[8:16], "big") != len(content):
        raise ValueError("Expected exactly one complete GRIB2 message")
    meta = {"url": url, "range": [start, end], "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "etag": etag,
            "archive_last_modified_at_utc": modified.isoformat(),
            "retrieved_at_utc": datetime.now(UTC).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(content)
    temp.replace(path)
    write_json(meta_path, meta)
    return content, meta


def fetch_message(config, data_dir, evening, stamp, offline):
    run = run_at(config, evening)
    lead = int((stamp - run).total_seconds() / 3600)
    url = object_url(run, lead)
    path = data_dir / "raw" / "gfs-single" / f"{run:%Y%m%d%H}" / f"f{lead:03}"
    index, index_meta = cached_download(url + ".idx", path.with_suffix(".idx"), offline, fetcher=aws_fetch)
    start, end = temperature_range(index, run, lead)
    content, meta = range_download(url, path.with_suffix(".grib2"), start, end, offline)
    return evening, stamp, content, meta, index_meta


def decode_temperature(content, config, run, valid):
    try:
        import eccodes as ec
    except ImportError as exc:
        raise ValueError("Install archive dependencies: python -m pip install -r requirements-archive.txt") from exc
    handle = ec.codes_new_from_message(content)
    try:
        expected = {"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2, "units": "K",
                    "dataDate": int(run.strftime("%Y%m%d")), "dataTime": run.hour * 100,
                    "validityDate": int(valid.strftime("%Y%m%d")), "validityTime": valid.hour * 100,
                    "centre": "kwbc", "gridType": "regular_ll", "stepType": "instant"}
        for key, value in expected.items():
            if ec.codes_get(handle, key) != value:
                raise ValueError(f"GRIB {key} mismatch: {ec.codes_get(handle, key)} != {value}")
        if ec.codes_get(handle, "iDirectionIncrementInDegrees") != 0.25 or ec.codes_get(handle, "jDirectionIncrementInDegrees") != 0.25:
            raise ValueError("Expected GFS 0.25-degree grid")
        point = ec.codes_grib_find_nearest(handle, config["latitude"], config["longitude"], npoints=1)[0]
        if ec.codes_get(handle, "bitmapPresent") and ec.codes_get_element(handle, "bitmap", point["index"]) == 0:
            raise ValueError("Missing GRIB temperature at selected grid point")
        value = float(point["value"]) - 273.15
        if not -90 < value < 65:
            raise ValueError("Invalid grid temperature")
        return {"forecast_temperature_c": value, "grid_latitude": float(point["lat"]),
                "grid_longitude": float(point["lon"]), "grid_distance_km": float(point["distance"]),
                "lead_hours": int((valid - run).total_seconds() / 3600)}
    finally:
        ec.codes_release(handle)


def point_row(config, evening, stamp, content, meta):
    run = run_at(config, evening)
    decoded = decode_temperature(content, config, run, stamp)
    gate = datetime.fromisoformat(meta["archive_last_modified_at_utc"]) + timedelta(minutes=config["archive_availability_buffer_minutes"])
    return {"night_date_local": evening.isoformat(), "valid_at_utc": stamp.isoformat(),
            "forecast_run_at_utc": run.isoformat(), "issued_at_utc": issued_at(config, evening).isoformat(),
            "archive_last_modified_at_utc": meta["archive_last_modified_at_utc"],
            "availability_gate_at_utc": gate.isoformat(), "forecast_available_at_utc": None,
            "availability_evidence": "S3 Last-Modified plus buffer; historical public ACL assumed",
            "available_by_issue_proxy": gate <= issued_at(config, evening), **decoded}


def retry_failed(config, data_dir, rows, failures, manifest, workers):
    """Retry acquisition failures before freezing the comparison dataset."""
    remaining = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_message, config, data_dir, date.fromisoformat(f["night_date_local"]),
                               datetime.fromisoformat(f["valid_at_utc"]), False): f for f in failures}
        for future in as_completed(futures):
            failure = futures.pop(future)
            try:
                evening, stamp, content, meta, index_meta = future.result()
                rows.append(point_row(config, evening, stamp, content, meta))
                manifest.extend([index_meta, meta])
            except (HTTPError, URLError, TimeoutError, ValueError, OSError, KeyError) as exc:
                remaining.append({**failure, "error": str(exc)})
    rows.sort(key=lambda r: r["valid_at_utc"])
    manifest.sort(key=lambda m: (m["url"], str(m.get("range", ""))))
    write_json(data_dir / "raw" / "single-run-manifest.json", {"downloads": manifest, "failures": remaining,
                                                             "previous_acquisition_failures": failures})
    return rows, remaining, manifest


def collect(config, data_dir, offline=False, workers=6):
    """Network fetches overlap; ecCodes decoding runs only on the calling thread."""
    # Fail before scheduling downloads if the GRIB decoder is unavailable.
    try:
        import eccodes  # noqa: F401
    except ImportError as exc:
        raise ValueError("Install requirements-archive.txt first") from exc
    evenings = [d for year in config["years"] for d in season_dates(config, year)]
    jobs = [(evening, stamp) for evening in evenings for stamp in night_hours(config, evening)]
    rows, manifest, failures = [], [], []
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_message, config, data_dir, evening, stamp, offline): (evening, stamp)
                   for evening, stamp in jobs}
        for future in as_completed(futures):
            evening, stamp = futures.pop(future)
            try:
                _, _, content, meta, index_meta = future.result()
                rows.append(point_row(config, evening, stamp, content, meta))
                manifest.extend([index_meta, meta])
            except (HTTPError, URLError, TimeoutError, ValueError, OSError, KeyError) as exc:
                # Unavailable objects are audited and excluded, never replaced with a different run.
                failures.append({"night_date_local": evening.isoformat(), "valid_at_utc": stamp.isoformat(),
                                 "error": str(exc)})
            completed += 1
            if completed % 140 == 0 or completed == len(jobs):
                print(f"GFS: {completed}/{len(jobs)} hours; failures={len(failures)}", flush=True)
    rows.sort(key=lambda r: r["valid_at_utc"])
    manifest.sort(key=lambda m: (m["url"], str(m.get("range", ""))))
    write_json(data_dir / "raw" / "single-run-manifest.json", {"downloads": manifest, "failures": failures})
    return rows, failures, manifest
