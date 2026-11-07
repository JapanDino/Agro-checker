"""Frozen prior-year transfer choices on full-ISD stations with cold nights.

Zero-shot station exclusion and local-history adaptation have separate choices.
Original detector and production forecast are never executed or changed here.
"""
from collections import Counter
from datetime import datetime
import math
import numpy as np
from sklearn.linear_model import Ridge
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import cold_station_data as data
from . import station_transfer_study as previous
from . import vityazevo_split_study as airport

DIRECTORY = data.DIRECTORY
SOURCE = ROOT/'agro_checker/cold_station_study.py'
LOCK = DIRECTORY/'selection-prior-only.json'
CANDIDATES = ['gfs', 'airport_transfer', 'pooled_100', 'pooled_1000', 'pooled_geo_1000',
              'local_bias_20', 'local_ridge_100', 'local_ridge_1000', 'local_blend_25', 'local_blend_50']
FAMILIES = {'zero_shot': ['gfs', 'pooled_100', 'pooled_1000', 'pooled_geo_1000'],
            'local_history': ['gfs', 'local_bias_20', 'local_ridge_100', 'local_ridge_1000', 'local_blend_25', 'local_blend_50']}


def rows(nights):
    rr = previous.rows(nights); ix = {(n['id'], n['date']): n for n in nights}
    for r in rr:
        n = ix[(r['id'], r['date'])]
        r['gfs_surface_height_m'] = n['gfs_surface_height_m']
    return rr


def features(row, geo=False):
    x = airport.features(row, True)
    if geo:
        wind = math.hypot(row['gfs']['u10'], row['gfs']['v10']); clear = 1-row['gfs']['cloud']/100
        height = row['elevation_m']/1000; difference = (row['elevation_m']-row['gfs_surface_height_m'])/1000
        x += [height, difference, difference*wind, difference*clear]
    if not all(math.isfinite(v) for v in x): raise ValueError('Invalid forecast/terrain feature')
    return x


def fit(name, history, train_years, validation_year, target_id, target_position, airport_model=None):
    if name not in CANDIDATES: raise ValueError('Unregistered model')
    if validation_year not in [2023, 2024] or sorted(train_years) != list(range(2022, validation_year)):
        raise ValueError('Prior-year folds only; no controls')
    positions = previous.validate_rows(history, train_years)
    if name.startswith('pooled'):
        if target_id in positions: raise ValueError('Zero-shot fit contains target station')
        if any(r['role'] != 'development' for r in history): raise ValueError('Spatial labels forbidden in pooled model')
    if name.startswith('local'):
        if set(positions) != {target_id} or positions[target_id] != tuple(target_position): raise ValueError('Local model requires only exact target station')
    out = {'name': name, 'training_years': sorted(train_years), 'validation_year': validation_year,
           'training_sites': sorted(positions), 'target_id': target_id, 'target_position': list(target_position),
           'last_training_observation_utc': max(r['observed_at_utc'] for r in history),
           'training_positions': {k: list(v) for k, v in positions.items()}, 'production_admitted': False}
    if name == 'gfs': return {**out, 'training_sites': [], 'training_positions': {}, 'fit_required': False}
    if name == 'airport_transfer':
        if airport_model['name'] != 'ridge_weather_100' or airport_model['training_years'] != train_years:
            raise ValueError('Wrong frozen airport fold')
        return {**out, 'airport_model': airport_model, 'training_sites': ['vityazevo_airport'],
                'training_positions': {'vityazevo_airport': [45.002, 37.347, 53]},
                'last_training_observation_utc': airport_model['training_last_observation_utc']}
    usable = [r for r in history if r['target_temperature_c'] is not None]
    night_counts = Counter((r['id'], r['date']) for r in usable)
    nights_per_site = Counter(sid for sid, day in night_counts)
    if any(v < 20 for v in nights_per_site.values()) or set(nights_per_site) != set(positions): raise ValueError('Insufficient training-site support')
    out['training_slots'] = len(usable); out['training_nights_by_site'] = dict(nights_per_site)
    y = np.array([r['target_temperature_c']-r['gfs']['temperature'] for r in usable])
    if name == 'local_bias_20':
        offsets = {}
        for h in range(10, 24):
            pairs = [(r, v) for r, v in zip(usable, y) if r['lead_hours'] == h]
            n = len({r['date'] for r, v in pairs})
            if n>=20: offsets[str(h)] = {'offset_c': float(np.mean([v for r, v in pairs])*n/(n+20)), 'prior_nights': n}
        return {**out, 'lead_offsets': offsets}
    geo = name == 'pooled_geo_1000'; x = np.array([features(r, geo) for r in usable])
    w = np.array([1/(nights_per_site[r['id']]*night_counts[(r['id'], r['date'])]) for r in usable])
    w *= len(usable)/w.sum()
    mean = np.average(x, axis=0, weights=w); scale = np.sqrt(np.average((x-mean)**2, axis=0, weights=w)); scale[scale<1e-10] = 1
    alpha = 100 if name in ['pooled_100', 'local_ridge_100'] else 1000
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        model = Ridge(alpha=alpha, solver='svd').fit((x-mean)/scale, y, sample_weight=w)
    return {**out, 'alpha': alpha, 'geo_features': geo, 'mean': mean.tolist(), 'scale': scale.tolist(),
            'coefficients': model.coef_.tolist(), 'intercept': float(model.intercept_)}


