"""Isolated new-season forecast archive, opened only after the scenario selection is frozen."""

from datetime import datetime
import json

from .gfs_archive import collect
from .gfs_covariates import collect_covariates
from .humidity_archive import collect_humidity
from .pipeline import ROOT, UTC, cached_download, season_dates, write_json
from .scenario_study import load_model, load_scenario_protocol, sha
from .single_run import load_points, load_single_config, seal_points


def fresh_config(protocol):
    c = load_single_config(ROOT/"config/single_run.json")
    return {**c, "years": [protocol["fresh_year"]], "test_years": [protocol["fresh_year"]]}


def collect_fresh(data_dir, protocol_path, workers=10, offline=False):
    p = load_scenario_protocol(protocol_path); directory = data_dir.parent/"models/research-scenarios"
    choice = directory/"selection-before-history.json"
    if not choice.exists(): raise ValueError("Freeze scenario-select before opening a new season")
    selection = json.loads(choice.read_text(encoding="utf-8"))
    if selection["protocol_sha256"] != sha(protocol_path): raise ValueError("Frozen protocol mismatch")
    if set(selection["model_artifact_sha256"]) != {s["name"] for s in p["candidates"]}: raise ValueError("Frozen scenario model registry incomplete")
    for name, h in selection["model_artifact_sha256"].items(): load_model(directory/name, h)
    c = fresh_config(p); fresh_dir = data_dir/"scenario-2026"
    if max(season_dates(c, p["fresh_year"])) >= datetime.now(UTC).date(): raise ValueError("Fresh season must already be complete")
    marker = fresh_dir/"new-season-open.json"
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8"))["selection_sha256"] != sha(choice): raise ValueError("New season opened under another selection")
    else:
        write_json(marker, {"selection_sha256": sha(choice), "selected_before_new_targets_at_utc": selection["selection_saved_at_utc"],
                            "new_season_opened_at_utc": datetime.now(UTC).isoformat(), "year": p["fresh_year"]})
    write_json(fresh_dir/"source-config.json", c)
    # Fetch targets first to avoid a multi-GiB forecast transfer if station observations are unavailable.
    # The model and selection hashes above are already sealed and checked before this first new-target request.
    url = f"https://data.meteostat.net/hourly/{p['fresh_year']}/{c['station_id']}.csv.gz"
    cached_download(url, fresh_dir/"raw"/f"meteostat-{c['station_id']}-{p['fresh_year']}.csv.gz", offline)
    print("Fresh station observations saved under frozen choice; collecting one-run GFS", flush=True)
    if (fresh_dir/"raw/single-run-points.json").exists():
        points, failures, _, _ = load_points(fresh_dir, c)
        if failures: raise ValueError("Fresh temperature acquisition has failures; inspect manifest")
    else:
        points, failures, _ = collect(c, fresh_dir, offline, workers)
        seal_points(fresh_dir, c, points, failures)
        if failures: raise ValueError("Fresh temperature acquisition incomplete; inspect manifest")
    collect_covariates(c, fresh_dir, points, offline, workers)
    collect_humidity(c, fresh_dir, points, offline, workers)
    return fresh_dir
