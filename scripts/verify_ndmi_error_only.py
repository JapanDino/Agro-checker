"""Verify fixed point, detectors, NDVI bounds, split isolation and preservation."""
from pathlib import Path
import sys
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.ndmi_error_experiment import D,SRC,OLD,HOLDOUT,choose,key

if __name__=='__main__':
    f=read(D/'frozen-selection.json');p=read(D/'protocol.json');r=read(D/'report.json')
    assert digest(D/'frozen-selection.json')==r['frozen_sha256']
    assert digest(D/'protocol.json')==f['protocol_sha256']
    assert digest(OLD)==f['legacy_sha256']==p['legacy_sha256']
    assert digest(SRC)==f['source_2024_sha256']
    assert digest(ROOT/'agro_checker/ndmi_error_experiment.py')==f['fit_code_sha256']==r['evaluation_code_sha256']
    assert digest(D/'residuals-2024.json')==f['residual_archive_sha256']
    assert digest(D/'selection-table.json')==f['selection_table_sha256']
    selected,passed=choose(read(D/'selection-table.json'),p)
    assert selected==f['selected'] and passed==f['selection_constraints_passed']
    for keys in (f['residual_fit_keys'],f['calibration_keys']):
        assert all(a[0] not in HOLDOUT and a[1].startswith('2024-') and a[2].startswith('2024-') for a in keys)
        assert len(keys)==len(set(map(tuple,keys)))
    assert max(a[2] for a in f['residual_fit_keys'])<min(a[1] for a in f['calibration_keys'])
    for fold in f['outer_folds']:
        assert fold['point_max_target']<fold['start']
        assert fold['residual_fit_max_target']<fold['calibration_first_issue']
        assert fold['calibration_max_target']<fold['start']
    archive=read(D/'residuals-2024.json');legacy=read(OLD);cutoff=legacy['selected_model']['fit_max_target']
    assert all(q['fit_max_target']<q['issue'] for q in archive['reference_folds'])
    assert all(q['issue']>cutoff for q in archive['rows'] if q['residual_origin']=='frozen_point_out_of_fit')
    previous=[a for a in read(ROOT/'data/farm-research/field-index-model/predictions.json') if a['split'] in ['temporal2026','spatial2026']]
    current=[a for a in read(D/'test-predictions.json') if a['split']=='all2026']
    old={key(a):a for a in previous};new={key(a):a for a in current}
    assert len(old)==len(previous)==len(new)==len(current)==499 and set(old)==set(new)
    radius=legacy['interval']['radius'];old_lower_misses=[];new_lower_misses=[]
    for k,a in new.items():
        b=old[k];point=np.array(b['prediction']);now=np.array(b['persistence'])
        np.testing.assert_allclose(a['point'],point,rtol=0,atol=1e-12)
        np.testing.assert_array_equal(a['actual'],b['actual'])
        np.testing.assert_array_equal(a['detector'],point-now<=-.10)
        np.testing.assert_allclose([a['ndvi_low'],a['ndvi_high']],np.clip([point[0]-radius[0],point[0]+radius[0]],-1,1),rtol=0,atol=1e-12)
        if a['actual'][1]<a['oldlow']:old_lower_misses.append(k)
        if a['actual'][1]<a['low']:new_lower_misses.append(k)
    protected=read(D/'preservation-before.json')
    changed=[name for name,value in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=value]
    assert not changed,changed[:5]
    origins=Counter(a['residual_origin'] for a in archive['rows'])
    result=dict(only_2024_fit_calibration=True,reserved_ids_excluded=True,temporal_purge_verified=True,
        choice_reproduced=True,unchanged_cohort_pairs=499,point_predictions_match=True,both_detectors_identical=True,
        ndvi_bounds_identical=True,legacy_file_sha256=digest(OLD),frozen_sha256=r['frozen_sha256'],
        residual_origins=dict(origins),lower_miss_cases_identical=set(old_lower_misses)==set(new_lower_misses),
        legacy_lower_miss_count=len(old_lower_misses),new_lower_miss_count=len(new_lower_misses),
        protected_files=len(protected),changed=changed,field_store_files=sum(q.is_file() for q in (ROOT/'data/field').rglob('*')),
        production_promoted=False)
    dump(D/'verification.json',result);print(result)
