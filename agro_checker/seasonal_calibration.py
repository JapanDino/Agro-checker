"""Causal calibration and veto-only filtering of existing research detectors.

The final base models are immutable. NDVI uses season25; NDMI uses the existing
2024-only temporal classifier, not a previously rejected seasonal replacement.
"""
from datetime import datetime, timezone, date
import numpy as np
from scipy.special import expit

from .farm_context_data import ROOT, read, dump, digest
from . import season_version_decline as base
from .early_season_decline import selection2024
from .field_index_experiment import date_weights
from .field_decline_experiment import events, confusion
from .conservative_decline import gate, key, NAMES

D=ROOT/'data/farm-research/seasonal-detector-calibration'
FAMILIES=['identity','temperature','platt','beta','isotonic_shrink50','conditional','conditional_mix50']


def stamp():return datetime.now(timezone.utc).isoformat()


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    source=read(base.D/'frozen-selection.json')
    choices=[source['best_adapted'][0],source['primary'][1]]
    if not all(c['enabled'] for c in choices):raise ValueError('Required prior2024 choices absent')
    files=[base.EARLY,base.SOURCE,base.LEGACY,base.D/'protocol.json',base.D/'frozen-selection.json',
        ROOT/'agro_checker/season_version_decline.py']
    for j,c in enumerate(choices):files.append(ROOT/source['models'][f"{j}-{c['regime']}"]['path'])
    p=dict(schema=1,created_at_utc=stamp(),families=FAMILIES,base_choices=choices,
        input_hashes={q.relative_to(ROOT).as_posix():digest(q) for q in files},holdout_ids=base.HOLDOUT,
        source_scope='NDVI existing season25 candidate; NDMI existing2024-only temporal classifier. No accepted NDMI seasonal model exists.',
        goal='Reduce false-positive alerts while preserving EVERY true-positive case of each baseline, not merely equal TP totals',
        alert_policy='VETO ONLY: baseline_alarm AND calibrated_probability>=threshold. Cannot create an alert or add an FP case.',
        thresholds=[0.,.1,.2,.3,.4,.5,.6,.7,.8,.9,1.01],
        base_oof='Expanding issue-date2024 models; fit current2024 targets<issue with>=5 fit dates/20 pairs; fixed previous algorithms, features and source weights. Prior early years for NDVI only. No in-sample predictions for calibration.',
        calibration_folds=['2024-06-01','2024-07-01','2024-08-01','2024-09-01','2024-10-01'],
        meta_rule='Calibrator fits causal score rows whose target<validation month start; validation issue in month and target<next month. Crossing windows purged.',
        calibration_support=dict(min_pairs=20,min_dates=5,min_positive_dates=2,min_negative_dates=2),
        unsupported_policy='Identity fallback, retained in all denominators; no fabricated positives or missing-probability removal',
        fixed_parameters=dict(probability_clip=1e-5,temperature_bounds=[.25,4.],monotonic_penalty=.01,
            conditional_penalty=.1,maxiter=500,ftol=1e-12),
        calibration='Temperature, positive-slope regularized Platt/beta, half-shrunk isotonic; conditional logit correction with four past-only seasonal/lag inputs and its half-shrunk variant.',
        conditional_features=['observed_relative_range','past_weekly_slope_over_observed_amplitude','past_lag_capped21_div7','past_history_supported'],
        selection='2024 meta-OOF only: retain every baselineTP, no added alarms/FP and no worse Brier or logloss than raw. Minimize FP pairs, FP dates, date-balanced Brier, logloss, listed family order; ties choose LOWER veto threshold to avoid unsupported extra filtering.',
        minimum_reference='Baseline must contain>=2TP on>=2dates; otherwise retain identity/no veto and mark insufficient detection support',
        final_calibration='All causal2024 score rows, same selected family. Final base models remain exact previous binaries; calibration transfer from expanding fits to final fit is a limitation.',
        reliability_bins=[0.,.1,.2,.4,.6,.8,1.],
        acceptance='On all2026 AND spatial2026: lose0 baselineTP cases, strictly fewer FP, no growth of FP dates. Separately check original Ridge FP budget. No production promotion.',
        test='Existing499 pairs2026 only after frozen calibrators/thresholds; already examined in prior work, not pristine blind.2025 absent.',
        limitations=['2024 repeatedly used for upstream choices and calibration selection, selection estimates optimistic.',
            'Monotone probability calibration cannot improve ordering; isotonic can introduce ties.',
            'Brier/logloss reflect calibration and discrimination, not proof of calibrated agronomic probabilities.',
            'Correlated fragments and overlapping windows; no crop/operation/damage labels; no live delivery validation.'],
        reference='https://scikit-learn.org/stable/modules/calibration.html',operational=False)
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents:continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.cbm','.joblib','.csv']:continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def check_inputs(p):
    for path,sha in p['input_hashes'].items():
        if digest(ROOT/path)!=sha:raise ValueError('Changed immutable base source: '+path)


