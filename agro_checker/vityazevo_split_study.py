"""Season-forward hourly temperature correction, airport-only selection.

Anapa is opened for scoring only after selection is saved and verified.
No original detector is executed, refitted or transferred to these positions.
"""
from collections import defaultdict
from datetime import date, datetime
import importlib.metadata
import math
import numpy as np
from sklearn.linear_model import Ridge
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from .krymsk_official import preserve
from . import vityazevo_split_data as data

DIRECTORY = data.DIRECTORY
SOURCE = ROOT/'agro_checker/vityazevo_split_study.py'
LOCK = DIRECTORY/'selection-prior-only.json'
CANDIDATES = ['gfs', 'bias', 'lead_bias', 'ridge_temperature_10', 'ridge_weather_10', 'ridge_weather_100', 'ridge_weather_1000']


def rows(nights):
    result = []
    for n in nights:
        if n['year'] not in [2022, 2023, 2024] or len(n['times']) != 14 or len(n['target_temperature_c']) != 14:
            raise ValueError('Wrong year/observation schedule')
        for i, stamp in enumerate(n['times']):
            result.append({'id': n['id'], 'role': n['role'], 'date': n['date'], 'year': n['year'], 'observed_at_utc': stamp,
                           'lead_hours': i+10, 'gfs': {k: v[i] for k, v in n['gfs'].items()},
                           'forecast_night_minimum_c': min(n['gfs']['temperature']),
                           'forecast_night_cooling_c': n['gfs']['temperature'][-1]-n['gfs']['temperature'][0],
                           'target_temperature_c': n['target_temperature_c'][i]})
    return result


def features(row, weather=False):
    g = row['gfs']; d = date.fromisoformat(row['date'])
    day = (date(2001, d.month, d.day)-date(2001, 1, 1)).days; angle = 2*math.pi*day/365
    if row['lead_hours'] not in range(10, 24): raise ValueError('Unknown forecast horizon')
    x = [g['temperature'], row['forecast_night_minimum_c'], row['forecast_night_cooling_c'], math.sin(angle), math.cos(angle)]
    x += [float(row['lead_hours'] == h) for h in range(10, 24)]
    if weather:
        spread = g['temperature']-g['dewpoint']; wind = math.hypot(g['u10'], g['v10']); clear = 1-g['cloud']/100
        x += [spread, wind, g['u10'], g['v10'], g['rh'], g['cloud'], wind*clear, spread*clear]
    if not all(math.isfinite(v) for v in x): raise ValueError('Invalid forecast features')
    return x


def fit(name, rr):
    if name not in CANDIDATES or not rr: raise ValueError('Unknown/empty fit')
    if any(r['year'] not in [2022, 2023] or r['id'] != 'vityazevo_airport' or r['role'] != 'development' for r in rr):
        raise ValueError('Only prior2022-2023 airport training labels; no Anapa/2024/control labels')
    usable = [r for r in rr if r['target_temperature_c'] is not None]
    if not usable: raise ValueError('No training labels')
    y = np.array([r['target_temperature_c']-r['gfs']['temperature'] for r in usable])
    out = {'name': name, 'training_years': sorted({r['year'] for r in rr}), 'training_site': 'vityazevo_airport',
           'training_slots': len(usable), 'training_last_observation_utc': max(r['observed_at_utc'] for r in usable)}
    if name == 'gfs': return out
    if name == 'bias': return {**out, 'offset_c': float(y.mean())}
    if name == 'lead_bias':
        offsets = {}
        for h in range(10, 24):
            values = [v for r, v in zip(usable, y) if r['lead_hours'] == h]
            if not values: raise ValueError('No calibration labels for a forecast lead')
            offsets[str(h)] = float(np.mean(values))
        return {**out, 'offsets_c': offsets}
    x = np.array([features(r, 'weather' in name) for r in usable]); mean = x.mean(axis=0); scale = x.std(axis=0)
    scale[scale < 1e-10] = 1
    model = Ridge(alpha=float(name.rsplit('_', 1)[1]), solver='svd').fit((x-mean)/scale, y)
    return {**out, 'mean': mean.tolist(), 'scale': scale.tolist(), 'coefficients': model.coef_.tolist(), 'intercept': float(model.intercept_)}


def predict(model, row):
    name = model['name']; offset = 0.
    if name == 'bias': offset = model['offset_c']
    elif name == 'lead_bias': offset = model['offsets_c'][str(row['lead_hours'])]
    elif name.startswith('ridge'):
        x = (np.array(features(row, 'weather' in name))-model['mean'])/model['scale']
        offset = float(x @ model['coefficients']+model['intercept'])
    return float(row['gfs']['temperature']+offset)


