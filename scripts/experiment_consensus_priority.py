"""2024-only development: one replacement needs agreement of Logistic and ExtraTrees."""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from agro_checker import ndmi_turn_guard as guard
from agro_checker.farm_context_data import read,dump,digest

base=guard.base
D=ROOT/'data/farm-research/consensus-priority-2024'


def candidates():
    return [dict(id='baseline'),*[dict(id=f'consensus_protect_{p:g}_margin_{m:g}',protect=p,margin=m)
                                for p in (.10,.20) for m in (0.,.10)]]


def protocol():
    path=D/'protocol.json'
    if path.exists():return read(path)
    inputs=[base.D/'meta-oof.json',base.D/'pool-2024.json',base.D/'protocol.json',
            guard.evidence.D/'frozen-evaluation.json',guard.D/'protocol.json',
            ROOT/'agro_checker/spectral_triage.py',ROOT/'agro_checker/triage_outcomes.py',
            ROOT/'agro_checker/ndmi_turn_guard.py']
    p=dict(created_at_utc=base.stamp(),selection_years=[2024],prohibited_tuning_years=[2025,2026],
           inputs={f.relative_to(ROOT).as_posix():digest(f) for f in inputs},
           candidates=candidates(),budget_k=3,fixed_parameters=read(guard.D/'protocol.json')['fixed_parameters'],
           rule='Start recent-drop top3. At most one replacement per date. Both input-only OOF Logistic and ExtraTrees percentile gains must exceed margin. Preserve baseline anchors>=protect; candidate must have all boundary inputs supported. Hash ties. No labels, target QC or target dates in chooser.',
           outcomes='368 known/331 unknown2024 outcomes: original plus month-boundary recovery; exclude3 disputed additional-tile targets. Same699-candidate pool and6-8day/-.10 target.',
           acceptance='Every known baseline event retained; known FP not increased; paired worst-case negative growth<=0; strictly positive worst-case paired precision gain. Unknowns stay in denominator.',
           forward='Monthly Jun-Oct2024 settings selected only using prior completed targets. Fallback baseline when no eligible option.',
           caveat='Repeated development on already studied2024; historical acquisition order is not proven first availability. Not independent validation or inspection usefulness.',
           existing_models_refitted=False,production_promoted=False,human_visits_created=0)
    dump(path,p)
    return p


def choose(rows,records,v,p):
    """Reads ONLY round IDs, tie IDs, scores and issue-time boundary support."""
    if v not in p['candidates']:raise ValueError('Unregistered candidate')
    if len({base.key(r) for r in rows})!=len(rows):raise ValueError('Duplicate rows')
    for name in ('recent_drop','logit','extra'):
        a=np.asarray(records[name],float)
        if a.shape!=(len(rows),) or not np.isfinite(a).all():raise ValueError('Invalid score vector')
    selected=base.topk(rows,records['recent_drop'],p)
    if v['id']=='baseline':return selected
    groups=defaultdict(list)
    for i,r in enumerate(rows):groups[r['issue']].append(i)
    seed=p['fixed_parameters']['seed']
    tie=lambda i:hashlib.sha256(f'{seed}:{rows[i]["fid"]}'.encode()).hexdigest()
    for ix in groups.values():
        logistic=guard.retention.percentiles(np.asarray(records['logit'])[ix])
        extra=guard.retention.percentiles(np.asarray(records['extra'])[ix])
        rank={i:(logistic[j],extra[j]) for j,i in enumerate(ix)}
        options=[]
        for added in ix:
            if selected[added] or not rows[added]['quality']['all_boundaries_supported']:continue
            for removed in ix:
                if not selected[removed] or records['recent_drop'][removed]>=v['protect']:continue
                gains=[a-b for a,b in zip(rank[added],rank[removed])]
                if min(gains)>v['margin']:
                    options.append((min(gains),sum(gains),added,removed))
        if options:
            _,_,added,removed=min(options,key=lambda q:(-q[0],-q[1],tie(q[2]),tie(q[3])))
            selected[added]=True;selected[removed]=False
    return selected


