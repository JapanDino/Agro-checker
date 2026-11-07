"""Prior-only nominated conditional challenger, explicitly not the selected release."""

from datetime import datetime
import json
from .pipeline import ROOT, UTC, write_json
from .scenario_study import load_rows, nights, sha
from .cold_interval_study import parent_assets, summarize, validation_key
from .conditional_error_study import select, evaluate
from .conditional_error import crossfit_residuals, fit_head, calibration_pool, calibrate_shape, forecast
from .minimum_model import target_minimum


def nominate(path, data_dir):
    selection = select(path, data_dir); p = selection['protocol']; parent = parent_assets(data_dir)
    directory = data_dir.parent/'models/research-conditional-errors'; lock = directory/'challenger-before-controls.json'
    selection_hash = sha(directory/'selection-before-controls.json')
    source_hash = sha(ROOT/'agro_checker/conditional_error_challenger.py')
    if lock.exists():
        old = json.loads(lock.read_text(encoding='utf-8'))
        if old['selection_sha256'] != selection_hash or old['source_sha256'] != source_hash:
            raise ValueError('Frozen challenger protocol changed')
        for name in ['challenger-head.json', 'challenger-calibration.json']:
            if sha(directory/name) != old['artifact_sha256'][name]: raise ValueError('Frozen challenger artifact changed')
        return old
    candidates = [s for s in p['candidates'] if s['mode'] not in ['legacy', 'lower'] and all(selection['gates'][s['name']].values())]
    if not candidates: raise ValueError('No eligible conditional challenger; do not bypass width gates')
    spec = min(candidates, key=lambda s: validation_key(selection['pooled_validation'][s['name']]))
    # Selection is already frozen. No control observations or scores are opened here.
    rows, provenance = load_rows(data_dir, [2022, 2023, 2024]); samples = nights(rows)
    records, audit = crossfit_residuals([n for n in samples if n['year'] in [2022, 2023]], parent['point_model']['spec'], p['crossfit_blocks'])
    head = fit_head(records, audit, spec)
    pool = calibration_pool(parent['point_model'], head, [n for n in samples if n['year'] == 2024], p, 2024)
    cal = calibrate_shape(pool, p)
    write_json(directory/'challenger-head.json', head); write_json(directory/'challenger-calibration.json', cal)
    result = {'name': spec['name'], 'role': 'diagnostic challenger, not selected release',
              'criterion': 'best eligible conditional candidate by same frozen prior-year lexicographic criterion',
              'selection_sha256': selection_hash, 'source_sha256': source_hash, 'input_provenance': provenance,
              'observations_loaded_years': [2022, 2023, 2024], 'saved_at_utc': datetime.now(UTC).isoformat(),
              'artifact_sha256': {n: sha(directory/n) for n in ['challenger-head.json', 'challenger-calibration.json']}}
    write_json(lock, result)
    return result


def run(path, data_dir, report_dir):
    nomination = nominate(path, data_dir)  # Freeze challenger BEFORE evaluating/opening controls.
    result = evaluate(path, data_dir, report_dir); p = result['selection']['protocol']; parent = parent_assets(data_dir)
    directory = data_dir.parent/'models/research-conditional-errors'
    head = json.loads((directory/'challenger-head.json').read_text(encoding='utf-8'))
    cal = json.loads((directory/'challenger-calibration.json').read_text(encoding='utf-8'))
    file = data_dir/'processed/conditional-error-study/night-forecasts.json'
    if sha(file) != result['forecast_sha256']: raise ValueError('Winner snapshot changed')
    frames = json.loads(file.read_text(encoding='utf-8')); frame_map = {f['date']: f for f in frames}
    for year in p['evaluation_years']:
        if year == 2025: rows, _ = load_rows(data_dir, [year])
        else:
            fresh = data_dir/'scenario-2026'
            rows, _ = load_rows(fresh, [year], json.loads((fresh/'source-config.json').read_text(encoding='utf-8')))
        records = []
        for n in nights(rows):
            f = forecast(parent['point_model'], head, cal, n, p); frame = frame_map[n['date']]
            if f['point_c'] != frame['legacy']['point_c']: raise ValueError('Challenger point changed')
            frame['challenger'] = f
            records.append({'date': n['date'], 'forecast': f, 'actual_minimum_c': target_minimum(n)})
        result['seasons'][str(year)]['scores']['conditional_challenger'] = summarize(records)
    # Existing winner snapshot is preserved; this is a distinct diagnostic artifact.
    output = data_dir/'processed/conditional-error-study/challenger-forecasts.json'; write_json(output, frames)
    result.update(challenger=nomination, challenger_calibration=cal, challenger_lock_sha256=sha(directory/'challenger-before-controls.json'),
                  challenger_forecast_sha256=sha(output))
    nominate(path, data_dir)  # Verify immutable nomination and head after the comparison.
    write_json(report_dir/'CONDITIONAL_ERROR_SUMMARY.json', result)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--select-only', action='store_true'); args = parser.parse_args()
    r = nominate(ROOT/'config/conditional_error.json', ROOT/'data') if args.select_only else run(ROOT/'config/conditional_error.json', ROOT/'data', ROOT/'docs')
    print(json.dumps({'challenger': r.get('name', r.get('challenger', {}).get('name')), 'seasons': r.get('seasons')}, ensure_ascii=False))
