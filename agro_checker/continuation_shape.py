"""Full-season shape-only veto, preregistered after a 2024 support audit.

No base score is an input to this classifier; it can learn directly from all
past completed 2024 pairs. The base alarm still gates its output unchanged.
"""
import joblib
import numpy as np
from . import continuation_recovery as first
from .farm_context_data import ROOT,read,dump,digest
from .early_season_decline import selection2024
from .field_decline_experiment import chronological_folds,events
from .field_index_experiment import date_weights

D=ROOT/'data/farm-research/continuation-shape'
NAMES=first.NAMES
key=first.key
FAMILIES=first.FAMILIES


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    p=read(first.D/'protocol.json')
    p=dict(p,created_at_utc=first.prior.stamp(),
        preceding_2024_frozen_sha256=digest(first.D/'frozen-selection.json'),
        preceding_2024_diagnosis_sha256=digest(first.D/'diagnosis-2024.json'),
        revision_reason='Before ANY2026 evaluation: both2024 FP occur in June where causal-score meta-filter has only14 prior score rows/1date, so every learned head is unsupported. Test direct shape-only heads using all489 original2024 training pairs, no base score input. Same monthly274 validation cohort, support requirements and candidate grid; no lowered support threshold.',
        causal='Direct independent shape classifier fits original2024 pairs with target<month start. It uses NO base score, so early-season base in-sample scores are never needed. Reuse immutable prequential base scores only for validation alarm gates and comparison. All normalizations train-only.',
        temporal_features=[n for n in first.TEMPORAL if not n.startswith('logit_')],
        final_fit='All489 original nonreserved2024 pairs; no base score input. Exact old final base binary models unchanged.',
        score_policy='Base-score columns stripped from explicit matrix whitelist; placeholder0.5 used only by reused feature extractor, never seen by any classifier.',
        operational=False)
    p['input_hashes']=dict(p['input_hashes'],**{
        'agro_checker/continuation_recovery.py':digest(ROOT/'agro_checker/continuation_recovery.py'),
        (first.D/'frozen-selection.json').relative_to(ROOT).as_posix():digest(first.D/'frozen-selection.json'),
        (first.D/'diagnosis-2024.json').relative_to(ROOT).as_posix():digest(first.D/'diagnosis-2024.json')})
    dump(D/'protocol.json',p)
    protected=read(first.D/'preservation-before.json')
    for q in first.D.iterdir():
        if q.is_file():protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def matrix(rows,family):
    # Explicitly exclude both base scores. Feature names/layout remain fixed.
    return np.array([r['continuation'][:18]+r['continuation'][20:]
        +(r['pixel_continuation'] if family.startswith('pixels') else []) for r in rows],float)


def fit_filter(rows,j,family,p):
    selection2024(rows)
    if family not in p['families']:raise ValueError('Unregistered family')
    if family=='identity':return dict(constant=1.,family=family)
    label=np.array([r['y'][j]-r['x'][j]>=.03 for r in rows]) if family=='recovery_logit' else events(rows,j)
    support=p['support']
    if (len(rows)<support['min_pairs'] or len({r['issue'] for r in rows})<support['min_dates']
        or any(len({r['issue'] for r,y in zip(rows,label) if bool(y)==k})<support['min_positive_dates' if k else 'min_negative_dates'] for k in [False,True])):
        return dict(constant=1.,family=family,reason='Insufficient class/date support')
    hp=p['fixed_parameters'];x=matrix(rows,family);w=date_weights(rows);w*=len(rows)/w.sum()
    if family=='pixels_extra':
        from sklearn.ensemble import ExtraTreesClassifier
        model=ExtraTreesClassifier(n_estimators=hp['extra_trees'],max_depth=hp['max_depth'],
            min_samples_leaf=hp['min_leaf'],random_state=hp['seed'],n_jobs=hp['threads'])
        model.fit(x,label,sample_weight=w)
    else:
        # A direct weighted two-pass variance avoids roundoff-negative variance
        # on constant support flags in small early folds.
        from sklearn.linear_model import LogisticRegression
        mean=np.average(x,axis=0,weights=w)
        scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1.
        model=LogisticRegression(C=hp['c'],max_iter=2000,random_state=hp['seed'])
        model.fit((x-mean)/scale,label,sample_weight=w)
        return dict(model=model,family=family,mean=mean,scale=scale)
    return dict(model=model,family=family)


