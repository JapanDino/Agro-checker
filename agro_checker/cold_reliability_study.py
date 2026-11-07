"""Prior-only blocked cold-tail study; all science frozen before new stations."""
from collections import Counter
from datetime import datetime
import json
import numpy as np
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import regional_data as data
from . import regional_minimum as common
from . import cold_reliability as learner
from . import cold_reliability_archive as archive
from .regional_minimum_study import primary_records
from .scenario_study import load_rows, nights
from .minimum_model import fit_minimum, point_minimum, target_minimum
from .cold_interval_study import parent_assets

PROTOCOL = ROOT/'config/cold_reliability.json'
DIRECTORY = ROOT/'models/research-cold-reliability'
LOCK = DIRECTORY/'selection-before-controls.json'


def make_bank(samples, model, metadata):
    if any(n['year'] not in [2023, 2024] or n.get('role') == 'heldout_site' for n in samples):
        raise ValueError('Forbidden calibration labels')
    if any(y not in [2022, 2023] for y in model['training_years']): raise ValueError('Forbidden point fit years')
    result = []
    for n in samples:
        if not common.complete(n): continue
        point, ood = point_minimum(model, common.adapter(n))
        result.append({'id': n['id'], 'role': n['role'], 'date': n['date'], 'year': n['year'],
                       'elevation_m': metadata[n['id']]['elevation_m'], 'actual_c': min(n['target_temperature_c']),
                       'point_gfs_c': min(n['gfs']['temperature']), 'point_huber_c': point,
                       'huber_extrapolation': ood > 4, 'point_fit_last_year': max(model['training_years'])})
    learner.check_bank(result); return result


def validation_records(bank, samples, spec, p, scope):
    rr = []; cache = {}
    for n in samples:
        anchor = 'huber' if scope == 'primary' else 'gfs'
        window = learner.week_window(n['date'], p['purge_days'])
        key = (n['id'], window)
        if key not in cache:
            cache[key] = learner.calibrate(bank, spec, p, anchor=anchor, query_id=n['id'],
                                          elevation_m=n['elevation_m'], exclude_day=n['date'])
        cal = cache[key]
        # The bank contains complete targets only. Incomplete nights retained in
        # accounting; their point comes from the prior model before this call.
        point = n['point_huber_c'] if anchor == 'huber' else n['point_gfs_c']
        f = learner.forecast(cal, point_c=point, gfs_minimum_c=n['point_gfs_c'], date_string=n['date'],
                             query_id=n['id'], elevation_m=n['elevation_m'])
        rr.append({'id': n['id'], 'date': n['date'], 'forecast': f, 'actual_minimum_c': n['actual_c']})
    return rr


def choice(folds, scores, specs, p):
    gates = {}
    for spec in specs[1:]:
        name = spec['name']; checks = {}
        for fold in folds:
            for scope in ['primary', 'regional']:
                a = fold['scores'][scope][name]; b = fold['scores'][scope]['previous']; y = fold['year']
                for metric in (['coverage', 'cold_coverage'] if scope == 'primary' else ['site_macro_coverage', 'site_macro_cold_coverage']):
                    checks[f'{scope}_{y}_{metric}'] = a[metric] is not None and a[metric] >= b[metric]-1e-12
                for metric, ratio in [('warm_mean_width_c', p['warm_width_ratio_limit']), ('mean_width_c', p['overall_width_ratio_limit'])]:
                    checks[f'{scope}_{y}_{metric}'] = a[metric] <= ratio*b[metric]+1e-12
        gates[name] = checks
    def key(name):
        ss = [scores[g][name] for g in ['primary', 'regional']]
        return (float(np.mean([1-s['cold_coverage'] for s in ss])),
                float(np.mean([(s['cold_interval_score']+s['warm_interval_score'])/2 for s in ss])),
                float(np.mean([s['mean_width_c'] for s in ss])), name)
    eligible = ['previous']+[n for n, g in gates.items() if all(g.values())]
    winner = min(eligible, key=key)
    diagnostic = min([s['name'] for s in specs[1:]], key=key)
    return winner, diagnostic, gates


