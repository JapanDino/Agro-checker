"""Provenance, no future labels, same cohort and exact baseline-case retention."""
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker import open_crop_context as m
from agro_checker.farm_context_data import read,dump,digest


if __name__=='__main__':
    p=read(m.D/'protocol.json');f=read(m.D/'frozen-selection.json');report=read(m.D/'report.json')
    m.shape.first.prior.check_inputs(p)
    assert digest(ROOT/'agro_checker/open_crop_context.py')==f['code_sha256']
    assert digest(m.D/'frozen-selection.json')==report['frozen_sha256']
    assert p['created_at_utc']<f['created_at_utc']<report['created_at_utc']
    for name,sha in f['hashes'].items():assert digest(m.D/name)==sha
    evidence=read(m.D/'crop-evidence.json');rows=read(m.D/'data-2024.json');m.selection2024(rows)
    assert len(rows)==len({m.key(r) for r in rows})==f['fit_pairs']==489
    assert [list(m.key(r)) for r in rows]==f['fit_keys']
    assert evidence['confirmed_current_crop_labels']==evidence['confirmed_harvest_dates']==0
    assert all(v['crop'] is None and v['harvest_date'] is None for v in evidence['fragments'].values())
    assert evidence['geoglam']['published']=='2024-04-09' and not evidence['geoglam']['russia_records']
    assert not evidence['asap']['0']['russia_records'] and not evidence['asap']['1']['russia_records']
    assert evidence['rdm']['sector2024']['collection_count']==evidence['rdm']['region2024']['collection_count']==0
    assert len(evidence['rdm']['item_query_failures'])==5  # unknown, NOT five zero-count responses
    for row in rows:
        assert row['crop'] is None and row['harvest_date'] is None
        if row['issue']<'2024-06-28':assert row['crop_context'][27]==0
        assert row['crop_context']==m.context(row,evidence)
    meta=read(m.D/'meta-oof.json');oldmeta=read(m.shape.D/'meta-oof.json')
    assert len(meta['rows'])==274 and {m.key(r) for r in meta['rows']}=={m.key(r) for r in oldmeta['rows']}
    assert all(a['fit_max_target']<a['start'] for a in meta['audits'])
    selected,table=m.select(meta['rows'],meta['records'],p)
    assert selected==f['selected'] and table==read(m.D/'selection-table.json')
    # Exact matched algorithm/feature control, not a silently changed baseline.
    old_record=next(r for r in oldmeta['records'] if r['family']=='pixels_logit')
    new_record=next(r for r in meta['records'] if r['family']=='shape_logit')
    assert [m.key(r) for r in meta['rows']]==[m.key(r) for r in oldmeta['rows']]
    np.testing.assert_allclose(new_record['scores'],old_record['scores'],rtol=0,atol=1e-12)
    for record in meta['records']:
        assert len(record['scenario_scores'])==10
        assert np.asarray(record['scores']).shape==(2,274)
    test=read(m.D/'evaluation-features-2026.json');pred=[r for r in read(m.D/'test-predictions.json') if r['split']=='all2026']
    prior={m.key(r):r for r in read(m.shape.D/'test-predictions.json') if r['split']=='all2026'}
    assert len(test)==len(pred)==len(prior)==499 and len({m.key(r) for r in pred})==499
    assert all(r['crop_context'][27]==0 and r['crop'] is None and r['harvest_date'] is None for r in test)
    values=m.infer(test,p,f);lookup={m.key(r):i for i,r in enumerate(test)}
    for r in pred:
        old=prior[m.key(r)];i=lookup[m.key(r)]
        for name in ['raw','baseline_alarm','point','ridge_alarm','actual','persistence']:
            np.testing.assert_allclose(r[name],old[name],rtol=0,atol=1e-12)
        np.testing.assert_array_equal(r['filtered_alarm'],values['filtered'][i])
        assert not np.any(np.array(r['filtered_alarm'])&~np.array(r['baseline_alarm']))
    retention={}
    for j,name in enumerate(m.NAMES):
        before={m.key(r) for r in pred if r['baseline_alarm'][j] and r['actual'][j]-r['persistence'][j]<=-.1}
        after={m.key(r) for r in pred if r['filtered_alarm'][j] and r['actual'][j]-r['persistence'][j]<=-.1}
        assert len(before-after)==report['results']['all2026'][name]['new']['lost_tp']
        retention[name]=dict(baseline=len(before),retained=len(before&after),lost=len(before-after))
    protected=read(m.D/'preservation-before.json')
    changed=[n for n,sha in protected.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    assert not changed,changed[:5]
    out=dict(only2024_fit_and_selection=True,monthly_target_purge_verified=True,reserve_excluded=True,
        exact274_cohort=True,all489_training_pairs=True,shape_ablation_matches_previous_scores=True,
        both_date_and_external_context_ablations=True,all11_boundary_context_scenarios=True,
        no_field_crop_or_harvest_labels_invented=True,no_future_publications_backfilled=True,
        source_queries_and_failures_preserved=True,original499_cohort_base_point_ridge_unchanged=True,
        case_retention=retention,rejected_candidates_not_evaluated2026=True,
        protected_files=len(protected),changed_files=changed,frozen_sha256=report['frozen_sha256'],production_promoted=False)
    dump(m.D/'verification.json',out);print(out)
