"""Separate 2024 development study of input-only guards for triage replacement.

The September losses motivated these hypotheses: not an independent validation.
No IDs, future outcomes or calendar-specific branches enter ranking.
"""
from collections import defaultdict
from datetime import date
import numpy as np
from . import triage_outcomes as evidence
from .farm_context_data import ROOT,read,dump,digest

base=evidence.base
retention=evidence.prior
D=ROOT/'data/farm-research/ndmi-turn-guard-2024'


def variants():
    return [dict(id='baseline',kind='reference'),dict(id='extra',kind='reference'),dict(id='old_hybrid',kind='reference'),
        *[dict(id=f'weak_round_{t:g}',kind='weak_round',threshold=t) for t in [.05,.10,.20]],
        dict(id='divergent',kind='divergent'),dict(id='divergent_negative_ndmi',kind='divergent_negative'),
        dict(id='divergent_low_ndvi',kind='divergent_low',ndvi_max=.30),
        dict(id='weak_divergent_round',kind='weak_divergent_round',threshold=.10),
        dict(id='margin_0.1',kind='margin',margin=.10)]


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    files=[base.D/'meta-oof.json',base.D/'pool-2024.json',base.D/'protocol.json',
        base.INDEX/'observations.json',evidence.D/'frozen-evaluation.json',evidence.D/'report.json',
        evidence.D/'recovery-ledger.json',retention.D/'protocol.json',retention.D/'candidate-comparison-2024.json',
        ROOT/'agro_checker/ndmi_turn_guard.py',ROOT/'agro_checker/triage_retention.py',
        ROOT/'agro_checker/triage_outcomes.py',ROOT/'agro_checker/spectral_triage.py']
    p=dict(created_at_utc=base.stamp(),inputs={q.relative_to(ROOT).as_posix():digest(q) for q in files},
        variants=variants(),budget_k=3,fixed_parameters=dict(seed=20241021),
        primary_labels='Original known and94 month-boundary outcomes only:368 known/331 unknown on unchanged699 candidates. All3 additional-tile responses remain UNKNOWN, including cloud-mask conflict. Do not retune quality or recovery.',
        references='Fixed baseline, Extra and old protect_0.2_margin_0; causal monthly OOF2024 scores unchanged.',
        design='Three Sep29 losses motivate guards. This is2024 development reuse, not independent evidence. No field/date-ID-specific rescue.',
        divergence='Input pair ONLY: NDVI slope<0, NDMI slope>0; all11 boundary common-pixel deltas supported, max NDVI delta<0 and min NDMI delta>0.',
        rules='Keep old strong baseline anchors (weekly decline>=.20). Weak-round guard protects entire baseline when round maximum recent_drop<threshold. Divergence variants protect baseline rows only, optionally currentNDMI<=0 and NDVI<=.30. Weak-divergent-round requires weak max<.10 AND all baseline choices divergent. Margin rule requires Extra score gain>.10. Same3slots, deterministic hash ties.',
        repair_acceptance='Retain EVERY known baselineTP (includes June4 and Sep3), TP>=old_hybrid, knownFP<=both baseline and old_hybrid, paired worst-case negative growth vs old_hybrid<=0 and strict paired precision lower difference vs old_hybrid>0. Counts/retention refer to identities where stated; do NOT require all old_hybridTP identities when round capacity makes union impossible.',
        global_acceptance='Repair must also have worst-case negative growth vs baseline<=0 and strict paired lower gain vs baseline>0. No operational promotion even if development gate passes.',
        selection='Fixed2024 table only. Prefer maximum lower paired gain vs old hybrid, then fewer known negatives, then listed order. Default old_hybrid if no repair qualifies. Report global gate separately; never silently replace it.',
        forward_check='June-Oct2024: choose variant at month start using only prior ISSUE pools with target<monthstart labels; keep every prior candidate, mask unavailable targets UNKNOWN. >=20 known rows/5 issue dates otherwise old_hybrid. Apply fixed repair gate on past pools, same selection order. Never tune on current month or drop unknowns. Design still informed by September, not a blind validation.',
        test_policy='No2025/2026 matrix/outcome analysis; no refit of original point models, detectors, base classifiers or intervals.',
        labels_are='Future spectral index changes only, not inspection usefulness, crop damage or irrigation/disease diagnosis.',
        human_visits_created=0,production_promoted=False)
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
        if digest(ROOT/name)!=sha:raise ValueError('Frozen source changed: '+name)