def selection():
    p = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    if p['folds'] != [{'train_years': [2022], 'validation_year': 2023}, {'train_years': [2022, 2023], 'validation_year': 2024}]: raise ValueError('Invalid chronology')
    if p['control_years'] != [2025, 2026] or p['final_calibration_year'] != 2024: raise ValueError('Invalid controls')
    parent = parent_assets(ROOT/'data'); extra, meta = data.load('development')
    rows, provenance = load_rows(ROOT/'data', [2022, 2023, 2024]); old = nights(rows)
    primary = primary_records(old); regional = [n for n in extra if n['role'] == 'development']
    metadata = {s['id']: s for s in meta['sites']}
    # Register by metadata now; this function never opens new-station observations.
    archive.register(p)
    sources = ['cold_reliability.py', 'cold_reliability_study.py', 'cold_reliability_archive.py',
               'cold_interval.py', 'cold_interval_study.py', 'minimum_model.py', 'regional_data.py',
               'regional_minimum.py', 'regional_minimum_study.py', 'scenario_study.py', 'product_data.py',
               'frost_study.py', 'hourly_study.py', 'model_study.py', 'single_run.py', 'gfs_covariates.py', 'humidity_archive.py']
    state = {'protocol_sha256': sha(PROTOCOL), 'source_sha256': {n: sha(ROOT/'agro_checker'/n) for n in sources},
             'parent_hashes': parent['hashes'], 'regional_dataset_meta': meta, 'primary_provenance': provenance,
             'previous_envelope_sha256': sha(ROOT/'models/research-cold-intervals/envelope.json'),
             'previous_selection_sha256': sha(ROOT/'models/research-fixed-point-errors/selection-before-controls.json'),
             'new_station_registry_sha256': sha(archive.DIRECTORY/'new-stations-before-labels.json')}
    if LOCK.exists():
        s = json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k] != v for k, v in state.items()): raise ValueError('Frozen cold reliability source/config/input/parent changed')
        for file, h in s['artifact_sha256'].items():
            if sha(DIRECTORY/file) != h: raise ValueError('Frozen calibration bank changed')
        return s
    write_json(DIRECTORY/'protocol-lock.json', {**state, 'protocol': p, 'saved_at_utc': datetime.now(UTC).isoformat()})
    specs = learner.registry(p); records = {g: {s['name']: [] for s in specs} for g in ['primary', 'regional']}
    folds = []; banks = {}; raw_validation = {}
    for fold in p['folds']:
        y = fold['validation_year']; model = fit_minimum(parent['point_model']['spec'], [n for n in old if n['year'] in fold['train_years']])
        samples = [n for n in primary+regional if n['year'] == y]
        bank = make_bank(samples, model, metadata); banks[str(y)] = bank
        groups = {'primary': [r for r in bank if r['role'] == 'reference'], 'regional': [r for r in bank if r['role'] == 'development']}
        fs = {}; raw_validation[str(y)] = {}
        for scope, ns in groups.items():
            fs[scope] = {}; raw_validation[str(y)][scope] = {}
            for spec in specs:
                rr = validation_records(bank, ns, spec, p, scope)
                records[scope][spec['name']].extend(rr); fs[scope][spec['name']] = learner.score(rr)
                raw_validation[str(y)][scope][spec['name']] = rr
        folds.append({'year': y, 'scores': fs, 'calibration_site_nights': len(bank),
                      'expected_site_nights': len(samples), 'incomplete_target_site_nights': len(samples)-len(bank),
                      'calibration_cold_site_nights': sum(r['actual_c'] < 0 for r in bank),
                      'calibration_cold_dates': len({r['date'] for r in bank if r['actual_c'] < 0}),
                      'huber_tail_donor_extrapolations': sum(r['huber_extrapolation'] for r in bank)})
        print('Purged-week cold envelope validation', y, flush=True)
    scores = {g: {name: learner.score(rr) for name, rr in by.items()} for g, by in records.items()}
    winner, diagnostic, gates = choice(folds, scores, specs, p)
    file = 'calibration-banks.json'; write_json(DIRECTORY/file, banks)
    out = archive.DIRECTORY/'prior-validation-forecasts.json'; write_json(out, raw_validation)
    result = {**state, 'protocol': p, 'candidates': specs, 'winner': winner, 'diagnostic': diagnostic,
              'gates': gates, 'validation': scores, 'folds': folds, 'artifact_sha256': {file: sha(DIRECTORY/file)},
              'validation_forecasts_sha256': sha(out), 'selection_saved_at_utc': datetime.now(UTC).isoformat(),
              'observations_loaded_years': [2022, 2023, 2024], 'new_station_labels_loaded_in_selection': False,
              'old_heldout_sites_loaded_in_selection': False, 'detector_unchanged': True,
              'point_unchanged': True, 'deployment': p['deployment']}
    write_json(LOCK, result); return result