def score_filter(model,rows):
    if 'constant' in model:return np.full(len(rows),model['constant'])
    x=matrix(rows,model['family'])
    if 'mean' in model:x=(x-model['mean'])/model['scale']
    q=model['model'].predict_proba(x)[:,1]
    return 1-q if model['family']=='recovery_logit' else q


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Frozen experiment cannot be retuned')
    p=protocol();first.prior.check_inputs(p)
    source=[dict(r,raw_score=[.5,.5]) for r in read(first.prior.base.SOURCE) if r['fid'] not in p['holdout_ids']]
    selection2024(source);rows,audit=first.enrich(source,2024)
    dump(D/'data-2024.json',rows);dump(D/'features-2024-audit.json',audit)
    # Exact same validation gates and cohort as the preceding causal experiment.
    causal={key(r):r['raw_score'] for r in read(first.prior.D/'causal-scores-2024.json')['rows']}
    validation=[];records={f:[[],[]] for f in p['families']};audits=[]
    for start,tt,vv in chronological_folds(rows,p['folds']):
        vv=[dict(r,raw_score=causal[key(r)]) for r in vv]
        validation.extend(vv)
        for family in p['families']:
            for j in range(2):
                model=fit_filter(tt,j,family,p);records[family][j].extend(score_filter(model,vv).tolist())
                audits.append(dict(start=start,index=NAMES[j],family=family,fit_pairs=len(tt),
                    fit_dates=len({r['issue'] for r in tt}),fit_max_target=max(r['target'] for r in tt),
                    validation_pairs=len(vv),fallback=model.get('reason')))
    record_list=[dict(family=f,scores=records[f]) for f in p['families']]
    selected,table=first.choose(validation,record_list,p)
    dump(D/'meta-oof.json',dict(rows=validation,records=record_list,audits=audits));dump(D/'selection-table.json',table)
    models=[]
    for j,c in enumerate(selected):
        model=fit_filter(rows,j,c['family'],p);path=D/f'{NAMES[j]}-filter.joblib';joblib.dump(model,path)
        models.append(dict(path=path.relative_to(ROOT).as_posix(),sha256=digest(path)))
    f=dict(created_at_utc=first.prior.stamp(),selected=selected,models=models,fit_pairs=len(rows),
        fit_dates=len({r['issue'] for r in rows}),fit_keys=[list(key(r)) for r in rows],fit_max_target=max(r['target'] for r in rows),
        hashes={n:digest(D/n) for n in ['protocol.json','data-2024.json','features-2024-audit.json','meta-oof.json','selection-table.json']},
        code_sha256=digest(__file__),production_promoted=False)
    dump(D/'frozen-selection.json',f);print('Frozen full-season2024:',selected,flush=True)
    return f


def infer(rows,p,f):
    raw,old,point,ridge=first.prior.frozen_raw(rows,p);new=np.empty_like(old);scores=np.empty_like(raw)
    for j,c in enumerate(f['selected']):
        ref=f['models'][j];path=ROOT/ref['path']
        if digest(path)!=ref['sha256']:raise ValueError('Changed filter model')
        scores[:,j]=score_filter(joblib.load(path),rows);new[:,j]=first.prior.veto(old[:,j],scores[:,j],c['threshold'])
    return dict(raw=raw,baseline=old,filtered=new,keep_score=scores,point=point,ridge=ridge)


def evaluate():
    if (D/'report.json').exists():raise ValueError('Already evaluated; no test-driven retries')
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');first.prior.check_inputs(p)
    if digest(__file__)!=f['code_sha256']:raise ValueError('Changed frozen code')
    for name,sha in f['hashes'].items():
        if digest(D/name)!=sha:raise ValueError('Changed frozen artifact')
    frozen_sha=digest(D/'frozen-selection.json')
    spatial=[dict(r,raw_score=[.5,.5]) for r in read(first.prior.base.SOURCE) if r['fid'] in p['holdout_ids']]
    test=[dict(r,raw_score=[.5,.5]) for r in read(first.crop.D/'data-2026.json')]
    enriched={}
    for rr,year in [(spatial,2024),(test,2026)]:
        enriched[year],audit=first.enrich(rr,year)
        dump(D/f'evaluation-features-{year}.json',enriched[year]);dump(D/f'evaluation-feature-audit-{year}.json',audit)
    test=enriched[2026]
    splits=dict(spatial2024=enriched[2024],all2026=test,
        temporal2026=[r for r in test if r['fid'] not in p['holdout_ids']],spatial2026=[r for r in test if r['fid'] in p['holdout_ids']])
    results={};predictions=[]
    for split,rr in splits.items():
        values=infer(rr,p,f);results[split]={}
        for j,name in enumerate(NAMES):
            old=values['baseline'][:,j];new=values['filtered'][:,j]
            a=first.metrics(rr,j,old,old);b=first.metrics(rr,j,old,new)
            results[split][name]=dict(baseline=a,new=b,ridge=first.confusion(rr,events(rr,j),values['ridge'][:,j]),
                persistence=first.confusion(rr,events(rr,j),np.zeros(len(rr),bool)),
                accepted=b['lost_tp']==0 and b['added_alerts']==0 and b['metrics']['fp']<a['metrics']['fp']
                    and b['metrics']['fp_dates']<=a['metrics']['fp_dates'])
        for i,r in enumerate(rr):predictions.append(dict(split=split,fid=r['fid'],issue=r['issue'],target=r['target'],
            actual=r['y'],persistence=r['x'][:2],raw=values['raw'][i].tolist(),keep_score=values['keep_score'][i].tolist(),
            baseline_alarm=values['baseline'][i].tolist(),filtered_alarm=values['filtered'][i].tolist(),
            point=values['point'][i].tolist(),ridge_alarm=values['ridge'][i].tolist()))
    protected=read(D/'preservation-before.json')
    changed=[n for n,sha in protected.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha:raise ValueError('Original state changed')
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=first.prior.stamp(),results=results,selected=f['selected'],frozen_sha256=frozen_sha,
        protected_files=len(protected),changed_files=changed,production_promoted=False,
        acceptance={name:all(results[s][name]['accepted'] for s in ['all2026','spatial2026']) for name in NAMES})
    dump(D/'report.json',out);print('Frozen test results:',results['all2026'],flush=True)
    return out
