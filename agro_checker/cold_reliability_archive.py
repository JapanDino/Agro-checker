"""Unopened station panel, fixed by metadata before any labels are downloaded."""
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import date, datetime, timedelta
import csv
import gzip
import io
import json
import math
from .pipeline import ROOT, UTC, cached_download, write_json
from .product_data import sha
from .regional_data import validate_field

DIRECTORY = ROOT/'data/processed/cold-reliability'


def register(p):
    inventory = ROOT/'data/processed/product-stations/raw/isd-history.csv'
    old_paths = [ROOT/'data/processed/regional-minimum/station-audit.json',
                 ROOT/'data/processed/product-stations/station-audit.json']
    used = set()
    for path in old_paths:
        used.update(s['id'] for s in json.loads(path.read_text(encoding='utf-8'))['stations'])
    # Also exclude downloaded sites that might have escaped an old inventory.
    used.update(path.name[:12] for path in (ROOT/'data').rglob('*.gz')
                if DIRECTORY not in path.parents and len(path.name) == 20 and path.name[6] == '-')
    lo, hi, west, east = p['new_station_bbox']; candidates = []
    for r in csv.DictReader(io.StringIO(inventory.read_text(encoding='utf-8-sig'))):
        try: lat, lon, elevation = map(float, [r['LAT'], r['LON'], r['ELEV(M)']])
        except (ValueError, TypeError): continue
        sid = r['USAF']+'-'+r['WBAN']
        if sid in used or r['CTRY'] != 'RS' or not r['ICAO'] or r['BEGIN'] > '20220101' or r['END'] < '20240531': continue
        if not lo <= lat <= hi or not west <= lon <= east: continue
        distance = (lat-45.035)**2+((lon-38.975)*.7071)**2
        candidates.append({'id': sid, 'name': r['STATION NAME'], 'latitude': lat, 'longitude': lon,
                           'elevation_m': elevation, 'icao': r['ICAO'], 'role': 'new_spatial_test',
                           'metadata_distance_rank_value': distance})
    sites = sorted(candidates, key=lambda s: (s['metadata_distance_rank_value'], s['id']))[:p['new_station_count']]
    if len(sites) != p['new_station_count']: raise ValueError('Too few new metadata candidates')
    result = {'sites': sites, 'excluded_previously_audited_or_downloaded_sites': sorted(used),
              'inventory_sha256': sha(inventory), 'previous_inventory_sha256': {str(x.relative_to(ROOT)): sha(x) for x in old_paths},
              'labels_opened': False, 'rule': p['new_station_rule'], 'registered_at_utc': datetime.now(UTC).isoformat()}
    path = DIRECTORY/'new-stations-before-labels.json'
    if path.exists():
        old = json.loads(path.read_text(encoding='utf-8'))
        for key in result:
            if key != 'registered_at_utc' and old[key] != result[key]: raise ValueError('New station registry changed')
        return old
    write_json(path, result); return result


def parse_observations(raw, year):
    if year not in [2024, 2025, 2026]: raise ValueError('Only locked spatial test years')
    result = {}
    for line in gzip.decompress(raw).decode('ascii').splitlines():
        f = line.split()
        if len(f) != 12: raise ValueError('Unexpected ISD-Lite schema')
        t = datetime(*map(int, f[:4]), tzinfo=UTC)
        if t.year != year or t.isoformat() in result: raise ValueError('Duplicate/incorrect station timestamp')
        v = int(f[4]); result[t.isoformat()] = v/10 if v != -9999 and -800 <= v <= 600 else None
    return result


def times(day):
    return [datetime(day.year, day.month, day.day, 16, tzinfo=UTC)+timedelta(hours=h) for h in range(14)]


def spring(year): return [date(year, 3, 1)+timedelta(days=i) for i in range(92)]


def require_selection():
    # Hash every scientific source/config/parent/model before opening new labels.
    from .cold_reliability_study import selection
    selection()
    lock = ROOT/'models/research-cold-reliability/selection-before-controls.json'
    return sha(lock)


