"""Verify chronological fit, frozen choice, controls and all evaluated predictions."""
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.season_version_decline import D,EARLY,SOURCE,LEGACY,PRIOR,HOLDOUT,NAMES,key,check_inputs,infer,select2024
from agro_checker.field_decline_experiment import chronological_folds,confusion,events


if __name__=='__main__':
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');r=read(D/'report.json');check_inputs(p)
    assert digest(D/'frozen-selection.json')==r['frozen_sha256']
    assert digest(D/'protocol.json')==f['protocol_sha256']
    assert digest(ROOT/'agro_checker/season_version_decline.py')==f['code_sha256']==r['evaluation_code_sha256']
    for file,field in [('selection-table.json','selection_sha256'),('oof-predictions.json','oof_sha256'),
        ('fit-audits.json','audits_sha256'),('diagnosis-2024.json','diagnosis_sha256')]:
        assert digest(D/file)==f[field]
    assert f['created_at_utc']<r['created_at_utc']
    oo=read(D/'oof-predictions.json')
    primary,choices,table=select2024(oo['rows'],oo['records'],p)
    assert primary==f['primary'] and choices==f['choices'] and table==read(D/'selection-table.json')
    for j,group in enumerate(choices):
        eligible=[( (-(c['metrics']['date_balanced_recall'] or 0),-c['metrics']['tp_dates'],-c['metrics']['tp'],order),c)
            for order,c in enumerate(group) if c['enabled'] and c['regime'] not in ['2024_only','raw_pool']]
        if eligible:assert f['best_adapted'][j]==min(eligible,key=lambda v:v[0])[1]
        else:assert not f['best_adapted'][j]['enabled']
    current=[v for v in read(SOURCE) if v['fid'] not in HOLDOUT]
    early=[v for v in read(EARLY) if v['fid'] not in HOLDOUT]
    assert all(v['issue'][:4]<'2024' for v in early)
    folds=list(chronological_folds(current,p['folds']))
    assert [key(v) for _,_,vv in folds for v in vv]==[key(v) for v in oo['rows']]
    for a in read(D/'fit-audits.json'):
        assert a['fit_max_target']<a['start']<=a['validation_min_issue']
        spec=next(c for c in p['candidates'] if c['id']==a['candidate'])
        w=a['weight_info']
        if spec['balanced']:
            assert abs(w['source_weight_share']-.25)<1e-12
            assert abs(w['total_weight']-w['target_pairs'])<1e-9
        if w['overlap']:
            assert w['overlap']['fit_max_target']<a['start']
            assert w['overlap']['min_source']>=.1 and w['overlap']['max_source']<=10
    for identifier,entry in f['models'].items():
        assert digest(ROOT/entry['path'])==entry['sha256']
        assert all(k[0] not in HOLDOUT and k[1][:4]<='2024' and k[2][:4]==k[1][:4] for k in entry['fit_keys'])
        assert len(entry['fit_keys'])==len(set(map(tuple,entry['fit_keys'])))
    # Prior early-season controls reproduced, including their exact weight arithmetic.
    prior=read(PRIOR/'oof-predictions.json')
    assert [key(v) for v in prior['rows']]==[key(v) for v in oo['rows']]
    for here,there in [('2024_only','2024_only'),('raw_pool','all_early_plus_2024')]:
        a=next(v for v in oo['records'] if v['regime']==here)
        b=next(v for v in prior['records'] if v['regime']==there)
        np.testing.assert_allclose(a['scores'],b['scores'],rtol=0,atol=1e-12)
    oldrows=[v for v in read(ROOT/'data/farm-research/field-index-model/predictions.json') if v['split'] in ['temporal2026','spatial2026']]
    old={key(v):v for v in oldrows}
    pred=read(D/'test-predictions.json')
    test=read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    index={key(v):i for i,v in enumerate(test)}
    for version in f['evaluations']:
        subset=[v for v in pred if v['split']=='all2026' and v['version']==version]
        assert len(subset)==len({key(v) for v in subset})==len(old)==499
        assert {key(v) for v in subset}==old.keys()
        alarm,scores,point,legacy=infer(test,p,f,version)
        for v in subset:
            a=old[key(v)];i=index[key(v)]
            np.testing.assert_allclose(v['point'],a['prediction'],rtol=0,atol=1e-12)
            np.testing.assert_array_equal(v['actual'],a['actual'])
            np.testing.assert_array_equal(v['persistence'],a['persistence'])
            np.testing.assert_array_equal(v['legacy_alarm'],np.array(a['prediction'])-a['persistence']<=-.10)
            np.testing.assert_array_equal(v['new_alarm'],alarm[i])
        for j,name in enumerate(NAMES):
            assert confusion(test,events(test,j),alarm[:,j])==r['results']['all2026'][version][name]['new']
    protected=read(D/'preservation-before.json')
    changed=[name for name,sha in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=sha]
    assert not changed,changed[:5]
    result=dict(selection_2024_only=True,source_labels_before2024=True,reserve_excluded=True,
        chronological_purge=True,source_weight_cap_verified=True,no_validation_domain_fit=True,
        primary_and_best_adaptation_reproduced=True,controls_oof_match=True,
        four_frozen_evaluations_replayed=True,unchanged_cohort_pairs=499,
        point_original_detector_and_state_preserved=True,protected_files=len(protected),changed_files=changed,
        production_promoted=False,frozen_sha256=r['frozen_sha256'])
    dump(D/'verification.json',result);print(result)
