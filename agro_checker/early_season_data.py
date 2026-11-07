"""Immutable early-season Sentinel archive, same grid/indices as prior research."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np

from . import farm_context_data as source
from .farm_context_data import ROOT, read, dump, digest
from .field_index_experiment import make_pairs, day
from .field_decline_experiment import historical_vector
from .crop_season_experiment import phase_features

D = ROOT/'data/farm-research/early-season-decline'
ARCHIVE = D/'archive'
INDEX = ROOT/'data/farm-research/field-index-model'
HOLDOUT = ['R23', 'R24', 'R25', 'R26', 'R27']
ACCEPTED_BASELINES = ['05.00', '05.10', '05.11']


def archive_protocol():
    path = ARCHIVE/'protocol.json'
    if path.exists():
        return read(path)
    base = read(INDEX/'protocol.json')
    p = {**base, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'periods': [dict(id=f'archive{y}', label=f'Archive {y}',
            start=f'{y}-01-01T00:00:00Z', end=f'{y}-10-31T23:59:59Z') for y in range(2019, 2024)],
        'allowed_baselines': ACCEPTED_BASELINES,
        'acceptance': 'full common grid, solar elevation >=20 degrees, explicit C1 baseline allowlist; no tile-cloud filter; local SCL mask retained',
        'target_season': 'March-October; January-February past context only',
        'source_policy': 'No mixing with Collection-0 PB05.09; does not assert every excluded image is scientifically invalid',
        'sources': ['https://sentiwiki.copernicus.eu/web/s2-processing',
            'https://sentiwiki.copernicus.eu/web/s2-products',
            'https://github.com/Element84/earth-search'],
        'used_by_model': 'separate research only; original archives/contours/models never overwritten'}
    dump(path, p)
    return p


def reject_reason(item, p):
    props = item['properties']
    if props.get('s2:processing_baseline') not in p['allowed_baselines']:
        return 'unsupported_processing_baseline'
    if props.get('earthsearch:boa_offset_applied') is True:
        return 'ambiguous_applied_offset'
    elevation = props.get('view:sun_elevation')
    if elevation is None or elevation < 20:
        return 'solar_elevation_below_20_or_unknown'
    x0, y0, x1, y1 = p['bounds_utm']
    for name in ['scl', 'red', 'nir', 'swir16']:
        a = item['assets'][name]
        tr = a.get('proj:transform', [])
        shape = a.get('proj:shape', [])
        if len(tr) < 6 or len(shape) != 2:
            return 'missing_grid_metadata'
        if tr[1] or tr[3] or tr[0] not in [10, 20] or tr[4] != -tr[0]:
            return 'unsupported_grid'
        left, top = tr[2], tr[5]
        if x0 < left or y1 > top or x1 > left+shape[1]*tr[0] or y0 < top+shape[0]*tr[4]:
            return 'outside_full_grid'
    return None


def year_history(pair, observations):
    """Match original features: observed history within current calendar year."""
    return [r for r in observations if r['date'][:4]==pair['issue'][:4]]


def collect():
    p = archive_protocol()
    # Standalone process: explicitly redirect the existing proven collector.
    source.D = ARCHIVE
    catalogue = source.catalogue()
    existing = read(ARCHIVE/'satellite.json')['scenes'] if (ARCHIVE/'satellite.json').exists() else []
    results = {r['id']: r for r in existing if r['status'] == 'ready'}
    accepted = []
    for entry in catalogue['items']:
        f = entry['item']
        reason = reject_reason(f, p)
        if reason:
            results[f['id']] = dict(id=f['id'], acquired_at_utc=f['properties']['datetime'],
                processing_baseline=f['properties'].get('s2:processing_baseline'), status='excluded', reason=reason)
        else:
            accepted.append(entry)
    dump(ARCHIVE/'catalogue-audit.json', dict(items=len(catalogue['items']), accepted=len(accepted),
        exclusion_counts=dict(Counter(r['reason'] for r in results.values() if r['status']=='excluded')),
        baselines_by_year={str(y):dict(Counter(e['item']['properties'].get('s2:processing_baseline')
            for e in catalogue['items'] if e['item']['properties']['datetime'][:4]==str(y))) for y in range(2019,2024)}))
    print('Accepted scenes:', len(accepted), 'catalogue:', len(catalogue['items']), flush=True)
    def snapshot():
        rr = sorted(results.values(), key=lambda r: (r['acquired_at_utc'],r['id']))
        dump(ARCHIVE/'satellite.json', dict(scenes=rr, pagination_completed=True,
            completed=len(rr)==len(catalogue['items']), counts=dict(Counter(r['status'] for r in rr))))
    snapshot()
    def one(entry):
        f = entry['item']
        try:
            return source.scene(entry)
        except Exception as exc:
            return dict(id=f['id'], acquired_at_utc=f['properties']['datetime'],
                processing_baseline=f['properties'].get('s2:processing_baseline'),
                status='extraction_error', reason=f'{type(exc).__name__}: {exc}')
    # No chosen clear dates or tile-cloud shortcuts; each eligible scene attempted.
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(one,e):e for e in accepted}
        for number, future in enumerate(as_completed(jobs), 1):
            r = future.result()
            results[r['id']] = r
            snapshot()
            print('Progress', number, '/', len(jobs), r['id'], r['status'], r.get('reason',''), flush=True)
    return read(ARCHIVE/'satellite.json')


def extract():
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely import transform as st
    from shapely.geometry import shape
    from .farm_context_analysis import masks, scenarios, spectral_stats

    p = archive_protocol()
    geometry = ROOT/'data/farm-research/reconstruction/approximate-areas.geojson'
    if digest(geometry) != p['contours_sha256']:
        raise ValueError('Original contours changed')
    snapshot = read(ARCHIVE/'satellite.json')
    if not snapshot['completed']:
        raise ValueError('Collection not complete')
    x0, y0, x1, y1 = p['bounds_utm']
    transform = from_origin(x0, y1, 20, 20)
    grid_shape = (int((y1-y0)/20), int((x1-x0)/20))
    project = Transformer.from_crs(4326, 32637, always_xy=True)
    grids = {}
    for f in read(geometry)['features']:
        poly = st(shape(f['geometry']), project.transform, interleaved=False)
        grids[f['properties']['id']] = masks(scenarios(poly, f['properties']['working_band_m']),transform,grid_shape)['core']
    terrain = read(ROOT/'data/farm-research/area-context/summary.json')['features']
    records, pixel_cache = [], {}
    for scene in snapshot['scenes']:
        if scene['status'] != 'ready':
            continue
        folder = ARCHIVE/'sentinel'/scene['id']
        # Check every byte used, including offset/scale evidence in item and TIFF crops.
        for name, value in scene['crop_sha256'].items():
            if digest(folder/name) != value:
                raise ValueError('Changed source crop: '+scene['id'])
        with np.load(folder/'indices.npz') as a:
            if a['valid'].shape != grid_shape:
                raise ValueError('Wrong source grid')
            flat = {k:a[k].ravel() for k in ['ndvi','ndmi','valid']}
            pixel_cache[scene['id']] = {}
            for fid, ix in grids.items():
                values = np.column_stack([flat['ndvi'][ix],flat['ndmi'][ix]])
                valid = flat['valid'][ix]
                stats = [spectral_stats(flat[k], flat['valid'], ix) for k in ['ndvi','ndmi']]
                if not all(s['usable'] for s in stats):
                    continue
                pixel_cache[scene['id']][fid] = (values, valid)
                records.append(dict(fid=fid, scene_id=scene['id'], date=scene['acquired_at_utc'][:10],
                    acquired_at_utc=scene['acquired_at_utc'], baseline=scene['processing_baseline'],
                    values=dict(core=dict(value=[s['value'] for s in stats],coverage=stats[0]['valid_fraction'],
                        clear_cells=stats[0]['valid_cells'],total_cells=len(ix))),
                    terrain=[terrain[fid]['dsm']['height']['base'],terrain[fid]['dsm']['slope']['base']]))
    unique = {}
    for r in records:
        k = r['fid'],r['date']
        score = r['values']['core']['coverage'],r['acquired_at_utc'],r['scene_id']
        if k not in unique or score > unique[k][0]:
            unique[k] = score,r
    observations = sorted([v[1] for v in unique.values()], key=lambda r:(r['fid'],r['date']))
    grouped = defaultdict(list)
    for r in observations:
        grouped[r['fid']].append(r)
    index = {(r['fid'],r['date']):r for r in observations}
    pairs = []
    # Never build previous/target links across years or processing baselines.
    for year in range(2019,2024):
        rr = make_pairs([r for r in observations if r['date'][:4]==str(year)])
        for r in rr:
            if r['issue'][5:7] < '03' or r['target'][:4] != str(year):
                continue
            fid = r['fid']
            now = index[fid,r['issue']]
            current, valid = pixel_cache[r['issue_scene']][fid]
            previous, pvalid = pixel_cache[r['previous_scene']][fid]
            history = year_history(r,grouped[fid])
            r['features'] = historical_vector(r, history,current,valid,previous,pvalid,now['values']['core']['coverage'])
            r['season'] = phase_features(r,history)
            future,fvalid = pixel_cache[r['target_scene']][fid]
            common = valid & fvalid
            delta_common = None
            if common.sum()>=10 and common.mean()>=.8:
                delta_common = (np.median(future[common],axis=0)-np.median(current[common],axis=0)).tolist()
            r['audit'] = dict(common_target_coverage=float(common.mean()),common_delta=delta_common,
                role='label support audit only; never features, case filtering or model selection')
            # Both helper functions enforce <=issue and same processing baseline.
            if not np.isfinite(r['features']+r['season']).all():
                raise ValueError('Nonfinite input')
            pairs.append(r)
    dump(D/'observations-early.json',dict(rows=observations,archive_sha256=digest(ARCHIVE/'satellite.json')))
    dump(D/'data-early.json',pairs)
    summary = dict(observations=len(observations), pairs=len(pairs),
        by_year={str(y):dict(pairs=sum(r['issue'][:4]==str(y) for r in pairs),
            fit_pairs=sum(r['issue'][:4]==str(y) and r['fid'] not in HOLDOUT for r in pairs),
            dates=len({r['issue'] for r in pairs if r['issue'][:4]==str(y)}),
            first_pair=min((r['issue'] for r in pairs if r['issue'][:4]==str(y)),default=None),
            last_target=max((r['target'] for r in pairs if r['issue'][:4]==str(y)),default=None),
            events=[int(sum(r['issue'][:4]==str(y) and r['fid'] not in HOLDOUT and r['y'][j]-r['x'][j]<=-.10 for r in pairs)) for j in range(2)]) for y in range(2019,2024)},
        common_support_audit={name:dict(events=int(sum(r['y'][j]-r['x'][j]<=-.10 for r in pairs)),
            events_with_common_support=int(sum(r['y'][j]-r['x'][j]<=-.10 and r['audit']['common_delta'] is not None for r in pairs)),
            events_retained_on_common_support=int(sum(r['y'][j]-r['x'][j]<=-.10 and r['audit']['common_delta'] is not None and r['audit']['common_delta'][j]<=-.10 for r in pairs))) for j,name in enumerate(['ndvi','ndmi'])},
        source_manifest_sha256=digest(ARCHIVE/'satellite.json'), data_sha256=digest(D/'data-early.json'))
    dump(D/'archive-summary.json',summary)
    print(summary,flush=True)
    return summary