def base_alarm(rows,raw,j,p):
    c=p['base_choices'][j]
    return (np.asarray(raw)>=c['threshold'])&gate(rows,j,c['gate'])


def veto(old_alarm,probability,threshold):
    a=np.asarray(old_alarm,bool);q=np.asarray(probability,float)
    if a.shape!=q.shape or not np.isfinite(q).all():raise ValueError('Missing probability or wrong cohort')
    return a&(q>=threshold)


def causal_scores(p):
    current=[r for r in read(base.SOURCE) if r['fid'] not in base.HOLDOUT]
    early=[r for r in read(base.EARLY) if r['fid'] not in base.HOLDOUT]
    selection2024(current)
    bp=read(base.D/'protocol.json');rows=[];audits=[]
    for issue in sorted({r['issue'] for r in current}):
        fit=[r for r in current if r['target']<issue]
        if len(fit)<20 or len({r['issue'] for r in fit})<5:continue
        validation=[r for r in current if r['issue']==issue]
        scores=[]
        for j,c in enumerate(p['base_choices']):
            spec=next(s for s in bp['candidates'] if s['id']==c['regime'])
            training=(early if spec['source'] else [])+fit
            m=base.train(training,j,spec,bp)
            scores.append(base.score(m,validation,j,bp))
            audits.append(dict(index=NAMES[j],issue=issue,fit_pairs=len(training),
                fit_current_pairs=len(fit),fit_max_target=max(r['target'] for r in training),validation_pairs=len(validation)))
        for i,r in enumerate(validation):rows.append(dict(r,raw_score=[float(scores[j][i]) for j in range(2)]))
    dump(D/'causal-scores-2024.json',dict(rows=rows,folds=audits))
    return rows,audits


def context(rows,j):
    if not rows:return np.empty((0,4))
    s=base.season_matrix(rows)
    return np.array([[r['season'][11+j],s[i,6+j],min(r['x'][4],21)/7,r['season'][2]] for i,r in enumerate(rows)],float)


def logit(values,eps):
    v=np.clip(np.asarray(values,float),eps,1-eps)
    return np.log(v)-np.log1p(-v)


def support(rows,j,p):
    yy=events(rows,j);cfg=p['calibration_support']
    return (len(rows)>=cfg['min_pairs'] and len({r['issue'] for r in rows})>=cfg['min_dates']
        and len({r['issue'] for r,y in zip(rows,yy) if y})>=cfg['min_positive_dates']
        and len({r['issue'] for r,y in zip(rows,yy) if not y})>=cfg['min_negative_dates'])


