"""Immutable prior-year selection, then control evaluation; run as a module."""

from datetime import datetime
import json
from collections import Counter
from .pipeline import ROOT, UTC, write_json
from .scenario_study import load_rows, nights, sha
from .minimum_model import fit_minimum, target_minimum
from .cold_interval import residual_pool, calibrate_envelope, forecast_envelope
from .cold_interval_study import parent_assets, summarize, validation_key
from .conditional_error import crossfit_residuals, fit_head, calibration_pool, calibrate_shape, forecast


def validate_protocol(p):
    expected = [('legacy_symmetric', 'legacy'), ('risk_5_lower_95', 'lower'),
                ('scale_ridge_10', 'scale'), ('scale_ridge_100', 'scale'),
                ('tails_ridge_10', 'asymmetric'), ('tails_ridge_100', 'asymmetric'),
                ('quantile_linear_005', 'quantile'), ('quantile_linear_02', 'quantile'), ('quantile_trees', 'trees')]
    if p['schema_version'] != 1 or [(s['name'], s['mode']) for s in p['candidates']] != expected:
        raise ValueError('Invalid conditional registry')
    if p['folds'] != [{'train_years': [2022], 'validation_year': 2023}, {'train_years': [2022, 2023], 'validation_year': 2024}]:
        raise ValueError('Invalid chronology')
    for k, v in {'final_calibration_year': 2024, 'evaluation_years': [2025, 2026], 'crossfit_blocks': 6,
                 'coverage': .9, 'minimum_calibration_nights': 30, 'scale_floor_c': .5, 'scale_ceiling_c': 9.,
                 'warm_width_ratio_limit': 1.05, 'overall_width_ratio_limit': 1.05}.items():
        if p[k] != v: raise ValueError('Invalid protocol settings')


def baseline_protocol(p):
    return {'base_coverage': p['coverage'], 'minimum_pool_nights': p['minimum_calibration_nights']}


def pool_for(point, head, valid, p, year):
    if head['spec']['mode'] in ['legacy', 'lower']: return residual_pool(point, valid, [year])
    return calibration_pool(point, head, valid, p, year)


def calibration_for(pool, head, p, exclude_date=None):
    if head['spec']['mode'] in ['legacy', 'lower']:
        return calibrate_envelope(pool, head['spec'], baseline_protocol(p), exclude_date)
    return calibrate_shape(pool, p, exclude_date)


def forecast_for(point, head, cal, n, p):
    if head['spec']['mode'] in ['legacy', 'lower']: return forecast_envelope(point, cal, n, baseline_protocol(p))
    return forecast(point, head, cal, n, p)


def select_winner(scores, p):
    legacy = scores['legacy_symmetric']; previous = scores['risk_5_lower_95']; gates = {}
    for s in p['candidates']:
        r = scores[s['name']]
        gates[s['name']] = {'coverage_not_lower': r['covered'] >= legacy['covered'],
                           'warm_width_within_limit': r['warm_mean_width_c'] <= legacy['warm_mean_width_c']*p['warm_width_ratio_limit'],
                           'overall_width_within_limit': r['mean_width_c'] <= previous['mean_width_c']*p['overall_width_ratio_limit']}
    eligible = [s for s in p['candidates'] if all(gates[s['name']].values())]
    if not eligible: return 'legacy_symmetric', gates
    return min(eligible, key=lambda s: validation_key(scores[s['name']]))['name'], gates