def dataset():
    meta=read(base.D/'meta-oof.json');rows,_=evidence.extend(meta['rows'],read(base.D/'pool-2024.json'),[],[])
    base.only2024(rows)
    return rows,{r['method']:np.array(r['scores']) for r in meta['records']}


def divergent(row):
    q=row['quality'];d=q.get('boundary_delta',{})
    return bool(row['x'][2]<0 and row['x'][3]>0 and q['all_boundaries_supported'] and set(d)==set(base.SCENARIOS)
        and max(v[0] for v in d.values())<0 and min(v[1] for v in d.values())>0)


def choose(rows,recent,extra,v,p,trace=False):
    """Whitelisted issue-time inputs. Outcomes/target metadata never read here."""
    if v not in p['variants']:raise ValueError('Unregistered variant')
    if len({base.key(r) for r in rows})!=len(rows):raise ValueError('Duplicate candidate')
    a=np.asarray(recent,float);b=np.asarray(extra,float)
    if a.shape!=(len(rows),) or b.shape!=a.shape or not np.isfinite([a,b]).all():raise ValueError('Invalid scores')
    if v['id']=='baseline':return (base.topk(rows,a,p),[]) if trace else base.topk(rows,a,p)
    if v['id']=='extra':return (base.topk(rows,b,p),[]) if trace else base.topk(rows,b,p)
    grouped=defaultdict(list);selected=np.zeros(len(rows),bool);audit=[]
    for i,r in enumerate(rows):grouped[r['issue']].append(i)
    for issue,ix in grouped.items():
        rr=[rows[i] for i in ix];aa=a[ix];bb=b[ix];seed=p['fixed_parameters']['seed'];k=min(p['budget_k'],len(ix))
        old=retention.ordered(rr,aa,seed);ml=retention.ordered(rr,bb,seed);picks=old[:k]
        protected={i for i in picks if aa[i]>=.20};flags=[divergent(r) for r in rr]
        kind=v['kind'];whole_round=False
        if kind=='weak_round':whole_round=max(aa)<v['threshold']
        if kind=='weak_divergent_round':whole_round=max(aa)<v['threshold'] and all(flags[i] for i in picks)
        if whole_round:protected.update(picks)
        for i in picks:
            flag=flags[i]
            if kind in ['divergent_negative','divergent_low']:flag=flag and rr[i]['x'][1]<=0
            if kind=='divergent_low':flag=flag and rr[i]['x'][0]<=v['ndvi_max']
            if kind in ['divergent','divergent_negative','divergent_low'] and flag:protected.add(i)
        swaps=[];margin=v.get('margin',0.)
        for candidate in ml:
            if candidate in picks:continue
            replaceable=[i for i in reversed(ml) if i in picks and i not in protected]
            if not replaceable:break
            removed=replaceable[0];gap=bb[candidate]-bb[removed]
            if gap>margin:
                swaps.append(dict(removed=rr[removed]['fid'],added=rr[candidate]['fid'],score_gain=float(gap)))
                picks.remove(removed);picks.append(candidate)
        selected[[ix[i] for i in picks]]=True
        audit.append(dict(issue=issue,baseline=[rr[i]['fid'] for i in old[:k]],
            protected=[rr[i]['fid'] for i in old[:k] if i in protected],whole_round_guard=whole_round,
            swaps=swaps,selected=[rr[i]['fid'] for i in picks]))
    return (selected,audit) if trace else selected


def assessment(rows,chosen,reference,p):
    metrics=base.rank_metrics(rows,chosen,p)
    paired={name:base.paired_metrics(rows,choice,chosen) for name,choice in reference.items()}
    old=base.rank_metrics(rows,reference['old_hybrid'],p);simple=base.rank_metrics(rows,reference['baseline'],p)
    a=paired['baseline'];b=paired['old_hybrid']
    checks=dict(all_baseline_events_retained=a['lost_known_baseline_tp']==0,
        total_events_not_below_old_hybrid=metrics['tp']>=old['tp'],
        known_negatives_not_above_either=metrics['fp']<=min(old['fp'],simple['fp']),
        possible_negative_growth_vs_old_hybrid=b['negative_growth_upper']<=0,
        strict_paired_gain_vs_old_hybrid=b['difference_lower'] is not None and b['difference_lower']>1e-12)
    global_checks=dict(possible_negative_growth_vs_baseline=a['negative_growth_upper']<=0,
        strict_paired_gain_vs_baseline=a['difference_lower'] is not None and a['difference_lower']>1e-12)
    return dict(metrics=metrics,paired=paired,checks=checks,global_checks=global_checks,
        repair_passed=all(checks.values()),global_passed=all(checks.values()) and all(global_checks.values()))


