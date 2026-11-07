"""Hash-verified raw-METAR wind and source comparison; no model training or selection."""

from collections import Counter
from datetime import datetime
import csv
import gzip
import io
import re
from urllib.parse import urlencode

from .pipeline import UTC, cached_download, number, write_json


def parse_metar(text):
    temp = re.search(r"\s(M?\d{2})/(M?\d{2})(?:\s|$)", text)
    wind = re.search(r"\s(?:\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?(MPS|KT|KMH)(?:\s|$)", text)
    value = lambda s: -float(s[1:]) if s.startswith("M") else float(s)
    factors = {"MPS": 1., "KT": 1852/3600, "KMH": 1/3.6}
    return {"temperature_c": value(temp.group(1)) if temp else None,
            "wind_ms": float(wind.group(1))*factors[wind.group(2)] if wind else None,
            "wind_unit": wind.group(2) if wind else None}


def audit_fresh_metar(fresh_dir, offline=False):
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"+urlencode({"station": "URKK", "data": "metar",
              "year1": 2026, "month1": 3, "day1": 1, "year2": 2026, "month2": 6, "day2": 2,
              "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no", "elev": "no", "missing": "M", "report_type": [3, 4]}, doseq=True)
    raw, meta = cached_download(url, fresh_dir/"raw/iem-URKK-20260301-20260602.csv", offline)
    primary = {}; units = Counter()
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        if r["station"] != "URKK": raise ValueError("Wrong primary METAR station")
        parsed = parse_metar(r["metar"]); units[parsed["wind_unit"]] += 1
        time = datetime.fromisoformat(r["valid"]).replace(tzinfo=UTC)
        if time.minute == 0: primary[time.isoformat()] = {**parsed, "raw_metar": r["metar"]}
    data = gzip.decompress((fresh_dir/"raw/meteostat-34929-2026.csv.gz").read_bytes()).decode("utf-8-sig")
    comparisons = []
    for r in csv.DictReader(io.StringIO(data)):
        time = datetime(*(int(r[k]) for k in ["year", "month", "day", "hour"]), tzinfo=UTC)
        if time.isoformat() not in primary or not 3 <= time.month <= 5: continue
        raw_row = primary[time.isoformat()]
        t = number(r["temp"]) if r["temp_source"] == "metar" else None
        w = number(r["wspd"]) if r["wspd_source"] == "metar" else None
        w = w/3.6 if w is not None else None
        comparisons.append({"time": time.isoformat(), "meteostat_temperature_c": t, "raw_temperature_c": raw_row["temperature_c"],
                            "meteostat_wind_ms": w, "raw_wind_ms": raw_row["wind_ms"], "raw_wind_unit": raw_row["wind_unit"],
                            "temperature_abs_delta_c": abs(t-raw_row["temperature_c"]) if t is not None and raw_row["temperature_c"] is not None else None,
                            "wind_abs_delta_ms": abs(w-raw_row["wind_ms"]) if w is not None and raw_row["wind_ms"] is not None else None,
                            "low_wind_boundary_disagreement": (w < 2) != (raw_row["wind_ms"] < 2) if w is not None and raw_row["wind_ms"] is not None else None})
    result = {"role": "diagnostic comparison and primary wind labels; no training/selection change", "raw_metar_provenance": meta,
              "primary_station": "URKK", "wind_units_in_reports": dict(units), "matched_hourly_reports": len(comparisons),
              "rounding_note": "Meteostat wind is km/h; raw reports may use whole m/s. Rounding can flip the arbitrary <2 m/s proxy; never interpret it as confirmed equipment suitability.",
              "comparisons": comparisons}
    for label, key in [("temperature", "temperature_abs_delta_c"), ("wind", "wind_abs_delta_ms")]:
        values = [r[key] for r in comparisons if r[key] is not None]
        result[label] = {"pairs": len(values), "mean_abs_delta": sum(values)/len(values) if values else None,
                         "max_abs_delta": max(values) if values else None}
    result["low_wind_boundary_disagreements"] = sum(r["low_wind_boundary_disagreement"] is True for r in comparisons)
    wind_pairs = [r for r in comparisons if r["meteostat_wind_ms"] is not None and r["raw_wind_ms"] is not None]
    result["wind_close_to_correct_ms_pairs"] = sum(r["wind_abs_delta_ms"] <= .15 for r in wind_pairs)
    result["wind_numeric_mps_left_as_kmh_pattern_pairs"] = sum(r["wind_abs_delta_ms"] > .15 and abs(r["meteostat_wind_ms"]*3.6-r["raw_wind_ms"]) <= .5 for r in wind_pairs)
    result["primary_hourly_wind_targets"] = {t: r["wind_ms"] for t, r in primary.items() if r["wind_ms"] is not None}
    write_json(fresh_dir/"metar-cross-check.json", result)
    return result
