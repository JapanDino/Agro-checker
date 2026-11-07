"""Verify archive identity, 2024-only selection, frozen ablations and unchanged state."""
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.early_season_data import ARCHIVE,HOLDOUT
from agro_checker.early_season_decline import D,SOURCE,LEGACY,PRIOR,select,training_rows,infer,NAMES,key
from agro_checker.field_decline_experiment import chronological_folds,confusion,events
from agro_checker.conservative_decline import gate


if __name__=='__main__':
    p=read(D/'experiment-protocol.json');f=read(D/'frozen-selection.json');r=read(D/'report.json')
    assert digest(D/'frozen-selection.json')==r['frozen_sha256']
    assert digest(D/'experiment-protocol.json')==f['protocol_sha256']
    assert digest(ARCHIVE/'protocol.json')==p['archive_protocol_sha256']
    assert digest(SOURCE)==f['source_2024_sha256']==p['source_2024_sha256']
    assert digest(LEGACY)==f['legacy_sha256']==p['legacy_sha256']
    assert digest(PRIOR/'frozen-selection.json')==p['previous_freeze_sha256']
    assert digest(D/'data-early.json')==f['early_sha256']
    assert digest(D/'selection-table.json')==f['selection_sha256']
    assert digest(D/'oof-predictions.json')==f['oof_sha256']
    assert digest(ROOT/'agro_checker/early_season_decline.py')==f['code_sha256']==r['evaluation_code_sha256']
    assert digest(ROOT/'agro_checker/early_season_data.py')==f['data_code_sha256']
    assert f['created_at_utc']<r['created_at_utc']
    oo=read(D/'oof-predictions.json')
    primary,choices,table=select(oo['rows'],oo['records'],p)
    assert primary==f['primary'] and choices==f['choices'] and table==read(D/'selection-table.json')
    current=[v for v in read(SOURCE) if v['fid'] not in HOLDOUT]
    early=[v for v in read(D/'data-early.json') if v['fid'] not in HOLDOUT]
    folds=list(chronological_folds(current,p['folds']))
    assert [key(v) for _,_,vv in folds for v in vv]==[key(v) for v in oo['rows']]
    for regime in p['regimes']:
        tt=training_rows(current,early,regime)
        assert [list(key(v)) for v in tt]==f['fitting'][regime]['keys']
        assert all(v['issue'][:4]<='2024' and v['fid'] not in HOLDOUT for v in tt)
        for start,part,vv in folds:
            training=training_rows(part,early,regime)
            assert max(v['target'] for v in training)<min(v['issue'] for v in vv)
            assert max(v['target'] for v in training)<start
    # Same data/algorithm; floating weight normalization order need not give bitwise scores.
    prior=read(PRIOR/'oof-predictions.json')
    assert [key(v) for v in prior['rows']]==[key(v) for v in oo['rows']]
    control=next(v for v in oo['records'] if v['regime']=='2024_only')
    control_differences={}
    for j,spec in enumerate(p['algorithms']):
        old=next(v for v in prior['records'] if v['spec']['id']==spec['id'])
        a,b=np.array(control['scores'][j]),np.array(old['scores'][j])
        control_differences[NAMES[j]]=float(np.max(abs(a-b)))
        choice=next(c for c in f['choices'][j] if c['regime']=='2024_only')
        old_choice=read(PRIOR/'frozen-selection.json')['selected'][j]
        assert choice['threshold']==old_choice['threshold'] and choice['gate']==old_choice['gate']
        np.testing.assert_array_equal((a>=choice['threshold'])&gate(oo['rows'],j,choice['gate']),
            (b>=old_choice['threshold'])&gate(oo['rows'],j,old_choice['gate']))
        assert choice['metrics']==old_choice['metrics']
    archive=read(ARCHIVE/'satellite.json')
    assert archive['completed']
    count=0
    for scene in archive['scenes']:
        if scene['status']!='ready':continue
        assert scene['processing_baseline'] in read(ARCHIVE/'protocol.json')['allowed_baselines']
        for name,sha in scene['crop_sha256'].items():
            assert digest(ARCHIVE/'sentinel'/scene['id']/name)==sha
            count+=1
    obs={(v['fid'],v['date']):v for v in read(D/'observations-early.json')['rows']}
    for v in early:
        a,b,c=[obs[v['fid'],v[k]] for k in ['previous','issue','target']]
        assert a['baseline']==b['baseline']==c['baseline']==v['baseline']
        assert a['date']<b['date']<c['date'] and a['date'][:4]==b['date'][:4]==c['date'][:4]
        np.testing.assert_array_equal(v['y'],c['values']['core']['value'])
    oldrows=[v for v in read(ROOT/'data/farm-research/field-index-model/predictions.json') if v['split'] in ['temporal2026','spatial2026']]
    original={key(v):v for v in oldrows}
    predictions=read(D/'test-predictions.json')
    prior_test={key(v):v for v in read(PRIOR/'test-predictions.json') if v['split']=='all2026'}
    test=read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    for version in ['primary']+p['regimes']:
        subset=[v for v in predictions if v['split']=='all2026' and v['version']==version]
        assert len(subset)==len({key(v) for v in subset})==len(original)==499
        assert {key(v) for v in subset}==original.keys()
        alarms,_,point,legacy=infer(test,p,f,None if version=='primary' else version)
        index={key(v):i for i,v in enumerate(test)}
        for v in subset:
            a=original[key(v)];i=index[key(v)]
            np.testing.assert_allclose(v['point'],a['prediction'],rtol=0,atol=1e-12)
            np.testing.assert_array_equal(v['actual'],a['actual'])
            np.testing.assert_array_equal(v['legacy_alarm'],np.array(a['prediction'])-a['persistence']<=-.10)
            np.testing.assert_array_equal(v['new_alarm'],alarms[i])
            if version=='2024_only':
                np.testing.assert_array_equal(v['new_alarm'],prior_test[key(v)]['new_alarm'])
        for j,name in enumerate(NAMES):
            assert confusion(test,events(test,j),alarms[:,j])==r['results']['all2026'][version][name]['new']
    protected=read(D/'preservation-before.json')
    changed=[name for name,sha in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=sha]
    assert not changed,changed[:5]
    result=dict(earlier_fit_and_2024_only_selection=True,reserved_fragments_excluded=True,
        chronology_and_month_purge=True,control_2024_selected_alarms_metrics_identical=True,
        control_2026_alarms_identical=True,control_oof_score_max_abs_difference=control_differences,
        control_numerical_scores_identical=False,selection_reproduced=True,
        frozen_before_test=True,source_crop_hashes_checked=count,source_pairs_match_observations=True,
        all_five_frozen_variants_replayed=True,unchanged_cohort_pairs=499,
        point_and_original_detector_identical=True,protected_files=len(protected),changed_files=changed,
        production_promoted=False,frozen_sha256=r['frozen_sha256'])
    dump(D/'verification.json',result);print(result)
