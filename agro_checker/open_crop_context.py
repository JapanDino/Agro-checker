"""Regional crop-calendar context, never a manufactured field crop/harvest label.

Fit/select only2024. Extra context can veto immutable detector alarms only.
Date-only ablations distinguish a calendar clock from genuine added information.
"""
from datetime import date
import numpy as np
import joblib
from . import continuation_shape as shape
from .farm_context_data import ROOT,read,dump,digest
from .field_decline_experiment import chronological_folds,events
from .field_index_experiment import date_weights,day
from .early_season_decline import selection2024

D=ROOT/'data/farm-research/open-crop-context'
NAMES=shape.NAMES
key=shape.key
SEASONS=['tc-wintercereals','tc-maize-main','tc-springcereals']
FIELDS=['relative_earliest_eos','relative_latest_eos','possible_near_eos','all_alternatives_near_eos',
    'possible_active','all_alternatives_active','supported','all_alternatives_supported','eos_spread']
FAMILIES=['identity','shape_logit','date_logit','calendar_logit','date_extra','calendar_extra']


def annual_day(value):
    """Source DOY is365-day climatology; do not silently shift after Feb29."""
    d=day(value)
    return date(2001,d.month,d.day).timetuple().tm_yday if (d.month,d.day)!=(2,29) else 59.5


def cyclic_between(value,start,end):
    return (value-start)%365 <= (end-start)%365


def calendar_for_scenario(issue,entries):
    doy=annual_day(issue);out=[]
    for season in SEASONS:
        candidates=[e['properties'] for e in entries if e['weight']>1e-7]
        valid=[q for q in candidates if q.get(season+'_sos') is not None and q.get(season+'_eos') is not None]
        if not valid:out += [0.]*len(FIELDS);continue
        # No area normalization over conflicting overlapping AEZs. They are
        # alternatives, NOT probabilities of current crops.
        differences=[(doy-float(q[season+'_eos'])+182.5)%365-182.5 for q in valid]
        near=[-45<=v<=0 for v in differences]
        active=[cyclic_between(doy,q[season+'_sos'],q[season+'_eos']) for q in valid]
        all_known=len(valid)==len(candidates) and sum(e['weight'] for e in entries)>=1-1e-6
        out += [float(np.clip(max(differences)/90,-2,2)),float(np.clip(min(differences)/90,-2,2)),
            float(any(near)),float(all_known and all(near)),float(any(active)),float(all_known and all(active)),
            1.,float(all_known),float((max(differences)-min(differences))/90)]
    return out


def context(row,evidence,scenario='base'):
    entry=evidence['fragments'][row['fid']]
    values=calendar_for_scenario(row['issue'],entry['scenarios'].get(scenario,[]))
    report=evidence['regional_report'];issue=row['issue']
    available=report['usable_after']<=issue<report['expires'] and issue[:4]==report['publication_date'][:4]
    age=(day(issue)-day(report['publication_date'])).days if available else 0
    # This is a known regional publication, NOT a report of harvesting this field.
    values += [float(available),min(age,90)/90 if available else 0.,float(available and age<=30)]
    for k in range(len(SEASONS)):
        near=values[k*len(FIELDS)+2]
        values += [near*row['x'][2]*7,near*row['x'][3]*7]
    return values


