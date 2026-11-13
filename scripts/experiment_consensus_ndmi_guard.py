"""Separate2024-only repair of a rejected consensus ranker, never a farm diagnosis."""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from scripts import experiment_consensus_priority as previous

base=previous.base
guard=previous.guard
read,dump,digest=previous.read,previous.dump,previous.digest
D=ROOT/'data/farm-research/consensus-ndmi-guard-2024'
OLD='consensus_protect_0.2_margin_0'


def variants():
    return [dict(id='baseline',kind='baseline'),dict(id='old_consensus',kind='none'),
            dict(id='protect_divergence',kind='divergence'),
            dict(id='protect_negative_divergence',kind='negative_divergence'),
            dict(id='protect_weak_divergent_round',kind='weak_divergent_round',weak_threshold=.10),
            dict(id='raw_margin_0.05',kind='none',raw_margin=.05),
            dict(id='raw_margin_0.1',kind='none',raw_margin=.10),
            dict(id='protect_divergence_raw_0.1',kind='divergence',raw_margin=.10)]


def protocol():
    path=D/'protocol.json'
    if path.exists():return read(path)
    paths=[previous.D/'protocol.json',previous.D/'report.json',base.D/'meta-oof.json',
           base.D/'pool-2024.json',guard.D/'protocol.json',guard.evidence.D/'frozen-evaluation.json',
           ROOT/'scripts/experiment_consensus_priority.py',ROOT/'agro_checker/ndmi_turn_guard.py',
           ROOT/'agro_checker/spectral_triage.py',ROOT/'agro_checker/triage_outcomes.py']
    p=dict(schema_version=1,created_at_utc=base.stamp(),selection_years=[2024],
        prohibited_tuning_years=[2025,2026],inputs={f.relative_to(ROOT).as_posix():digest(f) for f in paths},
        variants=variants(),old_variant=next(v for v in read(previous.D/'protocol.json')['candidates'] if v['id']==OLD),
        fixed_parameters=read(previous.D/'protocol.json')['fixed_parameters'],budget_k=3,
        design='Hypotheses motivated by the already analysed2024 R07 loss. Development reuse, not independent validation. No field/date-specific branch in chooser.',
        rule='Unchanged recent-drop top3, strong anchors>=.20, Logistic+ExtraTrees percentile agreement, at most1swap per date. Add selected issue-time guard; raw margins are score differences, NOT calibrated probabilities.',
        divergence='Same frozen definition: NDVI slope<0, NDMI slope>0, all11 boundary deltas supported and every NDVI delta<0/every NDMI delta>0. Protect EACH baseline divergent slot, not a named fragment. Negative variant additionally currentNDMI<=0. Weak-round protects entire baseline only when maxrecent<.10 and ALL baseline slots divergent.',
        selection='Repair: retain every known baseline AND old-consensus positive identity, TP>=old, knownFP<=old, paired worst-case negative growth vsold<=0 and strict worst-case precision gain vsold>0. Tie by missing-robust gain, FP, declared order. Global additionally same missing-robust checks vsbaseline. No deployment from this study.',
        forward='Jun-Oct guard/settings chosen using prior2024 issue rows with targets>=monthstart masked UNKNOWN; fall back to old_consensus for repair, baseline for global. The inherited old-consensus setting was motivated/compared using full2024: this is NOT end-to-end blind temporal validation.',
        target='Future NDVI OR NDMI core decline<=-.10 on same-baseline6-8day target.368known331unknown on6992024 rows; do not recover disputed labels or change masks/window.',
        minimum_selection_known=20,minimum_selection_dates=5,
        model_refit_performed=False,point_forecast_changed=False,detector_changed=False,
        production_promoted=False,human_visits_created=0)
    dump(path,p)
    dump(D/'code-snapshot.json',dict(code=Path(__file__).read_text(encoding='utf-8'),sha256=digest(__file__)))
    return p