def fit_calibrator(rows,j,family,p):
    if family not in p['families']:raise ValueError('Unregistered calibrator')
    if rows:selection2024(rows)
    if family=='identity':return dict(family='identity',requested=family)
    if not rows or not support(rows,j,p):
        return dict(family='identity',requested=family,reason='Insufficient causal2024 calibration support')
    from scipy.optimize import minimize,minimize_scalar
    hp=p['fixed_parameters'];eps=hp['probability_clip']
    raw=np.array([r['raw_score'][j] for r in rows]);y=events(rows,j).astype(float)
    w=date_weights(rows);w/=w.sum();z=logit(raw,eps)
    out=dict(family=family,requested=family,fit_pairs=len(rows),fit_dates=len({r['issue'] for r in rows}),
        fit_max_target=max(r['target'] for r in rows))
    def loss(linear):return float(np.average(np.logaddexp(0,linear)-y*linear,weights=w))
    if family=='temperature':
        limits=np.log(hp['temperature_bounds'])
        result=minimize_scalar(lambda t:loss(z/np.exp(t)),bounds=limits,method='bounded')
        if not result.success:return dict(family='identity',requested=family,reason='Optimizer failed')
        return dict(out,temperature=float(np.exp(result.x)))
    if family=='isotonic_shrink50':
        from sklearn.isotonic import IsotonicRegression
        m=IsotonicRegression(y_min=eps,y_max=1-eps,out_of_bounds='clip')
        m.fit(raw,y,sample_weight=w)
        return dict(out,x=m.X_thresholds_.tolist(),y=m.y_thresholds_.tolist(),mix=.5)
    if family=='platt':
        x=np.column_stack([z,np.ones(len(rows))]);initial=np.array([1.,0.]);bounds=[(0,10),(-10,10)]
        penalty=hp['monotonic_penalty']
    elif family=='beta':
        clipped=np.clip(raw,eps,1-eps)
        x=np.column_stack([np.log(clipped),-np.log1p(-clipped),np.ones(len(rows))])
        initial=np.array([1.,1.,0.]);bounds=[(0,10),(0,10),(-10,10)];penalty=hp['monotonic_penalty']
    else:
        cc=context(rows,j);mean=np.average(cc,axis=0,weights=w)
        scale=np.sqrt(np.average((cc-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1
        x=np.column_stack([z,np.ones(len(rows)),(cc-mean)/scale]);initial=np.array([1.,0.,0.,0.,0.,0.])
        bounds=[(0,10)]+[(-5,5)]*5;penalty=hp['conditional_penalty']
        out.update(mean=mean.tolist(),scale=scale.tolist(),mix=.5 if family=='conditional_mix50' else 1.)
    def objective(a):return loss(x@a)+penalty*float(np.sum((a-initial)**2))
    result=minimize(objective,initial,method='L-BFGS-B',bounds=bounds,
        options=dict(maxiter=hp['maxiter'],ftol=hp['ftol']))
    if not result.success:return dict(family='identity',requested=family,reason='Optimizer failed: '+str(result.message))
    return dict(out,coef=result.x.tolist())


def calibrate(model,rows,raw,j,p):
    raw=np.asarray(raw,float)
    if raw.shape!=(len(rows),) or not np.isfinite(raw).all():raise ValueError('Missing raw scores')
    family=model['family'];eps=p['fixed_parameters']['probability_clip'];z=logit(raw,eps)
    if family=='identity':return raw.copy()
    if family=='temperature':q=expit(z/model['temperature'])
    elif family=='isotonic_shrink50':q=(1-model['mix'])*raw+model['mix']*np.interp(raw,model['x'],model['y'])
    elif family=='platt':q=expit(np.column_stack([z,np.ones(len(rows))])@model['coef'])
    elif family=='beta':
        v=np.clip(raw,eps,1-eps)
        q=expit(np.column_stack([np.log(v),-np.log1p(-v),np.ones(len(rows))])@model['coef'])
    else:
        cc=(context(rows,j)-model['mean'])/model['scale']
        correction=expit(np.column_stack([z,np.ones(len(rows)),cc])@model['coef'])
        q=(1-model['mix'])*raw+model['mix']*correction
    return np.clip(q,eps,1-eps)


def probability_metrics(rows,j,q,p):
    q=np.asarray(q,float);yy=events(rows,j);w=date_weights(rows)
    if q.shape!=(len(rows),) or not np.isfinite(q).all():raise ValueError('Incomplete probability cohort')
    eps=p['fixed_parameters']['probability_clip'];v=np.clip(q,eps,1-eps);y=yy.astype(float)
    out=dict(brier=float(np.average((q-yy)**2,weights=w)),
        logloss=float(np.average(-y*np.log(v)-(1-y)*np.log1p(-v),weights=w)),reliability=[])
    total=w.sum();ece=0.
    for i,(lo,hi) in enumerate(zip(p['reliability_bins'],p['reliability_bins'][1:])):
        mask=(q>=lo)&(q<=hi if i==len(p['reliability_bins'])-2 else q<hi)
        if not mask.any():continue
        pred=float(np.average(q[mask],weights=w[mask]));actual=float(np.average(yy[mask],weights=w[mask]))
        ece+=float(w[mask].sum()/total)*abs(pred-actual)
        out['reliability'].append(dict(lower=lo,upper=hi,pairs=int(mask.sum()),
            dates=len({r['issue'] for r,m in zip(rows,mask) if m}),mean_probability=pred,event_frequency=actual))
    out['ece']=ece
    return out


def detection_metrics(rows,j,old,new,q,p):
    yy=events(rows,j);old=np.asarray(old,bool);new=np.asarray(new,bool)
    return dict(metrics=confusion(rows,yy,new),lost_tp=int((old&yy&~new).sum()),
        removed_fp=int((old&~yy&~new).sum()),added_alerts=int((new&~old).sum()),
        probability=probability_metrics(rows,j,q,p))


def choose(rows,records,p):
    selection2024(rows);selected=[];table=[]
    for j,name in enumerate(NAMES):
        raw=np.array([r['raw_score'][j] for r in rows]);old=base_alarm(rows,raw,j,p)
        reference=confusion(rows,events(rows,j),old);raw_quality=probability_metrics(rows,j,raw,p)
        eligible=[]
        enough=reference['tp']>=2 and reference['tp_dates']>=2
        for order,record in enumerate(records):
            q=np.asarray(record['scores'][j]);quality=probability_metrics(rows,j,q,p)
            for threshold in p['thresholds']:
                new=veto(old,q,threshold);m=detection_metrics(rows,j,old,new,q,p)
                passed=(enough and m['lost_tp']==0 and m['added_alerts']==0
                    and m['metrics']['fp']<=reference['fp'] and m['metrics']['fp_dates']<=reference['fp_dates']
                    and quality['brier']<=raw_quality['brier']+1e-12 and quality['logloss']<=raw_quality['logloss']+1e-12)
                entry=dict(index=name,family=record['family'],threshold=threshold,eligible=passed,**m)
                table.append(entry)
                if passed:eligible.append(((m['metrics']['fp'],m['metrics']['fp_dates'],quality['brier'],quality['logloss'],order,threshold),entry))
        if eligible:
            best=min(eligible,key=lambda v:v[0])[1]
            selected.append(dict(index=name,family=best['family'],threshold=best['threshold'],
                selection_metrics=best,reference=reference,raw_quality=raw_quality,enabled=True))
        else:
            selected.append(dict(index=name,family='identity',threshold=0.,reference=reference,raw_quality=raw_quality,
                enabled=False,reason='No supported2024 lossless calibration passed; raw detector retained'))
    return selected,table


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Frozen experiment cannot be retuned')
    p=protocol();check_inputs(p);rows,audits=causal_scores(p)
    validation=[];records={family:[[],[]] for family in p['families']};meta_audits=[]
    for start in p['calibration_folds']:
        d=date.fromisoformat(start);end=date(d.year+d.month//12,d.month%12+1,1).isoformat()
        tt=[r for r in rows if r['target']<start]
        vv=[r for r in rows if start<=r['issue'] and r['target']<end]
        if not vv:continue
        validation.extend(vv)
        for family in p['families']:
            for j in range(2):
                m=fit_calibrator(tt,j,family,p);raw=[r['raw_score'][j] for r in vv]
                records[family][j].extend(calibrate(m,vv,raw,j,p).tolist())
                meta_audits.append(dict(index=NAMES[j],family=family,start=start,fit_pairs=len(tt),
                    fit_max_target=max((r['target'] for r in tt),default=None),validation_min_issue=min(r['issue'] for r in vv),
                    fitted_family=m['family'],fallback=m.get('reason')))
    record_list=[dict(family=family,scores=records[family]) for family in p['families']]
    selected,table=choose(validation,record_list,p)
    dump(D/'selection-table.json',table);dump(D/'meta-oof.json',dict(rows=validation,records=record_list,audits=meta_audits))
    models=[fit_calibrator(rows,j,c['family'],p) for j,c in enumerate(selected)]
    f=dict(created_at_utc=stamp(),selected=selected,calibrators=models,
        calibration_keys=[list(key(r)) for r in rows],calibration_pairs=len(rows),calibration_dates=len({r['issue'] for r in rows}),
        calibration_max_target=max(r['target'] for r in rows),protocol_sha256=digest(D/'protocol.json'),
        causal_sha256=digest(D/'causal-scores-2024.json'),meta_sha256=digest(D/'meta-oof.json'),
        selection_sha256=digest(D/'selection-table.json'),code_sha256=digest(__file__),production_promoted=False)
    dump(D/'frozen-selection.json',f)
    print('Frozen:',[(c['index'],c['family'],c['threshold'],c['reference'],c.get('selection_metrics',{}).get('metrics')) for c in selected],flush=True)
    return f


def frozen_raw(rows,p):
    bp=read(base.D/'protocol.json');bf=read(base.D/'frozen-selection.json')
    first=base.infer(rows,bp,bf,'best_adapted');second=base.infer(rows,bp,bf,'2024_only')
    raw=np.array([[a[0],b[1]] for a,b in zip(first[1],second[1])],float)
    old=np.column_stack([first[0][:,0],second[0][:,1]])
    return raw,old,first[2],first[3]


def infer(rows,p,f):
    raw,old,point,ridge=frozen_raw(rows,p);q=np.empty_like(raw);new=np.empty_like(old)
    for j,c in enumerate(f['selected']):
        q[:,j]=calibrate(f['calibrators'][j],rows,raw[:,j],j,p)
        new[:,j]=veto(old[:,j],q[:,j],c['threshold'])
    return dict(raw=raw,baseline=old,calibrated=q,filtered=new,point=point,ridge=ridge)


def evaluate():
    if (D/'report.json').exists():raise ValueError('Already evaluated; no test-driven retries')
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');check_inputs(p)
    if digest(__file__)!=f['code_sha256'] or digest(D/'protocol.json')!=f['protocol_sha256']:raise ValueError('Changed frozen protocol/code')
    frozen_sha=digest(D/'frozen-selection.json')
    test=read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    splits=dict(spatial2024=[r for r in read(base.SOURCE) if r['fid'] in base.HOLDOUT],all2026=test,
        temporal2026=[r for r in test if r['fid'] not in base.HOLDOUT],spatial2026=[r for r in test if r['fid'] in base.HOLDOUT])
    results={};predictions=[]
    for split,rr in splits.items():
        values=infer(rr,p,f);results[split]={}
        for j,name in enumerate(NAMES):
            old=values['baseline'][:,j];new=values['filtered'][:,j]
            raw_m=detection_metrics(rr,j,old,old,values['raw'][:,j],p)
            new_m=detection_metrics(rr,j,old,new,values['calibrated'][:,j],p)
            ridge=confusion(rr,events(rr,j),values['ridge'][:,j])
            passed=new_m['lost_tp']==0 and new_m['added_alerts']==0 and new_m['metrics']['fp']<raw_m['metrics']['fp'] and new_m['metrics']['fp_dates']<=raw_m['metrics']['fp_dates']
            results[split][name]=dict(raw=raw_m,new=new_m,ridge=ridge,lossless_fp_reduction=passed,
                passes_working_fp_budget=new_m['metrics']['fp']<=ridge['fp'] and new_m['metrics']['fp_dates']<=ridge['fp_dates'])
        for i,r in enumerate(rr):predictions.append(dict(split=split,fid=r['fid'],issue=r['issue'],target=r['target'],
            actual=r['y'],persistence=r['x'][:2],raw=values['raw'][i].tolist(),calibrated=values['calibrated'][i].tolist(),
            baseline_alarm=values['baseline'][i].tolist(),filtered_alarm=values['filtered'][i].tolist(),
            point=values['point'][i].tolist(),ridge_alarm=values['ridge'][i].tolist()))
    protected=read(D/'preservation-before.json')
    changed=[name for name,sha in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=sha]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha:raise ValueError('Original state or frozen choice changed: '+repr(changed[:5]))
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=stamp(),selected=f['selected'],results=results,frozen_sha256=frozen_sha,
        test_constraints={name:all(results[s][name]['lossless_fp_reduction'] for s in ['all2026','spatial2026']) for name in NAMES},
        protected_files=len(protected),changed_files=changed,production_promoted=False,
        evaluation_code_sha256=digest(__file__),no2025='Absent from existing archive; no fitting/selection on2025/2026')
    dump(D/'report.json',out)
    print('2026:',results['all2026'],flush=True)
    return out