def paired_cold_blocks(candidate, baseline, repetitions=1000):
    """Descriptive paired cold coverage/width differences, all sites per week."""
    ref = {(r['id'], r['date']): r for r in baseline}; blocks = {}
    for r in candidate:
        a = r['actual_minimum_c']
        if a is None or a >= 0: continue
        b = ref[(r['id'], r['date'])]
        if a != b['actual_minimum_c']: raise ValueError('Different paired targets')
        covered = lambda q: q['forecast']['lower_c'] <= a <= q['forecast']['upper_c']
        width = lambda q: q['forecast']['upper_c']-q['forecast']['lower_c']
        key = learner.week_window(r['date'], 0)[0]
        blocks.setdefault(key, []).append([int(covered(r))-int(covered(b)), width(r)-width(b)])
    if len(blocks) < 2: return {'status': 'insufficient_cold_calendar_blocks', 'blocks': len(blocks)}
    values = list(blocks.values()); rng = np.random.default_rng(10414); draws = []
    for _ in range(repetitions): draws.append(np.mean([v for i in rng.integers(0, len(values), len(values)) for v in values[i]], axis=0))
    return {'status': 'descriptive_not_independent_weather', 'blocks': len(blocks),
            'delta_coverage_width_mean': np.mean([v for g in values for v in g], axis=0).tolist(),
            'delta_coverage_width_95': np.quantile(draws, [.025, .975], axis=0).tolist()}