def predict(model, row):
    name = model['name']
    if row['year'] != model['validation_year'] or max(model['training_years']) >= row['year']:
        raise ValueError('No current-season or control-year inference')
    if row['id'] != model['target_id'] or previous.physical_key(row) != tuple(model['target_position']):
        raise ValueError('Wrong target physical site')
    expected = datetime.fromisoformat(row['date']).replace(tzinfo=UTC)
    from datetime import timedelta
    expected += timedelta(hours=6+row['lead_hours'])
    if row['year'] != int(row['date'][:4]) or datetime.fromisoformat(row['observed_at_utc']) != expected or row['lead_hours'] not in range(10, 24):
        raise ValueError('Wrong original issue/lead/time')
    if row['observed_at_utc'] <= model['last_training_observation_utc']: raise ValueError('Future training observation')
    features(row, name == 'pooled_geo_1000'); base = row['gfs']['temperature']
    if name == 'gfs': return float(base)
    if name == 'airport_transfer': return airport.predict(model['airport_model'], row)
    if name == 'local_bias_20': return float(base+model['lead_offsets'].get(str(row['lead_hours']), {}).get('offset_c', 0.))
    x = (np.array(features(row, model['geo_features']))-model['mean'])/model['scale']
    offset = float(x @ model['coefficients']+model['intercept'])
    weight = .25 if name == 'local_blend_25' else .5 if name == 'local_blend_50' else 1.
    return float(base+weight*offset)


def score(rr):
    sites = sorted({r['id'] for r in rr}); per_site = {sid: previous.score([r for r in rr if r['id']==sid]) for sid in sites}
    def macro(path):
        values = []
        for m in per_site.values():
            for key in path: m = m[key]
            if m is not None: values.append(m)
        return float(np.mean(values)) if values else None
    return {'by_site': per_site, 'site_macro_hourly_mae_c': macro(['all_slots', 'mae_c']),
            'site_macro_cold_slot_mae_c': macro(['cold_slots', 'mae_c']),
            'site_macro_minimum_mae_c': macro(['complete_14_slot_nights', 'minimum_error', 'mae_c']),
            'complete_nights': sum(m['complete_14_slot_nights']['nights'] for m in per_site.values()),
            'cold_complete_nights': sum(m['complete_14_slot_nights']['cold_nights'] for m in per_site.values()),
            'hits': sum(m['complete_14_slot_nights']['hits'] for m in per_site.values()),
            'misses': sum(m['complete_14_slot_nights']['misses'] for m in per_site.values()),
            'false_cold': sum(m['complete_14_slot_nights']['false_cold'] for m in per_site.values()),
            'is_original_detector_score': False}


def select(results):
    choices = {}; gates = {}
    for family, names in FAMILIES.items():
        eligible = ['gfs']
        for name in names[1:]:
            reasons = []
            for year in ['2023', '2024']:
                b, c = results['gfs'][year], results[name][year]
                if c['site_macro_hourly_mae_c'] > .95*b['site_macro_hourly_mae_c']: reasons.append(year+': hourly gain below5%')
                for metric in ['site_macro_cold_slot_mae_c', 'site_macro_minimum_mae_c']:
                    if c[metric] is None or b[metric] is None or c[metric]>b[metric]: reasons.append(year+': worsened/unavailable '+metric)
                if c['misses']>b['misses']: reasons.append(year+': additional missed cold full nights')
                for sid, bm in b['by_site'].items():
                    cm = c['by_site'][sid]
                    if not bm['complete_14_slot_nights']['cold_nights']: reasons.append(year+': no cold support '+sid)
                    if cm['complete_14_slot_nights']['false_cold'] > bm['complete_14_slot_nights']['false_cold']:
                        reasons.append(year+': additional false-cold full nights '+sid)
            gates[name] = {'eligible': not reasons, 'reasons': reasons}
            if not reasons: eligible.append(name)
        choices[family] = min(eligible, key=lambda n: np.mean([results[n][y]['site_macro_hourly_mae_c'] for y in ['2023', '2024']]))
    return choices, gates


def evaluate(nights, sources, models_airport, candidates):
    rr = rows(nights); sr = rows(sources); positions = {n['id']: previous.physical_key(n) for n in nights}
    predictions = {}; models = {}; results = {}
    for name in candidates:
        predictions[name] = []; models[name] = {}; results[name] = {}
        for year in [2023, 2024]:
            train_years = list(range(2022, year)); scored = []; models[name][str(year)] = {}
            for sid, position in positions.items():
                pool = rr if name.startswith('local') else sr
                train = [r for r in pool if r['year'] in train_years and
                         (r['id']==sid if name.startswith('local') else r['id']!=sid)]
                model = fit(name, train, train_years, year, sid, position, models_airport[str(year)])
                valid = [r for r in rr if r['id']==sid and r['year']==year]
                predicted = [{**r, 'prediction_c': predict(model, r), 'route': name,
                              'local_calibration_applied': name.startswith('local')} for r in valid]
                models[name][str(year)][sid] = model; scored.extend(predicted)
            predictions[name].extend(scored); results[name][str(year)] = score(scored)
    return results, predictions, models


