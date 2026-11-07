"""Prior-only archive-size selection; fixed point and exact frozen detector."""
from datetime import datetime, date, timedelta
import json
from collections import Counter
from .pipeline import ROOT, UTC, write_json
from .scenario_study import load_rows, nights, sha
from .minimum_model import fit_minimum, target_minimum
from .cold_interval_study import parent_assets, summarize, validation_key
from .conditional_error import calibrate_shape
from .conditional_error_study import pool_for, calibration_for, forecast_for
from . import no_cloud_error as nc
from .early_cold_archive import rows as early_rows
from .extended_archive import archive_config


def protocol(path):
    p = json.loads(path.read_text(encoding='utf-8'))
    reference = json.loads((ROOT/'config/conditional_error.json').read_text(encoding='utf-8'))
    for key in ['folds', 'final_calibration_year', 'evaluation_years', 'crossfit_blocks', 'coverage',
                'minimum_calibration_nights', 'scale_floor_c', 'scale_ceiling_c', 'warm_width_ratio_limit', 'overall_width_ratio_limit']:
        if p[key] != reference[key]: raise ValueError('Chronology/calibration/width gates changed')
    if p['conditional_families'] != [{'name':'global_90','mode':'constant'}]+reference['candidates'][2:] or p['baseline_candidates'] != reference['candidates'][:2]:
        raise ValueError('Unexpected paired-family registry')
    if p['additional_years'] != [2021] or p['training_windows'] != ['recent', 'extended'] or p['additional_season_start'] != '03-01' or p['additional_season_end'] != '05-31':
        raise ValueError('Unsupported early archive window')
    p['candidates'] = p['baseline_candidates']+[{**s, 'family': s['name'], 'window': window, 'name': window+'__'+s['name']}
        for window in p['training_windows'] for s in p['conditional_families']]
    return p


def counts(samples):
    full = [n for n in samples if target_minimum(n) is not None]; cold = [n for n in full if target_minimum(n) < 0]
    dates = {date.fromisoformat(n['date']) for n in cold}
    return {'forecast_nights': len(samples), 'complete_target_nights': len(full), 'missing_target_nights': len(samples)-len(full),
            'cold_nights': len(cold), 'consecutive_cold_groups': sum(d-timedelta(days=1) not in dates for d in dates)}


def choose(scores, p):
    baseline = scores['legacy_symmetric']; previous = scores['risk_5_lower_95']; gates = {}
    for name, r in scores.items():
        gates[name] = {'coverage_not_lower': r['covered'] >= baseline['covered'],
                      'point_mae_not_higher': r['mae_c'] <= baseline['mae_c'],
                      'warm_width_within_limit': r['warm_mean_width_c'] <= baseline['warm_mean_width_c']*p['warm_width_ratio_limit'],
                      'overall_width_within_limit': r['mean_width_c'] <= previous['mean_width_c']*p['overall_width_ratio_limit']}
    eligible = [s for s in p['candidates'] if all(gates[s['name']].values())]
    best = lambda specs: min(specs, key=lambda s: validation_key(scores[s['name']]))['name'] if specs else None
    winner = best(eligible) or 'legacy_symmetric'
    expanded = best([s for s in eligible if s.get('window') == 'extended'])
    if expanded is None: expanded=best([s for s in p['candidates'] if s.get('window')=='extended'])
    pair = expanded.replace('extended__', 'recent__', 1) if expanded else None
    return winner, expanded, pair, gates