def choose(rows,records,v,p,trace=False):
    """Issue inputs only. Future labels, target dates and target coverage are not read."""
    if v not in p['variants']:raise ValueError('Unregistered guard')
    if len({base.key(r) for r in rows})!=len(rows):raise ValueError('Duplicate candidate')
    for name in ('recent_drop','logit','extra'):
        values=np.asarray(records[name],float)
        if values.shape!=(len(rows),) or not np.isfinite(values).all():raise ValueError('Invalid scores')
    selected=base.topk(rows,records['recent_drop'],p)
    if v['kind']=='baseline':return (selected,[]) if trace else selected
    grouped=defaultdict(list);audit=[]
    for i,r in enumerate(rows):grouped[r['issue']].append(i)
    seed=p['fixed_parameters']['seed']
    tie=lambda i:hashlib.sha256(f'{seed}:{rows[i]["fid"]}'.encode()).hexdigest()
    for issue,ix in grouped.items():
        initial=[i for i in ix if selected[i]]
        divergent={i:guard.divergent(rows[i]) for i in initial}
        weak=(max(records['recent_drop'][i] for i in ix)<v.get('weak_threshold',0.)
              and all(divergent.values()))
        protected={i for i in initial if records['recent_drop'][i]>=p['old_variant']['protect']}
        for i in initial:
            if ((v['kind']=='divergence' and divergent[i]) or
                (v['kind']=='negative_divergence' and divergent[i] and rows[i]['x'][1]<=0) or
                (v['kind']=='weak_divergent_round' and weak)):
                protected.add(i)
        logistic=guard.retention.percentiles(np.asarray(records['logit'])[ix])
        extra=guard.retention.percentiles(np.asarray(records['extra'])[ix])
        ranks={i:(logistic[j],extra[j]) for j,i in enumerate(ix)}
        options=[]
        for added in ix:
            if selected[added] or not rows[added]['quality']['all_boundaries_supported']:continue
            for removed in initial:
                if removed in protected:continue
                gains=[a-b for a,b in zip(ranks[added],ranks[removed])]
                raw=[records[m][added]-records[m][removed] for m in ('logit','extra')]
                if min(gains)>p['old_variant']['margin'] and ('raw_margin' not in v or min(raw)>v['raw_margin']):
                    options.append((min(gains),sum(gains),added,removed,raw))
        swaps=[]
        if options:
            minimum,total,added,removed,raw=min(options,key=lambda q:(-q[0],-q[1],tie(q[2]),tie(q[3])))
            selected[added]=True;selected[removed]=False
            swaps=[dict(removed=rows[removed]['fid'],added=rows[added]['fid'],
                        minimum_percentile_gain=float(minimum),percentile_gain_sum=float(total),
                        raw_logistic_gain=float(raw[0]),raw_extra_gain=float(raw[1]))]
        if trace:
            audit.append(dict(issue=issue,baseline=[rows[i]['fid'] for i in initial],
                protected=[rows[i]['fid'] for i in initial if i in protected],
                divergent=[rows[i]['fid'] for i in initial if divergent[i]],weak_round_guard=bool(weak and v['kind']=='weak_divergent_round'),
                swaps=swaps,selected=[rows[i]['fid'] for i in ix if selected[i]]))
    return (selected,audit) if trace else selected


def assess(rows,records,v,p):
    base.only2024(rows)
    chosen=choose(rows,records,v,p)
    old=choose(rows,records,p['variants'][1],p)
    reference=choose(rows,records,p['variants'][0],p)
    metrics=base.rank_metrics(rows,chosen,p);old_metrics=base.rank_metrics(rows,old,p)
    a=base.paired_metrics(rows,reference,chosen);b=base.paired_metrics(rows,old,chosen)
    checks=dict(all_baseline_events_retained=a['lost_known_baseline_tp']==0,
                all_old_consensus_events_retained=b['lost_known_baseline_tp']==0,
                events_not_below_old=metrics['tp']>=old_metrics['tp'],
                known_false_not_increased_vs_old=metrics['fp']<=old_metrics['fp'],
                possible_false_not_increased_vs_old=b['negative_growth_upper']<=0,
                strict_missing_robust_gain_vs_old=b['difference_lower'] is not None and b['difference_lower']>1e-12)
    global_checks=dict(possible_false_not_increased_vs_baseline=a['negative_growth_upper']<=0,
                       strict_missing_robust_gain_vs_baseline=a['difference_lower'] is not None and a['difference_lower']>1e-12)
    return dict(variant=v,metrics=metrics,paired_baseline=a,paired_old_consensus=b,
                repair_checks=checks,global_checks=global_checks,repair_passed=all(checks.values()),
                global_passed=all(checks.values()) and all(global_checks.values()),
                selected_keys=[list(base.key(r)) for r,c in zip(rows,chosen) if c])


