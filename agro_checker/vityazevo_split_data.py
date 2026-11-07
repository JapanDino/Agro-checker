"""Full ISD annual archive split by actual physical position, never WMO alone."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
import gzip
import json
import math
import re
from .pipeline import ROOT, UTC, write_json
from .product_data import sha, night_times
from .regional_data import decode_day

DIRECTORY = ROOT/'data/working-zone-01/vityazevo-split'
PROTOCOL = ROOT/'config/vityazevo_split.json'
SOURCE = ROOT/'agro_checker/vityazevo_split_data.py'


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def protocol():
    p = read(PROTOCOL)
    if p['years'] != [2022, 2023, 2024] or [s['role'] for s in p['sites']] != ['development', 'spatial_check_only']:
        raise ValueError('Unregistered years/sites')
    return p


def full_source(year):
    if year not in [2022, 2023, 2024]: raise ValueError('Controls forbidden')
    folder = ROOT/'data/working-zone-01/regional-sources/raw' if year == 2024 else DIRECTORY/'raw'
    return folder/f'370010-99999-{year}-full-isd.gz'


def numeric(line, start, end, missing, scale, low, high):
    text = line[start:end]
    if not re.fullmatch(r'[+-]?\d+', text): raise ValueError('Invalid full-ISD numeric field')
    value = int(text)
    if value == missing: return None
    value /= scale
    if not low <= value <= high: raise ValueError('Invalid full-ISD field scale/range')
    return value


def parse_full(content, year, sites):
    if year not in [2022, 2023, 2024]: raise ValueError('Controls forbidden')
    groups = {s['id']: [] for s in sites}; unassigned = []
    index = {(s['latitude'], s['longitude'], s['elevation_m']): s['id'] for s in sites}
    for row_number, line in enumerate(gzip.decompress(content).decode('ascii').splitlines(), 1):
        if len(line) < 105 or line[4:15] != '37001099999': raise ValueError('Wrong full-ISD station/schema')
        stamp = datetime.strptime(line[15:27], '%Y%m%d%H%M').replace(tzinfo=UTC)
        if stamp.year != year: raise ValueError('Mixed source year')
        position = (int(line[28:34])/1000, int(line[34:41])/1000, int(line[46:51]))
        value = numeric(line, 87, 92, 9999, 10, -93.2, 61.8); qc = line[92]
        r = {'source_station_id': '370010-99999', 'source_year': year, 'source_row': row_number,
             'observed_at_utc': stamp.isoformat(), 'latitude': position[0], 'longitude': position[1], 'elevation_m': position[2],
             'report_type': line[41:46], 'source_code': line[27], 'qc_process': line[56:60],
             'raw_temperature_c': value, 'temperature_qc': qc,
             'temperature_c': value if qc in ['1', '5'] else None,
             'raw_dewpoint_c': numeric(line, 93, 98, 9999, 10, -98.2, 36.8), 'dewpoint_qc': line[98],
             'raw_wind_ms': numeric(line, 65, 69, 9999, 10, 0, 90), 'wind_qc': line[69],
             'is_orchard_truth': False}
        sid = index.get(position)
        if sid is None: unassigned.append({**r, 'reason': 'unregistered exact physical position'})
        else: groups[sid].append({**r, 'site_id': sid})
    if not any(groups.values()) and not unassigned: raise ValueError('Empty archive')
    return groups, unassigned


def index_observations(records, site):
    buckets = defaultdict(list)
    for r in records:
        if r['site_id'] != site['id'] or (r['latitude'], r['longitude'], r['elevation_m']) != (site['latitude'], site['longitude'], site['elevation_m']):
            raise ValueError('Physical positions must never be pooled')
        if r['temperature_c'] is not None: buckets[r['observed_at_utc']].append(r)
    values = {}; conflicts = []
    for stamp, rr in buckets.items():
        unique = {r['temperature_c'] for r in rr}
        if len(unique) > 1:
            conflicts.append({'site_id': site['id'], 'observed_at_utc': stamp, 'values_c': sorted(unique), 'source_rows': [r['source_row'] for r in rr]})
        else: values[stamp] = {'value': next(iter(unique)), 'qc': sorted({r['temperature_qc'] for r in rr}),
                              'source_rows': [r['source_row'] for r in rr]}
    return values, conflicts


def night(day, site, curves, observations):
    if day.year not in [2022, 2023, 2024] or day.month not in [3, 4, 5]: raise ValueError('Wrong season/year')
    if set(curves) != {'temperature', 'dewpoint', 'u10', 'v10', 'rh', 'cloud'} or any(len(v) != 14 or any(not math.isfinite(x) for x in v) for v in curves.values()):
        raise ValueError('Incomplete GFS issue')
    run = datetime(day.year, day.month, day.day, 6, tzinfo=UTC); times = [t.isoformat() for t in night_times(day)]
    return {**site, 'date': day.isoformat(), 'year': day.year, 'run_at_utc': run.isoformat(),
            'issued_at_utc': (run+timedelta(hours=9)).isoformat(), 'times': times, 'gfs': curves,
            'target_temperature_c': [observations.get(t, {}).get('value') for t in times],
            'target_provenance': [observations.get(t) for t in times], 'is_orchard_truth': False}


def prepare():
    if (DIRECTORY/'dataset.meta.json').exists(): return load('development')[1]
    p = protocol(); groups = {s['id']: [] for s in p['sites']}; raw_meta = {}; unassigned = []
    for year in p['years']:
        path = full_source(year); m = read(path.with_name(path.name+'.meta.json'))
        if sha(path) != m['sha256'] or m['url'] != f'https://www.ncei.noaa.gov/pub/data/noaa/{year}/370010-99999-{year}.gz':
            raise ValueError('Source provenance mismatch')
        split, unknown = parse_full(path.read_bytes(), year, p['sites']); unassigned += unknown
        for sid, rr in split.items(): groups[sid].extend(rr)
        raw_meta[str(path.relative_to(ROOT))] = m
    obs = {}; conflicts = []; annual_hashes = {}; stats = {}
    for site in p['sites']:
        sid = site['id']; rr = groups[sid]; obs[sid], bad = index_observations(rr, site); conflicts += bad
        path = DIRECTORY/sid/'annual-observations.json'; write_json(path, rr); annual_hashes[str(path.relative_to(DIRECTORY))] = sha(path)
        stats[sid] = {}
        for year in p['years']:
            spring = [r for r in rr if r['source_year'] == year and r['observed_at_utc'][5:7] in ['03', '04', '05']]
            stats[sid][str(year)] = {'annual_reports': sum(r['source_year'] == year for r in rr), 'spring_reports': len(spring),
                                    'spring_temperature_qc': dict(Counter(r['temperature_qc'] for r in spring)),
                                    'report_types': dict(Counter(r['report_type'] for r in spring))}
    write_json(DIRECTORY/'unassigned-positions.json', unassigned); write_json(DIRECTORY/'conflicting-duplicates.json', conflicts)
    records = {'development': [], 'spatial_check_only': []}; manifest = {}; distances = {s['id']: 0. for s in p['sites']}
    jobs = [(date(y, 3, 1)+timedelta(days=i), p['sites']) for y in p['years'] for i in range(92)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for i, (day, curves, dm, ds) in enumerate(pool.map(decode_day, jobs), 1):
            manifest.update(dm)
            for site in p['sites']:
                sid = site['id']; records[site['role']].append(night(day, site, curves[sid], obs[sid])); distances[sid] = max(distances[sid], ds[sid])
            if i % 30 == 0: print('Split-position GFS', i, '/276', flush=True)
    data_hashes = {}
    for role, nn in records.items():
        path = DIRECTORY/(role+'.json'); write_json(path, nn); data_hashes[role] = sha(path)
        for year in p['years']:
            subset = [n for n in nn if n['year'] == year]
            stats[subset[0]['id']][str(year)].update(expected_nights=len(subset), expected_slots=len(subset)*14,
                observed_exact_slots=sum(t is not None for n in subset for t in n['target_temperature_c']),
                complete_nights=sum(all(t is not None for t in n['target_temperature_c']) for n in subset))
    write_json(DIRECTORY/'gfs-manifest.json', manifest)
    parents = {str(path.relative_to(ROOT)): sha(path) for path in [ROOT/'agro_checker/regional_data.py', ROOT/'agro_checker/product_data.py', ROOT/'agro_checker/pipeline.py']}
    m = {'protocol_sha256': sha(PROTOCOL), 'source_sha256': sha(SOURCE), 'parents': parents, 'raw_sources': raw_meta,
         'dataset_sha256': data_hashes, 'annual_sha256': annual_hashes, 'statistics': stats, 'unassigned_reports': len(unassigned),
         'conflicting_duplicates': len(conflicts), 'max_grid_distance_km': distances, 'manifest_sha256': sha(DIRECTORY/'gfs-manifest.json'),
         'other_sha256': {name: sha(DIRECTORY/name) for name in ['unassigned-positions.json', 'conflicting-duplicates.json']},
         'availability': 'LastModified+10min proxy only; not historical first-publication proof',
         'generated_at_utc': datetime.now(UTC).isoformat(), 'controls_downloaded': False, 'production_admitted': False}
    write_json(DIRECTORY/'dataset.meta.json', m)
    return m


def load(role):
    if role not in ['development', 'spatial_check_only']: raise ValueError('Unknown split')
    m = read(DIRECTORY/'dataset.meta.json')
    checks = {PROTOCOL: m['protocol_sha256'], SOURCE: m['source_sha256'], DIRECTORY/(role+'.json'): m['dataset_sha256'][role],
              DIRECTORY/'gfs-manifest.json': m['manifest_sha256']}
    checks.update({ROOT/path: h for path, h in m['parents'].items()})
    checks.update({ROOT/path: info['sha256'] for path, info in m['raw_sources'].items()})
    checks.update({DIRECTORY/path: h for path, h in {**m['annual_sha256'], **m['other_sha256']}.items()})
    if any(sha(path) != h for path, h in checks.items()): raise ValueError('Frozen split data/source/protocol changed')
    nn = read(DIRECTORY/(role+'.json'))
    if any(n['year'] not in [2022, 2023, 2024] or n['role'] != role for n in nn): raise ValueError('Wrong partition/control labels')
    return nn, m


if __name__ == '__main__':
    m = prepare(); print(json.dumps(m['statistics'], ensure_ascii=False))
