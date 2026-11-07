"""Reproduce2024 guards, exact exchanges and prior-only monthly settings."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
import numpy as np
from agro_checker import ndmi_turn_guard as m
from agro_checker.farm_context_data import read,dump,digest


if __name__=='__main__':
    p=read(m.D/'protocol.json');m.check_inputs(p);f=read(m.D/'frozen-selection.json')
    assert p['created_at_utc']<f['created_at_utc'] and digest(ROOT/'agro_checker/ndmi_turn_guard.py')==f['code_sha256']
    for name,sha in f['hashes'].items():assert digest(m.D/name)==sha
    rows,records=m.dataset();m.base.only2024(rows)
    assert len(rows)==699 and sum(r['y'] is not None for r in rows)==368
    excluded={m.base.key(r) for r in read(m.evidence.D/'recovery-ledger.json') if r['reason']=='additional_scene_restored'}
    assert len(excluded)==3 and all(r['y'] is None for r in rows if m.base.key(r) in excluded)
    table,choices=m.compare(rows,records,p);assert table==read(m.D/'selection-table.json')
    assert m.pick(table,p)==f['selected_variant']
    old=m.evidence.fixed_choices(rows)
    for name,new in [('baseline',choices['baseline']),('extra',choices['extra']),('hybrid',choices['old_hybrid'])]:np.testing.assert_array_equal(old[name],new)
    assert m.diagnostic(rows,records,p)==read(m.D/'diagnostics.json')
    ff,audits=m.forward(rows,records,p);saved=read(m.D/'forward-check.json')
    refs={n:choices[n] for n in ['baseline','extra','old_hybrid']}
    assert audits==saved['audits'] and m.assessment(rows,ff,refs,p)==saved['result']
    assert [list(m.base.key(r)) for r,c in zip(rows,ff) if c]==saved['selected_keys']
    assert all(a['max_known_target'] is None or a['max_known_target']<a['start'] for a in audits)
    for a in audits:
        assert a['prior_pool']==sum(r['issue']<a['start'] for r in rows)
        assert a['prior_known']==sum(r['issue']<a['start'] and r['y'] is not None and r['target']<a['start'] for r in rows)
    poisoned=[dict(r,y=[999,-999],target='2099-01-01',target_scene='future') for r in rows]
    for v in p['variants']:
        a=m.choose(rows,records['recent_drop'],records['extra'],v,p)
        np.testing.assert_array_equal(a,m.choose(poisoned,records['recent_drop'],records['extra'],v,p))
        assert m.base.rank_metrics(rows,a,p)['slots']==133
        for issue in {r['issue'] for r in rows}:
            assert sum(c for r,c in zip(rows,a) if r['issue']==issue)==min(3,sum(r['issue']==issue for r in rows))
    candidate=choices[f['selected_variant']['id']];before=choices['old_hybrid'];labels=m.base.labels(rows)
    exchanged=[]
    for r,a,b,y in zip(rows,before,candidate,labels):
        if a!=b:exchanged.append(dict(fid=r['fid'],issue=r['issue'],old_selected=bool(a),new_selected=bool(b),outcome=y))
    # The uncertainty cancellation is checked by case identity, not by equal totals.
    same_unknown={m.base.key(r) for r,a,y in zip(rows,before,labels) if a and y is None}=={m.base.key(r) for r,a,y in zip(rows,candidate,labels) if a and y is None}
    dump(m.D/'exchange-audit.json',dict(exchanged=exchanged,unknown_selected_identities_equal=same_unknown,
        explanation='Known2024 exchanges only; does not establish generalization or usefulness of real visits.'))
    research=read(m.D/'research-choices-2024.json')
    assert research['variant']==f['selected_variant'] and research['tasks_created']==0
    assert [(x['fid'],x['issue'],x['selected'],x['outcome']) for x in research['rows']]==[(r['fid'],r['issue'],bool(c),y) for r,c,y in zip(rows,candidate,labels)]
    preserved=read(m.D/'preservation-before.json');changed=[n for n,sha in preserved.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    assert not changed,changed[:5]
    out=dict(frozen_sha256=digest(m.D/'frozen-selection.json'),full699_and368_known_conservative_cohort=True,
        all3_additional_tile_outcomes_stay_unknown=True,source_references_unchanged=True,
        all11_guard_rankings_reproduced=True,same133_positions=True,diagnostic_classifier_paths_reproduced=True,
        forward_prior_only_monthly_selection_reproduced=True,future_label_poisoning_does_not_affect_ranking=True,
        exact_exchange_and_unknown_identity_audit=True,protected_files=len(preserved),changed_files=changed,
        test_years_evaluated=[],production_promoted=False,human_visits_created=0)
    dump(m.D/'verification.json',out);print(out)
