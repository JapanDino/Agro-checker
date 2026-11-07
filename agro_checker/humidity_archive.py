"""Direct RH 2m from the same GFS objects; independent cache leaves old studies intact."""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, date
import hashlib
import json

from .gfs_archive import object_url, range_download, run_at
from .gfs_covariates import check_object_version, decode_hour, field_range
from .pipeline import UTC, cached_download, night_hours, season_dates, write_json

RH_FIELDS = {"rh": {"index_parameter": "RH", "index_level": "2 m above ground", "shortName": "2r",
                    "typeOfLevel": "heightAboveGround", "level": 2, "units": "%",
                    "parameterCategory": 1, "parameterNumber": 1}}


def fetch_rh(config, data_dir, evening, stamp, offline):
    run = run_at(config, evening)
    lead = int((stamp - run).total_seconds()/3600)
    url = object_url(run, lead)
    original = data_dir/"raw"/"gfs-single"/f"{run:%Y%m%d%H}"/f"f{lead:03}"
    index, _ = cached_download(url+".idx", original.with_suffix(".idx"), True)
    reference = json.loads(original.with_name(original.name+".grib2.meta.json").read_text(encoding="utf-8"))
    start, end = field_range(index, run, lead, "rh", RH_FIELDS)
    content, meta = range_download(url, data_dir/"raw"/"gfs-humidity"/f"{run:%Y%m%d%H}"/f"f{lead:03}_rh.grib2", start, end, offline)
    check_object_version(meta, reference)
    return content, meta


def seal_humidity(data_dir, config, rows, failures, downloads):
    raw = data_dir/"raw"
    write_json(raw/"humidity-points.json", {"config": config, "field_schema": RH_FIELDS,
               "rows": sorted(rows, key=lambda r:r["valid_at_utc"]), "failures": failures})
    write_json(raw/"humidity-manifest.json", {"downloads": sorted(downloads,key=lambda r:r["url"]), "failures": failures})
    write_json(raw/"humidity-points.json.meta.json", {"sha256": hashlib.sha256((raw/"humidity-points.json").read_bytes()).hexdigest(),
               "manifest_sha256": hashlib.sha256((raw/"humidity-manifest.json").read_bytes()).hexdigest(), "decoder": "eccodes"})


def load_humidity(data_dir, config):
    raw = data_dir/"raw"
    content, manifest = (raw/"humidity-points.json").read_bytes(), (raw/"humidity-manifest.json").read_bytes()
    meta = json.loads((raw/"humidity-points.json.meta.json").read_text(encoding="utf-8"))
    if hashlib.sha256(content).hexdigest()!=meta["sha256"] or hashlib.sha256(manifest).hexdigest()!=meta["manifest_sha256"]:
        raise ValueError("Humidity extraction hash mismatch")
    p = json.loads(content)
    if p["config"]!=config or p["field_schema"]!=RH_FIELDS:
        raise ValueError("Humidity cache schema/config mismatch")
    return p["rows"], p["failures"], json.loads(manifest)["downloads"], meta


def collect_humidity(config, data_dir, temperature_points, offline=False, workers=10):
    if not 1<=workers<=12:
        raise ValueError("workers must be 1–12")
    import eccodes  # noqa: F401
    rows, downloads = [], []
    if (data_dir/"raw/humidity-points.json").exists():
        rows, _, downloads, _ = load_humidity(data_dir, config)
    points = {p["valid_at_utc"]:p for p in temperature_points}
    done = {r["valid_at_utc"] for r in rows}
    jobs = [(d,t) for year in config["years"] for d in season_dates(config,year) for t in night_hours(config,d) if t.isoformat() not in done]
    if not jobs:
        return load_humidity(data_dir, config)
    if offline:
        raise ValueError(f"Humidity cache incomplete: {len(jobs)} hours; run humidity-fetch online")
    failures, iterator, attempted = [], iter(jobs), 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}
        def submit():
            job = next(iterator,None)
            if job:
                pending[pool.submit(fetch_rh,config,data_dir,*job,False)] = job
        for _ in range(workers*2): submit()
        while pending:
            ready,_ = wait(pending,return_when=FIRST_COMPLETED)
            for future in ready:
                d,t = pending.pop(future)
                try:
                    content,meta = future.result()
                    rows.append(decode_hour(config,d,t,{"rh":(content,meta)},points[t.isoformat()],RH_FIELDS))
                    downloads.append(meta)
                except (ValueError,OSError,KeyError) as exc:
                    failures.append({"night_date_local":d.isoformat(),"valid_at_utc":t.isoformat(),"error":str(exc)})
                attempted+=1
                if attempted%280==0 or attempted==len(jobs):
                    seal_humidity(data_dir,config,rows,failures,downloads)
                    print(f"GFS RH: {len(rows)}/{len(points)} hours; failures={len(failures)}; {sum(m['bytes'] for m in downloads)/1024**3:.2f} GiB",flush=True)
                submit()
    return load_humidity(data_dir,config)