def pick(table,p,global_gate=False):
    eligible=[r for r in table if r['global_passed' if global_gate else 'repair_passed']]
    if not eligible:return p['variants'][0 if global_gate else 1]
    return min(eligible,key=lambda r:(-r['paired_old_consensus']['difference_lower'],r['metrics']['fp'],
                                      p['variants'].index(r['variant'])))['variant']


def forward(rows,records,p,global_gate=False):
    result=np.zeros(len(rows),bool);folds=[]
    for month in range(6,11):
        start=date(2024,month,1).isoformat();end=date(2024,month+1,1).isoformat()
        ti=[i for i,r in enumerate(rows) if r['issue']<start]
        vi=[i for i,r in enumerate(rows) if start<=r['issue']<end]
        training=[dict(rows[i],y=rows[i]['y'] if rows[i]['target'] and rows[i]['target']<start else None) for i in ti]
        known=sum(r['y'] is not None for r in training)
        v=p['variants'][0 if global_gate else 1]
        if known>=p['minimum_selection_known'] and len({r['issue'] for r in training})>=p['minimum_selection_dates']:
            table=[assess(training,{n:np.asarray(s)[ti] for n,s in records.items()},c,p) for c in p['variants']]
            v=pick(table,p,global_gate)
        if vi:result[vi]=choose([rows[i] for i in vi],{n:np.asarray(s)[vi] for n,s in records.items()},v,p)
        folds.append(dict(start=start,variant=v,prior_pool=len(ti),prior_known=known,validation_pool=len(vi),
                          max_known_target=max((r['target'] for r in training if r['y'] is not None),default=None)))
    return result,folds


def diagnosis(rows,records,p,selected):
    """After ranking: outcomes describe the error; none of these fields enter chooser."""
    old,trace=choose(rows,records,p['variants'][1],p,True)
    new,new_trace=choose(rows,records,selected,p,True)
    yy=base.labels(rows);by_key={base.key(r):i for i,r in enumerate(rows)}
    losses=base.paired_metrics(rows,choose(rows,records,p['variants'][0],p),old)['lost_cases']
    cases=[]
    for loss in losses:
        i=by_key[base.key(loss)];row=rows[i];issue=row['issue'];ix=[j for j,r in enumerate(rows) if r['issue']==issue]
        round_old=next(v for v in trace if v['issue']==issue)
        round_new=next(v for v in new_trace if v['issue']==issue)
        cases.append(dict(fid=row['fid'],issue=issue,target=row['target'],
            future_index_delta=[a-b for a,b in zip(row['y'],row['x'][:2])],
            observed_current_indices=row['x'][:2],prior_gap_days=row['x'][4],
            observed_prior_delta=[row['x'][j]*row['x'][4] for j in (2,3)],
            divergence=guard.divergent(row),all_boundary_delta=row['quality']['boundary_delta'],
            old_round=round_old,new_round=round_new,rows=[dict(fid=rows[j]['fid'],
                scores={n:float(records[n][j]) for n in ('recent_drop','logit','extra')},
                outcome=yy[j],chosen_old=bool(old[j]),chosen_new=bool(new[j]),
                future_delta=[a-b for a,b in zip(rows[j]['y'],rows[j]['x'][:2])] if rows[j]['y'] else None,
                divergence=guard.divergent(rows[j])) for j in ix]))
    changed=[]
    for issue in sorted({r['issue'] for r in rows}):
        ix=[i for i,r in enumerate(rows) if r['issue']==issue]
        if any(old[i]!=new[i] for i in ix):
            changed.append(dict(issue=issue,removed=[dict(fid=rows[i]['fid'],outcome=yy[i]) for i in ix if old[i] and not new[i]],
                                added=[dict(fid=rows[i]['fid'],outcome=yy[i]) for i in ix if new[i] and not old[i]]))
    unknown_old={base.key(r) for r,c,y in zip(rows,old,yy) if c and y is None}
    unknown_new={base.key(r) for r,c,y in zip(rows,new,yy) if c and y is None}
    return dict(lost_cases=cases,changed_dates=changed,unknown_selected_identities_equal=unknown_old==unknown_new,
                unknown_removed=[list(v) for v in sorted(unknown_old-unknown_new)],unknown_added=[list(v) for v in sorted(unknown_new-unknown_old)])