def enrich(rows,evidence,scenario='base'):
    result=[]
    for row in rows:
        values=context(row,evidence,scenario)
        if not np.isfinite(values).all():raise ValueError('Nonfinite context')
        result.append(dict(row,crop_context=values,crop=None,harvest_date=None,
            context_audit=dict(scenario=scenario,confirmed_crop=False,actual_harvest_known=False,
                regional_report_available=bool(values[27]),calendar_is_regional_prior=True)))
    return result


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    p=read(shape.D/'protocol.json');evidence=read(D/'crop-evidence.json')
    if evidence['confirmed_current_crop_labels'] or evidence['confirmed_harvest_dates']:
        raise ValueError('Protocol assumes unknown field truth; new protocol needed for actual labels')
    files=[shape.D/'data-2024.json',shape.D/'frozen-selection.json',shape.D/'protocol.json',
        ROOT/'agro_checker/continuation_shape.py',ROOT/'agro_checker/continuation_recovery.py',
        D/'crop-evidence.json',D/'source-manifest.json',
        ROOT/'data/farm-research/crop-season-model/crop-evidence.json',
        ROOT/'data/farm-research/crop-season-model/sources/worldcereal-aez-record.json',
        ROOT/'data/farm-research/crop-season-model/sources/worldcereal-aez.geojson']
    files += [D/'sources'/e['name'] for e in read(D/'source-manifest.json')['entries'] if 'sha256' in e]
    inputs=dict(p['input_hashes']);inputs.update({q.relative_to(ROOT).as_posix():digest(q) for q in files})
    p=dict(schema=1,created_at_utc=shape.first.prior.stamp(),input_hashes=inputs,
        families=FAMILIES,thresholds=p['thresholds'],folds=p['folds'],base_choices=p['base_choices'],
        holdout_ids=p['holdout_ids'],support=p['support'],fixed_parameters=p['fixed_parameters'],
        target='UNCHANGED spectral drop>=0.10 within6-8days, even if caused by harvest. Not a label of damage or inspection need.',
        crop_truth='All27 fragments: crop=null,harvest_date=null. Field20soy2024 screenshot remains unlocated; not assigned toRxx.',
        source_policy='No2025/2026 crop labels/maps/calendars train or select. No2021crop map promoted to2024 truth. GEOGLAMv1 and ASAP lack Russia records; RDM2024 queries empty; failed historical item queries are unknown, not zero.',
        calendar_policy='ONLY immutable AEZ2023 regional season endpoints; conflicting AEZ retained as alternative min/max/any/all, not normalized as crop probabilities. EOS is not observed harvest date.',
        calendar_fields=[f'{s}_{field}' for s in SEASONS for field in FIELDS]+['regional_publication_available','publication_age','publication_recent']+[f'{s}_near_x_last_{n}' for s in SEASONS for n in NAMES],
        calendar_window_days=45,year_handling='365-day climatology: explicit month/day mapping, Feb29=59.5',
        publication='June27Krasnodar report available fromJune28 conservatively, never backfilled into earlierJune. Only2024. No expiry/endpoint tuned from labels.',
        fitting='All489 original2024 nonreserved pairs. Direct classifier excludes base logits; training target<validation month. Monthly274/25same cohort, validation target<nextmonth.',
        source_bias_control='Compare identical42shape features vs+date_sin_cos vs+date_and_calendar. No field ID input. Linear and ExtraTrees algorithms fixed, nohyperparameter search.',
        fallback='keep-score1 with insufficient class/date support; all cohort rows retained',
        selection=p['selection'],
        boundary_guard='Candidate must satisfy lossless exactTP and strictFP reduction on base AND each8shift/core/outer context scenario on2024. Original spectral target/features fixed; varies only external calendar context.',
        final_fit='All4892024 pairs, fixed selected family. Final base weights/point/intervals never retrained.',
        evaluation='Only frozen selected model vs exact baseline, Ridge and persistence on original4992026 plus spatial2024; no rejected candidate tested on2026.',
        test_status='2026 previously examined, not blind;2025 absent. No tuning with either year.',
        limitations=['No current crop or operation truth; crop rotation prevents relabelling prior maps.',
            'Calendar dates are coarse/alternative regional priors, not dates of harvesting fields.',
            '2024 reused across prior experiments; correlated fragments/windows; no local agronomic outcomes.',
            'Historical bytes/delivery availability not established for all present-day public web pages.'],
        operational=False)
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for path in (ROOT/folder).rglob('*'):
            if not path.is_file() or D in path.parents:continue
            if folder=='data/farm-research' and path.suffix not in ['.json','.geojson','.joblib','.cbm','.csv']:continue
            protected[path.relative_to(ROOT).as_posix()]=digest(path)
    dump(D/'preservation-before.json',protected)
    return p


def matrix(rows,family):
    x=shape.matrix(rows,'pixels_logit')
    if family=='shape_logit':return x
    if family.startswith('date') or family.startswith('calendar'):
        angle=2*np.pi*np.array([annual_day(r['issue']) for r in rows])/365
        x=np.column_stack([x,np.sin(angle),np.cos(angle)])
    if family.startswith('calendar'):x=np.column_stack([x,np.array([r['crop_context'] for r in rows])])
    return x


