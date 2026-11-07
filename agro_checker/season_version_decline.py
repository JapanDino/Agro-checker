"""Season/domain adaptation research; no radiometric correction or product write.

Fit and select exclusively with <=2024 labels. Evaluation uses frozen choices.
Processing baseline and season are confounded in the available data.
"""
from datetime import datetime, timezone
from collections import Counter
import numpy as np

from .farm_context_data import ROOT, read, dump, digest
from .early_season_decline import (SOURCE, LEGACY, HOLDOUT, validate_fit,
    selection2024, select as select2024, rank)
from .field_index_experiment import date_weights, predict as point_predict
from .field_decline_experiment import events, confusion, chronological_folds
from .conservative_decline import matrix as raw_matrix, gate, key, NAMES

D = ROOT/'data/farm-research/season-version-decline'
EARLY = ROOT/'data/farm-research/early-season-decline/data-early.json'
PRIOR = ROOT/'data/farm-research/early-season-decline'
BASELINES = ['05.00', '05.10', '05.11']
SPECS = [
    dict(id='2024_only', source=False, balanced=False, season=False, version=False),
    dict(id='raw_pool', source=True, balanced=False, season=False, version=False),
    dict(id='pool_fixed_mass', source=True, balanced=False, season=False, version=False, fixed_mass=True),
    dict(id='balanced25', source=True, balanced=True, season=False, version=False),
    dict(id='season25', source=True, balanced=True, season=True, version=False),
    dict(id='version25', source=True, balanced=True, season=False, version=True),
    dict(id='season_version25', source=True, balanced=True, season=True, version=True),
    dict(id='overlap_season_version25', source=True, balanced=True, season=True, version=True, overlap=True),
    dict(id='consensus_season_version25', source=True, balanced=True, season=True, version=True, consensus=True),
]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def protocol():
    if (D/'protocol.json').exists():
        return read(D/'protocol.json')
    previous = read(PRIOR/'experiment-protocol.json')
    p = dict(schema=1, created_at_utc=stamp(), algorithms=previous['algorithms'],
        fixed_hyperparameters=previous['fixed_hyperparameters'], candidates=SPECS,
        regimes=[c['id'] for c in SPECS], gates=previous['gates'], thresholds=previous['thresholds'],
        folds=previous['folds'], holdout_ids=HOLDOUT,
        input_hashes={q.relative_to(ROOT).as_posix():digest(q) for q in [EARLY,SOURCE,LEGACY,
            PRIOR/'frozen-selection.json',PRIOR/'oof-predictions.json']},
        goal='More true spectral-decline alerts without increasing false-positive pairs or dates',
        target='UNCHANGED core median decline <= -0.10 through6-8 days, same baseline within each pair',
        diagnosis='2019-2021 and2024 nonreserved only; descriptive differences, not causal attribution',
        source_cap=.25, source_cap_policy='For balanced models source dates together get25% total weight, known2024 dates75%. No source labels or densities from validation.',
        effective_mass='Balanced and pool_fixed_mass total training weight equals count of2024 training pairs; controls preserve original weight normalization.',
        season_features='Past-observed range and relative slopes/drawdown with support flags, amplitude floor0.05. No true crop or future seasonal maximum.',
        baseline_features=BASELINES, version_policy='Append categorical PB, never add an index/radiance offset. In this archive baseline and year/season cannot be separated causally.',
        unknown_version='For version-aware models average predictions over supported2024 PBs using their fit-date-balanced weights. This is a frozen statistical fallback, not verified sensor harmonization. Count unseen versions explicitly.',
        overlap='Logistic domain classifier C0.1 on seven past-only inputs; balance source/target domain weights; source importance odds clipped[0.1,10]; then enforce25% source cap. Fit domains on training rows only.',
        consensus='Minimum of2024-only score and season_version25 score, both fitted only on fold training rows',
        selection='Same274 purged monthly2024 OOF pairs; zeroFP pairs/dates, >=2TP on>=2 issue dates; maximize date-balanced recall then TP dates/count; ties prefer candidate order, ungated, higher threshold.',
        fitted_choices='Freeze primary, best eligible adaptation (excluding2024_only/raw_pool), and both controls BEFORE test. Other candidates are2024-only diagnostics, not evaluated on2026.',
        test='Existing499 pairs2026 after freeze; reserved2024 also reported. Previously examined2026 is not new blind test.2025 absent.',
        acceptance='Per index: strictly moreTP than original Ridge, no growth ofFP pairs/dates versus BOTH Ridge and zero-alarm persistence, on all2026 and reserved spatial2026.',
        preservation='Original point forecasts, detectors, bounds, UI and journal are immutable; no production promotion',
        limitations=['2024 repeatedly used for selection; optimistic selection scores.',
            'Unknown crops/operations, nearby fragments and overlapping windows are dependent.',
            'Source weighting assumes transferable relationships; conditional-label drift may remain.',
            'Archive availability at historical issue time and live delivery latency not established.'],
        official_source='https://sentiwiki.copernicus.eu/web/s2-processing')
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents: continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.cbm','.joblib','.csv']: continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def check_inputs(p):
    for name,sha in p['input_hashes'].items():
        if digest(ROOT/name)!=sha: raise ValueError('Changed frozen source: '+name)