def select(path, data_dir):
    p = json.loads(path.read_text(encoding='utf-8')); validate_protocol(p)
    directory = data_dir.parent/'models/research-conditional-errors'; selected_path = directory/'selection-before-controls.json'
    parent = parent_assets(data_dir)
    sources = {name: sha(ROOT/'agro_checker'/name) for name in [
        'conditional_error.py', 'conditional_error_study.py', 'minimum_model.py', 'cold_interval.py',
        'cold_interval_study.py', 'scenario_study.py', 'frost_study.py']}
    if selected_path.exists():
        old = json.loads(selected_path.read_text(encoding='utf-8'))
        if old['protocol_sha256'] != sha(path) or old['source_sha256'] != sources or old['parent_hashes'] != parent['hashes']:
            raise ValueError('Frozen protocol/source/parent mismatch')
        if sha(directory/'head.json') != old['head_sha256'] or sha(directory/'calibration.json') != old['calibration_sha256']:
            raise ValueError('Frozen conditional artifact changed')
        _, provenance = load_rows(data_dir, [2022, 2023, 2024])
        if old['input_provenance'] != provenance: raise ValueError('Frozen inputs changed')
        return old
    write_json(directory/'protocol-lock.json', {'protocol': p, 'protocol_sha256': sha(path), 'source_sha256': sources,
                                               'saved_at_utc': datetime.now(UTC).isoformat()})
    rows, provenance = load_rows(data_dir, [2022, 2023, 2024]); samples = nights(rows)
    records = {s['name']: [] for s in p['candidates']}; folds = []
    final_records = final_audit = None
    for fold in p['folds']:
        train = [n for n in samples if n['year'] in fold['train_years']]
        valid = [n for n in samples if n['year'] == fold['validation_year']]
        point = fit_minimum(parent['point_model']['spec'], train)
        oof, audit = crossfit_residuals(train, parent['point_model']['spec'], p['crossfit_blocks'])
        if fold['train_years'] == [2022, 2023]: final_records, final_audit = oof, audit
        scores = {}
        for spec in p['candidates']:
            head = fit_head(oof, audit, spec); pool = pool_for(point, head, valid, p, fold['validation_year'])
            rr = []
            for n in valid:
                if target_minimum(n) is None: continue
                cal = calibration_for(pool, head, p, n['date'])
                rr.append({'date': n['date'], 'forecast': forecast_for(point, head, cal, n, p),
                           'actual_minimum_c': target_minimum(n)})
            records[spec['name']].extend(rr); scores[spec['name']] = summarize(rr)
        folds.append({**fold, 'oof_nights': len(oof), 'crossfit_audit': audit, 'scores': scores})
    scores = {name: summarize(rr) for name, rr in records.items()}
    winner, gates = select_winner(scores, p)
    spec = next(s for s in p['candidates'] if s['name'] == winner)
    head = fit_head(final_records, final_audit, spec)
    valid = [n for n in samples if n['year'] == 2024]
    cal = calibration_for(pool_for(parent['point_model'], head, valid, p, 2024), head, p)
    write_json(directory/'head.json', head); write_json(directory/'calibration.json', cal)
    result = {'protocol': p, 'protocol_sha256': sha(path), 'source_sha256': sources, 'parent_hashes': parent['hashes'],
              'selection_saved_at_utc': datetime.now(UTC).isoformat(), 'winner': winner, 'gates': gates,
              'observations_loaded_years': [2022, 2023, 2024], 'folds': folds, 'pooled_validation': scores,
              'input_provenance': provenance, 'head_sha256': sha(directory/'head.json'), 'calibration_sha256': sha(directory/'calibration.json')}
    write_json(selected_path, result)
    return result