def error(rr):
    if not rr: return {'n': 0, 'mae_c': None, 'bias_c': None, 'p90_absolute_error_c': None}
    e = np.array([r['prediction_c']-r['target_temperature_c'] for r in rr])
    return {'n': len(e), 'mae_c': float(np.abs(e).mean()), 'bias_c': float(e.mean()), 'p90_absolute_error_c': float(np.quantile(np.abs(e), .9))}


def metrics(rr):
    usable = [r for r in rr if r['target_temperature_c'] is not None]; groups = defaultdict(list)
    for r in rr: groups[(r['id'], r['date'])].append(r)
    complete = []; sampled = []
    for group in groups.values():
        if len(group) != 14 or sorted(r['lead_hours'] for r in group) != list(range(10, 24)): raise ValueError('Wrong night grouping')
        observed = [r for r in group if r['target_temperature_c'] is not None]
        if observed:
            sampled.append({'target_temperature_c': min(r['target_temperature_c'] for r in observed),
                            'prediction_c': min(r['prediction_c'] for r in observed)})
        if len(observed) == 14:
            complete.append({'target_temperature_c': min(r['target_temperature_c'] for r in group),
                             'prediction_c': min(r['prediction_c'] for r in group)})
    def event(nn):
        return {'nights': len(nn), 'cold_nights': sum(r['target_temperature_c'] < 0 for r in nn),
                'hits': sum(r['prediction_c'] < 0 and r['target_temperature_c'] < 0 for r in nn),
                'misses': sum(r['target_temperature_c'] < 0 <= r['prediction_c'] for r in nn),
                'false_cold': sum(r['prediction_c'] < 0 <= r['target_temperature_c'] for r in nn),
                'minimum_error': error(nn)}
    return {'expected_slots': len(rr), 'missing_slots': len(rr)-len(usable), 'all_slots': error(usable),
            'cold_slots': error([r for r in usable if r['target_temperature_c'] < 0]),
            'by_lead': {str(h): error([r for r in usable if r['lead_hours'] == h]) for h in range(10, 24)},
            'complete_14_slot_nights': event(complete), 'available_term_sampled_minima': event(sampled),
            'is_original_detector_score': False, 'continuous_minimum_or_duration_validated': False}


def select(results):
    eligible = ['gfs']; gates = {}
    for name in CANDIDATES[1:]:
        reasons = []
        for y in ['2023', '2024']:
            r, b = results[name][y], results['gfs'][y]; rn, bn = r['complete_14_slot_nights'], b['complete_14_slot_nights']
            if r['all_slots']['mae_c'] > .95*b['all_slots']['mae_c']: reasons.append(y+': hourly gain below5%')
            if not b['cold_slots']['n'] or not bn['cold_nights']: reasons.append(y+': insufficient cold validation support')
            elif r['cold_slots']['mae_c'] > b['cold_slots']['mae_c']: reasons.append(y+': cold-slot MAE worsened')
            if rn['minimum_error']['mae_c'] is None or rn['minimum_error']['mae_c'] > bn['minimum_error']['mae_c']: reasons.append(y+': full-night minimum MAE worsened or unavailable')
            if rn['false_cold'] > bn['false_cold']: reasons.append(y+': additional false-cold full nights')
            if rn['misses'] > bn['misses']: reasons.append(y+': additional missed cold full nights')
        gates[name] = {'eligible': not reasons, 'reasons': reasons}
        if not reasons: eligible.append(name)
    winner = min(eligible, key=lambda n: np.mean([results[n][y]['all_slots']['mae_c'] for y in ['2023', '2024']]))
    return winner, gates


def paired_blocks(candidate, baseline, p):
    index = {(r['id'], r['observed_at_utc']): r for r in baseline}; blocks = defaultdict(list)
    for r in candidate:
        if r['target_temperature_c'] is None: continue
        b = index[(r['id'], r['observed_at_utc'])]
        if b['target_temperature_c'] != r['target_temperature_c']: raise ValueError('Unpaired targets')
        d = date.fromisoformat(r['date']); block = (d-date(d.year, 3, 1)).days//p['block_nights']
        blocks[(d.year, block)].append(abs(r['prediction_c']-r['target_temperature_c'])-abs(b['prediction_c']-b['target_temperature_c']))
    rng = np.random.default_rng(p['seed']); draws = []
    for _ in range(p['replicates']):
        means = []
        for y in [2023, 2024]:
            values = [v for (year, _), v in blocks.items() if year == y]
            if not values: return {'status': 'insufficient_blocks'}
            means.append(float(np.mean([v for i in rng.integers(0, len(values), len(values)) for v in values[i]])))
        draws.append(float(np.mean(means)))
    return {'status': 'descriptive_same_selection_years', 'delta': 'candidate minus GFS equal-year slot MAE',
            'interval_95_c': np.quantile(draws, [.025, .975]).tolist(), 'calendar_blocks': len(blocks),
            'independent_confirmation': False}


