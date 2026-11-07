"""2024-only, capacity-constrained rescue of frozen spectral ranking cases.

Ranks use issue-time inputs and already causal OOF scores. Outcomes are used
only after choices are made, for diagnostics and explicitly optimistic selection.
No visits, damage labels, production ranking, or old experiment is modified.
"""
from collections import defaultdict
import hashlib
import numpy as np
from . import spectral_triage as base
from .farm_context_data import ROOT, read, dump, digest

D = ROOT / 'data/farm-research/triage-retention-2024'


def variants():
    return [dict(id='baseline', kind='blend', alpha=0.),
            dict(id='extra', kind='blend', alpha=1.),
            *[dict(id=f'blend_{a:g}', kind='blend', alpha=a) for a in [.25,.5,.75]],
            *[dict(id=f'anchor_{n}', kind='anchor', anchors=n) for n in [1,2]],
            *[dict(id=f'protect_{t:g}_margin_{g:g}', kind='protect', threshold=t, margin=g)
              for t in [.10,.20] for g in [0.,.05]]]


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    files=[base.D/n for n in ['protocol.json','pool-2024.json','meta-oof.json',
                              'selection-table.json','frozen-selection.json']]
    files += [ROOT/'agro_checker/spectral_triage.py', ROOT/'agro_checker/triage_retention.py']
    p=dict(created_at_utc=base.stamp(),variants=variants(),budget_k=3,
        fixed_parameters=dict(seed=20241021),
        inputs={q.relative_to(ROOT).as_posix():digest(q) for q in files},
        source='Unchanged causal monthly OOF2024 scores; all699 issue-time candidates including unknown outcomes; no new fit of base models.',
        selection_year=2024,selection_status='Development reuse of2024, not an independent estimate after selecting settings.',
        holdout_ids=base.HOLDOUT,
        blend='Per-date average tied-rank percentiles of recent_drop and Extra, weights fixed in variants. Alpha0/1 exactly reproduce source order.',
        anchor='Keep first1/2 baseline candidates, fill remaining slots by Extra score; never exceed original min(3,N).',
        protect='Start at baseline. Protect its candidates with recent_drop>=.10/.20 weekly index units. Consider Extra outsiders in score order; replace weakest unprotected candidate only if Extra score advantage strictly exceeds0/.05. No future-label branch.',
        acceptance='Restore all four lost baselineTP, retain EVERY known baselineTP, knownTP>=originalExtra, knownFP<=BOTH references; paired worst-case negative growth<=0 against BOTH references; strictly positive date-balanced paired precision lower bound vs baseline. Same slots each date. Otherwise baseline retained with no claim of improvement.',
        unknown='None remains unknown, shared choices cancel. Missing-outcome scenarios are not confidence intervals; no assumption clouds missing at random.',
        tests='Do not load2025/2026 matrices or analyse their outcomes in this experiment. Hash-only preservation is allowed. Reserved2024 fragments excluded from tuning. No invented human labels or operational writes.',
        known_development_diagnosis='Four losses are R02/R17/R21 on2024-06-03 and R13 on2024-06-11. These IDs/dates/outcomes must never enter prediction rules.',
        deployment='Research-only. Preserve prior detectors, forecasts, intervals, UI and journals.')
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents:continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.joblib','.cbm','.csv']:continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def check_inputs(p):
    for name,sha in p['inputs'].items():
        if digest(ROOT/name)!=sha:raise ValueError('Changed frozen source: '+name)


def ordered(rows, scores, seed):
    return sorted(range(len(rows)),key=lambda i:(-scores[i],hashlib.sha256(f'{seed}:{rows[i]["fid"]}'.encode()).hexdigest()))


def percentiles(values):
    """Equal scores have equal percentile, independent of fragment identity."""
    x=np.asarray(values,float)
    if len(x)<=1:return np.ones(len(x))
    return np.array([(np.sum(x<v)+(np.sum(x==v)-1)/2)/(len(x)-1) for v in x])