def run():
    p=protocol()
    if (D/'report.json').exists():raise ValueError('Frozen development result; do not retune')
    for name,value in p['inputs'].items():
        if digest(ROOT/name)!=value:raise ValueError(f'Changed source {name}')
    rows,records=guard.dataset();base.only2024(rows)
    # Exact reproduction is a prerequisite, not a new fit or a changed detector.
    original_p=read(previous.D/'protocol.json')
    np.testing.assert_array_equal(choose(rows,records,p['variants'][1],p),previous.choose(rows,records,p['old_variant'],original_p))
    table=[assess(rows,records,v,p) for v in p['variants']]
    selected=pick(table,p);global_selected=pick(table,p,True)
    ff,folds=forward(rows,records,p);gg,global_folds=forward(rows,records,p,True)
    old=choose(rows,records,p['variants'][1],p);simple=choose(rows,records,p['variants'][0],p)
    frozen_references=guard.evidence.fixed_choices(rows)
    report=dict(created_at_utc=base.stamp(),protocol_sha256=digest(D/'protocol.json'),
        code_sha256=digest(__file__),code_snapshot_sha256=digest(D/'code-snapshot.json'),
        selection_years=[2024],test_years_evaluated=[],candidate_count=len(rows),dates=len({r['issue'] for r in rows}),
        known=sum(r['y'] is not None for r in rows),unknown=sum(r['y'] is None for r in rows),
        comparison=table,selected_repair=selected,selected_global=global_selected,
        references={n:base.rank_metrics(rows,c,p) for n,c in frozen_references.items()},
        forward_repair=dict(metrics=base.rank_metrics(rows,ff,p),paired_old_consensus=base.paired_metrics(rows,old,ff),folds=folds),
        forward_global=dict(metrics=base.rank_metrics(rows,gg,p),paired_baseline=base.paired_metrics(rows,simple,gg),folds=global_folds),
        diagnosis=diagnosis(rows,records,p,selected),
        production_promoted=False,model_refit_performed=False,detector_changed=False,point_forecast_changed=False,human_visits_created=0)
    dump(D/'report.json',report)
    for entry in table:
        print(entry['variant']['id'],{k:entry['metrics'][k] for k in ('tp','fp','unknown')},
              'repair',entry['repair_passed'],'global',entry['global_passed'],
              'lost',entry['paired_baseline']['lost_known_baseline_tp'],
              'possible false growth/old',entry['paired_old_consensus']['negative_growth_upper'])
    print('REPAIR:',selected,'GLOBAL:',global_selected)
    print('Forward repair:',{k:report['forward_repair']['metrics'][k] for k in ('tp','fp','unknown')})
    print('Forward global:',{k:report['forward_global']['metrics'][k] for k in ('tp','fp','unknown')})
    return report


def verify():
    p=read(D/'protocol.json');report=read(D/'report.json')
    for name,value in p['inputs'].items():
        if digest(ROOT/name)!=value:raise ValueError(f'Changed frozen source {name}')
    if report['protocol_sha256']!=digest(D/'protocol.json') or report['code_snapshot_sha256']!=digest(D/'code-snapshot.json'):
        raise ValueError('Changed experiment artifacts')
    snapshot=read(D/'code-snapshot.json')
    if snapshot['sha256']!=report['code_sha256'] or hashlib.sha256(snapshot['code'].encode()).hexdigest()!=report['code_sha256']:
        raise ValueError('Changed frozen code snapshot')
    rows,records=guard.dataset();base.only2024(rows)
    table=[assess(rows,records,v,p) for v in p['variants']]
    if table!=report['comparison']:raise ValueError('Changed results')
    if pick(table,p)!=report['selected_repair'] or pick(table,p,True)!=report['selected_global']:
        raise ValueError('Changed selection')
    for name,global_gate in [('forward_repair',False),('forward_global',True)]:
        chosen,folds=forward(rows,records,p,global_gate)
        if base.rank_metrics(rows,chosen,p)!=report[name]['metrics'] or folds!=report[name]['folds']:
            raise ValueError('Changed forward selection')
    if diagnosis(rows,records,p,report['selected_repair'])!=report['diagnosis']:
        raise ValueError('Changed case diagnosis')
    print('Verified frozen2024 results, case identities and forward selections; no tuning or writes.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('protocol','run','verify'))
    action=parser.parse_args().action
    dict(protocol=protocol,run=run,verify=verify)[action]()
