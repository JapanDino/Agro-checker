"""Optional process-local daily shadow collection; no notifications or equipment commands."""

from datetime import timedelta

from .field_live import capture_forecast
from .field_store import utc_now


def collect_due(store, now=None, capture=capture_forecast):
    now = now or utc_now(); local = now+timedelta(hours=3)
    # A fixed collection window avoids silently issuing tomorrow's forecast after tonight began.
    if not 17 <= local.hour < 19: return []
    night = local.date().isoformat(); latest = {}
    for r in store.records("profile"): latest[r["profile_id"]] = r["payload"]
    issued = {(r["profile_id"], r["payload"]["zone_id"], r["payload"]["night_date_local"])
              for r in store.records("forecast") if r["payload"]["origin"] == "live_gfs"}
    results = []
    for pid, p in latest.items():
        if p["mode"] != "field": continue
        for z in p["zones"]:
            if z["latitude"] is None or (pid, z["id"], night) in issued: continue
            try:
                record = capture(store, pid, z["id"], night, now=now)
                results.append({"profile_id": pid, "zone_id": z["id"], "forecast_id": record["id"], "status": "saved"})
            except (ValueError, OSError, KeyError) as exc:
                # Persist failed attempts too; the next poll may retry within the same pre-night window.
                store.append("collection_failure", pid, {"zone_id": z["id"], "night_date_local": night, "error": str(exc)}, now)
                results.append({"profile_id": pid, "zone_id": z["id"], "status": "failed", "error": str(exc)})
    return results
