"""Product-facing model limits derived solely from the saved forecast."""

import math
from .field_store import stamp


def model_quality(forecast):
    hours = forecast["hours"]
    def valid(v, lo, hi):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi
    temperature = sum(valid(h.get("temperature_c"), -90, 65) for h in hours)
    humidity = sum(valid(h.get("rh_pct"), 0, 100) for h in hours)
    covariates = sum(all(valid(h.get(k), lo, hi) for k, lo, hi in [
        ("wind_ms", 0, 100), ("dewpoint_c", -100, 65), ("cloud_pct", 0, 100)]) for h in hours)
    run, issued = stamp(forecast["run_at_utc"]), stamp(forecast["issued_at_utc"])
    synthetic = forecast["origin"] == "demo"
    return {"model": "synthetic_demo" if synthetic else "gfs_uncorrected",
            "temperature_hours": temperature, "humidity_hours": humidity,
            "complete_weather_feature_hours": covariates,
            "data_complete": len(hours) == temperature == humidity == 14,
            "run_age_at_issue_hours": (issued-run).total_seconds()/3600,
            "local_correction_applied": False, "local_error_range_verified": False,
            "crop_damage_probability": None, "all_clear_allowed": False,
            "status": "synthetic_learning" if synthetic else "local_accuracy_unverified",
            "negative_signal_message": "Порог не пересекается в GFS. Это не подтверждает отсутствие холода в саду: локальная ошибка не измерена.",
            "research_model_applied": False, "research_model_deployment": "station March-May GFS06 leads10..23 only; no orchard release"}
