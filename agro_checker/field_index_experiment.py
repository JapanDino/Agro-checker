"""Research-only, acquisition-date spectral forecast. Never writes operational state.

The response is satellite reflectance indices, not disease, soil water or yield.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import math

import numpy as np

from .farm_context_data import ROOT, read, dump, digest

D = ROOT / 'data/farm-research/field-index-model'
OLD = ROOT / 'data/farm-research/area-context'
INDEX_NAMES = ['ndvi', 'ndmi']
TEMPORAL = ['ndvi_now', 'ndmi_now', 'ndvi_slope', 'ndmi_slope', 'past_lag_days', 'doy_sin', 'doy_cos']
TERRAIN = ['dsm_height_m', 'dsm_slope_p90_deg']


def day(value):
    return date.fromisoformat(value[:10])


def freeze_revision():
    """Source-driven revision BEFORE reading labels or fitting, not a test-driven choice."""
    path = D / 'protocol.json'
    original = read(path)
    revision = D / 'training-protocol.json'
    if revision.exists():
        p = read(revision)
        if p['archive_protocol_sha256'] != digest(path):
            raise ValueError('Archive protocol changed')
        return p
    p = dict(schema=1, created_at_utc=datetime.now(timezone.utc).isoformat(),
        archive_protocol_sha256=digest(path), candidates=original['experiment']['candidates'],
        source_revision='2023 excluded: all 197 catalogue entries rejected by existing collector baseline policy. No 2023 labels extracted or used. This does NOT establish that all PB05.09 products are invalid.',
        initial_fit='2024 issue AND target before 2024-06-01; non-holdout fragments',
        selection='2024 issue >=2024-06-01 AND target before 2024-08-01; non-holdout fragments',
        final_fit='initial_fit + selection only',
        calibration='2024 issue >=2024-08-01; non-holdout fragments; never used for model/feature/alpha selection',
        spatial_holdout='five southernmost representative-point latitudes, never fit/select/calibrate',
        temporal_test='2026, no fitting, calibration or selection; previously seen descriptively, not pristine blind test',
        selection_metric='mean of NDVI/NDMI issue-date-balanced MAE; ties prefer listed order',
        target='core median NDVI/NDMI on nearest available clear acquisition 6-8 days after issue; one record per UTC day',
        features=TEMPORAL+TERRAIN,
        baseline_policy='exclude unsupported baselines; issue, previous and target must have identical processing baseline',
        intervals='90% nominal, per-index maximum residual per calibration issue day; temporal dependence means no guaranteed coverage',
        production_policy='research only even if test wins; original weather model/detector and operational data untouched',
        historical_live_backtest=False,
        historical_availability='Reprocessed archive availability at original issue time not established. Retrospective acquisition-date experiment only.',
        boundary_policy='fixed eroded core + full-pixel guard; >=10 clear cells and >=80% clear coverage; all 11 geometry scenarios checked separately, not confidence probabilities',
        auxiliary_event='index decline <= -0.10, NOT a disease/pest/stress ground-truth label')
    dump(revision, p)
    return p


def extract_observations():
    """Only measured accepted pixels; no cloud interpolation or synthetic labels."""
    import rasterio
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely import transform as st
    from shapely.geometry import shape
    from .farm_context_analysis import masks, scenarios, spectral_stats

    p = read(D / 'protocol.json')
    geo_path = ROOT / 'data/farm-research/reconstruction/approximate-areas.geojson'
    if digest(geo_path) != p['contours_sha256']:
        raise ValueError('Changed geometry')
    features = read(geo_path)['features']
    holdout = [f['properties']['id'] for f in sorted(features, key=lambda f: shape(f['geometry']).representative_point().y)[:5]]
    # Holdout IDs frozen before spectral values are read.
    dump(D / 'spatial-split.json', dict(holdout_ids=holdout, rule='five southernmost representative-point latitudes', contours_sha256=digest(geo_path)))
    x0, y0, x1, y1 = p['bounds_utm']
    transform = from_origin(x0, y1, 20, 20)
    grid_shape = (int((y1-y0)/20), int((x1-x0)/20))
    project = Transformer.from_crs(4326, 32637, always_xy=True)
    grids = {}
    static = read(OLD / 'summary.json')['features']
    for f in features:
        fid = f['properties']['id']
        poly = st(shape(f['geometry']), project.transform, interleaved=False)
        grids[fid] = masks(scenarios(poly, f['properties']['working_band_m']), transform, grid_shape)
    rows = []
    scenes = []
    for archive in [D, OLD]:
        for scene in read(archive / 'satellite.json')['scenes']:
            if scene['status'] != 'ready':
                continue
            yr = day(scene['acquired_at_utc']).year
            if (archive == D and yr != 2024) or (archive == OLD and yr != 2026):
                continue
            folder = ROOT / scene['local_folder'] if scene.get('local_folder') else archive / 'sentinel' / scene['id']
            # Validate each actual input byte, including read-only reused crops.
            for name, sha in scene['crop_sha256'].items():
                if digest(folder / name) != sha:
                    raise ValueError('Source crop changed: '+scene['id'])
            item = read(folder / 'item.json')
            # ESA recommends not quantitatively using SZA >70 degrees.
            sun_elev = item['properties'].get('view:sun_elevation')
            if sun_elev is None or not sun_elev >= 20:
                raise ValueError('Unsupported solar zenith: '+scene['id'])
            scenes.append(dict(id=scene['id'], acquired_at_utc=scene['acquired_at_utc'], baseline=scene['processing_baseline'], indices_sha256=scene['crop_sha256']['indices.npz'], sun_elevation_deg=sun_elev))
            with np.load(folder / 'indices.npz') as compressed:
                # Decompress each raster once, not again for every geometry/statistic.
                pixels = {k:compressed[k] for k in ['ndvi','ndmi','valid']}
                if pixels['valid'].shape != grid_shape:
                    raise ValueError('Wrong grid')
                for fid, variants in grids.items():
                    values = {}
                    for variant, ix in variants.items():
                        stats = [spectral_stats(pixels[name], pixels['valid'], ix) for name in INDEX_NAMES]
                        if all(s['usable'] for s in stats):
                            values[variant] = dict(value=[s['value'] for s in stats], coverage=stats[0]['valid_fraction'], clear_cells=stats[0]['valid_cells'], total_cells=len(ix))
                    if 'core' not in values:
                        continue
                    height = static[fid]['dsm']['height']['base']
                    slope = static[fid]['dsm']['slope']['base']
                    if height is None or slope is None:
                        continue
                    rows.append(dict(fid=fid, scene_id=scene['id'], date=scene['acquired_at_utc'][:10], acquired_at_utc=scene['acquired_at_utc'], baseline=scene['processing_baseline'], values=values, terrain=[height, slope]))
            if len(scenes)%20 == 0:
                print('Extracted scenes',len(scenes),flush=True)
    # No same-day pseudo replication. No selection using target value.
    daily = {}
    for r in rows:
        k = (r['fid'], r['date'])
        score = (r['values']['core']['coverage'], r['acquired_at_utc'], r['scene_id'])
        if k not in daily or score > (daily[k]['values']['core']['coverage'], daily[k]['acquired_at_utc'], daily[k]['scene_id']):
            daily[k] = r
    output = dict(rows=sorted(daily.values(), key=lambda r: (r['fid'], r['date'])), source_scenes=scenes,
        core_cells={fid: len(g['core']) for fid, g in grids.items()}, holdout_ids=holdout,
        temporal_unit='UTC calendar day; multiple fragments on same date correlated',
        indices='observed reflectance proxies, not crop or health labels')
    dump(D / 'observations.json', output)
    return output


def make_pairs(rows, variant='core'):
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['fid']].append(r)
    pairs = []
    for fid, rr in grouped.items():
        rr = sorted(rr, key=lambda r: r['date'])
        for current in rr:
            if variant not in current['values']:
                continue
            previous = [r for r in rr if 2 <= (day(current['date'])-day(r['date'])).days <= 21 and r['baseline']==current['baseline'] and variant in r['values']]
            future = [r for r in rr if 6 <= (day(r['date'])-day(current['date'])).days <= 8 and r['baseline']==current['baseline'] and variant in r['values']]
            if not previous or not future:
                continue
            prev = max(previous, key=lambda r: r['date'])
            target = min(future, key=lambda r: (abs((day(r['date'])-day(current['date'])).days-7), r['date']))
            lag = (day(current['date'])-day(prev['date'])).days
            now = np.array(current['values'][variant]['value'])
            slope = (now-np.array(prev['values'][variant]['value']))/lag
            doy = day(current['date']).timetuple().tm_yday
            angle = 2*math.pi*doy/365.25
            pairs.append(dict(fid=fid, issue=current['date'], target=target['date'], previous=prev['date'],
                issue_scene=current['scene_id'], target_scene=target['scene_id'], previous_scene=prev['scene_id'],
                baseline=current['baseline'], x=[*now, *slope, lag, math.sin(angle), math.cos(angle), *current['terrain']],
                y=target['values'][variant]['value']))
    return pairs


def partition(pairs, holdout):
    splits = {key: [] for key in ['train', 'selection', 'calibration', 'spatial2024', 'temporal2026', 'spatial2026']}
    for r in pairs:
        yr = day(r['issue']).year
        if yr == 2026:
            splits['spatial2026' if r['fid'] in holdout else 'temporal2026'].append(r)
        elif yr == 2024:
            if r['fid'] in holdout:
                splits['spatial2024'].append(r)
            elif r['target'] < '2024-06-01':
                splits['train'].append(r)
            elif r['issue'] >= '2024-06-01' and r['target'] < '2024-08-01':
                splits['selection'].append(r)
            elif r['issue'] >= '2024-08-01':
                splits['calibration'].append(r)
            # Purge pairs crossing the training/selection/calibration cuts.
    return splits


def date_weights(rows):
    counts = Counter(r['issue'] for r in rows)
    return np.array([1/counts[r['issue']] for r in rows])


def fit_model(name, rows):
    if not rows:
        raise ValueError('No fit rows')
    out = dict(name=name, fit_pairs=len(rows), fit_dates=len({r['issue'] for r in rows}), fit_max_target=max(r['target'] for r in rows))
    if name in ['persistence', 'linear_trend']:
        return out
    n = 9 if name.startswith('terrain') else 7
    x = np.array([r['x'][:n] for r in rows])
    y = np.array([r['y'] for r in rows])-x[:, :2]
    w = date_weights(rows)
    mean = np.average(x, axis=0, weights=w)
    scale = np.sqrt(np.average((x-mean)**2, axis=0, weights=w))
    scale[scale < 1e-9] = 1
    z = np.column_stack([np.ones(len(x)), (x-mean)/scale])
    penalty = np.eye(n+1)*float(name.rsplit('_', 1)[1]);penalty[0, 0] = 0
    coef = np.linalg.solve(z.T@(z*w[:, None])+penalty, z.T@(y*w[:, None]))
    return {**out, 'features': (TEMPORAL+TERRAIN)[:n], 'mean':mean.tolist(), 'scale':scale.tolist(), 'coef':coef.tolist()}


def predict(model, rows):
    if not rows:
        return np.empty((0, 2))
    x = np.array([r['x'] for r in rows])
    if model['name']=='persistence':
        return x[:, :2]
    if model['name']=='linear_trend':
        return np.clip(x[:, :2]+7*x[:, 2:4], -1, 1)
    n = len(model['mean'])
    z = np.column_stack([np.ones(len(x)), (x[:, :n]-model['mean'])/model['scale']])
    return np.clip(x[:, :2]+z@model['coef'], -1, 1)


def metrics(model, rows, radius=None):
    if not rows:
        return dict(pairs=0, dates=0, mae=None)
    predicted = predict(model, rows)
    actual = np.array([r['y'] for r in rows])
    errors = predicted-actual
    w = date_weights(rows)
    per_index = {}
    for j, name in enumerate(INDEX_NAMES):
        pred_event = predicted[:, j]-np.array([r['x'][j] for r in rows]) <= -.10
        event = actual[:, j]-np.array([r['x'][j] for r in rows]) <= -.10
        per_index[name] = dict(mae=float(np.average(abs(errors[:, j]), weights=w)), rmse=float(np.sqrt(np.average(errors[:, j]**2, weights=w))),
            bias=float(np.average(errors[:, j], weights=w)), observed_declines=int(event.sum()),
            decline_proxy_confusion=dict(tp=int((pred_event & event).sum()), fp=int((pred_event & ~event).sum()), fn=int((~pred_event & event).sum()), tn=int((~pred_event & ~event).sum())))
        if radius is not None:
            low = np.clip(predicted[:, j]-radius[j], -1, 1)
            high = np.clip(predicted[:, j]+radius[j], -1, 1)
            per_index[name].update(coverage=float(np.average((actual[:, j]>=low)&(actual[:, j]<=high), weights=w)),
                mean_width=float(np.average(high-low, weights=w)))
    return dict(pairs=len(rows), dates=len({r['issue'] for r in rows}), fragments=len({r['fid'] for r in rows}), first_issue=min(r['issue'] for r in rows), last_target=max(r['target'] for r in rows),
        mae=float(np.average(abs(errors).mean(axis=1), weights=w)), per_index=per_index)


def calibrate(model, rows, alpha=.10):
    errors = abs(predict(model, rows)-np.array([r['y'] for r in rows]))
    by_date = defaultdict(list)
    for r, error in zip(rows, errors):
        by_date[r['issue']].append(error)
    maxima = np.array([np.max(v, axis=0) for v in by_date.values()])
    rank = math.ceil((len(maxima)+1)*(1-alpha))
    if rank > len(maxima):
        raise ValueError('Too few calibration dates for finite 90% rank')
    radius = np.sort(maxima, axis=0)[rank-1]
    return dict(nominal_coverage=1-alpha, calibration_pairs=len(rows), calibration_dates=len(maxima), rank=rank,
        radius=radius.tolist(), method='maximum per issue date across non-holdout fragments; separate index radii; NOT joint coverage or a temporal guarantee')


def boundary_check(model, observations, core_rows):
    """Same scene triples; geometry sensitivity must not silently choose easier dates."""
    variants = ['base','N','NE','E','SE','S','SW','W','NW','core','outer']
    variant_pairs = {v:{(r['fid'],r['issue']):r for r in make_pairs(observations, v)} for v in variants}
    out = []
    for core in core_rows:
        rr = []
        for v in variants:
            r = variant_pairs[v].get((core['fid'],core['issue']))
            if r and (r['previous_scene'],r['target_scene']) == (core['previous_scene'],core['target_scene']):
                rr.append(r)
        if len(rr) != len(variants):
            continue
        predicted = predict(model, rr)
        future = np.array([r['y'] for r in rr])
        out.append(dict(fid=core['fid'], issue=core['issue'], predicted_span=np.ptp(predicted,axis=0).tolist(), target_span=np.ptp(future,axis=0).tolist()))
    return dict(eligible_pairs=len(core_rows), complete_11_scenario_pairs=len(out), pairs=out,
        interpretation='range over heuristic shifted/eroded/dilated geometries, not prediction confidence')


def run():
    protocol = freeze_revision()
    observations = extract_observations()
    pairs = make_pairs(observations['rows'])
    splits = partition(pairs, observations['holdout_ids'])
    for name in ['train','selection','calibration']:
        if not splits[name]:
            raise ValueError('Empty '+name+' split')
    scores = []
    for name in protocol['candidates']:
        model = fit_model(name, splits['train'])
        scores.append(dict(name=name, selection=metrics(model,splits['selection'])))
    winner = min(scores, key=lambda r:r['selection']['mae'])['name']
    train = splits['train']+splits['selection']
    model = fit_model(winner, train)
    baseline = fit_model('persistence',train)
    trend = fit_model('linear_trend',train)
    interval = calibrate(model,splits['calibration'])
    baseline_interval = calibrate(baseline,splits['calibration'])
    # Freeze every fitted parameter, selection and calibration BEFORE evaluation on 2026.
    selection_artifact = dict(protocol_sha256=digest(D/'training-protocol.json'),
        split_sha256=digest(D/'spatial-split.json'), scores=scores, selected_model=model,
        interval=interval, persistence_interval=baseline_interval,
        fitting_pairs=[dict(fid=r['fid'],issue=r['issue'],target=r['target']) for r in train],
        calibration_pairs=[dict(fid=r['fid'],issue=r['issue'],target=r['target']) for r in splits['calibration']],
        evaluated_2026=False, operational=False)
    dump(D/'frozen-model.json',selection_artifact)
    frozen_sha = digest(D/'frozen-model.json')
    evaluation = {}
    for name in ['calibration','spatial2024','temporal2026','spatial2026']:
        rr = splits[name]
        evaluation[name] = dict(selected=metrics(model,rr,interval['radius']), persistence=metrics(baseline,rr,baseline_interval['radius']), linear_trend=metrics(trend,rr))
    # Report the entire 2026 set as well, to avoid highlighting only the winning region.
    entire_test = splits['temporal2026']+splits['spatial2026']
    evaluation['all2026'] = dict(selected=metrics(model,entire_test,interval['radius']), persistence=metrics(baseline,entire_test,baseline_interval['radius']), linear_trend=metrics(trend,entire_test))
    prediction_rows=[]
    for name,rr in splits.items():
        for r,y in zip(rr,predict(model,rr)):
            prediction_rows.append(dict(split=name,fid=r['fid'],issue=r['issue'],target=r['target'],baseline=r['baseline'],actual=r['y'],prediction=y.tolist(),persistence=r['x'][:2]))
    dump(D/'predictions.json',prediction_rows)
    boundary = boundary_check(model,observations['rows'],splits['temporal2026']+splits['spatial2026'])
    dump(D/'boundary-sensitivity.json',boundary)
    if digest(D/'frozen-model.json') != frozen_sha:
        raise ValueError('Model changed during holdout evaluation')
    before = read(ROOT/'tmp/field-index-model-before.json')
    changed = [p for p,h in before.items() if not (ROOT/p).exists() or digest(ROOT/p)!=h]
    if changed:
        raise ValueError('Previously frozen/operational bytes changed: '+str(changed[:5]))
    report = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), status='research_only',task=protocol['target'],
        case_alignment='inspect unexpected spectral changes before considering a protection decision; no inferred disease/pest or pesticide need',
        selected=winner,selection_scores=scores,split_counts={k:dict(pairs=len(v),dates=len({r['issue'] for r in v}),fragments=len({r['fid'] for r in v})) for k,v in splits.items()},
        source_scene_counts=dict(Counter(str(day(s['acquired_at_utc']).year) for s in observations['source_scenes'])),
        core_cells=observations['core_cells'],unsupported_cores=[k for k,v in observations['core_cells'].items() if v<10],
        spatial_holdout=observations['holdout_ids'],interval=interval,evaluation=evaluation,
        boundary=dict(eligible_pairs=boundary['eligible_pairs'],complete_11_scenario_pairs=boundary['complete_11_scenario_pairs'],
            median_prediction_span=np.median([r['predicted_span'] for r in boundary['pairs']],axis=0).tolist() if boundary['pairs'] else None,
            max_prediction_span=np.max([r['predicted_span'] for r in boundary['pairs']],axis=0).tolist() if boundary['pairs'] else None,
            target_span_le_005_both=sum(max(r['target_span'])<=.05 for r in boundary['pairs'])),
        source_protocol_sha256=digest(D/'protocol.json'),training_protocol_sha256=digest(D/'training-protocol.json'),model_sha256=frozen_sha,
        observations_sha256=digest(D/'observations.json'),code_sha256=digest(__file__),
        prior_files_verified_unchanged=len(before),operational_model_changed=False,
        limitations=['Only one fit season (2024), source-policy audit pending for 2023.',
            '2026 held out from fitting/selection but seen descriptively; no pristine prospective claim.',
            '2026 July-October tests omit spring. Crops, sowing/harvest and treatment histories unconfirmed.',
            'Only clear-sky observed-date pairs are scored; cloudy periods are missing, not successes.',
            'Retrospective reprocessed reflectance, not a demonstrated live forecast backtest.',
            'Correlated fragments/dates, heuristic boundaries, no disease/soil-water/yield labels.',
            'Intervals are empirical and may fail under seasonal, crop or processing shifts.',
            'No agronomist inspection outcomes or demonstrated treatment savings.'])
    dump(D/'report.json',report)
    print('Experiment complete:',winner,flush=True)
    print({k:dict(model=round(v['selected']['mae'],4) if v['selected']['mae'] is not None else None,persistence=round(v['persistence']['mae'],4) if v['persistence']['mae'] is not None else None) for k,v in evaluation.items()},flush=True)
    return report
