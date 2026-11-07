"""Prior-only temperature correction at actual station observation times.

Research outputs stay outside the forecast service and detector. Validation
labels never enter features or the fit used to predict their season.
"""
from collections import defaultdict
from datetime import date, datetime
import importlib.metadata
import math
import numpy as np
from sklearn.linear_model import Ridge
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import krymsk_observed_data as data

DIRECTORY = data.DIRECTORY
LOCK = DIRECTORY/'selection-prior-only.json'
SOURCE = ROOT/'agro_checker/krymsk_observed_study.py'
CANDIDATES = ['gfs', 'bias', 'slot_bias', 'ridge_temperature_10', 'ridge_weather_10', 'ridge_weather_100', 'ridge_weather_1000']


def features(row, weather=False):
    """Forecast/date only: accepts a row with every target field removed."""
    g = row['gfs']; d = date.fromisoformat(row['night_date'])
    seasonal_day = (date(2001, d.month, d.day)-date(2001, 1, 1)).days
    angle = 2*math.pi*seasonal_day/365
    values = [g['temperature'], row['forecast_night_minimum_c'], row['forecast_night_cooling_c'],
              math.sin(angle), math.cos(angle)] + [float(row['lead_hours'] == h) for h in [12, 15, 18, 21]]
    if weather:
        spread = g['temperature']-g['dewpoint']; wind = math.hypot(g['u10'], g['v10']); clear = 1-g['cloud']/100
        values += [spread, wind, g['u10'], g['v10'], g['rh'], g['cloud'], wind*clear, spread*clear]
    if not all(math.isfinite(v) for v in values): raise ValueError('Nonfinite forecast feature')
    return values


def fit(name, rows):
    if name not in CANDIDATES: raise ValueError('Unknown candidate')
    for r in rows: data.prior_year(r['year'])
    accepted = [r for r in rows if r['target_temperature_c'] is not None]
    if not accepted: raise ValueError('No training observations')
    residual = np.array([r['target_temperature_c']-r['gfs']['temperature'] for r in accepted])
    out = {'name': name, 'training_years': sorted({r['year'] for r in rows}), 'training_slots': len(accepted)}
    if name == 'gfs': return out
    if name == 'bias': return {**out, 'offset_c': float(residual.mean())}
    if name == 'slot_bias':
        return {**out, 'slot_offsets_c': {str(h): float(np.mean([e for r, e in zip(accepted, residual) if r['lead_hours'] == h])) for h in [12, 15, 18, 21]}}
    x = np.array([features(r, 'weather' in name) for r in accepted]); center = x.mean(axis=0); scale = x.std(axis=0)
    scale[scale < 1e-10] = 1
    model = Ridge(alpha=float(name.rsplit('_', 1)[1])).fit((x-center)/scale, residual)
    return {**out, 'center': center.tolist(), 'scale': scale.tolist(), 'coefficients': model.coef_.tolist(), 'intercept': float(model.intercept_)}


def predict(model, row):
    name = model['name']; correction = 0.
    if name == 'bias': correction = model['offset_c']
    elif name == 'slot_bias': correction = model['slot_offsets_c'][str(row['lead_hours'])]
    elif name.startswith('ridge_'):
        x = (np.array(features(row, 'weather' in name))-model['center'])/model['scale']
        correction = float(x @ model['coefficients']+model['intercept'])
    return float(row['gfs']['temperature']+correction)


def errors(rows):
    if not rows: return {'n': 0, 'mae_c': None, 'bias_c': None, 'p90_absolute_error_c': None}
    e = np.array([r['prediction_c']-r['target_temperature_c'] for r in rows])
    return {'n': len(rows), 'mae_c': float(np.abs(e).mean()), 'bias_c': float(e.mean()),
            'p90_absolute_error_c': float(np.quantile(np.abs(e), .9))}


def metrics(rows):
    accepted = [r for r in rows if r['target_temperature_c'] is not None]
    nights = defaultdict(list)
    for r in rows: nights[r['night_date']].append(r)
    full = [v for v in nights.values() if len(v) == 4 and all(r['target_temperature_c'] is not None for r in v)]
    tn = fp = fn = tp = 0
    minimum_rows = []
    for night in full:
        actual = min(r['target_temperature_c'] for r in night); pred = min(r['prediction_c'] for r in night)
        tp += int(actual < 0 and pred < 0); fn += int(actual < 0 <= pred)
        fp += int(pred < 0 <= actual); tn += int(actual >= 0 and pred >= 0)
        minimum_rows.append({'target_temperature_c': actual, 'prediction_c': pred})
    return {'expected_slots': len(rows), 'missing_slots': len(rows)-len(accepted), 'all_slots': errors(accepted),
            'cold_slots': errors([r for r in accepted if r['target_temperature_c'] < 0]),
            'cold_slot_false_positive': sum(r['prediction_c'] < 0 <= r['target_temperature_c'] for r in accepted),
            'cold_slot_false_negative': sum(r['target_temperature_c'] < 0 <= r['prediction_c'] for r in accepted),
            'by_lead': {str(h): errors([r for r in accepted if r['lead_hours'] == h]) for h in [12, 15, 18, 21]},
            'sampled_night': {'complete_nights': len(full), 'cold_nights': tp+fn, 'hits': tp, 'misses': fn,
                              'false_cold': fp, 'correct_warm': tn, 'sampled_minimum': errors(minimum_rows),
                              'continuous_minimum_validated': False, 'is_original_detector_score': False}}


