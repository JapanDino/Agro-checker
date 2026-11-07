"""Separate exact-time station benchmark, reading frozen source caches only."""
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
import json
import math
from .pipeline import ROOT, UTC, write_json
from .product_data import sha, night_times
from .regional_data import decode_day

DIRECTORY = ROOT/'data/working-zone-01/krymsk-observed-time'
RESEARCH = ROOT/'data/working-zone-01/research'
PROTOCOL = ROOT/'config/krymsk_observed_time.json'
SOURCE = ROOT/'agro_checker/krymsk_observed_data.py'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prior_year(year):
    if year not in [2022, 2023, 2024]:
        raise ValueError('Only prior2022-2024 data admitted; no control targets')


def join_slots(day, curves, observations, station_id='37002'):
    """Exact UTC join. Missing temperatures stay missing, even within a night."""
    prior_year(day.year)
    if station_id != '37002' or day.month not in [3, 4, 5]:
        raise ValueError('Wrong station/season')
    times = night_times(day)
    if set(curves) != {'temperature', 'rh', 'dewpoint', 'u10', 'v10', 'cloud'}:
        raise ValueError('Unexpected forecast fields')
    if any(len(v) != 14 or any(not math.isfinite(x) for x in v) for v in curves.values()):
        raise ValueError('Incomplete/nonfinite forecast')
    run = datetime(day.year, day.month, day.day, 6, tzinfo=UTC)
    rows = []
    for i, stamp in enumerate(times):
        if stamp.hour % 3: continue
        obs = observations.get(stamp.isoformat())
        if obs is not None and (obs['station_id'] != station_id or obs['observed_at_utc'] != stamp.isoformat()):
            raise ValueError('Observation identity/time mismatch')
        target = None if obs is None or obs['provider_model_filled_temperature'] else obs['temperature_c']
        if target is not None and (not math.isfinite(target) or not -80 <= target <= 60):
            raise ValueError('Invalid target')
        rows.append({'station_id': station_id, 'night_date': day.isoformat(), 'year': day.year,
                     'observed_at_utc': stamp.isoformat(), 'lead_hours': int((stamp-run).total_seconds()/3600),
                     'run_at_utc': run.isoformat(), 'scenario_issued_at_utc': (run+timedelta(hours=9)).isoformat(),
                     'gfs': {key: values[i] for key, values in curves.items()},
                     'forecast_night_minimum_c': min(curves['temperature']),
                     'forecast_night_cooling_c': curves['temperature'][-1]-curves['temperature'][0],
                     'target_temperature_c': target,
                     'target_source': 'pogodaiklimat37002_processed_synop_claim; original bulletin unverified',
                     'target_qc_original_available': False, 'is_orchard_truth': False})
    if [r['lead_hours'] for r in rows] != [12, 15, 18, 21]:
        raise ValueError('Unexpected exact observation schedule')
    return rows


def prepare():
    """Offline forecast extraction at the station, never the farm-as-truth shortcut."""
    if (DIRECTORY/'dataset.meta.json').exists():
        return load()[1]
    p = read(PROTOCOL)
    if p['development_years'] != [2022, 2023, 2024] or p['validation_years'] != [2023, 2024]:
        raise ValueError('Forbidden development/control protocol')
    audit = read(RESEARCH/'data-audit.json')['krymsk']
    obs_path = RESEARCH/'krymsk-three-hour-observations.json'
    if sha(obs_path) != audit['observations_sha256']:
        raise ValueError('Observation bytes changed')
    for name, info in audit['provenance'].items():
        if sha(RESEARCH/'raw'/name) != info['sha256']:
            raise ValueError('Raw observation page changed')
    obs = {}
    for r in read(obs_path):
        prior_year(datetime.fromisoformat(r['observed_at_utc']).year)
        if r['station_id'] != '37002' or r['observed_at_utc'] in obs:
            raise ValueError('Duplicate/wrong station observation')
        obs[r['observed_at_utc']] = r
    site = {'id': '37002', 'latitude': p['station_latitude'], 'longitude': p['station_longitude']}
    jobs = [(date(y, 3, 1)+timedelta(days=i), [site]) for y in p['development_years'] for i in range(92)]
    rows = []; manifest = {}; distance = 0.
    # Frozen regional decoder checks the original06UTC issue/fields/bitmap/grid,
    # object identity/SHA and Last-Modified+10min availability proxy.
    with ProcessPoolExecutor(max_workers=4) as pool:
        for i, (day, curves, dm, ds) in enumerate(pool.map(decode_day, jobs), 1):
            rows.extend(join_slots(day, curves['37002'], obs))
            manifest.update(dm); distance = max(distance, ds['37002'])
            if i % 30 == 0: print('Krymsk exact-time archive', i, '/276', flush=True)
    write_json(DIRECTORY/'dataset.json', rows)
    write_json(DIRECTORY/'gfs-manifest.json', manifest)
    parents = {str(path.relative_to(ROOT)): sha(path) for path in [
        obs_path, RESEARCH/'data-audit.json', ROOT/'agro_checker/farm_data_research.py',
        ROOT/'agro_checker/regional_data.py', ROOT/'agro_checker/product_data.py', ROOT/'agro_checker/pipeline.py']}
    parents.update({str((RESEARCH/'raw'/name).relative_to(ROOT)): info['sha256'] for name, info in audit['provenance'].items()})
    m = {'protocol_sha256': sha(PROTOCOL), 'source_sha256': sha(SOURCE), 'parents': parents,
         'dataset_sha256': sha(DIRECTORY/'dataset.json'), 'manifest_sha256': sha(DIRECTORY/'gfs-manifest.json'),
         'station': site, 'max_grid_distance_km': distance, 'expected_slots': len(rows),
         'accepted_slots': sum(r['target_temperature_c'] is not None for r in rows),
         'target': p['target'], 'is_orchard_truth': False, 'production_admitted': False,
         'availability': 'Archive Last-Modified+10min<=15UTC proxy; first historical publication not proven',
         'created_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'dataset.meta.json', m)
    return m


def load():
    m = read(DIRECTORY/'dataset.meta.json')
    checks = {PROTOCOL: m['protocol_sha256'], SOURCE: m['source_sha256'],
              DIRECTORY/'dataset.json': m['dataset_sha256'], DIRECTORY/'gfs-manifest.json': m['manifest_sha256']}
    checks.update({ROOT/path: h for path, h in m['parents'].items()})
    if any(sha(path) != h for path, h in checks.items()):
        raise ValueError('Frozen exact-time source/protocol/data changed; use a new experiment')
    rows = read(DIRECTORY/'dataset.json')
    for r in rows: prior_year(r['year'])
    return rows, m


if __name__ == '__main__':
    print(json.dumps(prepare(), ensure_ascii=False))