def compare(rows,records,p):
    base.only2024(rows);choices={v['id']:choose(rows,records['recent_drop'],records['extra'],v,p) for v in p['variants']}
    refs={n:choices[n] for n in ['baseline','extra','old_hybrid']}
    table=[dict(variant=v,**assessment(rows,choices[v['id']],refs,p),selected_keys=[list(base.key(r)) for r,c in zip(rows,choices[v['id']]) if c]) for v in p['variants']]
    return table,choices


def pick(table,p):
    options=[e for e in table if e['repair_passed']]
    if not options:return next(e['variant'] for e in table if e['variant']['id']=='old_hybrid')
    return min(options,key=lambda e:(-e['paired']['old_hybrid']['difference_lower'],e['metrics']['fp'],p['variants'].index(e['variant'])))['variant']


def forward(rows,records,p):
    """Choose settings from prior completed2024 outcomes, not current-month labels."""
    base.only2024(rows);chosen=np.zeros(len(rows),bool);audit=[]
    for month in range(6,11):
        start=date(2024,month,1).isoformat();end=date(2024,month+1,1).isoformat()
        ti=[i for i,r in enumerate(rows) if r['issue']<start];vi=[i for i,r in enumerate(rows) if start<=r['issue']<end]
        tt=[dict(rows[i],y=rows[i]['y'] if rows[i]['target'] is not None and rows[i]['target']<start else None) for i in ti]
        known=sum(r['y'] is not None for r in tt)
        v=next(v for v in p['variants'] if v['id']=='old_hybrid')
        if known>=20 and len({r['issue'] for r in tt})>=5:
            table,_=compare(tt,{n:values[ti] for n,values in records.items()},p);v=pick(table,p)
        vv=[rows[i] for i in vi];ss=choose(vv,records['recent_drop'][vi],records['extra'][vi],v,p);chosen[vi]=ss
        audit.append(dict(start=start,variant=v,prior_pool=len(tt),prior_known=known,
            max_known_target=max((r['target'] for r in tt if r['y'] is not None),default=None),validation_pool=len(vi)))
    return chosen,audit