def fit_filter(rows,j,family,p):
    selection2024(rows)
    if family not in p['families']:raise ValueError('Unregistered family')
    if family=='identity':return dict(family=family,constant=1.)
    y=events(rows,j);support=p['support']
    if (len(rows)<support['min_pairs'] or len({r['issue'] for r in rows})<support['min_dates']
        or any(len({r['issue'] for r,v in zip(rows,y) if bool(v)==k})<support['min_positive_dates' if k else 'min_negative_dates'] for k in [False,True])):
        return dict(family=family,constant=1.,reason='Insufficient class/date support')
    x=matrix(rows,family);hp=p['fixed_parameters'];w=date_weights(rows);w*=len(rows)/w.sum()
    if family.endswith('extra'):
        from sklearn.ensemble import ExtraTreesClassifier
        model=ExtraTreesClassifier(n_estimators=hp['extra_trees'],max_depth=hp['max_depth'],min_samples_leaf=hp['min_leaf'],
            random_state=hp['seed'],n_jobs=hp['threads']);model.fit(x,y,sample_weight=w)
        return dict(family=family,model=model)
    from sklearn.linear_model import LogisticRegression
    mean=np.average(x,axis=0,weights=w);scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1.
    model=LogisticRegression(C=hp['c'],max_iter=2000,random_state=hp['seed']);model.fit((x-mean)/scale,y,sample_weight=w)
    return dict(family=family,model=model,mean=mean,scale=scale)


def score_filter(model,rows):
    if 'constant' in model:return np.full(len(rows),model['constant'])
    x=matrix(rows,model['family'])
    if 'mean' in model:x=(x-model['mean'])/model['scale']
    return model['model'].predict_proba(x)[:,1]


def select(rows,records,p):
    selected,table=shape.first.choose(rows,records,p)
    # The same old case identities must survive all calendar boundary scenarios.
    for entry in table:
        rec=next(r for r in records if r['family']==entry['family']);j=NAMES.index(entry['index'])
        old=shape.first.prior.base_alarm(rows,[r['raw_score'][j] for r in rows],j,p)
        sensitivity={}
        for scenario,scores in rec['scenario_scores'].items():
            new=shape.first.prior.veto(old,scores[j],entry['threshold']);v=shape.first.metrics(rows,j,old,new)
            sensitivity[scenario]=v
        entry['scenario_metrics']=sensitivity
        entry['eligible']=entry['eligible'] and all(v['lost_tp']==0 and v['added_alerts']==0
            and v['metrics']['fp']<selected[j]['reference']['fp'] and v['metrics']['fp_dates']<=selected[j]['reference']['fp_dates'] for v in sensitivity.values())
    selected=[]
    for j,name in enumerate(NAMES):
        options=[e for e in table if e['index']==name and e['eligible']]
        old=shape.first.prior.base_alarm(rows,[r['raw_score'][j] for r in rows],j,p)
        reference=shape.first.confusion(rows,events(rows,j),old)
        best=min(options,key=lambda e:(e['metrics']['fp'],e['metrics']['fp_dates'],p['families'].index(e['family']),e['threshold'])) if options else None
        selected.append(dict(index=name,family=best['family'] if best else 'identity',threshold=best['threshold'] if best else 0.,
            enabled=best is not None,reference=reference,selection=best))
    return selected,table


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Frozen experiment cannot be retuned')
    p=protocol();shape.first.prior.check_inputs(p);evidence=read(D/'crop-evidence.json')
    rows=enrich(read(shape.D/'data-2024.json'),evidence);selection2024(rows);dump(D/'data-2024.json',rows)
    causal={key(r):r['raw_score'] for r in read(shape.first.prior.D/'causal-scores-2024.json')['rows']}
    scenarios=['N','NE','E','SE','S','SW','W','NW','core','outer'];validation=[];audits=[]
    record_map={f:dict(family=f,scores=[[],[]],scenario_scores={s:[[],[]] for s in scenarios}) for f in FAMILIES}
    for start,tt,vv in chronological_folds(rows,p['folds']):
        vv=[dict(r,raw_score=causal[key(r)]) for r in vv];validation.extend(vv)
        for family,record in record_map.items():
            for j in range(2):
                model=fit_filter(tt,j,family,p);record['scores'][j].extend(score_filter(model,vv).tolist())
                for s in scenarios:record['scenario_scores'][s][j].extend(score_filter(model,enrich(vv,evidence,s)).tolist())
                audits.append(dict(start=start,index=NAMES[j],family=family,fit_pairs=len(tt),fit_max_target=max(r['target'] for r in tt),fallback=model.get('reason')))
    records=list(record_map.values());selected,table=select(validation,records,p)
    dump(D/'meta-oof.json',dict(rows=validation,records=records,audits=audits));dump(D/'selection-table.json',table)
    models=[]
    for j,c in enumerate(selected):
        path=D/f'{NAMES[j]}-filter.joblib';joblib.dump(fit_filter(rows,j,c['family'],p),path)
        models.append(dict(path=path.relative_to(ROOT).as_posix(),sha256=digest(path)))
    f=dict(created_at_utc=shape.first.prior.stamp(),selected=selected,models=models,fit_pairs=len(rows),
        fit_keys=[list(key(r)) for r in rows],fit_max_target=max(r['target'] for r in rows),
        hashes={n:digest(D/n) for n in ['protocol.json','crop-evidence.json','source-manifest.json','data-2024.json','meta-oof.json','selection-table.json']},
        code_sha256=digest(__file__),production_promoted=False)
    dump(D/'frozen-selection.json',f);print('Frozen2024:',selected,flush=True)
    return f