def selection():
    nights, meta = data.load('development'); p = data.protocol()
    if p['candidates'] != CANDIDATES: raise ValueError('Unregistered candidates')
    state = {'source_sha256': sha(SOURCE), 'dataset_meta_sha256': sha(DIRECTORY/'dataset.meta.json'),
             'preserved_sha256': preserve(), 'protocol': p}
    if LOCK.exists():
        old = data.read(LOCK)
        if any(old[k] != v for k, v in state.items()): raise ValueError('Frozen study/data/model state changed; new experiment required')
        for name, h in old['artifact_sha256'].items():
            if sha(DIRECTORY/name) != h: raise ValueError('Frozen predictions/model changed')
        return old
    write_json(DIRECTORY/'protocol-before-fit.json', {**state, 'saved_at_utc': datetime.now(UTC).isoformat()})
    rr = rows(nights); forecasts = {}; models = {}; results = {}; folds = []
    for name in CANDIDATES:
        forecasts[name] = []; models[name] = {}; results[name] = {}
        for fold in p['folds']:
            y = fold['validation_year']; train = [r for r in rr if r['year'] in fold['train_years']]
            valid = [r for r in rr if r['year'] == y]
            if max(r['year'] for r in train) >= y: raise ValueError('Temporal leakage')
            model = fit(name, train); models[name][str(y)] = model
            predicted = [{**r, 'prediction_c': predict(model, r)} for r in valid]
            forecasts[name].extend(predicted); results[name][str(y)] = metrics(predicted)
            if name == 'gfs': folds.append({'training_years': model['training_years'], 'validation_year': y,
                                          'training_slots': model['training_slots'], 'validation_slots': len(valid)})
    winner, gates = select(results)
    write_json(DIRECTORY/'fold-models.json', models)
    write_json(DIRECTORY/'airport-validation-predictions.json', forecasts)
    write_json(DIRECTORY/'research-model.json', models[winner]['2024'])
    r = {**state, 'selected': winner, 'gates': gates, 'validation': results, 'folds': folds,
         'paired_mae': {name: paired_blocks(forecasts[name], forecasts['gfs'], p['bootstrap']) for name in CANDIDATES},
         'artifact_sha256': {n: sha(DIRECTORY/n) for n in ['fold-models.json', 'airport-validation-predictions.json', 'research-model.json', 'protocol-before-fit.json']},
         'software': {n: importlib.metadata.version(n) for n in ['numpy', 'scikit-learn']},
         'anapa_used_in_selection': False, 'control_years_used': False, 'detector_changed': False, 'production_admitted': False,
         'selected_at_utc': datetime.now(UTC).isoformat()}
    write_json(LOCK, r)
    return r


def evaluate_anapa():
    s = selection()  # Saves/verifies immutable choice before reading spatial targets.
    nn, m = data.load('spatial_check_only'); rr = rows(nn); models = data.read(DIRECTORY/'fold-models.json')
    results = {}; predictions = {}
    for name in dict.fromkeys(['gfs', s['selected']]):
        results[name] = {}; predictions[name] = []
        for y in [2023, 2024]:
            subset = [r for r in rr if r['year'] == y]; model = models[name][str(y)]
            if model['training_site'] != 'vityazevo_airport' or max(model['training_years']) >= y: raise ValueError('Spatial/temporal leakage')
            scored = [{**r, 'prediction_c': predict(model, r)} for r in subset]
            predictions[name].extend(scored); results[name][str(y)] = metrics(scored)
    path = DIRECTORY/'anapa-check-predictions.json'; write_json(path, predictions)
    r = {'selection_sha256': sha(LOCK), 'selected': s['selected'], 'scores': results, 'prediction_sha256': sha(path),
         'settings_changed_after_spatial_check': False, 'anapa_labels_used_for_training': False,
         'scope': 'Already inspected sparse same-region archive, not an independent blind station test', 'controls_used': False}
    write_json(DIRECTORY/'anapa-check.json', r)
    return r


if __name__ == '__main__':
    s = selection(); print('Prior-only selected', s['selected'])
    for name in CANDIDATES:
        print(name, {y: (round(v['all_slots']['mae_c'], 4), v['complete_14_slot_nights']['misses'], v['complete_14_slot_nights']['false_cold']) for y, v in s['validation'][name].items()}, s['gates'].get(name, {}))
    e = evaluate_anapa(); print('Anapa selected model', {y: v['all_slots'] for y, v in e['scores'][s['selected']].items()})
