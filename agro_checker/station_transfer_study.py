"""Prior-only, position-aware transfer experiment. Never an operational detector.

The inspected Anapa archive becomes development data in this NEW experiment.
Reference models and all earlier studies remain immutable.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import math
import numpy as np
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import vityazevo_split_data as data
from . import vityazevo_split_study as reference

DIRECTORY = ROOT/'data/working-zone-01/station-transfer'
SOURCE = ROOT/'agro_checker/station_transfer_study.py'
PROTOCOL = ROOT/'config/station_transfer.json'
LOCK = DIRECTORY/'selection-prior-only.json'
CANDIDATES = ['gfs', 'naive_reference', 'identity_gfs', 'local_bias_5', 'local_bias_20',
              'paired_delta_5', 'paired_delta_20', 'residual_bias_5', 'residual_bias_20']


def physical_key(row):
    values = tuple(row[k] for k in ['latitude', 'longitude', 'elevation_m'])
    if not all(math.isfinite(v) for v in values) or not -90 <= values[0] <= 90 or not -180 <= values[1] <= 180:
        raise ValueError('Invalid physical position')
    return values


def rows(nights):
    rr = reference.rows(nights)
    positions = {(n['id'], n['date']): physical_key(n) for n in nights}
    for r in rr:
        lat, lon, height = positions[(r['id'], r['date'])]
        r.update(latitude=lat, longitude=lon, elevation_m=height)
    return rr


def validate_rows(rr, years):
    if not rr or not years or not set(years) <= {2022, 2023}: raise ValueError('Only prior2022-2023 fits')
    positions = {}; keys = set()
    for r in rr:
        if r['year'] not in years or r['year'] != int(r['date'][:4]): raise ValueError('Unexpected training/validation/control year')
        if r['lead_hours'] not in range(10, 24): raise ValueError('Wrong lead')
        expected = datetime.fromisoformat(r['date']).replace(tzinfo=UTC)+timedelta(hours=6+r['lead_hours'])
        if datetime.fromisoformat(r['observed_at_utc']) != expected: raise ValueError('Observation time does not match original issue/lead')
        reference.features(r, True)
        position = physical_key(r)
        if r['id'] in positions and positions[r['id']] != position: raise ValueError('Mixed physical positions within a site')
        positions[r['id']] = position
        key = (r['id'], r['observed_at_utc'])
        if key in keys: raise ValueError('Duplicate training term')
        keys.add(key)
        if r['target_temperature_c'] is not None and not math.isfinite(r['target_temperature_c']): raise ValueError('Invalid target')
    if len(set(positions.values())) != len(positions): raise ValueError('Two IDs for same position')
    return positions


def fit(name, history, reference_model, train_years, validation_year, min_nights=3):
    if name not in CANDIDATES: raise ValueError('Unregistered candidate')
    if validation_year not in [2023, 2024] or sorted(train_years) != list(range(2022, validation_year)):
        raise ValueError('Non-forward fold')
    positions = validate_rows(history, train_years)
    if reference_model['training_years'] != sorted(train_years) or reference_model['training_site'] not in positions:
        raise ValueError('Reference model fold does not match history')
    if reference_model['name'] != 'ridge_weather_100' or min_nights != 3: raise ValueError('Unregistered reference/support')
    reference_id = reference_model['training_site']
    ix = {r['observed_at_utc']: r for r in history if r['id'] == reference_id}
    models = {}
    for sid, position in positions.items():
        if sid == reference_id: continue
        lead_models = {}
        for h in range(10, 24):
            local = [r for r in history if r['id'] == sid and r['lead_hours'] == h and r['target_temperature_c'] is not None]
            if name.startswith('paired_delta'):
                local = [r for r in local if r['observed_at_utc'] in ix and ix[r['observed_at_utc']]['target_temperature_c'] is not None]
            nights = {r['date'] for r in local}
            if len(nights) < min_nights: continue
            if name.startswith('local_bias'):
                values = [r['target_temperature_c']-r['gfs']['temperature'] for r in local]
            elif name.startswith('paired_delta'):
                values = [r['target_temperature_c']-ix[r['observed_at_utc']]['target_temperature_c'] for r in local]
            elif name.startswith('residual_bias'):
                values = [r['target_temperature_c']-reference.predict(reference_model, r) for r in local]
            else: continue
            k = int(name.rsplit('_', 1)[1]); weight = len(nights)/(len(nights)+k)
            lead_models[str(h)] = {'distinct_nights': len(nights), 'shrinkage_weight': weight,
                                  'offset_c': float(np.mean(values)*weight),
                                  'gfs_temperature_range_c': [min(r['gfs']['temperature'] for r in local), max(r['gfs']['temperature'] for r in local)],
                                  'training_terms': [r['observed_at_utc'] for r in local]}
        models[sid] = {'position': list(position), 'leads': lead_models}
    return {'name': name, 'reference_id': reference_id, 'reference_position': list(positions[reference_id]),
            'reference_model': reference_model, 'local': models, 'training_years': sorted(train_years),
            'validation_year': validation_year, 'last_training_observation_utc': max(r['observed_at_utc'] for r in history),
            'min_nights': min_nights, 'production_admitted': False}


def predict(model, row):
    """Inference only reads position, date/issue, lead and GFS, never target fields."""
    if row['year'] != model['validation_year'] or int(row['date'][:4]) != row['year']:
        raise ValueError('Wrong validation year; no implicit control-year evaluation')
    expected = datetime.fromisoformat(row['date']).replace(tzinfo=UTC)+timedelta(hours=6+row['lead_hours'])
    if row['lead_hours'] not in range(10, 24) or datetime.fromisoformat(row['observed_at_utc']) != expected:
        raise ValueError('Prediction timestamp differs from original issue/lead')
    if max(model['training_years']) >= row['year'] or model['last_training_observation_utc'] >= row['observed_at_utc']:
        raise ValueError('Temporal leakage')
    position = physical_key(row); name = model['name']; base = row['gfs']['temperature']
    if not math.isfinite(base): raise ValueError('Invalid GFS')
    def result(value, route, calibrated=False):
        return {'prediction_c': float(value), 'route': route, 'local_calibration_applied': calibrated,
                'is_original_detector_score': False, 'site_cold_validated': False}
    if name == 'gfs': return result(base, 'gfs_baseline')
    if name == 'naive_reference': return result(reference.predict(model['reference_model'], row), 'naive_unvalidated_transfer')
    if row['id'] == model['reference_id']:
        if position != tuple(model['reference_position']): raise ValueError('Reference ID physical position changed')
        return result(reference.predict(model['reference_model'], row), 'frozen_reference')
    if position == tuple(model['reference_position']): raise ValueError('Reference position under another ID')
    if name == 'identity_gfs': return result(base, 'different_position_no_validated_transfer')
    local = model['local'].get(row['id'])
    if local is None: return result(base, 'new_position_no_prior_observations')
    if position != tuple(local['position']): raise ValueError('Local ID physical position changed')
    lead = local['leads'].get(str(row['lead_hours']))
    if lead is None: return result(base, 'insufficient_prior_exact_lead_support')
    low, high = lead['gfs_temperature_range_c']
    if not low <= base <= high: return result(base, 'outside_prior_local_temperature_support')
    value = base if name.startswith('local_bias') else reference.predict(model['reference_model'], row)
    return result(value+lead['offset_c'], 'prior_local_'+name, True)


def diagnose(rr, parent_predictions):
    """Matched-time counterfactual: identical GFS and correction, different targets."""
    ix = {(r['id'], r['observed_at_utc']): r for r in rr}; out = {}
    predictions = {(r['id'], r['observed_at_utc']): r['prediction_c'] for r in parent_predictions}
    for year in [2022, 2023, 2024]:
        paired = []
        for r in rr:
            if r['id'] != 'anapa_synoptic' or r['year'] != year or r['target_temperature_c'] is None: continue
            airport = ix.get(('vityazevo_airport', r['observed_at_utc']))
            if airport is None or airport['target_temperature_c'] is None: continue
            if r['gfs'] != airport['gfs']: raise ValueError('Matched sites do not share the forecast cell')
            value = predictions.get(('anapa_synoptic', r['observed_at_utc']))
            paired.append({'observed_at_utc': r['observed_at_utc'], 'lead_hours': r['lead_hours'],
                           'gfs_c': r['gfs']['temperature'], 'airport_c': airport['target_temperature_c'],
                           'anapa_c': r['target_temperature_c'], 'anapa_minus_airport_c': r['target_temperature_c']-airport['target_temperature_c'],
                           'naive_prediction_c': value, 'gfs': r['gfs']})
        differences = [r['anapa_minus_airport_c'] for r in paired]
        d = {'paired_slots': len(paired), 'lead_counts': dict(Counter(r['lead_hours'] for r in paired)),
             'mean_anapa_minus_airport_c': float(np.mean(differences)), 'median_anapa_minus_airport_c': float(np.median(differences)),
             'range_anapa_minus_airport_c': [min(differences), max(differences)], 'paired_rows': paired}
        if year > 2022:
            d['matched_scores'] = {sid: {name: reference.error([{'target_temperature_c': r[key],
                        'prediction_c': r['gfs_c'] if name == 'gfs' else r['naive_prediction_c']} for r in paired])
                        for name in ['gfs', 'naive_reference']}
                        for sid, key in [('vityazevo_airport', 'airport_c'), ('anapa_synoptic', 'anapa_c')]}
            d['mean_correction_c'] = float(np.mean([r['naive_prediction_c']-r['gfs_c'] for r in paired]))
        out[str(year)] = d
    return out


def score(rr):
    m = reference.metrics(rr)
    usable = [r for r in rr if r['target_temperature_c'] is not None]
    m['observed_term_false_cold'] = sum(r['prediction_c'] < 0 <= r['target_temperature_c'] for r in usable)
    m['observed_term_missed_cold'] = sum(r['target_temperature_c'] < 0 <= r['prediction_c'] for r in usable)
    m['observed_routes'] = dict(Counter(r['route'] for r in usable))
    m['all_forecast_routes'] = dict(Counter(r['route'] for r in rr))
    m['calibrated_observed_slots'] = sum(r['local_calibration_applied'] for r in usable)
    return m


def select(scores, preservation):
    gates = {}; eligible = ['identity_gfs']
    for name in CANDIDATES[3:]:
        reasons = []
        for y in ['2023', '2024']:
            b = scores['identity_gfs']['anapa_synoptic'][y]; c = scores[name]['anapa_synoptic'][y]
            if not b['all_slots']['n'] or c['all_slots']['n'] != b['all_slots']['n']: reasons.append(y+': missing/unpaired support')
            elif c['all_slots']['mae_c'] > .95*b['all_slots']['mae_c']: reasons.append(y+': local MAE gain below5% versus GFS')
            if c['observed_term_false_cold'] > b['observed_term_false_cold']: reasons.append(y+': more observed-term false cold')
            if c['observed_term_missed_cold'] > b['observed_term_missed_cold']: reasons.append(y+': more observed-term cold misses')
        if not preservation[name]: reasons.append('Reference predictions changed')
        gates[name] = {'eligible': not reasons, 'reasons': reasons}
        if not reasons: eligible.append(name)
    def objective(name):
        return float(np.mean([scores[name][sid][y]['all_slots']['mae_c'] for sid in ['vityazevo_airport', 'anapa_synoptic'] for y in ['2023', '2024']]))
    return min(eligible, key=objective), gates


def selection():
    p = data.read(PROTOCOL)
    if p['years'] != [2022, 2023, 2024] or p['candidates'] != CANDIDATES: raise ValueError('Unregistered years/candidates')
    expected = [{'train_years': [2022], 'validation_year': 2023}, {'train_years': [2022, 2023], 'validation_year': 2024}]
    if p['folds'] != expected or p['minimum_distinct_nights_per_lead'] != 3: raise ValueError('Unregistered folds/support')
    old = reference.selection()  # Read-only verification of earlier frozen models.
    airport, meta = data.load('development'); anapa, _ = data.load('spatial_check_only')
    state = {'source_sha256': sha(SOURCE), 'protocol_sha256': sha(PROTOCOL), 'protocol': p,
             'parent_selection_sha256': sha(reference.LOCK), 'dataset_meta_sha256': sha(data.DIRECTORY/'dataset.meta.json'),
             'datasets_sha256': meta['dataset_sha256'], 'preserved_sha256': old['preserved_sha256'],
             'parent_sources_sha256': {str(path.relative_to(ROOT)): sha(path) for path in [reference.SOURCE, data.SOURCE, data.PROTOCOL]},
             'parent_spatial_predictions_sha256': sha(data.DIRECTORY/'anapa-check-predictions.json')}
    if LOCK.exists():
        saved = data.read(LOCK)
        if any(saved[k] != v for k, v in state.items()): raise ValueError('Frozen transfer study changed; new experiment required')
        for name, h in saved['artifact_sha256'].items():
            if sha(DIRECTORY/name) != h: raise ValueError('Frozen transfer artifact changed')
        return saved
    write_json(DIRECTORY/'protocol-before-fit.json', {**state, 'saved_at_utc': datetime.now(UTC).isoformat()})
    rr = rows(airport+anapa)
    parent = data.read(data.DIRECTORY/'anapa-check-predictions.json')['ridge_weather_100']
    diagnosis = diagnose(rr, parent); write_json(DIRECTORY/'diagnosis-before-candidates.json', diagnosis)
    parents = data.read(data.DIRECTORY/'fold-models.json')['ridge_weather_100']
    prior_predictions = data.read(data.DIRECTORY/'airport-validation-predictions.json')['ridge_weather_100']
    prior_ix = {(r['id'], r['observed_at_utc']): r['prediction_c'] for r in prior_predictions}
    models = {}; predictions = {}; results = {}; preservation = {}
    for name in CANDIDATES:
        models[name] = {}; predictions[name] = []; results[name] = {}; same = True
        for fold in p['folds']:
            year = fold['validation_year']; training = [r for r in rr if r['year'] in fold['train_years']]
            model = fit(name, training, parents[str(year)], fold['train_years'], year, p['minimum_distinct_nights_per_lead'])
            models[name][str(year)] = model
            valid = [{**r, **predict(model, r)} for r in rr if r['year'] == year]
            for sid in ['vityazevo_airport', 'anapa_synoptic']:
                subset = [r for r in valid if r['id'] == sid]
                results[name].setdefault(sid, {})[str(year)] = score(subset)
            same &= all(r['prediction_c'] == prior_ix[(r['id'], r['observed_at_utc'])] for r in valid if r['id'] == 'vityazevo_airport')
            predictions[name].extend(valid)
        preservation[name] = same
    winner, gates = select(results, preservation)
    write_json(DIRECTORY/'fold-models.json', models); write_json(DIRECTORY/'predictions.json', predictions)
    write_json(DIRECTORY/'research-model.json', models[winner]['2024'])
    intervals = {}
    for name in CANDIDATES[2:]:
        cc = [r for r in predictions[name] if r['id'] == 'anapa_synoptic']
        intervals[name] = {}
        for baseline in ['gfs', 'naive_reference']:
            bb = [r for r in predictions[baseline] if r['id'] == 'anapa_synoptic']
            intervals[name][baseline] = reference.paired_blocks(cc, bb, p['bootstrap'])
            intervals[name][baseline]['delta'] = 'candidate minus '+baseline+' equal-year slot MAE'
    artifacts = ['protocol-before-fit.json', 'diagnosis-before-candidates.json', 'fold-models.json', 'predictions.json', 'research-model.json']
    result = {**state, 'selected': winner, 'gates': gates, 'validation': results, 'reference_predictions_preserved': preservation,
              'paired_blocks': intervals, 'artifact_sha256': {name: sha(DIRECTORY/name) for name in artifacts},
              'anapa_is_development_now': True, 'unseen_station_validation': False, 'controls_used': False,
              'detector_changed': False, 'production_admitted': False, 'selected_at_utc': datetime.now(UTC).isoformat()}
    write_json(LOCK, result)
    return result


if __name__ == '__main__':
    s = selection(); print('Selected', s['selected'])
    for name in CANDIDATES:
        print(name, {y: s['validation'][name]['anapa_synoptic'][y]['all_slots'] for y in ['2023', '2024']}, s['gates'].get(name))