def selection(path, data_dir):
    p = protocol(path); parent = parent_assets(data_dir)
    directory = data_dir.parent/'models/research-extended-errors'; lock = directory/'selection-before-controls.json'
    previous = data_dir.parent/'models/research-conditional-errors/selection-before-controls.json'
    sources = {n: sha(ROOT/'agro_checker'/n) for n in ['extended_error.py','extended_error_study.py','extended_archive.py','early_cold_archive.py','no_cloud_error.py',
        'conditional_error.py','conditional_error_study.py','minimum_model.py','cold_interval.py','cold_interval_study.py',
        'scenario_study.py','single_run.py','gfs_archive.py','gfs_covariates.py','humidity_archive.py','hourly_study.py','frost_study.py']}
    old = json.loads(lock.read_text(encoding='utf-8')) if lock.exists() else None
    if old:
        if old['protocol_sha256'] != sha(path) or old['source_sha256'] != sources or old['parent_hashes'] != parent['hashes'] or old['previous_selection_sha256'] != sha(previous):
            raise ValueError('Frozen extended protocol/source/parent changed')
        for file, digest in old['artifact_sha256'].items():
            if sha(directory/file) != digest: raise ValueError('Frozen extended model changed')
    else:
        write_json(directory/'protocol-lock.json', {'protocol': p,'protocol_sha256': sha(path),'source_sha256': sources,'saved_at_utc': datetime.now(UTC).isoformat()})
    rows, provenance = load_rows(data_dir, [2022, 2023, 2024]); samples = nights(rows)
    extra_dir = data_dir/'archive-2021'; c = json.loads((extra_dir/'source-config.json').read_text(encoding='utf-8'))
    if c != archive_config(): raise ValueError('Incompatible early archive config')
    extra_rows, extra_provenance = load_rows(extra_dir, [2021], c)
    early, early_provenance = early_rows(extra_dir); extra = nights(early+extra_rows)
    if len(extra)!=92 or len({n['date'] for n in extra})!=92: raise ValueError('Need full92-night2021 forecast archive')
    inputs = {'recent': provenance, 'extra_post_upgrade': extra_provenance, 'extra_pre_upgrade':early_provenance}
    if old:
        if old['input_provenance'] != inputs: raise ValueError('Frozen extended inputs changed')
        return old
    records = {s['name']: [] for s in p['candidates']}; folds = []; final_pools = None
    for fold in p['folds']:
        train = [n for n in samples if n['year'] in fold['train_years']]; valid = [n for n in samples if n['year'] == fold['validation_year']]
        point = fit_minimum(parent['point_model']['spec'], train)
        window_samples={'recent':train,'extended':extra+train}
        pools={window:nc.crossfit(ss) for window,ss in window_samples.items()}
        point_models={window:nc.fit_point(ss) for window,ss in window_samples.items()}; scores = {}
        if fold['validation_year'] == 2024: final_pools = pools
        for spec in p['candidates']:
            window=spec.get('window'); rr=[]
            if window:
                current_point=point_models[window]; head=nc.fit_head(*pools[window],spec)
                pool=nc.pool(current_point,head,valid,p,fold['validation_year'])
            else:
                current_point=point; head={'spec':spec}; pool=pool_for(point,head,valid,p,fold['validation_year'])
            for n in valid:
                if target_minimum(n) is None: continue
                cal=calibrate_shape(pool,p,n['date']) if window else calibration_for(pool,head,p,n['date'])
                forecast=nc.forecast(current_point,head,cal,n,p) if window else forecast_for(point,head,cal,n,p)
                rr.append({'date': n['date'], 'forecast': forecast, 'actual_minimum_c': target_minimum(n)})
            scores[spec['name']] = summarize(rr); records[spec['name']].extend(rr)
        folds.append({**fold, 'recent_residual_nights': len(pools['recent'][0]), 'extended_residual_nights': len(pools['extended'][0]), 'scores': scores})
    scores = {name: summarize(rr) for name, rr in records.items()}
    # Original baseline runs must reproduce the frozen prior-only experiment.
    reference = json.loads(previous.read_text(encoding='utf-8'))
    for name in ['legacy_symmetric','risk_5_lower_95']:
        if scores[name] != reference['pooled_validation'][name]: raise ValueError('Original validation comparison changed')
    winner, expanded, paired, gates = choose(scores, p)
    artifacts = {}; models = {}
    for name in dict.fromkeys(n for n in [winner, expanded, paired] if n):
        spec = next(s for s in p['candidates'] if s['name'] == name)
        window=spec.get('window')
        if window:
            final_train=[n for n in samples if n['year'] in [2022,2023]]+(extra if window=='extended' else [])
            current_point=nc.fit_point(final_train); head=nc.fit_head(*final_pools[window],spec)
        else: current_point=parent['point_model']; head={'spec':spec}
        valid = [n for n in samples if n['year'] == 2024]
        cal=calibrate_shape(nc.pool(current_point,head,valid,p,2024),p) if window else calibration_for(pool_for(current_point,head,valid,p,2024),head,p)
        for label, value in [('point',current_point),('head', head), ('calibration', cal)]:
            file = name+'-'+label+'.json'; write_json(directory/file, value); artifacts[file] = sha(directory/file)
        models[name] = {'point':name+'-point.json','head': name+'-head.json', 'calibration': name+'-calibration.json'}
    result = {'protocol': p, 'protocol_sha256': sha(path), 'source_sha256': sources, 'parent_hashes': parent['hashes'],
              'previous_selection_sha256': sha(previous), 'winner': winner, 'expanded_challenger': expanded, 'paired_recent': paired,
              'diagnostic_role': 'not promoted unless winner by prior years', 'gates': gates, 'pooled_validation': scores, 'folds': folds,
              'selection_saved_at_utc': datetime.now(UTC).isoformat(), 'input_provenance': inputs, 'artifact_sha256': artifacts, 'models': models,
              'observations_loaded_years': [2021, 2022, 2023, 2024],
              'archive_counts': {str(y): counts(extra if y == 2021 else [n for n in samples if n['year'] == y]) for y in [2021, 2022, 2023, 2024]}}
    write_json(lock, result); return result