def evaluate():
    s = selection(); p = s['protocol']; parent = parent_assets(ROOT/'data')
    banks = json.loads((DIRECTORY/'calibration-banks.json').read_text(encoding='utf-8'))
    specs = {q['name']: q for q in s['candidates']}
    def comparison(samples, year, anchor, model=None):
        bank = banks['2023' if year == 2024 else '2024']; rr = {k: [] for k in ['previous', 'selected', 'diagnostic']}; cache = {}
        for n in samples:
            point = n['point_c']; gfs = n['gfs_minimum_c']
            for label, name in [('previous', 'previous'), ('selected', s['winner']), ('diagnostic', s['diagnostic'])]:
                key = (n['id'], name)
                if key not in cache:
                    cache[key] = learner.calibrate(bank, specs[name], p, anchor=anchor, query_id=n['id'], elevation_m=n['elevation_m'])
                f = learner.forecast(cache[key], point_c=point, gfs_minimum_c=gfs,
                                     date_string=n['date'], query_id=n['id'], elevation_m=n['elevation_m'])
                if f['point_c'] != point: raise ValueError('Point changed')
                rr[label].append({'id': n['id'], 'date': n['date'], 'forecast': f, 'actual_minimum_c': n['actual_c']})
        return rr, {'scores': {k: learner.score(v) for k, v in rr.items()},
                    'paired_cold_vs_previous': {k: paired_cold_blocks(v, rr['previous']) for k, v in rr.items() if k != 'previous'}}
    # Only after the immutable selection and bank are verified do controls open.
    minimum = ROOT/'data/processed/minimum-study/night-forecasts.json'
    prior_interval = ROOT/'data/processed/cold-interval-study/night-forecasts.json'
    original = {r['date']: r for r in json.loads(minimum.read_text(encoding='utf-8'))}
    prior = {r['date']: r for r in json.loads(prior_interval.read_text(encoding='utf-8'))}
    reference_detector_file = data.DIRECTORY/'control-forecasts.json'
    for path, summary, key in [(minimum, 'MINIMUM_STUDY_SUMMARY.json', 'forecast_sha256'),
                               (prior_interval, 'COLD_INTERVAL_SUMMARY.json', 'forecast_sha256'),
                               (reference_detector_file, 'REGIONAL_MINIMUM_SUMMARY.json', 'forecast_sha256')]:
        if sha(path) != json.loads((ROOT/'docs'/summary).read_text(encoding='utf-8'))[key]:
            raise ValueError('Original forecast snapshot changed')
    reference_detector = {r['date']: r['legacy_detector'] for r in json.loads(reference_detector_file.read_text(encoding='utf-8'))}
    from .frost_study import predict_cold
    controls = {}; frames = []; verified = 0
    meta = {q['id']: q for q in s['regional_dataset_meta']['sites']}
    for year in p['control_years']:
        if year == 2025: rows, provenance = load_rows(ROOT/'data', [year])
        else:
            d = ROOT/'data/scenario-2026'; rows, provenance = load_rows(d, [year], json.loads((d/'source-config.json').read_text(encoding='utf-8')))
        ns = nights(rows); events = predict_cold(parent['detector'], rows, .25)
        if len(ns) != len(events): raise ValueError('Detector alignment mismatch')
        samples = []; counts = Counter()
        for n, event in zip(ns, events):
            if event != reference_detector[n['date']]: raise ValueError('Original probability/event/mask/timing changed')
            point, ood = point_minimum(parent['point_model'], n)
            if point != original[n['date']]['selected']['point_c']: raise ValueError('Original point changed')
            actual = target_minimum(n); verified += 1
            if actual is not None:
                cold, detected = actual < 0, event['frost']['event']
                counts['hits' if cold and detected else 'misses' if cold else 'false_alarms' if detected else 'correct_negatives'] += 1
            samples.append({'id': '349290-99999', 'date': n['date'], 'year': year, 'point_c': point,
                            'gfs_minimum_c': min(n['base']['temperature']), 'actual_c': actual,
                            'elevation_m': meta['349290-99999']['elevation_m']})
            frames.append({'date': n['date'], 'previous_detector': event, 'point_c': point, 'extrapolation': ood > 4})
        rr, report = comparison(samples, year, 'huber')
        for label in rr:
            for r in rr[label]:
                if label == 'previous':
                    for key in ['point_c', 'lower_c', 'upper_c']:
                        if r['forecast'][key] != prior[r['date']]['selected'][key]: raise ValueError('Previous range changed')
        controls[str(year)] = {**report, 'detector': dict(counts), 'detector_verified': len(events), 'input_provenance': provenance}
        by_date = {r['date']: {label: rr[label][i]['forecast'] for label in rr} for i, r in enumerate(rr['previous'])}
        for f in frames:
            if f['date'] in by_date: f['forecasts'] = by_date[f['date']]
    new, new_meta = archive.load(); spatial = {}; spatial_frames = []
    for year in p['new_station_years']:
        samples = [{'id': n['id'], 'date': n['date'], 'year': year, 'elevation_m': n['elevation_m'],
                    'point_c': n['gfs_minimum_c'], 'gfs_minimum_c': n['gfs_minimum_c'],
                    'actual_c': min(n['target_temperature_c']) if common.complete(n) else None} for n in new if n['year'] == year]
        rr, report = comparison(samples, year, 'gfs')
        spatial[str(year)] = {**report, 'calibration_year': 2023 if year == 2024 else 2024,
                             'detector_status': 'unsupported_site; no model alerts created',
                             'point_status': 'original GFS minimum, no correction'}
        for i, r in enumerate(rr['previous']):
            spatial_frames.append({'id': r['id'], 'date': r['date'], 'actual_minimum_c': r['actual_minimum_c'],
                                   'forecasts': {label: rr[label][i]['forecast'] for label in rr},
                                   'detector': None, 'detector_status': 'unsupported_site'})
    if selection() != s: raise ValueError('Selection changed after controls')
    file = archive.DIRECTORY/'reference-control-forecasts.json'; write_json(file, frames)
    spatial_file = archive.DIRECTORY/'spatial-control-forecasts.json'; write_json(spatial_file, spatial_frames)
    result = {'selection': s, 'selection_sha256': sha(LOCK), 'reference_controls': controls,
              'new_stations': spatial, 'new_station_dataset_meta': new_meta,
              'new_station_audit': json.loads((archive.DIRECTORY/'new-station-audit.json').read_text(encoding='utf-8')),
              'reference_forecasts_sha256': sha(file), 'spatial_forecasts_sha256': sha(spatial_file),
              'original_detector_verified': verified, 'point_unchanged': True,
              'previous_snapshots_sha256': {str(x.relative_to(ROOT)): sha(x) for x in [minimum, prior_interval, reference_detector_file]},
              'generated_at_utc': datetime.now(UTC).isoformat(), 'deployment': p['deployment']}
    write_json(ROOT/'docs/COLD_RELIABILITY_SUMMARY.json', result); return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--select-only', action='store_true'); a = p.parse_args()
    r = selection() if a.select_only else evaluate(); s = r if a.select_only else r['selection']
    print(json.dumps({'winner': s['winner'], 'diagnostic': s['diagnostic'],
                      'reference_controls': r.get('reference_controls'), 'new_stations': r.get('new_stations')}, ensure_ascii=False))