def choose(rows, recent, extra, variant, p):
    """Only fid (ties), issue (round), scores. Never inspect y or target."""
    if variant not in p['variants']:raise ValueError('Unregistered rule')
    if len({base.key(r) for r in rows})!=len(rows):raise ValueError('Duplicate candidate')
    a=np.asarray(recent,float);b=np.asarray(extra,float)
    if a.shape!=(len(rows),) or b.shape!=a.shape or not np.isfinite([a,b]).all():raise ValueError('Invalid scores')
    result=np.zeros(len(rows),bool);rounds=defaultdict(list)
    for i,r in enumerate(rows):rounds[r['issue']].append(i)
    for ix in rounds.values():
        rr=[rows[i] for i in ix];aa=a[ix];bb=b[ix];k=min(p['budget_k'],len(ix));seed=p['fixed_parameters']['seed']
        old=ordered(rr,aa,seed);ml=ordered(rr,bb,seed)
        kind=variant['kind']
        if kind=='blend':
            alpha=variant['alpha']
            score=aa if alpha==0 else bb if alpha==1 else (1-alpha)*percentiles(aa)+alpha*percentiles(bb)
            selected=ordered(rr,score,seed)[:k]
        elif kind=='anchor':
            selected=old[:min(variant['anchors'],k)]
            selected += [i for i in ml if i not in selected][:k-len(selected)]
        elif kind=='protect':
            selected=old[:k];protected={i for i in selected if aa[i]>=variant['threshold']}
            # Replacement takes place in deterministic ML order, at most k times.
            # Fixed baseline anchors cannot be removed, no temporary extra slots.
            for candidate in ml:
                if candidate in selected:continue
                eligible=[i for i in reversed(ml) if i in selected and i not in protected]
                if not eligible:break
                removed=eligible[0]
                if bb[candidate]-bb[removed]>variant['margin']:
                    selected.remove(removed);selected.append(candidate)
        else:raise ValueError('Unknown rule family')
        result[[ix[i] for i in selected]]=True
    return result


def assess(rows,recent,extra,variant,p):
    base.only2024(rows)
    old=base.topk(rows,recent,p);ml=base.topk(rows,extra,p);new=choose(rows,recent,extra,variant,p)
    metrics=base.rank_metrics(rows,new,p);a=base.paired_metrics(rows,old,new);b=base.paired_metrics(rows,ml,new)
    ref=base.rank_metrics(rows,old,p);mref=base.rank_metrics(rows,ml,p)
    lost={base.key(r) for r in base.paired_metrics(rows,old,ml)['lost_cases']}
    picked={base.key(r) for r,t in zip(rows,new) if t}
    checks=dict(four_restored=lost<=picked,all_baseline_events_retained=a['lost_known_baseline_tp']==0,
        total_events_not_below_extra=metrics['tp']>=mref['tp'],
        known_negatives_not_above_either=metrics['fp']<=min(ref['fp'],mref['fp']),
        possible_negative_growth_vs_baseline=a['negative_growth_upper']<=0,
        possible_negative_growth_vs_extra=b['negative_growth_upper']<=0,
        strict_missing_robust_gain_vs_baseline=a['difference_lower']>1e-12)
    return dict(variant=variant,eligible=all(checks.values()),checks=checks,metrics=metrics,
        paired_baseline=a,paired_extra=b,restored_cases=[dict(fid=k[0],issue=k[1]) for k in sorted(lost&picked)],
        selected_keys=[list(base.key(r)) for r,t in zip(rows,new) if t])


def selection(rows,records,p):
    base.only2024(rows)
    recent=records['recent_drop'];extra=records['extra']
    table=[assess(rows,recent,extra,v,p) for v in p['variants']]
    options=[v for v in table if v['eligible']]
    winner=min(options,key=lambda e:(-e['paired_baseline']['difference_lower'],e['metrics']['fp'],p['variants'].index(e['variant']))) if options else table[0]
    return dict(variant=winner['variant'],enabled=bool(options),metrics=winner['metrics'],
                selected_keys=winner['selected_keys'],checks=winner['checks']),table