def state():
    parent = previous.selection()
    return {'source_sha256': sha(SOURCE), 'data_source_sha256': sha(data.SOURCE), 'protocol_sha256': sha(data.PROTOCOL),
            'dataset_meta_sha256': sha(DIRECTORY/'dataset.meta.json'), 'protocol': data.protocol(),
            'parent_selection_sha256': sha(previous.LOCK), 'preserved_sha256': parent['preserved_sha256'],
            'parent_sources_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [previous.SOURCE, airport.SOURCE]},
            'software': {'numpy': np.__version__}}


def selection():
    p = data.protocol()
    if p['candidates'] != CANDIDATES: raise ValueError('Unregistered candidates')
    expected_folds = [{'train_years': [2022], 'validation_year': 2023}, {'train_years': [2022, 2023], 'validation_year': 2024}]
    if p['folds'] != expected_folds: raise ValueError('Wrong folds')
    nn, _ = data.load('development'); st = state()
    if LOCK.exists():
        old = data.read(LOCK)
        if any(old[k] != v for k, v in st.items()): raise ValueError('Frozen study changed; new experiment required')
        for name, h in old['artifact_sha256'].items():
            if sha(DIRECTORY/name) != h: raise ValueError('Frozen study artifact changed')
        return old
    write_json(DIRECTORY/'protocol-before-fit.json', {**st, 'saved_at_utc': datetime.now(UTC).isoformat()})
    models_airport = data.read(airport.DIRECTORY/'fold-models.json')['ridge_weather_100']
    results, predictions, models = evaluate(nn, nn, models_airport, CANDIDATES)
    chosen, gates = select(results)
    write_json(DIRECTORY/'development-models.json', models); write_json(DIRECTORY/'development-predictions.json', predictions)
    out = {**st, 'selected': chosen, 'gates': gates, 'validation': results,
           'artifact_sha256': {n: sha(DIRECTORY/n) for n in ['protocol-before-fit.json', 'development-models.json', 'development-predictions.json']},
           'spatial_errors_used_for_selection': False, 'controls_used': False, 'original_detector_changed': False,
           'production_admitted': False, 'selected_at_utc': datetime.now(UTC).isoformat()}
    write_json(LOCK, out); return out


def evaluate_spatial():
    s = selection()  # Choice saved and verified before any spatial prediction/errors.
    path = DIRECTORY/'spatial-check.json'
    if path.exists():
        old = data.read(path)
        if old['selection_sha256'] != sha(LOCK): raise ValueError('Spatial parent changed')
        for name, h in old['artifact_sha256'].items():
            if sha(DIRECTORY/name) != h: raise ValueError('Frozen spatial artifact changed')
        return old
    nn, _ = data.load('spatial_check'); sources, _ = data.load('development')
    models_airport = data.read(airport.DIRECTORY/'fold-models.json')['ridge_weather_100']
    results, predictions, models = evaluate(nn, sources, models_airport, CANDIDATES)
    write_json(DIRECTORY/'spatial-models.json', models); write_json(DIRECTORY/'spatial-predictions.json', predictions)
    comparisons = {}
    for family, name in s['selected'].items():
        comparisons[family] = {}
        for baseline in ['gfs', 'airport_transfer']:
            ci = airport.paired_blocks(predictions[name], predictions[baseline], s['protocol']['bootstrap'])
            ci['delta'] = 'candidate minus '+baseline+' equal-year pooled-slot MAE (not site macro)'
            ci['status'] = 'descriptive_previously_inspected_stations_after_fixed_choice'
            comparisons[family][baseline] = ci
    out = {'selection_sha256': sha(LOCK), 'selected': s['selected'], 'scores': results, 'paired_blocks': comparisons,
           'artifact_sha256': {n: sha(DIRECTORY/n) for n in ['spatial-models.json', 'spatial-predictions.json']},
           'spatial_used_for_selection': False, 'previously_inspected_stations': True,
           'controls_used': False, 'original_detector_changed': False, 'production_admitted': False}
    write_json(path, out); return out


if __name__ == '__main__':
    s = selection(); print('Selected', s['selected'], flush=True)
    for name in CANDIDATES:
        print(name, {y: {k: m[k] for k in ['site_macro_hourly_mae_c', 'site_macro_cold_slot_mae_c', 'site_macro_minimum_mae_c', 'misses', 'false_cold']} for y, m in s['validation'][name].items()}, s['gates'].get(name), flush=True)
    a = evaluate_spatial(); print('Spatial selected', {f: a['scores'][n] for f, n in s['selected'].items()})