def audit(offline=False):
    selection_hash = require_selection()
    s = json.loads((ROOT/'models/research-cold-reliability/selection-before-controls.json').read_text(encoding='utf-8'))
    registry = json.loads((DIRECTORY/'new-stations-before-labels.json').read_text(encoding='utf-8'))
    p = s['protocol']
    def fetch(job):
        site, year = job; sid = site['id']; path = DIRECTORY/'raw'/f'{sid}-{year}.gz'
        url = f'https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/{year}/{sid}-{year}.gz'
        try:
            raw, meta = cached_download(url, path, offline); obs = parse_observations(raw, year)
        except OSError as exc:
            return sid, str(year), {'error': str(exc), 'complete_nights': 0, 'eligible': False}
        full = sum(all(obs.get(t.isoformat()) is not None for t in times(d)) for d in spring(year))
        return sid, str(year), {'path': str(path.relative_to(ROOT)), 'provenance': meta,
                                'complete_nights': full, 'incomplete_nights': 92-full,
                                'eligible': full >= p['new_station_minimum_complete_nights']}
    sites = [{**site, 'years': {}} for site in registry['sites']]; by_id = {s['id']: s for s in sites}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sid, year, info in pool.map(fetch, [(s, y) for s in sites for y in p['new_station_years']]):
            by_id[sid]['years'][year] = info
            print('New-site observation audit', sid, year, info['complete_nights'], flush=True)
    result = {'stations': sites, 'selection_sha256': selection_hash,
              'registry_sha256': sha(DIRECTORY/'new-stations-before-labels.json'),
              'observations': 'NOAA ISD-Lite hourly air temperature; no filling; detailed source QC flags not present',
              'eligibility_uses': 'complete nights only; all registered sites retained, no substitutions',
              'generated_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'new-station-audit.json', result); return result


def decode_temperature(job):
    import eccodes as ec
    day, sites = job
    source = ROOT/('data/scenario-2026' if day.year == 2026 else 'data')
    run = datetime(day.year, day.month, day.day, 6, tzinfo=UTC); issue = run+timedelta(hours=9)
    curves = {s['id']: [] for s in sites}; manifest = {}; grids = {}; distance = {}
    spec = {'shortName': '2t', 'typeOfLevel': 'heightAboveGround', 'level': 2, 'units': 'K'}
    for lead, t in zip(range(10, 24), times(day)):
        path = source/'raw/gfs-single'/run.strftime('%Y%m%d%H')/f'f{lead:03}.grib2'
        raw = path.read_bytes(); m = json.loads(path.with_name(path.name+'.meta.json').read_text(encoding='utf-8'))
        if sha(path) != m['sha256']: raise ValueError('GFS bytes changed')
        if datetime.fromisoformat(m['archive_last_modified_at_utc'])+timedelta(minutes=10) > issue: raise ValueError('Late GFS object')
        h = ec.codes_new_from_message(raw)
        try:
            validate_field(ec, h, spec, run, t)
            pts = ec.codes_grib_find_nearest_multiple(h, False, [s['latitude'] for s in sites], [s['longitude'] for s in sites])
            for s, pt in zip(sites, pts):
                if ec.codes_get(h, 'bitmapPresent') and not ec.codes_get_element(h, 'bitmap', pt['index']): raise ValueError('Missing temperature')
                value = float(pt['value'])-273.15
                if not math.isfinite(value) or not -80 <= value <= 60: raise ValueError('Invalid temperature')
                grid = (float(pt['lat']), float(pt['lon']))
                if s['id'] in grids and grids[s['id']] != grid: raise ValueError('Mixed grids across hours')
                grids[s['id']] = grid; distance[s['id']] = float(pt['distance']); curves[s['id']].append(value)
        finally: ec.codes_release(h)
        manifest[str(path.relative_to(ROOT))] = m
    return day, curves, manifest, distance


def extract():
    selection_hash = require_selection()
    a = json.loads((DIRECTORY/'new-station-audit.json').read_text(encoding='utf-8'))
    if a['selection_sha256'] != selection_hash: raise ValueError('Audit used different choice')
    sites = [s for s in a['stations'] if any(v['eligible'] for v in s['years'].values())]
    observations = {}
    for s in sites:
        observations[s['id']] = {}
        for y, info in s['years'].items():
            if 'path' not in info: continue
            path = ROOT/info['path']
            if sha(path) != info['provenance']['sha256']: raise ValueError('Observation bytes changed')
            observations[s['id']].update(parse_observations(path.read_bytes(), int(y)))
    records = []; manifest = {}; distances = {}
    if sites:
        with ProcessPoolExecutor(max_workers=4) as pool:
            jobs = [(d, sites) for y in [2024, 2025, 2026] for d in spring(y)]
            for done, (d, curves, dm, distance) in enumerate(pool.map(decode_temperature, jobs), 1):
                manifest.update(dm)
                for sid, v in distance.items(): distances[sid] = max(v, distances.get(sid, 0.))
                for s in sites:
                    if not s['years'][str(d.year)]['eligible']: continue
                    records.append({**{k: s[k] for k in ['id', 'name', 'latitude', 'longitude', 'elevation_m', 'role']},
                                    'year': d.year, 'date': d.isoformat(), 'gfs_minimum_c': min(curves[s['id']]),
                                    'target_temperature_c': [observations[s['id']].get(t.isoformat()) for t in times(d)]})
                if done % 30 == 0: print('New-site original GFS decoded', done, '/276', flush=True)
    file = DIRECTORY/'new-station-nights.json'; write_json(file, records)
    write_json(DIRECTORY/'new-station-gfs-manifest.json', manifest)
    meta = {'selection_sha256': selection_hash, 'audit_sha256': sha(DIRECTORY/'new-station-audit.json'),
            'registry_sha256': sha(DIRECTORY/'new-stations-before-labels.json'), 'dataset_sha256': sha(file),
            'manifest_sha256': sha(DIRECTORY/'new-station-gfs-manifest.json'), 'records': len(records),
            'max_grid_distance_km': distances, 'generated_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'new-station-dataset.meta.json', meta); return meta


def load():
    m = json.loads((DIRECTORY/'new-station-dataset.meta.json').read_text(encoding='utf-8'))
    paths = {'dataset': 'new-station-nights.json', 'audit': 'new-station-audit.json',
             'registry': 'new-stations-before-labels.json', 'manifest': 'new-station-gfs-manifest.json'}
    for key, file in paths.items():
        if sha(DIRECTORY/file) != m[key+'_sha256']: raise ValueError('New station input changed')
    if require_selection() != m['selection_sha256']: raise ValueError('New station choice changed')
    return json.loads((DIRECTORY/'new-station-nights.json').read_text(encoding='utf-8')), m


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--audit', action='store_true'); p.add_argument('--extract', action='store_true'); p.add_argument('--offline', action='store_true')
    a = p.parse_args()
    if a.audit: audit(a.offline)
    if a.extract: print(json.dumps(extract(), ensure_ascii=False))