def evaluate(rows,records,v,p):
    base.only2024(rows)
    original=choose(rows,records,p['candidates'][0],p)
    selected=choose(rows,records,v,p)
    paired=base.paired_metrics(rows,original,selected)
    metrics=base.rank_metrics(rows,selected,p)
    reference=base.rank_metrics(rows,original,p)
    checks=dict(retain_baseline_events=paired['lost_known_baseline_tp']==0,
                known_false_not_increased=metrics['fp']<=reference['fp'],
                possible_false_not_increased=paired['negative_growth_upper']<=0,
                strict_missing_robust_gain=paired['difference_lower']>1e-12)
    return dict(candidate=v,metrics=metrics,paired=paired,checks=checks,eligible=all(checks.values()))


def select(table):
    valid=[e for e in table if e['eligible']]
    return min(valid,key=lambda e:(-e['paired']['difference_lower'],e['metrics']['fp'],e['candidate']['id']))['candidate'] if valid else table[0]['candidate']


def run():
    p=protocol()
    if (D/'report.json').exists():raise ValueError('Frozen2024 experiment; do not retry after seeing outcomes')
    for name,value in p['inputs'].items():
        if digest(ROOT/name)!=value:raise ValueError(f'Changed input {name}')
    rows,records=guard.dataset()
    base.only2024(rows)
    table=[evaluate(rows,records,v,p) for v in p['candidates']]
    forward=np.zeros(len(rows),bool);folds=[]
    for month in range(6,11):
        start=date(2024,month,1).isoformat();end=date(2024,month+1,1).isoformat()
        ti=[i for i,r in enumerate(rows) if r['issue']<start]
        vi=[i for i,r in enumerate(rows) if start<=r['issue']<end]
        training=[dict(rows[i],y=rows[i]['y'] if rows[i]['target'] and rows[i]['target']<start else None) for i in ti]
        subset={name:np.asarray(values)[ti] for name,values in records.items()}
        v=select([evaluate(training,subset,c,p) for c in p['candidates']]) if training else p['candidates'][0]
        validation=[rows[i] for i in vi]
        if vi:forward[vi]=choose(validation,{name:np.asarray(values)[vi] for name,values in records.items()},v,p)
        folds.append(dict(start=start,candidate=v,prior_pool=len(ti),validation_pool=len(vi),
                          max_known_target=max((r['target'] for r in training if r['y'] is not None),default=None)))
    reference=choose(rows,records,p['candidates'][0],p)
    selected=select(table)
    fixed=guard.evidence.fixed_choices(rows)
    yy=base.labels(rows)
    unknown_dates=sorted({r['issue'] for r,y,c in zip(rows,yy,reference) if c and y is None})
    report=dict(created_at_utc=base.stamp(),protocol_sha256=digest(D/'protocol.json'),
                code_sha256=digest(__file__),selection_years=[2024],test_years_evaluated=[],
                candidate_count=len(rows),dates=len({r['issue'] for r in rows}),
                known=sum(y is not None for y in yy),unknown=sum(y is None for y in yy),
                comparison=table,selected=selected,
                references={name:base.rank_metrics(rows,mask,p) for name,mask in fixed.items()},
                forward=dict(metrics=base.rank_metrics(rows,forward,p),paired=base.paired_metrics(rows,reference,forward),folds=folds),
                diagnostics=dict(baseline_unknown_dates=unknown_dates,
                    selected_outcomes_by_month={str(m):base.rank_metrics([r for r in rows if int(r['issue'][5:7])==m],
                       reference[[i for i,r in enumerate(rows) if int(r['issue'][5:7])==m]],p) for m in range(6,11)}),
                model_refit_performed=False,production_promoted=False,human_visits_created=0,
                conclusion='Development-only. Keep transparent baseline unless conservative gain survives missing outcomes; no evidence about useful farm visits.')
    dump(D/'report.json',report)
    print('2024-only pool:',report['candidate_count'],'dates',report['dates'],'known',report['known'],'unknown',report['unknown'])
    for entry in table:
        print(entry['candidate']['id'],{k:entry['metrics'][k] for k in ('tp','fp','unknown')},entry['eligible'],entry['checks'])
    print('Selected:',selected,'forward:',{k:report['forward']['metrics'][k] for k in ('tp','fp','unknown')})
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('protocol','run'))
    args=parser.parse_args()
    protocol() if args.action=='protocol' else run()
