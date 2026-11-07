"""New full-ISD QC benchmark; never rewrites legacy ISD-Lite targets."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import json
from .pipeline import ROOT, UTC, cached_download, write_json
from .product_data import sha
from . import regional_data, terrain_archive
from .vityazevo_split_data import numeric, index_observations

DIRECTORY = ROOT/'data/working-zone-01/cold-station-transfer'
PROTOCOL = ROOT/'config/cold_station_transfer.json'
SOURCE = ROOT/'agro_checker/cold_station_data.py'


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def protocol():
    p = read(PROTOCOL)
    if p['years'] != [2022, 2023, 2024]: raise ValueError('Control years forbidden')
    if len({s['id'] for s in p['sites']}) != len(p['sites']): raise ValueError('Duplicate site')
    return p


def cell(lat, lon):
    # The reused regular_ll grid is global0.25. Ties are not silently resolved.
    values = [lat*4, lon*4]
    if any(abs((v % 1)-.5) < 1e-8 for v in values): raise ValueError('Ambiguous nearest cell')
    return tuple(round(v)/4 for v in values)


def parse(raw, sid, year):
    if year not in [2022, 2023, 2024]: raise ValueError('Controls forbidden')
    result = []
    for number, line in enumerate(gzip.decompress(raw).decode('ascii').splitlines(), 1):
        if len(line) < 105 or line[4:15] != sid.replace('-', ''): raise ValueError('Wrong full-ISD station/schema')
        stamp = datetime.strptime(line[15:27], '%Y%m%d%H%M').replace(tzinfo=UTC)
        if stamp.year != year: raise ValueError('Wrong source year')
        t = numeric(line, 87, 92, 9999, 10, -93.2, 61.8); qc = line[92]
        result.append({'source_station_id': sid, 'source_year': year, 'source_row': number,
                       'observed_at_utc': stamp.isoformat(), 'latitude': int(line[28:34])/1000,
                       'longitude': int(line[34:41])/1000, 'elevation_m': int(line[46:51]),
                       'report_type': line[41:46], 'source_code': line[27], 'qc_process': line[56:60],
                       'raw_temperature_c': t, 'temperature_qc': qc, 'temperature_c': t if qc in ['1', '5'] else None,
                       'is_orchard_truth': False})
    if not result: raise ValueError('Empty full ISD')
    return result


def choose_position(records, site):
    counts = Counter((r['latitude'], r['longitude'], r['elevation_m']) for r in records
                     if datetime.fromisoformat(r['observed_at_utc']).month in [3, 4, 5]
                     and abs(r['latitude']-site['latitude']) <= .01 and abs(r['longitude']-site['longitude']) <= .01
                     and abs(r['elevation_m']-site['elevation_m']) <= 2)
    if not counts: raise ValueError('No physical position agrees with inventory')
    ranked = counts.most_common()
    if len(ranked)>1 and ranked[0][1] == ranked[1][1]: raise ValueError('Ambiguous dominant position')
    position = ranked[0][0]
    if cell(*position[:2]) != cell(site['latitude'], site['longitude']): raise ValueError('Different GFS cell; cannot reuse forecast')
    return position


def fetch(job):
    site, year = job
    if year not in [2022, 2023, 2024]: raise ValueError('Controls forbidden')
    sid = site['id']; path = DIRECTORY/'raw'/f'{sid}-{year}-full-isd.gz'
    raw, meta = cached_download(f'https://www.ncei.noaa.gov/pub/data/noaa/{year}/{sid}-{year}.gz', path)
    rr = parse(raw, sid, year)
    print('Full ISD', sid, year, len(rr), flush=True)
    return sid, year, rr, meta, path


def prepare():
    if (DIRECTORY/'dataset.meta.json').exists(): return load('development')[1]
    p = protocol(); old = read(regional_data.DIRECTORY/'dataset.meta.json'); inventory = {s['id']: s for s in old['sites']}
    sites = [{**inventory[s['id']], **s} for s in p['sites']]
    write_json(DIRECTORY/'protocol-before-observations.json', {'protocol': p, 'source_sha256': sha(SOURCE),
                                                            'saved_at_utc': datetime.now(UTC).isoformat()})
    records = defaultdict(list); sources = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for sid, year, rr, meta, path in pool.map(fetch, [(s, y) for s in sites for y in p['years']]):
            records[sid].extend(rr); sources[str(path.relative_to(ROOT))] = meta
    # Reuse only forecasts/metadata, replacing ALL old labels with full-ISD values.
    development, _ = regional_data.load('development'); spatial, _ = regional_data.load('heldout_site')
    old_nights = {(n['id'], n['date']): n for n in development+spatial}
    terrain, terrain_meta = terrain_archive.load(False)
    partitions = {'development': [], 'spatial_check': []}; stats = {}; annual_hashes = {}; positions = []
    for site in sites:
        sid = site['id']; rr = records[sid]; position = choose_position(rr, site)
        physical = {**site, 'latitude': position[0], 'longitude': position[1], 'elevation_m': position[2]}
        selected = [{**r, 'site_id': sid} for r in rr if (r['latitude'], r['longitude'], r['elevation_m']) == position]
        other = [r for r in rr if (r['latitude'], r['longitude'], r['elevation_m']) != position]
        observed, conflicts = index_observations(selected, physical)
        for name, content in [('annual-observations.json', selected), ('other-positions.json', other), ('conflicts.json', conflicts)]:
            path = DIRECTORY/sid/name; write_json(path, content); annual_hashes[str(path.relative_to(DIRECTORY))] = sha(path)
        stats[sid] = {}; positions.append({**physical, 'inventory_position': [site[k] for k in ['latitude', 'longitude', 'elevation_m']],
                                         'other_position_reports': len(other), 'conflicting_terms': len(conflicts)})
        for year in p['years']:
            nn = []
            for (old_sid, day), original in old_nights.items():
                if old_sid != sid or original['year'] != year: continue
                provenance = [observed.get(t) for t in original['times']]
                hgt = terrain['records'][day][sid]
                if cell(*position[:2]) != (hgt['grid_latitude'], hgt['grid_longitude']): raise ValueError('Terrain cell mismatch')
                n = {**original, **physical, 'target_temperature_c': [r['value'] if r else None for r in provenance],
                     'target_provenance': provenance, 'gfs_surface_height_m': hgt['gfs_surface_height'],
                     'gfs_grid_latitude': hgt['grid_latitude'], 'gfs_grid_longitude': hgt['grid_longitude'],
                     'is_orchard_truth': False}
                nn.append(n)
            spring = [r for r in selected if r['source_year'] == year and datetime.fromisoformat(r['observed_at_utc']).month in [3, 4, 5]]
            full = [n for n in nn if all(t is not None for t in n['target_temperature_c'])]
            stats[sid][str(year)] = {'annual_reports': sum(r['source_year']==year for r in selected),
                                    'spring_reports': len(spring), 'spring_qc': dict(Counter(r['temperature_qc'] for r in spring)),
                                    'spring_report_types': dict(Counter(r['report_type'] for r in spring)),
                                    'observed_slots': sum(t is not None for n in nn for t in n['target_temperature_c']),
                                    'complete_nights': len(full), 'cold_complete_nights': sum(min(n['target_temperature_c'])<0 for n in full)}
            if len(nn) != 92 or len(full)<30: raise ValueError('Insufficient complete nights; do not silently change station roster')
            partitions[site['role']].extend(sorted(nn, key=lambda n: n['date']))
    hashes = {}
    for role, nn in partitions.items():
        path = DIRECTORY/(role+'.json'); write_json(path, nn); hashes[role] = sha(path)
    parents = {str(path.relative_to(ROOT)): sha(path) for path in [PROTOCOL, SOURCE, ROOT/'agro_checker/vityazevo_split_data.py',
               ROOT/'agro_checker/regional_data.py', ROOT/'agro_checker/terrain_archive.py', ROOT/'agro_checker/pipeline.py', ROOT/'agro_checker/product_data.py',
               regional_data.DIRECTORY/'dataset.meta.json', regional_data.DIRECTORY/'gfs-manifest.json',
               terrain_archive.DIRECTORY/'terrain-prior.json', terrain_archive.DIRECTORY/'terrain-prior.json.meta.json']}
    m = {'dataset_sha256': hashes, 'annual_sha256': annual_hashes, 'parents_sha256': parents, 'sources': sources,
         'sites': positions, 'statistics': stats, 'controls_used': False, 'production_admitted': False,
         'generated_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'dataset.meta.json', m); return m


def load(role):
    if role not in ['development', 'spatial_check']: raise ValueError('Unknown partition')
    m = read(DIRECTORY/'dataset.meta.json')
    checks = {DIRECTORY/(role+'.json'): m['dataset_sha256'][role]}
    checks.update({ROOT/path: h for path, h in m['parents_sha256'].items()})
    checks.update({ROOT/path: v['sha256'] for path, v in m['sources'].items()})
    checks.update({DIRECTORY/path: h for path, h in m['annual_sha256'].items()})
    if any(sha(path) != h for path, h in checks.items()): raise ValueError('Frozen cold-station data/source/protocol changed')
    nn = read(DIRECTORY/(role+'.json'))
    if any(n['year'] not in [2022, 2023, 2024] or n['role'] != role for n in nn): raise ValueError('Wrong partition/control labels')
    return nn, m


if __name__ == '__main__':
    m = prepare(); print(json.dumps(m['statistics'], ensure_ascii=False, indent=2))
