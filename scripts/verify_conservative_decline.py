"""Verify chronology, unchanged cohort/point forecasts and frozen selection."""
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read, dump, digest
from agro_checker.conservative_decline import D, SOURCE, LEGACY, HOLDOUT, key, select, infer, NAMES
from agro_checker.field_decline_experiment import chronological_folds, events, confusion


if __name__ == '__main__':
    p = read(D/'protocol.json')
    f = read(D/'frozen-selection.json')
    report = read(D/'report.json')
    assert digest(D/'frozen-selection.json') == report['frozen_sha256']
    assert digest(D/'protocol.json') == f['protocol_sha256']
    assert digest(SOURCE) == f['source_2024_sha256'] == p['source_2024_sha256']
    assert digest(LEGACY) == f['legacy_sha256'] == p['legacy_sha256']
    assert digest(ROOT/'agro_checker/conservative_decline.py') == f['code_sha256'] == report['evaluation_code_sha256']
    assert digest(D/'selection-table.json') == f['selection_table_sha256']
    assert digest(D/'oof-predictions.json') == f['oof_sha256']
    oo = read(D/'oof-predictions.json')
    selected, diagnostic, table = select(oo['rows'], oo['records'], p)
    assert selected == f['selected'] and diagnostic == f['diagnostic']
    assert table == read(D/'selection-table.json')
    fitrows = [r for r in read(SOURCE) if r['fid'] not in HOLDOUT]
    assert f['fit_keys'] == [list(key(r)) for r in fitrows]
    assert all(r['issue'].startswith('2024-') and r['target'].startswith('2024-') for r in fitrows)
    assert all(r['previous'] < r['issue'] < r['target'] for r in fitrows)
    folds = list(chronological_folds(fitrows, p['folds']))
    assert [key(r) for _, _, vv in folds for r in vv] == [key(r) for r in oo['rows']]
    for start, tt, vv in folds:
        assert max(r['target'] for r in tt) < min(r['issue'] for r in vv)
        assert max(r['target'] for r in tt) < start
    assert f['created_at_utc'] < report['created_at_utc']
    for entry in f['models'].values():
        assert digest(ROOT/entry['path']) == entry['sha256']
    predictions = read(D/'test-predictions.json')
    current = [r for r in predictions if r['split'] == 'all2026']
    original = [r for r in read(ROOT/'data/farm-research/field-index-model/predictions.json')
                if r['split'] in ['temporal2026', 'spatial2026']]
    a, b = {key(r): r for r in current}, {key(r): r for r in original}
    assert len(a) == len(current) == len(b) == len(original) == 499
    assert a.keys() == b.keys()
    for k, row in a.items():
        old = b[k]
        np.testing.assert_allclose(row['point'], old['prediction'], rtol=0, atol=1e-12)
        np.testing.assert_array_equal(row['actual'], old['actual'])
        np.testing.assert_array_equal(row['persistence'], old['persistence'])
        np.testing.assert_array_equal(row['legacy_alarm'], np.array(old['prediction'])-old['persistence'] <= -.10)
    all2026 = read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    alarm, _, point, legacy = infer(all2026, p, f)
    lookup = {key(r): i for i, r in enumerate(all2026)}
    for r in current:
        i = lookup[key(r)]
        np.testing.assert_array_equal(r['new_alarm'], alarm[i])
        np.testing.assert_allclose(r['point'], point[i], rtol=0, atol=1e-12)
    for j, name in enumerate(NAMES):
        m = confusion(all2026, events(all2026, j), alarm[:, j])
        assert m == report['results']['all2026'][name]['new']
    protected = read(D/'preservation-before.json')
    changed = [name for name, sha in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name) != sha]
    assert not changed, changed[:5]
    result = dict(only_2024_fit_selection=True, spatial_reserve_excluded=True,
        chronological_targets_purged=True, selection_reproduced=True, model_hashes_verified=True,
        frozen_before_evaluation=True, original_cohort_pairs=499, point_predictions_unchanged=True,
        original_detector_unchanged=True, all2026_predictions_reproduced=True,
        protected_files=len(protected), changed_files=changed, production_promoted=False,
        frozen_sha256=report['frozen_sha256'])
    dump(D/'verification.json', result)
    print(result)