def diagnostic(rows,records,p):
    refs={name:choose(rows,records['recent_drop'],records['extra'],next(v for v in p['variants'] if v['id']==name),p) for name in ['baseline','old_hybrid']}
    lost=base.paired_metrics(rows,refs['baseline'],refs['old_hybrid'])['lost_cases'];keys={base.key(r) for r in lost}
    original={(r['fid'],r['date']):r for r in read(base.INDEX/'observations.json')['rows'] if r['date'][:4]=='2024'}
    pool=read(base.D/'pool-2024.json');sourcep=read(base.D/'protocol.json');cases=[];capacity=[]
    _,trace=choose(rows,records['recent_drop'],records['extra'],next(v for v in p['variants'] if v['id']=='old_hybrid'),p,trace=True)
    dates={r['issue'] for r in lost}
    for issue in sorted(dates):
        start=issue[:7]+'-01';training=[r for r in pool if r['fid'] not in base.HOLDOUT and r['y'] is not None and r['target']<start]
        forest=base.fit_model(training,'extra',sourcep)['model'];ii=[i for i,r in enumerate(rows) if r['issue']==issue]
        for i in ii:
            r=rows[i]
            if not(refs['baseline'][i] or refs['old_hybrid'][i]):continue
            x=np.array(r['triage_features']);terms=np.zeros(len(x));roots=[]
            for estimator in forest.estimators_:
                tree=estimator.tree_;node=0;roots.append(tree.value[0,0,1]/tree.value[0,0].sum())
                while tree.children_left[node]!=tree.children_right[node]:
                    feature=tree.feature[node];child=tree.children_left[node] if x[feature]<=tree.threshold[node] else tree.children_right[node]
                    terms[feature]+=tree.value[child,0,1]/tree.value[child,0].sum()-tree.value[node,0,1]/tree.value[node,0].sum();node=child
            terms/=len(forest.estimators_)
            if abs(float(np.mean(roots)+terms.sum())-records['extra'][i])>1e-10:raise ValueError('Different diagnostic classifier')
            before=original[base.key(r)]['values'];after=original[r['fid'],r['target']]['values'];delta={}
            for name in base.SCENARIOS:
                delta[name]=None if name not in before or name not in after else (np.array(after[name]['value'])-before[name]['value']).tolist()
            cases.append(dict(fid=r['fid'],issue=issue,target=r['target'],lost_baseline_case=base.key(r) in keys,
                baseline_selected=bool(refs['baseline'][i]),old_hybrid_selected=bool(refs['old_hybrid'][i]),
                current=r['x'][:2],past_gap_days=r['x'][4],last_weekly_change=(np.array(r['x'][2:4])*7).tolist(),
                future_delta=(np.array(r['y'])-r['x'][:2]).tolist(),recent_score=float(records['recent_drop'][i]),
                extra_score=float(records['extra'][i]),divergent=divergent(r),
                future_geometry_deltas=delta,issue_core_coverage=before['core']['coverage'],target_core_coverage=after['core']['coverage'],
                root_score=float(np.mean(roots)),path_contributions=dict(zip(base.FEATURES,terms.tolist())),
                training_pairs=len(training),training_dates=len({r['issue'] for r in training}),training_positives=sum(base.labels(training)),
                crop=None,operation=None,human_visit=None))
        oldtp={rows[i]['fid'] for i in ii if refs['baseline'][i] and base.labels([rows[i]])[0] is True}
        hybtp={rows[i]['fid'] for i in ii if refs['old_hybrid'][i] and base.labels([rows[i]])[0] is True}
        capacity.append(dict(issue=issue,baseline_events=sorted(oldtp),hybrid_events=sorted(hybtp),
            union_size=len(oldtp|hybtp),budget=min(len(ii),p['budget_k']),all_identities_possible=len(oldtp|hybtp)<=min(len(ii),p['budget_k'])))
    return dict(cases=cases,capacity=capacity,replacement_trace=[r for r in trace if r['issue'] in dates],
        explanation='Arithmetic classifier paths explain ranking, not physical/causal reasons for NDMI changes. No agronomic labels.')


def run():
    if (D/'frozen-selection.json').exists():raise ValueError('Frozen2024 study cannot be retuned')
    p=protocol();check_inputs(p);rows,records=dataset();table,choices=compare(rows,records,p)
    selected=pick(table,p);selected_result=next(e for e in table if e['variant']==selected)
    ff,audit=forward(rows,records,p);refs={n:choices[n] for n in ['baseline','extra','old_hybrid']}
    dump(D/'diagnostics.json',diagnostic(rows,records,p));dump(D/'selection-table.json',table)
    dump(D/'forward-check.json',dict(audits=audit,result=assessment(rows,ff,refs,p),selected_keys=[list(base.key(r)) for r,c in zip(rows,ff) if c]))
    dump(D/'research-choices-2024.json',dict(variant=selected,rows=[dict(fid=r['fid'],issue=r['issue'],
        selected=bool(c),outcome=y,actual_visit=None) for r,c,y in zip(rows,choices[selected['id']],base.labels(rows))],
        meaning='Research satellite outcomes, not inspection assignments',tasks_created=0))
    dump(D/'frozen-selection.json',dict(created_at_utc=base.stamp(),selected_variant=selected,repair_passed=selected_result['repair_passed'],
        global_passed=selected_result['global_passed'],hashes={n:digest(D/n) for n in ['protocol.json','diagnostics.json','selection-table.json','forward-check.json','research-choices-2024.json']},
        code_sha256=digest(__file__),model_fit_performed=False,diagnostic_classifier_refit_only=True,test_years_evaluated=[],production_promoted=False,human_visits_created=0))
    for e in table:print(e['variant']['id'],{k:e['metrics'][k] for k in ['tp','fp','unknown']},
        'baseTP_lost',e['paired']['baseline']['lost_known_baseline_tp'],'old_bounds',e['paired']['old_hybrid']['difference_lower'],
        e['paired']['old_hybrid']['difference_upper'],'worst_old_fp',e['paired']['old_hybrid']['negative_growth_upper'],
        'repair',e['repair_passed'],'global',e['global_passed'],flush=True)
    print('Selected',selected,'forward',[(a['start'],a['variant']['id']) for a in audit],{k:base.rank_metrics(rows,ff,p)[k] for k in ['tp','fp','unknown']},flush=True)
