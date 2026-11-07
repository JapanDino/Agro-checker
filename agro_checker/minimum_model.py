"""Night-minimum head and held-season error envelope; no observations at inference."""

import math
import numpy as np

from .field_store import stamp


def features(night, temperature_only=False):
    rows = night["rows"]
    if len(rows) != 14 or [r["hour_index"] for r in rows] != list(range(14)):
        raise ValueError("Expected 14 ordered hourly inputs")
    if len({r["forecast_run_at_utc"] for r in rows}) != 1 or len({r["issued_at_utc"] for r in rows}) != 1:
        raise ValueError("Mixed GFS issue")
    run = stamp(rows[0]["forecast_run_at_utc"])
    issue = stamp(rows[0]["issued_at_utc"])
    times = [stamp(r["valid_at_utc"]) for r in rows]
    if night["year"] != int(night["date"][:4]) or any(r["date"] != night["date"] or r["year"] != night["year"] for r in rows):
        raise ValueError("Mixed night/year")
    if run.hour != 6 or run.minute or run.second or run.microsecond or issue >= times[0] or issue < run:
        raise ValueError("Unsupported run or late issuance")
    if [int((t-run).total_seconds()) for t in times] != [h*3600 for h in range(10, 24)]:
        raise ValueError("Unsupported forecast horizon")
    if times[0].date().isoformat() != night["date"] or times[0].month not in [3, 4, 5]:
        raise ValueError("Unsupported forecast season/date")
    curve = [r["base"]["temperature"] for r in rows]
    if night["base"]["temperature"] != curve: raise ValueError("Mismatched temperature curve")
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not -80 <= v <= 60 for v in curve):
        raise ValueError("Invalid temperature inputs")
    x = [min(curve), max(curve), sum(curve)/14, curve[0], curve[-1],
         curve[-1]-curve[0], max(curve)-min(curve), curve.index(min(curve))/13]
    if not temperature_only:
        weather = [r["features_weather"] for r in rows]
        for w in weather:
            if len(w) < 6 or any(isinstance(w[i], bool) or not isinstance(w[i], (int, float)) or not math.isfinite(w[i]) for i in [1, 2, 3, 4, 5]):
                raise ValueError("Missing weather features")
            if not (0 <= w[1] <= 110 and -100 <= w[2] <= 60 and 0 <= w[4] <= 100 and 0 <= w[5] <= 100):
                raise ValueError("Invalid weather feature range")
        x += [sum(w[i] for w in weather)/14 for i in [1, 2, 3, 4, 5]]
        x += [min(w[4] for w in weather), min(w[5] for w in weather)]
    return np.asarray(x, dtype=float)


def target_minimum(night):
    values = night["target"]["temperature"]
    return min(values) if len(values) == 14 and all(v is not None and math.isfinite(v) for v in values) else None


def fit_minimum(spec, samples):
    if not samples or any(n["year"] not in [2022, 2023] for n in samples):
        raise ValueError("Minimum fitting accepts only 2022/2023 targets")
    usable = [n for n in samples if target_minimum(n) is not None]
    if len(usable) < 20: raise ValueError("Too few complete training nights")
    x = np.asarray([features(n, spec.get("temperature_only", False)) for n in usable])
    y = np.array([target_minimum(n)-min(n["base"]["temperature"]) for n in usable])
    scale = x.std(axis=0); scale[scale == 0] = 1
    model = {"spec": spec, "mean": x.mean(axis=0).tolist(), "scale": scale.tolist(),
             "training_years": sorted({n["year"] for n in usable}),
             "training_dates": [n["date"] for n in usable], "training_nights": len(usable),
             "excluded_training_nights": len(samples)-len(usable)}
    if spec["kind"] == "gfs": model["bias"] = 0.
    elif spec["kind"] == "bias": model["bias"] = float(np.median(y))
    else:
        from sklearn.linear_model import HuberRegressor, Ridge
        estimator = Ridge(alpha=spec["alpha"], solver="svd") if spec["kind"] == "ridge" else HuberRegressor(alpha=spec["alpha"], max_iter=1000)
        estimator.fit((x-np.array(model["mean"]))/scale, y)
        model.update(coef=estimator.coef_.tolist(), intercept=float(estimator.intercept_))
    return model


def point_minimum(model, night):
    x = features(night, model["spec"].get("temperature_only", False))
    z = (x-np.asarray(model["mean"]))/np.asarray(model["scale"])
    residual = model["bias"] if "bias" in model else model["intercept"]+float(z @ np.array(model["coef"]))
    point = min(night["base"]["temperature"])+residual
    if not math.isfinite(point): raise ValueError("Nonfinite minimum prediction")
    return float(point), float(np.max(np.abs(z)))


def calibrate(model, samples, p):
    if not samples or any(n["year"] != p["calibration_year"] for n in samples):
        raise ValueError("Calibration accepts only held-out 2024")
    if set(model["training_dates"]) & {n["date"] for n in samples}: raise ValueError("Calibration overlaps fit")
    usable = [n for n in samples if target_minimum(n) is not None]
    if len(usable) < p["minimum_calibration_nights"]: raise ValueError("Too few calibration nights")
    errors = sorted(abs(target_minimum(n)-point_minimum(model, n)[0]) for n in usable)
    rank = math.ceil((len(errors)+1)*(1-p["interval_alpha"]))
    if rank > len(errors): raise ValueError("Insufficient calibration for finite envelope")
    return {"radius_c": errors[rank-1], "calibration_nights": len(errors),
            "excluded_calibration_nights": len(samples)-len(usable), "calibration_year": p["calibration_year"],
            "rank": rank, "nominal_coverage": 1-p["interval_alpha"]}


def predict_minimum(model, calibration, night, p):
    point, ood = point_minimum(model, night)
    radius = calibration["radius_c"]
    if not math.isfinite(radius) or radius < 0: raise ValueError("Invalid error envelope")
    lo, hi = point-radius, point+radius
    return {"point_c": point, "lower_c": lo, "upper_c": hi, "max_abs_z": ood,
            "extrapolation": ood > p["ood_max_abs_z"],
            "threshold_state": "cold_range" if hi < 0 else "warm_range" if lo >= 0 else "crosses_zero",
            "probability_of_crop_damage": None, "coverage_guaranteed": False,
            "scope": "station research minimum; not hourly curve, timing, orchard or continuous minimum"}


def safe_prediction(model, calibration, night, p, station_id):
    # Out-of-domain outputs are not silently published as a corrected orchard forecast.
    if station_id != "34929": return {"status": "unsupported_site", "prediction": None}
    try: result = predict_minimum(model, calibration, night, p)
    except (ValueError, KeyError, TypeError, IndexError): return {"status": "invalid_or_unsupported_inputs", "prediction": None}
    if result["extrapolation"]: return {"status": "extrapolation", "prediction": None}
    return {"status": "research_only", "prediction": result}