def season_matrix(rows):
    out=[]
    for r in rows:
        s=r['season'];x=r['x']
        amplitude=np.maximum(np.array(s[3:5])-s[5:7],0.)
        support=np.array(s[13:15])>.5
        scale=np.maximum(amplitude,.05)
        slopes=np.where(support,np.clip(np.array(x[2:4])*7/scale,-4,4),0.)
        drawdown=np.where(support,np.clip(np.array(s[9:11])/scale,-4,0),0.)
        out.append([*amplitude,*s[11:15],*slopes,*drawdown,s[2],min(s[0],180)/180])
    return np.asarray(out,float)


def design(rows,j,spec,p,override_baseline=None):
    x=raw_matrix(rows,p['algorithms'][j])
    columns=[x]
    if spec['season']: columns.append(season_matrix(rows))
    if spec['version']:
        columns.append(np.array([[(override_baseline or r['baseline'])==b for b in BASELINES] for r in rows],float))
    result=np.column_stack(columns)
    if not np.isfinite(result).all(): raise ValueError('Nonfinite model input')
    return result


def overlap_weights(rows):
    """No event labels, no validation features: estimate training-domain overlap."""
    from sklearn.linear_model import LogisticRegression
    x=np.array([r['x'][:7] for r in rows],float)
    target=np.array([r['issue'][:4]=='2024' for r in rows])
    w=date_weights(rows)
    if not target.any() or target.all(): return np.ones(len(rows)),None
    w[target]/=w[target].sum();w[~target]/=w[~target].sum();w*=len(rows)/2
    mean=np.average(x,axis=0,weights=w)
    scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1
    m=LogisticRegression(C=.1,max_iter=2000,random_state=20241021)
    m.fit((x-mean)/scale,target.astype(int),sample_weight=w)
    prob=m.predict_proba((x-mean)/scale)[:,1]
    ratios=np.clip(prob/np.maximum(1-prob,1e-9),.1,10)
    ratios[target]=1.
    return ratios,dict(min_source=float(ratios[~target].min()),max_source=float(ratios[~target].max()),
        fit_rows=len(rows),fit_max_target=max(r['target'] for r in rows),
        policy='Training covariates/domain labels only; no event targets or validation features')


def training_weights(rows,spec,p):
    validate_fit(rows)
    target=np.array([r['issue'][:4]=='2024' for r in rows])
    if not target.any(): raise ValueError('Known2024 target-domain training rows required')
    w=date_weights(rows);overlap=None
    if spec.get('overlap'):
        ratio,overlap=overlap_weights(rows);w*=ratio
    if spec['balanced']:
        if target.all(): raise ValueError('Balanced model needs source and target')
        w[target]*=(1-p['source_cap'])/w[target].sum()
        w[~target]*=p['source_cap']/w[~target].sum()
        w*=int(target.sum())/w.sum()
    elif spec.get('fixed_mass'):
        w*=int(target.sum())/w.sum()
    else:
        # Same arithmetic order as early-season comparator, including rounding.
        w=w*len(rows)/w.sum()
    return w,dict(source_weight_share=float(w[~target].sum()/w.sum()),total_weight=float(w.sum()),
        source_pairs=int((~target).sum()),target_pairs=int(target.sum()),overlap=overlap)


