"""Independent artifact/split/preservation checks; never fits or changes selection."""
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.full_season_intervals import D,HOLDOUT,select_candidate

if __name__=='__main__':
    f=read(D/'frozen-selection.json');p=read(D/'protocol.json');r=read(D/'report.json')
    assert digest(D/'frozen-selection.json')==r['frozen_sha256']
    assert digest(D/'protocol.json')==f['protocol_sha256']
    assert digest(ROOT/'agro_checker/full_season_intervals.py')==f['fit_code_sha256']==r['evaluation_code_sha256']
    chosen,passed=select_candidate(read(D/'selection-table.json'),p)
    assert chosen==f['selected_interval'] and passed==f['selection_constraints_passed']
    for keys in (f['fit_keys'],f['residual_fit_keys'],f['calibration_keys']):
        assert all(k[0] not in HOLDOUT and k[1].startswith('2024-') and k[2].startswith('2024-') for k in keys)
        assert len({tuple(k) for k in keys})==len(keys)
    assert max(k[2] for k in f['residual_fit_keys'])<min(k[1] for k in f['calibration_keys'])
    oof=read(D/'causal-oof-2024.json')
    assert all(x['fit_max_target']<x['issue'] for x in oof['folds'])
    for fold in f['outer_folds']:
        assert fold['point_fit_max_target']<fold['start']
        assert fold['residual_fit_max_target']<fold['calibration_first_issue']
        assert fold['calibration_max_target']<fold['start']
    predictions=read(D/'test-predictions.json')
    previous=[v for v in read(ROOT/'data/farm-research/field-index-model/predictions.json') if v['split'] in ['temporal2026','spatial2026']]
    before={(a['fid'],a['issue'],a['target']):a for a in previous}
    selected=[a for a in predictions if a['split']=='all2026']
    after={(a['fid'],a['issue'],a['target']):a for a in selected}
    assert len(before)==len(previous)==len(after)==len(selected)==499
    assert set(before)==set(after)
    for k,a in after.items():
        b=before[k]
        np.testing.assert_allclose(a['previous'],b['prediction'],atol=1e-12,rtol=0)
        np.testing.assert_array_equal(a['actual'],b['actual'])
    protected=read(ROOT/'outputs/01a12f84-40ae-7493-8aee-01bfa7e3ff6e/sector-audit-2026-10-20/preservation-before.json')
    changed=[name for name,value in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=value]
    assert not changed,changed[:5]
    result=dict(fit_keys_2024_only=True,holdout_excluded=True,chronological_purge_verified=True,
        residual_fit_separate_from_calibration=True,selected_choice_reproduced=True,
        unchanged_2026_cohort_pairs=499,previous_predictions_reproduced=True,
        frozen_sha256=r['frozen_sha256'],protected_files=len(protected),changed=changed,
        field_store_files=sum(a.is_file() for a in (ROOT/'data/field').rglob('*')),
        no_test_refit=True,production_promoted=False)
    dump(D/'verification.json',result)
    print(result)
