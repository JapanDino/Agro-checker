"""Reproduce2024 calibration choice and exact preserved case identities."""
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.seasonal_calibration import D,base,NAMES,key,check_inputs,choose,infer,fit_calibrator,base_alarm,events


if __name__=='__main__':
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');r=read(D/'report.json');check_inputs(p)
    assert digest(D/'protocol.json')==f['protocol_sha256']
    assert digest(D/'frozen-selection.json')==r['frozen_sha256']
    assert digest(ROOT/'agro_checker/seasonal_calibration.py')==f['code_sha256']==r['evaluation_code_sha256']
    assert f['created_at_utc']<r['created_at_utc']
    for filename,field in [('causal-scores-2024.json','causal_sha256'),('meta-oof.json','meta_sha256'),('selection-table.json','selection_sha256')]:
        assert digest(D/filename)==f[field]
    causal=read(D/'causal-scores-2024.json');meta=read(D/'meta-oof.json')
    assert len(causal['rows'])==len({key(v) for v in causal['rows']})==f['calibration_pairs']
    assert [list(key(v)) for v in causal['rows']]==f['calibration_keys']
    assert all(v['fid'] not in base.HOLDOUT and v['issue'][:4]=='2024' and v['target'][:4]=='2024' for v in causal['rows'])
    assert all(v['fit_max_target']<v['issue'] for v in causal['folds'])
    for a in meta['audits']:
        assert a['fit_max_target'] is None or a['fit_max_target']<a['start']
        assert a['start']<=a['validation_min_issue']
    selected,table=choose(meta['rows'],meta['records'],p)
    assert selected==f['selected'] and table==read(D/'selection-table.json')
    for j,c in enumerate(selected):
        # Reproduce fixed2024-only calibration; never save or change frozen parameters.
        reproduced=fit_calibrator(causal['rows'],j,c['family'],p)
        assert reproduced==f['calibrators'][j]
    previous=read(base.D/'test-predictions.json')
    ndvi={key(v):v for v in previous if v['split']=='all2026' and v['version']=='best_adapted'}
    ndmi={key(v):v for v in previous if v['split']=='all2026' and v['version']=='2024_only'}
    original=[v for v in read(ROOT/'data/farm-research/field-index-model/predictions.json') if v['split'] in ['temporal2026','spatial2026']]
    point={key(v):v for v in original}
    pred=[v for v in read(D/'test-predictions.json') if v['split']=='all2026']
    assert len(pred)==len({key(v) for v in pred})==len(point)==499
    assert {key(v) for v in pred}==point.keys()==ndvi.keys()==ndmi.keys()
    test=read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    values=infer(test,p,f);lookup={key(v):i for i,v in enumerate(test)}
    for v in pred:
        k=key(v);i=lookup[k]
        np.testing.assert_allclose(v['raw'],[ndvi[k]['scores'][0],ndmi[k]['scores'][1]],rtol=0,atol=1e-12)
        np.testing.assert_array_equal(v['baseline_alarm'],[ndvi[k]['new_alarm'][0],ndmi[k]['new_alarm'][1]])
        np.testing.assert_allclose(v['point'],point[k]['prediction'],rtol=0,atol=1e-12)
        np.testing.assert_array_equal(v['actual'],point[k]['actual'])
        np.testing.assert_array_equal(v['ridge_alarm'],np.array(point[k]['prediction'])-point[k]['persistence']<=-.10)
        np.testing.assert_allclose(v['calibrated'],values['calibrated'][i],rtol=0,atol=1e-12)
        np.testing.assert_array_equal(v['filtered_alarm'],values['filtered'][i])
        assert not np.any(np.array(v['filtered_alarm'])&~np.array(v['baseline_alarm']))
    retention={}
    for j,name in enumerate(NAMES):
        before={key(v) for v in pred if v['baseline_alarm'][j] and v['actual'][j]-v['persistence'][j]<=-.10}
        after={key(v) for v in pred if v['filtered_alarm'][j] and v['actual'][j]-v['persistence'][j]<=-.10}
        assert len(before-after)==r['results']['all2026'][name]['new']['lost_tp']
        retention[name]=dict(baseline_tp=len(before),retained_tp=len(before&after),lost_cases=len(before-after))
        if f['selected'][j]['threshold']==0:
            np.testing.assert_array_equal(values['filtered'][:,j],values['baseline'][:,j])
    protected=read(D/'preservation-before.json')
    changed=[name for name,sha in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=sha]
    assert not changed,changed[:5]
    result=dict(fit_and_selection_2024_only=True,causal_base_oof_verified=True,no_in_sample_calibration_scores=True,
        meta_target_purge_verified=True,spatial_reserve_excluded=True,selection_and_calibrators_reproduced=True,
        original499_pair_cohort=True,frozen_base_scores_unchanged=True,point_ridge_and_state_preserved=True,
        veto_no_added_alerts=True,tp_case_retention=retention,protected_files=len(protected),changed_files=changed,
        production_promoted=False,frozen_sha256=r['frozen_sha256'])
    dump(D/'verification.json',result);print(result)