def fit_estimator(x,y,w,family,p):
    if len(np.unique(y))<2: return dict(kind='constant',value=float((w@y+.5)/(w.sum()+1)))
    hp=p['fixed_hyperparameters']
    if family['family']=='extra':
        from sklearn.ensemble import ExtraTreesClassifier
        m=ExtraTreesClassifier(n_estimators=hp['extra_trees'],max_depth=hp['extra_max_depth'],
            min_samples_leaf=family['leaf'],max_features=1.,random_state=hp['seed'],n_jobs=hp['threads'])
        m.fit(x,y,sample_weight=w)
        return dict(kind='classifier',model=m)
    from sklearn.linear_model import LogisticRegression
    mean=np.average(x,axis=0,weights=w)
    scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1
    m=LogisticRegression(C=family['c'],solver='lbfgs',max_iter=2000,random_state=hp['seed'])
    m.fit((x-mean)/scale,y,sample_weight=w)
    return dict(kind='logit',model=m,mean=mean,scale=scale)


def train(rows,j,spec,p):
    validate_fit(rows)
    current=[r for r in rows if r['issue'][:4]=='2024']
    selection2024(current)
    if not spec['source']: rows=current
    if spec.get('consensus'):
        control=next(c for c in p['candidates'] if c['id']=='2024_only')
        adapted=next(c for c in p['candidates'] if c['id']=='season_version25')
        return dict(kind='consensus',control=train(current,j,control,p),adapted=train(rows,j,adapted,p))
    w,weight_info=training_weights(rows,spec,p)
    m=fit_estimator(design(rows,j,spec,p),events(rows,j).astype(int),w,p['algorithms'][j],p)
    target=current
    tw=date_weights(target)
    baseline_mass={b:float(sum(v for r,v in zip(target,tw) if r['baseline']==b)) for b in BASELINES}
    total=sum(baseline_mass.values())
    if not total: raise ValueError('No supported2024 processing version')
    m.update(spec=spec,baseline_mixture={b:v/total for b,v in baseline_mass.items() if v},weight_info=weight_info,
        fit_pairs=len(rows),fit_max_target=max(r['target'] for r in rows),fit_years=sorted({r['issue'][:4] for r in rows}))
    return m


def predict_x(m,x):
    if m['kind']=='constant': return np.full(len(x),m['value'])
    if m['kind']=='logit': x=(x-m['mean'])/m['scale']
    return m['model'].predict_proba(x)[:,1]


def score(m,rows,j,p):
    if m['kind']=='consensus':
        return np.minimum(score(m['control'],rows,j,p),score(m['adapted'],rows,j,p))
    spec=m['spec'];x=design(rows,j,spec,p)
    values=predict_x(m,x)
    unknown=np.array([r['baseline'] not in BASELINES for r in rows])
    if spec['version'] and unknown.any():
        subset=[r for r,u in zip(rows,unknown) if u]
        values[unknown]=sum(weight*predict_x(m,design(subset,j,spec,p,baseline))
            for baseline,weight in m['baseline_mixture'].items())
    return values


def weighted_quantile(x,w,q=.5):
    order=np.argsort(x,kind='stable');xx=np.asarray(x)[order];ww=np.asarray(w)[order]
    return float(xx[min(np.searchsorted(np.cumsum(ww),q*ww.sum()),len(xx)-1)])