def infer(rows,p,f):
    raw,old,point,ridge=shape.first.prior.frozen_raw(rows,p);new=np.empty_like(old);scores=np.empty_like(raw)
    for j,c in enumerate(f['selected']):
        ref=f['models'][j];path=ROOT/ref['path']
        if digest(path)!=ref['sha256']:raise ValueError('Changed model')
        scores[:,j]=score_filter(joblib.load(path),rows);new[:,j]=shape.first.prior.veto(old[:,j],scores[:,j],c['threshold'])
    return dict(raw=raw,baseline=old,filtered=new,keep_score=scores,point=point,ridge=ridge)


def evaluate():
    if (D/'report.json').exists():raise ValueError('Already evaluated; no test-driven retries')
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');shape.first.prior.check_inputs(p)
    if digest(__file__)!=f['code_sha256']:raise ValueError('Changed frozen code')
    for name,sha in f['hashes'].items():
        if digest(D/name)!=sha:raise ValueError('Changed frozen artifact')
    frozen_sha=digest(D/'frozen-selection.json');evidence=read(D/'crop-evidence.json')
    test=enrich(read(shape.D/'evaluation-features-2026.json'),evidence)
    spatial=enrich(read(shape.D/'evaluation-features-2024.json'),evidence)
    dump(D/'evaluation-features-2026.json',test)
    splits=dict(all2026=test,spatial2024=spatial,temporal2026=[r for r in test if r['fid'] not in p['holdout_ids']],
        spatial2026=[r for r in test if r['fid'] in p['holdout_ids']]);results={};predictions=[]
    for split,rr in splits.items():
        values=infer(rr,p,f);results[split]={}
        for j,name in enumerate(NAMES):
            a=shape.first.metrics(rr,j,values['baseline'][:,j],values['baseline'][:,j]);b=shape.first.metrics(rr,j,values['baseline'][:,j],values['filtered'][:,j])
            results[split][name]=dict(baseline=a,new=b,ridge=shape.first.confusion(rr,events(rr,j),values['ridge'][:,j]),
                persistence=shape.first.confusion(rr,events(rr,j),np.zeros(len(rr),bool)),
                accepted=b['lost_tp']==0 and b['added_alerts']==0 and b['metrics']['fp']<a['metrics']['fp'] and b['metrics']['fp_dates']<=a['metrics']['fp_dates'])
        for i,r in enumerate(rr):predictions.append(dict(split=split,fid=r['fid'],issue=r['issue'],target=r['target'],
            actual=r['y'],persistence=r['x'][:2],raw=values['raw'][i].tolist(),keep_score=values['keep_score'][i].tolist(),
            baseline_alarm=values['baseline'][i].tolist(),filtered_alarm=values['filtered'][i].tolist(),point=values['point'][i].tolist(),ridge_alarm=values['ridge'][i].tolist()))
    protected=read(D/'preservation-before.json');changed=[n for n,sha in protected.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha:raise ValueError('Original state changed')
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=shape.first.prior.stamp(),results=results,selected=f['selected'],frozen_sha256=frozen_sha,
        protected_files=len(protected),changed_files=changed,production_promoted=False,
        acceptance={name:all(results[s][name]['accepted'] for s in ['all2026','spatial2026']) for name in NAMES})
    dump(D/'report.json',out);print('Fixed2026:',results['all2026'],flush=True)
    return out
