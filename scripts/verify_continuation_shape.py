"""Reproduce selection, cohort, immutable base and exact found-case identities."""
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker import continuation_shape as m
from agro_checker.farm_context_data import read,dump,digest


if __name__=='__main__':
    p=read(m.D/'protocol.json');f=read(m.D/'frozen-selection.json');report=read(m.D/'report.json')
    m.first.prior.check_inputs(p)
    assert digest(ROOT/'agro_checker/continuation_shape.py')==f['code_sha256']
    assert digest(m.D/'frozen-selection.json')==report['frozen_sha256']
    assert p['created_at_utc']<f['created_at_utc']<report['created_at_utc']
    for name,sha in f['hashes'].items():assert digest(m.D/name)==sha
    # The earlier causal-filter experiment remains frozen, with NO2026 evaluation.
    first=m.first;fp=read(first.D/'protocol.json');ff=read(first.D/'frozen-selection.json')
    assert not (first.D/'report.json').exists()
    assert digest(ROOT/'agro_checker/continuation_recovery.py')==ff['code_sha256']
    for name,sha in ff['hashes'].items():assert digest(first.D/name)==sha
    meta=read(m.D/'meta-oof.json');oldmeta=read(first.D/'meta-oof.json')
    for module,protocol,meta_file,frozen in [(first,fp,oldmeta,ff),(m,p,meta,f)]:
        rows=read(module.D/'data-2024.json');m.selection2024(rows)
        assert len(rows)==len({m.key(r) for r in rows})==frozen['fit_pairs']
        assert [list(m.key(r)) for r in rows]==frozen['fit_keys']
        for a in meta_file['audits']:assert a['fit_max_target']<a['start']
        selected,table=first.choose(meta_file['rows'],meta_file['records'],protocol)
        assert selected==frozen['selected'] and table==read(module.D/'selection-table.json')
        for r in rows:
            audit=r['feature_audit'];assert audit['latest_input']==r['issue']
            assert r['target_scene'] not in audit['input_scenes']
            if audit['third_date']:
                assert r['previous']>audit['third_date']
                assert 2<=(m.first.day(r['previous'])-m.first.day(audit['third_date'])).days<=21
    assert {m.key(r) for r in meta['rows']}=={m.key(r) for r in oldmeta['rows']}
    assert len(meta['rows'])==274 and len({r['issue'] for r in meta['rows']})==25
    # Base score inputs are absent from BOTH direct classifier feature sets.
    rr=read(m.D/'data-2024.json');poisoned=read(m.D/'data-2024.json')
    for r in poisoned:r['raw_score']=[0,1];r['continuation'][18:20]=[-1e6,1e6]
    for family in m.FAMILIES[1:]:np.testing.assert_array_equal(m.matrix(rr,family),m.matrix(poisoned,family))
    for name in ['features-2024-audit.json','evaluation-feature-audit-2024.json','evaluation-feature-audit-2026.json']:
        audit=read(m.D/name)
        for path,sha in audit['pixel_hashes'].items():assert digest(ROOT/path)==sha
    previous={m.key(r):r for r in read(m.first.prior.D/'test-predictions.json') if r['split']=='all2026'}
    predictions=[r for r in read(m.D/'test-predictions.json') if r['split']=='all2026']
    assert len(predictions)==len(previous)==len({m.key(r) for r in predictions})==499
    rows=read(m.D/'evaluation-features-2026.json');lookup={m.key(r):i for i,r in enumerate(rows)}
    fresh=m.infer(rows,p,f)
    for r in predictions:
        old=previous[m.key(r)];i=lookup[m.key(r)]
        for name in ['raw','point','actual','persistence','ridge_alarm','baseline_alarm']:
            np.testing.assert_allclose(r[name],old[name],rtol=0,atol=1e-12)
        np.testing.assert_array_equal(r['filtered_alarm'],fresh['filtered'][i])
        np.testing.assert_allclose(r['keep_score'],fresh['keep_score'][i],rtol=0,atol=1e-12)
        assert not np.any(np.array(r['filtered_alarm'])&~np.array(r['baseline_alarm']))
    retention={}
    for j,name in enumerate(m.NAMES):
        before={m.key(r) for r in predictions if r['baseline_alarm'][j] and r['actual'][j]-r['persistence'][j]<=-.1}
        after={m.key(r) for r in predictions if r['filtered_alarm'][j] and r['actual'][j]-r['persistence'][j]<=-.1}
        assert len(before-after)==report['results']['all2026'][name]['new']['lost_tp']
        retention[name]=dict(baseline=len(before),retained=len(before&after),lost=len(before-after))
    protected=read(m.D/'preservation-before.json')
    changed=[n for n,sha in protected.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    assert not changed,changed[:5]
    out=dict(only2024_fit_and_selection=True,monthly_target_purge_verified=True,spatial_reserve_excluded=True,
        same274_validation_keys=True,full_season489_pairs=True,direct_model_has_no_base_score=True,
        both_selection_tables_reproduced=True,past_only_scene_requests_verified=True,pixel_hashes_verified=True,
        original499_cohort_and_base_point_ridge_preserved=True,case_retention=retention,
        rejected_candidates_not_evaluated_on2026=True,earlier_stage_not_evaluated_on2026=True,
        protected_files=len(protected),changed_files=changed,production_promoted=False,frozen_sha256=report['frozen_sha256'])
    dump(m.D/'verification.json',out);print(out)