def diagnosis(early,current):
    validate_fit(early);selection2024(current)
    out={}
    for year in ['2019','2020','2021','2024']:
        rr=[r for r in early+current if r['issue'][:4]==year];w=date_weights(rr)
        out[year]=dict(pairs=len(rr),dates=len({r['issue'] for r in rr}),baselines=dict(Counter(r['baseline'] for r in rr)),
            median_index=[weighted_quantile([r['x'][j] for r in rr],w) for j in range(2)],
            event_rate=[float(np.average(events(rr,j),weights=w)) for j in range(2)],
            monthly_events={month:[int(sum(r['issue'][5:7]==month and r['y'][j]-r['x'][j]<=-.10 for r in rr)) for j in range(2)]
                for month in sorted({r['issue'][5:7] for r in rr})},
            continuation={name:confusion([r for r,g in zip(rr,gate(rr,j,'joint_momentum')) if g],
                events([r for r,g in zip(rr,gate(rr,j,'joint_momentum')) if g],j),
                np.ones(int(gate(rr,j,'joint_momentum').sum()),bool)) for j,name in enumerate(NAMES) if gate(rr,j,'joint_momentum').any()})
    shares=[]
    source_dates=len({r['issue'] for r in early})
    for start,tt,vv in chronological_folds(current,read(PRIOR/'experiment-protocol.json')['folds']):
        dates=len({r['issue'] for r in tt})
        shares.append(dict(start=start,source_dates=source_dates,target_dates=dates,source_share=source_dates/(source_dates+dates)))
    versions={b:dict(pairs=sum(r['baseline']==b for r in early+current),
        years=sorted({r['issue'][:4] for r in early+current if r['baseline']==b}),
        min_issue=min(r['issue'] for r in early+current if r['baseline']==b),
        max_target=max(r['target'] for r in early+current if r['baseline']==b)) for b in BASELINES}
    return dict(created_at_utc=stamp(),by_year=out,raw_pool_source_share=source_dates/(source_dates+len({r['issue'] for r in current})),
        fold_weight_shares=shares,versions=versions,version_effect_identifiable=False,
        reason='No matched same-acquisition products from alternative PBs; source PB05.00 confounded with early years,2024 PB05.10/05.11 with season.',
        limitations='Descriptive differences are not proof that PB, crop, weather or operations caused errors.')


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists(): raise ValueError('Frozen experiment cannot be retuned')
    p=protocol();check_inputs(p)
    early=[r for r in read(EARLY) if r['fid'] not in HOLDOUT]
    current=[r for r in read(SOURCE) if r['fid'] not in HOLDOUT]
    if any(r['issue'][:4]>='2024' for r in early): raise ValueError('Early data must precede2024')
    dump(D/'diagnosis-2024.json',diagnosis(early,current))
    folds=list(chronological_folds(current,p['folds']))
    validation=[r for _,_,vv in folds for r in vv]
    records=[];audits=[]
    for spec in p['candidates']:
        scores=[[],[]]
        for start,tt,vv in folds:
            training=(early if spec['source'] else [])+tt
            if max(r['target'] for r in training)>=start: raise ValueError('Future validation target in fit')
            for j in range(2):
                m=train(training,j,spec,p)
                scores[j].extend(score(m,vv,j,p).tolist())
                effective=m['adapted'] if m['kind']=='consensus' else m
                audits.append(dict(candidate=spec['id'],index=NAMES[j],start=start,
                    fit_max_target=effective['fit_max_target'],validation_min_issue=min(r['issue'] for r in vv),
                    weight_info=effective['weight_info']))
        records.append(dict(regime=spec['id'],scores=scores))
        print('2024 OOF',spec['id'],flush=True)
    primary,choices,table=select2024(validation,records,p)
    best_adapted=[]
    for j in range(2):
        options=[(rank(c['metrics'])+(order,),c) for order,c in enumerate(choices[j]) if c['enabled'] and c['regime'] not in ['2024_only','raw_pool']]
        best_adapted.append(min(options,key=lambda v:v[0])[1] if options else dict(index=NAMES[j],regime=None,enabled=False,reason='No eligible adaptation on2024'))
    evaluations=dict(primary=primary,best_adapted=best_adapted)
    for control in ['2024_only','raw_pool']:
        evaluations[control]=[next(c for c in group if c['regime']==control) for group in choices]
    dump(D/'selection-table.json',table)
    dump(D/'oof-predictions.json',dict(rows=validation,records=records))
    dump(D/'fit-audits.json',audits)
    import joblib
    models={}
    for group in evaluations.values():
        for j,c in enumerate(group):
            if not c['enabled']: continue
            identifier=f"{j}-{c['regime']}"
            if identifier in models: continue
            spec=next(s for s in p['candidates'] if s['id']==c['regime'])
            training=(early if spec['source'] else [])+current
            m=train(training,j,spec,p);path=D/(identifier+'.joblib');joblib.dump(m,path)
            effective=m['adapted'] if m['kind']=='consensus' else m
            models[identifier]=dict(path=path.relative_to(ROOT).as_posix(),sha256=digest(path),
                fit_keys=[list(key(r)) for r in training],weight_info=effective['weight_info'])
    f=dict(created_at_utc=stamp(),primary=primary,best_adapted=best_adapted,choices=choices,
        evaluations=evaluations,models=models,protocol_sha256=digest(D/'protocol.json'),
        selection_sha256=digest(D/'selection-table.json'),oof_sha256=digest(D/'oof-predictions.json'),
        audits_sha256=digest(D/'fit-audits.json'),diagnosis_sha256=digest(D/'diagnosis-2024.json'),
        code_sha256=digest(__file__),production_promoted=False)
    dump(D/'frozen-selection.json',f)
    print('Frozen primary:',primary,'\nBest adaptation:',best_adapted,flush=True)
    return f