def select(results):
    """Every eligibility/ranking decision is fixed before reading any controls."""
    eligible = ['gfs']; gates = {}
    for name in CANDIDATES[1:]:
        reasons = []
        for year in [2023, 2024]:
            r, b = results[name][str(year)], results['gfs'][str(year)]
            if r['all_slots']['mae_c'] > .95*b['all_slots']['mae_c']: reasons.append(f'{year}: MAE reduction below5%')
            if r['cold_slots']['mae_c'] > b['cold_slots']['mae_c']: reasons.append(f'{year}: cold-slot MAE worsened')
            if r['sampled_night']['false_cold'] > b['sampled_night']['false_cold']: reasons.append(f'{year}: more false-cold sampled nights')
        gates[name] = {'eligible': not reasons, 'reasons': reasons}
        if not reasons: eligible.append(name)
    best = min(eligible, key=lambda n: sum(results[n][str(y)]['all_slots']['mae_c'] for y in [2023, 2024])/2)
    return best, gates


def paired_blocks(candidate, baseline, protocol):
    """Descriptive paired MAE uncertainty: resample weeks, not individual slots."""
    by_time = {r['observed_at_utc']: r for r in baseline}; groups = defaultdict(list)
    for r in candidate:
        if r['target_temperature_c'] is None: continue
        b = by_time[r['observed_at_utc']]
        if b['target_temperature_c'] != r['target_temperature_c']: raise ValueError('Unpaired targets')
        d = date.fromisoformat(r['night_date']); block = (d-date(d.year, 3, 1)).days//7
        groups[(d.year, block)].append(abs(r['prediction_c']-r['target_temperature_c'])-abs(b['prediction_c']-b['target_temperature_c']))
    rng = np.random.default_rng(protocol['seed']); draws = []
    for _ in range(protocol['replicates']):
        means = []
        for y in [2023, 2024]:
            blocks = [g for (year, _), g in groups.items() if year == y]
            sampled = [v for i in rng.integers(0, len(blocks), len(blocks)) for v in blocks[i]]
            means.append(float(np.mean(sampled)))
        draws.append(float(np.mean(means)))
    return {'metric': 'candidate minus GFS MAE; negative better; equal-year', 'calendar_week_blocks': len(groups),
            'descriptive_95_c': np.quantile(draws, [.025, .975]).tolist(),
            'independent_confirmation': False, 'caution': 'Same prior seasons used for selection; no post-selection confidence guarantee'}


def run():
    rows, meta = data.load(); p = data.read(data.PROTOCOL)
    if p['candidates'] != CANDIDATES or p['validation_years'] != [2023, 2024]: raise ValueError('Unexpected registry')
    if LOCK.exists():
        old = data.read(LOCK)
        if old['science_sha256'] != sha(SOURCE) or old['dataset_meta_sha256'] != sha(DIRECTORY/'dataset.meta.json'):
            raise ValueError('Frozen prior study changed; use a new experiment')
        for name, h in old['artifact_sha256'].items():
            if sha(DIRECTORY/name) != h: raise ValueError('Frozen research model/predictions changed')
        return old
    write_json(DIRECTORY/'protocol-before-fit.json', {'protocol': p, 'source_sha256': sha(SOURCE),
               'dataset_meta_sha256': sha(DIRECTORY/'dataset.meta.json'), 'saved_at_utc': datetime.now(UTC).isoformat()})
    results = {}; forecasts = {}; folds = []
    for name in CANDIDATES:
        results[name] = {}; forecasts[name] = []
        for year in [2023, 2024]:
            training = [r for r in rows if r['year'] < year]; validation = [r for r in rows if r['year'] == year]
            if not max(r['year'] for r in training) < year: raise ValueError('Temporal leakage')
            model = fit(name, training)
            predicted = [{**r, 'prediction_c': predict(model, r)} for r in validation]
            forecasts[name].extend(predicted); results[name][str(year)] = metrics(predicted)
            if name == 'gfs': folds.append({'train_years': model['training_years'], 'validation_year': year,
                                          'train_slots': model['training_slots'], 'validation_slots': len(validation)})
    winner, gates = select(results)
    # Final research fit may include2024 only after the prior-only choice.
    # No service imports or uses this artifact.
    write_json(DIRECTORY/'research-model.json', fit(winner, rows))
    write_json(DIRECTORY/'validation-predictions.json', forecasts)
    preserved = data.read(DIRECTORY/'preserved-artifacts.json')
    if any(sha(ROOT/path) != h for path, h in preserved.items()): raise ValueError('Existing model/detector changed')
    result = {'protocol': p, 'science_sha256': sha(SOURCE), 'dataset_meta_sha256': sha(DIRECTORY/'dataset.meta.json'),
              'dataset': meta, 'selected': winner, 'gates': gates, 'validation': results, 'folds': folds,
              'paired_mae': {n: paired_blocks(forecasts[n], forecasts['gfs'], p['bootstrap']) for n in CANDIDATES},
              'artifact_sha256': {name: sha(DIRECTORY/name) for name in ['research-model.json', 'validation-predictions.json', 'protocol-before-fit.json']},
              'preserved_sha256': preserved, 'controls_downloaded': False, 'control_targets_read': False,
              'production_admitted': False, 'new_detector_fitted': False, 'orchard_quality_validated': False,
              'software': {n: importlib.metadata.version(n) for n in ['numpy', 'scikit-learn']},
              'selected_at_utc': datetime.now(UTC).isoformat()}
    write_json(LOCK, result)
    return result


if __name__ == '__main__':
    r = run()
    print('Selected prior-only:', r['selected'])
    for name in CANDIDATES:
        print(name, {y: (round(v['all_slots']['mae_c'], 4), round(v['cold_slots']['mae_c'], 4), v['sampled_night']['false_cold']) for y, v in r['validation'][name].items()}, r['gates'].get(name, {}))
