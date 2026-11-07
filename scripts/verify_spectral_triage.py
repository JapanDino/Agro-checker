"""Reproduce full pools, unknown handling,2024 ordering and frozen comparisons."""
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker import spectral_triage as m
from agro_checker.farm_context_data import read,dump,digest


if __name__=='__main__':
    p=read(m.D/'protocol.json');f=read(m.D/'frozen-selection.json');r=read(m.D/'report.json');m.previous.check_inputs(p)
    assert digest(ROOT/'agro_checker/spectral_triage.py')==f['code_sha256']
    assert digest(m.D/'frozen-selection.json')==r['frozen_sha256']
    assert p['created_at_utc']<f['created_at_utc']<r['created_at_utc']
    for name,sha in f['hashes'].items():assert digest(m.D/name)==sha
    obs=read(m.INDEX/'observations.json')['rows'];pools={}
    for year in [2024,2026]:
        pool=read(m.D/f'pool-{year}.json');audit=read(m.D/f'pool-audit-{year}.json');pools[year]=pool
        original=[x for x in obs if x['date'][:4]==str(year)]
        inputs=m.issue_pool(original)
        assert [m.key(x) for x in inputs]==[m.key(x) for x in pool]
        assert audit['pool_keys']==[list(m.key(x)) for x in pool]
        assert len(pool)==len({m.key(x) for x in pool})==audit['pool_size']
        pairs=m.make_pairs(original);old={m.key(x):x for x in pairs}
        assert len(old)==audit['known_outcomes']
        # Exact old matched-response cohort preserved inside the larger pool.
        crop=read(ROOT/f'data/farm-research/crop-season-model/data-{year}.json')
        assert set(old)=={m.key(x) for x in crop}
        for row in pool:
            assert row['input_audit']['max_history_date']<=row['issue']
            assert row['input_audit']['max_input_date']==row['issue']
            assert row['input_audit']['scenes']==[row['previous_scene'],row['issue_scene']]
            if row['target_scene']:assert row['target_scene'] not in row['input_audit']['scenes']
            if m.key(row) in old:
                np.testing.assert_array_equal(row['y'],old[m.key(row)]['y'])
                assert row['target']==old[m.key(row)]['target']
            else:assert row['y'] is None and row['target'] is None
        for path,sha in audit['pixel_hashes'].items():assert digest(ROOT/path)==sha
    meta=read(m.D/'meta-oof.json');selection,table=m.select(meta['rows'],meta['records'],p)
    assert selection==f['selected'] and table==read(m.D/'selection-table.json')
    training=[x for x in pools[2024] if x['fid'] not in m.HOLDOUT and x['y'] is not None]
    m.only2024(training,require_known=True)
    assert len(training)==f['fit_pairs']==489
    assert [list(m.key(x)) for x in training]==f['fit_keys']
    # Recompute each fixed fold with only completed2024 outcomes. Never save/refit final model.
    reproduced={method:[] for method in m.METHODS}
    before=[]
    for start in p['folds']:
        tt=[x for x in training if x['target']<start]
        vv=[x for x in meta['rows'] if x['issue'][:7]==start[:7]]
        if not tt or not vv:continue
        for method in m.METHODS:
            model=m.fit_model(tt,method,p);reproduced[method].extend(m.score(model,vv).tolist())
        assert max(x['target'] for x in tt)<start
        end=m.date(m.day(start).year+m.day(start).month//12,m.day(start).month%12+1,1).isoformat()
        assert all(x['y'] is None or x['target']<end for x in vv)
        expected=[x for x in pools[2024] if x['fid'] not in m.HOLDOUT and start<=x['issue']<end]
        assert [m.key(x) for x in vv]==[m.key(x) for x in expected]
    for record in meta['records']:np.testing.assert_allclose(record['scores'],reproduced[record['method']],rtol=0,atol=1e-12)
    predictions=read(m.D/'test-predictions.json')
    for split in r['results']:
        for method,result in r['results'][split].items():
            chosen=[x for x in predictions if x['split']==split and x['method']==method and x['chosen']]
            assert len(chosen)==result['metrics']['slots']
            assert sum(x['outcome'] is None for x in chosen)==result['metrics']['unknown']
            assert sum(x['outcome'] is True for x in chosen)==result['metrics']['tp']
            assert sum(x['outcome'] is False for x in chosen)==result['metrics']['fp']
            assert result['metrics']['slots']==sum(result['metrics'][k] for k in ['tp','fp','unknown'])
    assert {x['method'] for x in predictions}=={'recent_drop','robust_change','selected'}
    protected=read(m.D/'preservation-before.json')
    changed=[n for n,sha in protected.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    assert not changed,changed[:5]
    out=dict(full_issue_time_pools_reproduced=True,unknown_future_not_used_to_filter_candidates=True,
        original573_and499_response_keys_and_labels_preserved=True,known_fit_pairs2024=489,
        input_date_and_scene_audit_verified=True,pixel_hashes_verified=True,selection_and_all_oof_scores_reproduced=True,
        no2025_2026_fit_selection=True,spatial_reserve_excluded=True,monthly_target_purge_preserves_pool=True,
        same_budgets_and_unknown_denominators=True,only_frozen_choice_and_fixed_rules_evaluated2026=True,
        protected_files=len(protected),changed_files=changed,real_inspection_labels=0,production_promoted=False,
        frozen_sha256=r['frozen_sha256'])
    dump(m.D/'verification.json',out);print(out)
