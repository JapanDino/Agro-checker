"""Capture one complete, observed GFS issuance for a future night; no research-model promotion."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import hashlib
import math

from .field_store import stamp, utc_now
from .gfs_archive import aws_fetch, decode_temperature, object_url, range_download, temperature_range
from .gfs_covariates import FIELDS, check_object_version, decode_field, field_range
from .humidity_archive import RH_FIELDS
from .pipeline import UTC, cached_download


def next_night(now):
    local = now+timedelta(hours=3)
    return local.date()+timedelta(days=int(local.hour >= 19))


def candidate_runs(now):
    # A 3-hour retrieval allowance is a search heuristic, not a publication-time assertion.
    t = now-timedelta(hours=3)
    latest = t.replace(hour=t.hour//6*6, minute=0, second=0, microsecond=0)
    return [latest-timedelta(hours=6*i) for i in range(3)]


def fetch_hour(directory, run, valid):
    lead = int((valid-run).total_seconds()/3600)
    if not 1 <= lead <= 120: raise ValueError("Нужны почасовые горизонты GFS 1–120")
    url = object_url(run, lead); path = directory/"raw"/f"{run:%Y%m%d%H}"/f"f{lead:03}"
    index, imeta = cached_download(url+".idx", path.with_suffix(".idx"), False, fetcher=aws_fetch)
    start, end = temperature_range(index, run, lead)
    temp, meta = range_download(url, path.with_suffix(".grib2"), start, end)
    values = {}
    specs = {**FIELDS, **RH_FIELDS}
    for key in specs:
        start, end = field_range(index, run, lead, key, specs)
        content, m = range_download(url, path.with_name(path.name+f"_{key}.grib2"), start, end)
        check_object_version(m, meta)
        values[key] = (content, m)
    return {"valid": valid, "temp": temp, "temp_meta": meta, "fields": values, "index_meta": imeta}


def capture_forecast(store, profile_id, zone_id, night=None, now=None, progress=None):
    now = now or utc_now(); profile_record = store.profile_record(profile_id); p = profile_record["payload"]
    zone = next((z for z in p["zones"] if z["id"] == zone_id), None)
    if not zone or zone["latitude"] is None: raise ValueError("Для сбора прогноза нужны координаты участка")
    evening = date.fromisoformat(night) if isinstance(night, str) else night or next_night(now)
    times = [datetime(evening.year, evening.month, evening.day, 16, tzinfo=UTC)+timedelta(hours=i) for i in range(14)]
    if times[0] <= now or times[0] > now+timedelta(hours=72): raise ValueError("Выберите будущую ночь в пределах 72 часов")
    errors = []; config = {"latitude": zone["latitude"], "longitude": zone["longitude"]}
    for run in candidate_runs(now):
        try:
            if progress: progress(f"Выпуск {run:%d.%m %H}:00 UTC: загрузка 14 часов")
            results = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(fetch_hour, store.directory/"gfs", run, valid) for valid in times]
                for future in as_completed(futures):
                    results.append(future.result())
                    if progress: progress(f"Выпуск {run:%H}:00 UTC: получено {len(results)}/14 часов")
            hours, manifest = [], []
            for result in sorted(results, key=lambda r: r["valid"]):
                valid = result["valid"]; temperature = decode_temperature(result["temp"], config, run, valid)
                decoded = {key: decode_field(content, config, run, valid, key, {**FIELDS, **RH_FIELDS}) for key, (content, _) in result["fields"].items()}
                for item in decoded.values():
                    if any(abs(item[k]-temperature[k]) > 1e-6 for k in ["grid_latitude", "grid_longitude", "grid_distance_km"]): raise ValueError("Поля GFS имеют разные точки сетки")
                manifest.extend([result["index_meta"], result["temp_meta"]]+[m for _, m in result["fields"].values()])
                lead = int((valid-run).total_seconds()/3600)
                hours.append({"valid_at_utc": valid.isoformat(), "lead_hours": lead, "temperature_c": temperature["forecast_temperature_c"],
                              "rh_pct": min(100., decoded["rh"]["value"]), "raw_rh_pct": decoded["rh"]["value"],
                              "dewpoint_c": decoded["dewpoint"]["value"], "wind_ms": math.hypot(decoded["u10"]["value"], decoded["v10"]["value"]),
                              "cloud_pct": decoded["cloud"]["value"], "grid_latitude": temperature["grid_latitude"],
                              "grid_longitude": temperature["grid_longitude"], "grid_distance_km": temperature["grid_distance_km"]})
            issued = utc_now()
            retrieved = max(stamp(m["retrieved_at_utc"]) for m in manifest)
            if any(stamp(m["archive_last_modified_at_utc"]) < run or stamp(m["archive_last_modified_at_utc"]) > issued for m in manifest if "archive_last_modified_at_utc" in m):
                raise ValueError("Время исходного объекта GFS некорректно")
            if issued >= times[0]: raise ValueError("Загрузка завершилась после начала ночи; оперативная выдача не сохранена")
            forecast = {"origin": "live_gfs", "night_date_local": evening.isoformat(), "run_at_utc": run.isoformat(),
                        "capture_profile_revision": profile_record["id"],
                        "issued_at_utc": issued.isoformat(), "retrieved_by_utc": retrieved.isoformat(), "availability_evidence": "observed_retrieval",
                        "first_publication_at_utc": None, "hours": hours, "manifest": manifest,
                        "attempted_runs": errors, "run_age_hours_at_issue": (issued-run).total_seconds()/3600,
                        "source_note": "Один реально полученный выпуск NOAA GFS; ближайшая ячейка, без локальной коррекции и прогноза повреждений"}
            record = store.issue(profile_id, zone_id, forecast, issued)
            if progress: progress("Прогноз сохранён до начала ночи")
            return record
        except (ValueError, OSError, KeyError) as exc:
            errors.append({"run_at_utc": run.isoformat(), "error": str(exc)})
    raise ValueError("Нет полного доступного выпуска GFS: "+"; ".join(r["run_at_utc"]+" "+r["error"] for r in errors))