def diagnose(rows,records,p):
    base.only2024(rows);a=np.array(records['recent_drop']);b=np.array(records['extra'])
    old=base.topk(rows,a,p);new=base.topk(rows,b,p);yy=base.labels(rows)
    lost=[i for i in range(len(rows)) if old[i] and not new[i] and yy[i] is True]
    pool=read(base.D/'pool-2024.json');training=[r for r in pool if r['fid'] not in base.HOLDOUT and r['y'] is not None and r['target']<'2024-06-01']
    model=base.fit_model(training,'extra',read(base.D/'protocol.json'));forest=model['model']
    cases=[]
    for i in lost:
        r=rows[i];ix=[j for j,v in enumerate(rows) if v['issue']==r['issue']]
        ordered_old=ordered([rows[j] for j in ix],a[ix],p['fixed_parameters']['seed'])
        ordered_ml=ordered([rows[j] for j in ix],b[ix],p['fixed_parameters']['seed'])
        features=np.array(r['triage_features']);terms=np.zeros(len(features));roots=[]
        for tree in forest.estimators_:
            t=tree.tree_;node=0;root=t.value[0,0];roots.append(root[1]/root.sum())
            while t.children_left[node]!=t.children_right[node]:
                feature=t.feature[node];child=t.children_left[node] if features[feature]<=t.threshold[node] else t.children_right[node]
                before=t.value[node,0];after=t.value[child,0]
                terms[feature]+=after[1]/after.sum()-before[1]/before.sum();node=child
        terms/=len(forest.estimators_)
        if abs(np.mean(roots)+terms.sum()-b[i])>1e-10:raise ValueError('Diagnostic model differs from causal OOF')
        cases.append(dict(fid=r['fid'],issue=r['issue'],previous=r['previous'],target=r['target'],
            future_delta=(np.array(r['y'])-r['x'][:2]).tolist(),weekly_change=(np.array(r['x'][2:4])*7).tolist(),
            actual_past_delta=r['quality']['core_delta'],past_gap_days=r['x'][4],current=r['x'][:2],
            common_coverage=r['quality']['common_coverage'],all_boundaries_supported=r['quality']['all_boundaries_supported'],
            boundary_span=r['quality']['boundary_span'],baseline_rank=ordered_old.index(ix.index(i))+1,
            extra_rank=ordered_ml.index(ix.index(i))+1,extra_score=float(b[i]),
            forest_root=float(np.mean(roots)),path_contributions=dict(zip(base.FEATURES,terms.tolist())),
            selected_instead=[dict(fid=rows[j]['fid'],outcome=yy[j],score=float(b[j])) for j in ix if new[j]],
            operation=None,crop=None,human_visit=None))
    capacity=[]
    for issue in sorted({r['issue'] for r in cases}):
        ix=[i for i,r in enumerate(rows) if r['issue']==issue]
        pos_old={rows[i]['fid'] for i in ix if old[i] and yy[i] is True}
        pos_new={rows[i]['fid'] for i in ix if new[i] and yy[i] is True}
        capacity.append(dict(issue=issue,baseline_events=sorted(pos_old),extra_events=sorted(pos_new),
            union_size=len(pos_old|pos_new),budget=min(p['budget_k'],len(ix)),
            all_event_identities_possible=len(pos_old|pos_new)<=min(p['budget_k'],len(ix))))
    return dict(cases=cases,capacity=capacity,june_training=dict(rows=len(training),dates=len({r['issue'] for r in training}),
        positives=sum(base.labels(training)),positive_dates=len({r['issue'] for r,y in zip(training,base.labels(training)) if y})),
        scope='2024 diagnostics only. Tree path contributions explain scores algebraically, not agronomic causes.',
        source_issues=[r['issue'] for r in rows],no2025_2026_outcome_analysis=True)


def run():
    if (D/'frozen-selection.json').exists():raise ValueError('Frozen2024 experiment cannot be retuned')
    p=protocol();check_inputs(p);meta=read(base.D/'meta-oof.json');rows=meta['rows'];base.only2024(rows)
    records={r['method']:r['scores'] for r in meta['records']}
    diagnostics=diagnose(rows,records,p);dump(D/'diagnostics.json',diagnostics)
    selected,table=selection(rows,records,p);dump(D/'selection-table.json',table)
    dump(D/'frozen-selection.json',dict(created_at_utc=base.stamp(),selection=selected,
        hashes={n:digest(D/n) for n in ['protocol.json','diagnostics.json','selection-table.json']},
        code_sha256=digest(__file__),test_years_evaluated=[],new_human_labels=0,production_promoted=False))
    print('Frozen2024:',selected['variant'],'enabled',selected['enabled'])
    for e in table:print(e['variant']['id'],{k:e['metrics'][k] for k in ['tp','fp','unknown']},
        'restored',len(e['restored_cases']),'lost',e['paired_baseline']['lost_known_baseline_tp'],
        'worst_negative_growth',e['paired_baseline']['negative_growth_upper'],e['paired_extra']['negative_growth_upper'],e['eligible'])


def verify():
    p=read(D/'protocol.json');check_inputs(p);f=read(D/'frozen-selection.json')
    if digest(__file__)!=f['code_sha256']:raise ValueError('Changed frozen code')
    for name,sha in f['hashes'].items():
        if digest(D/name)!=sha:raise ValueError('Changed frozen result')
    meta=read(base.D/'meta-oof.json');rows=meta['rows'];base.only2024(rows)
    records={r['method']:r['scores'] for r in meta['records']};s,t=selection(rows,records,p)
    assert s==f['selection'] and t==read(D/'selection-table.json')
    assert diagnose(rows,records,p)==read(D/'diagnostics.json')
    poisoned=[dict(r,y=[999,999],target='2099-01-01') for r in rows]
    for variant in p['variants']:
        a=choose(rows,records['recent_drop'],records['extra'],variant,p)
        np.testing.assert_array_equal(a,choose(poisoned,records['recent_drop'],records['extra'],variant,p))
        assert base.rank_metrics(rows,a,p)['slots']==133
    preserved=read(D/'preservation-before.json');changed=[n for n,sha in preserved.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    assert not changed,changed[:5]
    out=dict(selection_reproduced=True,diagnosis_reproduced=True,identical699_candidates=True,
        settings2024_only=True,outcome_poisoning_does_not_change_ranking=True,four_case_identities_verified=True,
        same133_slots=True,protected_files=len(preserved),changed_files=changed,test_years_evaluated=[],
        source_oof2024_frozen=True,new_human_labels=0,production_promoted=False,frozen_sha256=digest(D/'frozen-selection.json'))
    dump(D/'verification.json',out);print(out)
    return out
