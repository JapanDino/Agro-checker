"""Forecast-risk-conditioned, nested minimum envelope; frozen point and detector."""

import math

from .minimum_model import point_minimum, target_minimum


def finite_quantile(values, coverage):
    if not values or not 0 < coverage < 1: raise ValueError("Invalid quantile pool")
    if any(not math.isfinite(v) for v in values): raise ValueError("Nonfinite residual")
    rank = math.ceil((len(values)+1)*coverage)
    if rank > len(values): return None
    return {"value": sorted(values)[rank-1], "rank": rank, "nights": len(values)}


def residual_pool(model, samples, allowed_years):
    if not samples or any(n["year"] not in allowed_years or n["year"] >= 2025 for n in samples):
        raise ValueError("Only prior-season calibration targets are allowed")
    if set(model["training_dates"]) & {n["date"] for n in samples}: raise ValueError("Calibration overlaps fit")
    result = []
    seen = set()
    for n in samples:
        if n["date"] in seen: raise ValueError("Duplicate calibration night")
        seen.add(n["date"])
        actual = target_minimum(n)
        if actual is None: continue
        point, _ = point_minimum(model, n)
        result.append({"date": n["date"], "risk_temperature_c": min(point, min(n["base"]["temperature"])),
                       "overprediction_c": point-actual, "absolute_error_c": abs(point-actual)})
    return result


def calibrate_envelope(pool, spec, p, exclude_date=None):
    rows = [r for r in pool if r["date"] != exclude_date]
    if len(rows) < p["minimum_pool_nights"]: raise ValueError("Too few calibration nights")
    base = finite_quantile([r["absolute_error_c"] for r in rows], p["base_coverage"])
    if base is None: raise ValueError("No finite legacy envelope")
    radius = base["value"]
    result = {"spec": spec, "base_radius_c": radius, "lower_radius_c": radius, "upper_radius_c": radius,
              "pool_nights": len(rows), "global_rank": base["rank"], "risk_pool_nights": None,
              "fallback_to_global": False, "excluded_date": exclude_date}
    if spec["mode"] == "legacy": return result
    risk = [r for r in rows if r["risk_temperature_c"] <= spec["risk_threshold_c"]] if "risk_threshold_c" in spec else rows
    result["risk_pool_nights"] = len(risk)
    selectors = [("lower_radius_c", lambda r: r["absolute_error_c"] if spec["mode"] == "symmetric" else r["overprediction_c"])]
    if spec["mode"] in ["symmetric", "signed"]:
        selectors.append(("upper_radius_c", lambda r: r["absolute_error_c"] if spec["mode"] == "symmetric" else -r["overprediction_c"]))
    result["tail_ranks"] = {}
    for label, fn in selectors:
        q = finite_quantile([fn(r) for r in risk], spec["quantile"]) if len(risk) >= p["minimum_pool_nights"] else None
        fallback = q is None
        if fallback: q = finite_quantile([fn(r) for r in rows], spec["quantile"])
        if q is None: raise ValueError("No finite widened envelope; do not cap rank")
        result[label] = max(radius, q["value"])
        result["fallback_to_global"] |= fallback
        result["tail_ranks"][label] = {**q, "fallback_to_global": fallback}
    return result


def forecast_envelope(model, envelope, night, p):
    point, ood = point_minimum(model, night)
    spec = envelope["spec"]; risk_temperature = min(point, min(night["base"]["temperature"]))
    active = spec["mode"] != "legacy" and ("risk_threshold_c" not in spec or risk_temperature <= spec["risk_threshold_c"])
    lower = envelope["lower_radius_c"] if active else envelope["base_radius_c"]
    upper = envelope["upper_radius_c"] if active else envelope["base_radius_c"]
    return {"point_c": point, "lower_c": point-lower, "upper_c": point+upper,
            "legacy_lower_c": point-envelope["base_radius_c"], "legacy_upper_c": point+envelope["base_radius_c"],
            "risk_temperature_c": risk_temperature, "risk_extension_active": active,
            "calibration_fallback": envelope["fallback_to_global"] if active else False,
            "max_abs_z": ood, "extrapolation": ood > 4, "coverage_guaranteed": False,
            "threshold_state": "cold_range" if point+upper < 0 else "warm_range" if point-lower >= 0 else "crosses_zero",
            "probability_of_crop_damage": None}