def infer(rows,p,f,version='primary'):
    import joblib
    point=point_predict(read(LEGACY)['selected_model'],rows)
    old=point-np.array([r['x'][:2] for r in rows])<=-.10
    alarms=old.copy();scores=[[None,None] for _ in rows]
    for j,c in enumerate(f['evaluations'][version]):
        if not c['enabled']: continue
        entry=f['models'][f"{j}-{c['regime']}"];path=ROOT/entry['path']
        if digest(path)!=entry['sha256']: raise ValueError('Frozen model changed')
        values=score(joblib.load(path),rows,j,p)
        alarms[:,j]=(values>=c['threshold'])&gate(rows,j,c['gate'])
        for i,value in enumerate(values): scores[i][j]=float(value)
    return alarms,scores,point,old


def evaluate():
    if (D/'report.json').exists(): raise ValueError('Already evaluated; no test-driven retries')
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');check_inputs(p)
    if digest(D/'protocol.json')!=f['protocol_sha256'] or digest(__file__)!=f['code_sha256']: raise ValueError('Changed frozen protocol/code')
    frozen_sha=digest(D/'frozen-selection.json')
    # No training or selection below. Test covariates/outcomes first used here.
    test=read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    splits=dict(spatial2024=[r for r in read(SOURCE) if r['fid'] in HOLDOUT],all2026=test,
        temporal2026=[r for r in test if r['fid'] not in HOLDOUT],spatial2026=[r for r in test if r['fid'] in HOLDOUT])
    results={};predictions=[]
    for split,rr in splits.items():
        results[split]={}
        for version in f['evaluations']:
            alarm,scores,point,old=infer(rr,p,f,version)
            results[split][version]={}
            for j,name in enumerate(NAMES):
                yy=events(rr,j)
                mm={label:confusion(rr,yy,aa) for label,aa in [('persistence',np.zeros(len(rr),bool)),('legacy',old[:,j]),('new',alarm[:,j])]}
                mm['passes_vs_legacy']=mm['new']['tp']>mm['legacy']['tp'] and mm['new']['fp']<=mm['legacy']['fp'] and mm['new']['fp_dates']<=mm['legacy']['fp_dates']
                mm['passes_vs_persistence']=mm['new']['tp']>0 and mm['new']['fp']==0
                results[split][version][name]=mm
            for i,r in enumerate(rr):
                predictions.append(dict(split=split,version=version,fid=r['fid'],issue=r['issue'],target=r['target'],
                    baseline=r['baseline'],actual=r['y'],persistence=r['x'][:2],point=point[i].tolist(),
                    legacy_alarm=old[i].tolist(),new_alarm=alarm[i].tolist(),scores=scores[i]))
    protected=read(D/'preservation-before.json')
    changed=[name for name,h in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=h]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha: raise ValueError('Original state or frozen selection changed: '+repr(changed[:5]))
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=stamp(),frozen_sha256=frozen_sha,primary=f['primary'],best_adapted=f['best_adapted'],results=results,
        unknown_test_baselines=dict(Counter(r['baseline'] for r in test if r['baseline'] not in BASELINES)),
        test_constraints={name:f['primary'][j]['enabled'] and all(results[s]['primary'][name]['passes_vs_legacy'] and results[s]['primary'][name]['passes_vs_persistence'] for s in ['all2026','spatial2026']) for j,name in enumerate(NAMES)},
        production_promoted=False,protected_files=len(protected),changed_files=changed,
        no2025='Absent from existing evaluation archive; no fitting or selection on2025/2026',evaluation_code_sha256=digest(__file__))
    dump(D/'report.json',out)
    print('Primary2026:',results['all2026']['primary'],'\nAdaptation2026:',results['all2026']['best_adapted'],flush=True)
    return out