def evaluate(path, data_dir, report_dir):
    selection = select(path, data_dir); p = selection['protocol']; parent = parent_assets(data_dir)
    directory = data_dir.parent/'models/research-conditional-errors'
    head = json.loads((directory/'head.json').read_text(encoding='utf-8'))
    cal = json.loads((directory/'calibration.json').read_text(encoding='utf-8'))
    # Only the frozen winner is evaluated. Open previous control snapshots after selection.
    def snapshot(summary_name, relative, key):
        summary = json.loads((report_dir/summary_name).read_text(encoding='utf-8')); file = data_dir/relative
        if sha(file) != summary[key]: raise ValueError('Control snapshot changed')
        return {r['date']: r for r in json.loads(file.read_text(encoding='utf-8'))}, sha(file)
    old, old_hash = snapshot('MINIMUM_STUDY_SUMMARY.json', 'processed/minimum-study/night-forecasts.json', 'forecast_sha256')
    masks, mask_hash = snapshot('SCENARIO_STUDY_SUMMARY.json', 'processed/scenario-study/night-forecasts.json', 'forecast_artifact_sha256')
    previous, previous_hash = snapshot('COLD_INTERVAL_SUMMARY.json', 'processed/cold-interval-study/night-forecasts.json', 'forecast_sha256')
    from .frost_study import predict_cold
    frames = []; seasons = {}
    for year in p['evaluation_years']:
        if year == 2025: rows, provenance = load_rows(data_dir, [year])
        else:
            fresh = data_dir/'scenario-2026'
            rows, provenance = load_rows(fresh, [year], json.loads((fresh/'source-config.json').read_text(encoding='utf-8')))
        samples = nights(rows); events = predict_cold(parent['detector'], rows, .25)
        if len(samples) != len(events): raise ValueError('Detector alignment mismatch')
        rr = {name: [] for name in ['gfs', 'legacy_huber', 'previous_cold_range', 'conditional']}; counts = Counter()
        for n, event in zip(samples, events):
            ref = old[n['date']]; f = forecast_for(parent['point_model'], head, cal, n, p)
            if f['point_c'] != ref['selected']['point_c']: raise ValueError('Point changed')
            if event['date'] != n['date'] or event['cold_mask'] != masks[n['date']]['previous_detector_cold_mask'] or event['frost'] != ref['previous_detector']['frost']:
                raise ValueError('Detector mask/timing changed')
            actual = target_minimum(n)
            if actual is not None:
                cold, detected = actual < 0, any(event['cold_mask'])
                counts['hits' if cold and detected else 'misses' if cold else 'false_alarms' if detected else 'correct_negatives'] += 1
            for name, pred in [('gfs', ref['gfs']), ('legacy_huber', ref['selected']),
                               ('previous_cold_range', previous[n['date']]['selected']), ('conditional', f)]:
                rr[name].append({'date': n['date'], 'forecast': pred, 'actual_minimum_c': actual})
            frames.append({**ref, 'legacy': ref['selected'], 'previous_range': previous[n['date']]['selected'],
                           'selected': f, 'interval_method': selection['winner'], 'cold_mask': event['cold_mask'], 'detector_unchanged': True})
        seasons[str(year)] = {'scores': {name: summarize(v) for name, v in rr.items()}, 'detector': dict(counts),
                             'detector_masks_verified': len(samples), 'input_provenance': provenance}
    if parent_assets(data_dir)['hashes'] != selection['parent_hashes']: raise ValueError('Parent changed during evaluation')
    if sha(directory/'head.json') != selection['head_sha256'] or sha(directory/'calibration.json') != selection['calibration_sha256']:
        raise ValueError('Artifact changed during evaluation')
    output = data_dir/'processed/conditional-error-study/night-forecasts.json'; write_json(output, frames)
    result = {'selection': selection, 'selection_sha256': sha(directory/'selection-before-controls.json'), 'calibration': cal,
              'seasons': seasons, 'forecast_sha256': sha(output), 'legacy_forecast_sha256': old_hash,
              'legacy_mask_sha256': mask_hash, 'previous_range_sha256': previous_hash, 'generated_at_utc': datetime.now(UTC).isoformat(),
              'deployment': p['deployment']}
    write_json(report_dir/'CONDITIONAL_ERROR_SUMMARY.json', result)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--select-only', action='store_true')
    args = parser.parse_args()
    result = select(ROOT/'config/conditional_error.json', ROOT/'data') if args.select_only else evaluate(ROOT/'config/conditional_error.json', ROOT/'data', ROOT/'docs')
    print(json.dumps({'winner': result.get('winner', result.get('selection', {}).get('winner')),
                      'validation': result.get('pooled_validation'), 'seasons': result.get('seasons')}, ensure_ascii=False))
