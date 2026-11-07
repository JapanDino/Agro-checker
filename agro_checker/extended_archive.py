"""Isolated compatible GFS2021 archive; old layouts, exact six instantaneous fields."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
import json
from .pipeline import ROOT, UTC, cached_download, night_hours, season_dates, write_json
from .gfs_archive import BUCKET, aws_fetch, run_at, temperature_range, range_download, point_row
from .gfs_covariates import FIELDS, field_range, check_object_version, decode_hour, seal_covariates
from .humidity_archive import RH_FIELDS, seal_humidity
from .single_run import seal_points


def archive_config():
    c = json.loads((ROOT/'config/single_run.json').read_text(encoding='utf-8'))
    # Pre-v16 has only averaged total cloud; never substitute it for instant TCDC.
    c.update(years=[2021], season_start='03-23', development_years=[2021], validation_years=[2023], test_years=[2024])
    return c


def historical_url(run, lead):
    if run.year != 2021 or run.hour != 6 or not 10 <= lead <= 23: raise ValueError('Unsupported extension run/lead')
    folder = '' if run < datetime(2021, 3, 22, 12, tzinfo=UTC) else 'atmos/'
    return f'{BUCKET}/gfs.{run:%Y%m%d}/{run:%H}/{folder}gfs.t{run:%H}z.pgrb2.0p25.f{lead:03}'


def fetch_hour(c, directory, evening, stamp, offline=False):
    run = run_at(c, evening); lead = int((stamp-run).total_seconds()/3600); url = historical_url(run, lead)
    original = directory/'raw/gfs-single'/f'{run:%Y%m%d%H}'/f'f{lead:03}'
    index, imeta = cached_download(url+'.idx', original.with_suffix('.idx'), offline, fetcher=aws_fetch)
    start, end = temperature_range(index, run, lead)
    content, tmeta = range_download(url, original.with_suffix('.grib2'), start, end, offline)
    fields = {}
    for key, specs in [(k, FIELDS) for k in FIELDS]+[('rh', RH_FIELDS)]:
        start, end = field_range(index, run, lead, key, specs)
        subdir = 'gfs-humidity' if key == 'rh' else 'gfs-covariates'
        binary, meta = range_download(url, directory/'raw'/subdir/f'{run:%Y%m%d%H}'/f'f{lead:03}_{key}.grib2', start, end, offline)
        check_object_version(meta, tmeta); fields[key] = (binary, meta)
    return content, tmeta, fields, imeta


def collect(directory, offline=False, workers=10):
    if not 1 <= workers <= 12: raise ValueError('workers must be1–12')
    c = archive_config(); directory.mkdir(parents=True, exist_ok=True)
    file = directory/'source-config.json'
    if file.exists() and json.loads(file.read_text(encoding='utf-8')) != c: raise ValueError('Extension config changed')
    write_json(file, c)
    checkpoint = directory/'raw/combined-checkpoint.json'
    if checkpoint.exists():
        old = json.loads(checkpoint.read_text(encoding='utf-8'))
        if old['config'] != c: raise ValueError('Checkpoint config mismatch')
    else: old = {'config': c, 'temperature': [], 'covariates': [], 'humidity': [], 'tmanifest': [], 'cmanifest': [], 'rmanifest': []}
    done = {r['valid_at_utc'] for r in old['temperature']}
    for label in ['covariates', 'humidity']:
        if {r['valid_at_utc'] for r in old[label]} != done: raise ValueError('Partial checkpoint hour')
    jobs = [(d, t) for d in season_dates(c, 2021) for t in night_hours(c, d) if t.isoformat() not in done]
    failures = []; attempted = 0; iterator = iter(jobs)
    def seal():
        write_json(directory/'raw/single-run-manifest.json', {'downloads': old['tmanifest'], 'failures': failures})
        seal_points(directory, c, old['temperature'], failures)
        seal_covariates(directory, c, old['covariates'], failures, old['cmanifest'])
        seal_humidity(directory, c, old['humidity'], failures, old['rmanifest'])
        write_json(checkpoint, old)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}
        def submit():
            job = next(iterator, None)
            if job: pending[pool.submit(fetch_hour, c, directory, *job, offline)] = job
        for _ in range(workers*2): submit()
        while pending:
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                d, t = pending.pop(future)
                try:
                    content, tmeta, fields, imeta = future.result()
                    point = point_row(c, d, t, content, tmeta)
                    cov = decode_hour(c, d, t, {k: fields[k] for k in FIELDS}, point)
                    rh = decode_hour(c, d, t, {'rh': fields['rh']}, point, RH_FIELDS)
                    if not point['available_by_issue_proxy'] or any(not f['available_by_issue_proxy'] for f in cov['fields'].values()) or not rh['fields']['rh']['available_by_issue_proxy']:
                        raise ValueError('Early archive unavailable by issuance proxy; do not fake availability')
                    old['temperature'].append(point); old['covariates'].append(cov); old['humidity'].append(rh)
                    old['tmanifest'].extend([imeta, tmeta]); old['cmanifest'].extend(fields[k][1] for k in FIELDS); old['rmanifest'].append(fields['rh'][1])
                except (ValueError, OSError, KeyError) as exc:
                    failures.append({'night_date_local': d.isoformat(), 'valid_at_utc': t.isoformat(), 'error': str(exc)})
                attempted += 1
                if attempted % 40 == 0 or attempted == len(jobs):
                    seal(); size = sum(m.get('bytes', 0) for label in ['tmanifest','cmanifest','rmanifest'] for m in old[label])/1024**3
                    print(f'GFS2021: {len(old["temperature"])}/{len(done)+len(jobs)} hours; failures={len(failures)}; {size:.2f} GiB', flush=True)
                submit()
    seal()
    cached_download('https://data.meteostat.net/hourly/2021/34929.csv.gz', directory/'raw/meteostat-34929-2021.csv.gz', offline)
    if failures: raise ValueError(f'Extension incomplete: {len(failures)} failures; rerun to retry')
    return c


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--offline', action='store_true'); parser.add_argument('--workers', type=int, default=10)
    args = parser.parse_args(); collect(ROOT/'data/archive-2021', args.offline, args.workers)