def evaluate(path, data_dir, report_dir):
    s = selection(path, data_dir); p = s['protocol']; parent = parent_assets(data_dir)
    directory = data_dir.parent/'models/research-extended-errors'
    models = {name: {k: json.loads((directory/file).read_text(encoding='utf-8')) for k, file in files.items()} for name, files in s['models'].items()}
    # Controls opened only after winner + matched extended/recent pair have been frozen.
    def snapshot(summary, file, key):
        meta = json.loads((report_dir/summary).read_text(encoding='utf-8')); source = data_dir/file
        if sha(source) != meta[key]: raise ValueError('Frozen reference snapshot changed')
        return {f['date']: f for f in json.loads(source.read_text(encoding='utf-8'))}, sha(source)
    old, ohash = snapshot('MINIMUM_STUDY_SUMMARY.json','processed/minimum-study/night-forecasts.json','forecast_sha256')
    masks, mhash = snapshot('SCENARIO_STUDY_SUMMARY.json','processed/scenario-study/night-forecasts.json','forecast_artifact_sha256')
    prior, phash = snapshot('COLD_INTERVAL_SUMMARY.json','processed/cold-interval-study/night-forecasts.json','forecast_sha256')
    from .frost_study import predict_cold
    frames = []; seasons = {}
    for year in p['evaluation_years']:
        if year == 2025: rows, provenance = load_rows(data_dir, [year])
        else:
            fresh = data_dir/'scenario-2026'; rows, provenance = load_rows(fresh,[year],json.loads((fresh/'source-config.json').read_text(encoding='utf-8')))
        samples = nights(rows); events = predict_cold(parent['detector'], rows, .25)
        if len(samples) != len(events): raise ValueError('Detector alignment mismatch')
        records = {n: [] for n in ['gfs','legacy_huber','previous_cold_range','selected','extended_candidate','matched_recent']}; detector = Counter()
        for n, event in zip(samples, events):
            ref = old[n['date']]
            if event['date'] != n['date'] or event['cold_mask'] != masks[n['date']]['previous_detector_cold_mask'] or event['frost'] != ref['previous_detector']['frost']:
                raise ValueError('Detector mask/timing changed')
            forecasts = {}
            for name, model in models.items():
                if model['head']['spec'].get('window'):
                    forecasts[name]=nc.forecast(model['point'],model['head'],model['calibration'],n,p)
                else:
                    forecasts[name]=forecast_for(parent['point_model'],model['head'],model['calibration'],n,p)
                    if forecasts[name]['point_c'] != ref['selected']['point_c']: raise ValueError('Original point forecast changed')
            f = {'gfs': ref['gfs'], 'legacy_huber': ref['selected'], 'previous_cold_range': prior[n['date']]['selected'],
                 'selected': forecasts[s['winner']]}
            if s['expanded_challenger']:
                f.update(extended_candidate=forecasts[s['expanded_challenger']], matched_recent=forecasts[s['paired_recent']])
            actual = target_minimum(n)
            if actual is not None:
                cold, detected = actual < 0, any(event['cold_mask'])
                detector['hits' if cold and detected else 'misses' if cold else 'false_alarms' if detected else 'correct_negatives'] += 1
            for name, forecast in f.items(): records[name].append({'date': n['date'],'forecast': forecast,'actual_minimum_c': actual})
            frames.append({**ref,'forecasts': f,'cold_mask': event['cold_mask'],'detector_unchanged': True})
        seasons[str(year)] = {'scores': {name: summarize(rr) for name, rr in records.items() if rr}, 'detector': dict(detector),
                             'detector_masks_verified': len(samples), 'input_provenance': provenance}
    if selection(path, data_dir) != s: raise ValueError('Frozen selection changed during evaluation')
    file = data_dir/'processed/extended-error-study/night-forecasts.json'; write_json(file,frames)
    result = {'selection': s,'selection_sha256': sha(directory/'selection-before-controls.json'),'seasons': seasons,
              'forecast_sha256': sha(file),'legacy_forecast_sha256': ohash,'legacy_mask_sha256': mhash,'previous_range_sha256': phash,
              'generated_at_utc': datetime.now(UTC).isoformat(),'deployment': p['deployment']}
    write_json(report_dir/'EXTENDED_ERROR_SUMMARY.json',result); return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--select-only',action='store_true'); args=parser.parse_args()
    r = selection(ROOT/'config/extended_error.json',ROOT/'data') if args.select_only else evaluate(ROOT/'config/extended_error.json',ROOT/'data',ROOT/'docs')
    print(json.dumps({k:r.get(k) for k in ['winner','expanded_challenger','paired_recent','archive_counts','pooled_validation','seasons']},ensure_ascii=False))
