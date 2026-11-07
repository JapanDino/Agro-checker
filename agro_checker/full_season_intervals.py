"""2024-only full-season point fit and causal residual-tail experiment.

Original detector remains separate; bounds are not event probabilities or diagnoses.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import math
import numpy as np
from .farm_context_data import ROOT, read, dump, digest
from .field_index_experiment import fit_model, predict, date_weights

D=ROOT/'data/farm-research/full-season-intervals'
SOURCE=ROOT/'data/farm-research/field-decline-model/data-2024.json'
OLD=ROOT/'data/farm-research/field-index-model/frozen-model.json'
HOLDOUT=['R23','R24','R25','R26','R27']
SPECS=['date_max','global_asymmetric','conditional','event_weighted']


def key(r): return r['fid'],r['issue'],r['target']


def only2024(rows):
    if not rows or any(r['issue'][:4]!='2024' or r['target'][:4]!='2024' for r in rows):
        raise ValueError('Fit/selection/calibration accepts nonempty 2024 data only')
    if any(r['fid'] in HOLDOUT for r in rows):
        raise ValueError('Reserved fragments cannot fit/select/calibrate')
    if len(set(map(key,rows)))!=len(rows):
        raise ValueError('Duplicate cohort rows')


def protocol():
    path=D/'protocol.json'
    if path.exists(): return read(path)
    D.mkdir(parents=True,exist_ok=True)
    p=dict(schema=1,created_at_utc=datetime.now(timezone.utc).isoformat(),
        status='preregistered_before_fit',source_2024_sha256=digest(SOURCE),old_point_sha256=digest(OLD),
        holdout_ids=HOLDOUT,point_model='temporal_ridge_100',
        point_policy='Same fixed seven features and alpha previously chosen on 2024; isolate full-season fit. No new point hyperparameter search.',
        final_fit='all eligible 2024 pairs outside five reserved fragments, March-October',
        oof='one issue-date fold; fit only rows with target < issue; minimum 5 fit dates and 20 rows',
        interval_outer_starts=['2024-08-01','2024-09-01','2024-10-01'],
        outer_target_policy='issue in month and target before next month; crossing pairs purged',
        meta_split='Only OOF rows with target < outer start. Last 8 issue dates for calibration; residual-fit target < first calibration issue. >=3 residual-fit dates and >=20 rows.',
        interval_candidates=SPECS,quantile_alphas=[.05,.95],
        tail_hyperparameters=dict(iterations=120,depth=2,learning_rate=.05,l2_leaf_reg=5,random_seed=202410,thread_count=2),
        calibration='Empirical date-balanced one-sided 95th residual shortfall; event_weighted also 90th lower shortfall among actual declines. If <3 event dates use event maximum, if none use overall only.',
        event='future index minus current index <= -0.10, spectral event only',
        event_fit_weight=3,minimum_overall_coverage=.90,minimum_decline_coverage=.85,
        maximum_width_ratio_vs_date_max=1.10,
        selection='Among candidates meeting both per-index coverage floors and width budget on causal outer 2024 folds, minimize date-balanced 90% interval score with weight 3 on declines. If none, keep date_max comparator and declare no candidate passed.',
        selection_limit='2024 outer predictions used for candidate selection, not independent quality estimates. Final full-season point and causal-OOF residual models differ in fit size; no exact conformal guarantee.',
        detector_policy='Keep original frozen point-derived event alarms bitwise. Independently report unprotected alarms from full-season point; any increase in FP or FP dates on all2026 or spatial2026 fails point-warning replacement.',
        test_constraints='Interval: improve decline coverage for both indices vs legacy, overall>=90%, full width<=110% of legacy, on all2026 and spatial2026. No promotion even if passes without local observations.',
        evaluation='Only frozen choice on all2026, temporal2026, spatial2026 and reserved spatial2024. No test-driven retries. 2026 already seen, not pristine blind.',
        unavailable='2025 absent; no fabricated crop, disease, inspection or moisture labels',
        source_archive='Retrospective acquisition-date experiment; reprocessed bytes availability at original issue time not proven')
    dump(path,p);return p


def train_point(rows):
    only2024(rows)
    return fit_model('temporal_ridge_100',rows)


def causal_oof(rows):
    only2024(rows)
    result=[];folds=[]
    for issue in sorted({r['issue'] for r in rows}):
        train=[r for r in rows if r['target']<issue]
        val=[r for r in rows if r['issue']==issue]
        if len(train)<20 or len({r['issue'] for r in train})<5: continue
        model=train_point(train);pred=predict(model,val)
        for r,point in zip(val,pred):
            result.append(dict(r,point_oof=point.tolist(),residual=(np.array(r['y'])-point).tolist()))
        folds.append(dict(issue=issue,fit_pairs=len(train),fit_max_target=max(r['target'] for r in train),validation_pairs=len(val)))
    return result,folds


def meta_split(oof, cutoff):
    only2024(oof)
    known=[r for r in oof if r['target']<cutoff]
    dates=sorted({r['issue'] for r in known})
    if len(dates)<11: raise ValueError('Too few past residual dates')
    first=dates[-8]
    fit=[r for r in known if r['target']<first]
    calib=[r for r in known if r['issue']>=first]
    if len(fit)<20 or len({r['issue'] for r in fit})<3:
        raise ValueError('Too few purged residual-fit dates')
    return fit,calib


def features(rows):
    # Current/previous inputs only. No target, residual, event or future phase.
    x=np.array([r['x'][:7] for r in rows],float)
    return np.column_stack([x,abs(x[:,2:4]),x[:,2]*7,x[:,3]*7])


def weighted_quantile(values, weights, q):
    values=np.asarray(values,float); weights=np.asarray(weights,float)
    if not len(values) or not np.isfinite(values).all() or not np.isfinite(weights).all() or np.any(weights<=0):
        raise ValueError('Finite samples and positive weights required')
    order=np.argsort(values,kind='stable');v,w=values[order],weights[order]
    i=min(int(np.searchsorted(np.cumsum(w),q*w.sum(),side='left')),len(v)-1)
    return float(v[i])


def fit_tails(rows,spec,p,folder):
    only2024(rows)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    residual=np.array([r['residual'] for r in rows]);w=date_weights(rows)
    result=dict(spec=spec,fit_pairs=len(rows),fit_dates=len({r['issue'] for r in rows}),fit_max_target=max(r['target'] for r in rows))
    if spec=='date_max':
        result['constant']=[[-max(abs(residual[:,j])),max(abs(residual[:,j]))] for j in range(2)]
    elif spec=='global_asymmetric':
        result['constant']=[[weighted_quantile(residual[:,j],w,.05),weighted_quantile(residual[:,j],w,.95)] for j in range(2)]
    else:
        from catboost import CatBoostRegressor
        result['models']=[]
        for j in range(2):
            event=np.array([r['y'][j]-r['x'][j]<=-.10 for r in rows])
            weight=w*np.where(event,p['event_fit_weight'] if spec=='event_weighted' else 1,1)
            weight=weight*len(rows)/weight.sum()
            entries=[]
            for alpha in p['quantile_alphas']:
                model=CatBoostRegressor(**p['tail_hyperparameters'],loss_function=f'Quantile:alpha={alpha}',
                                       verbose=False,allow_writing_files=False)
                model.fit(features(rows),residual[:,j],sample_weight=weight)
                path=folder/f'{spec}-{j}-{alpha}.cbm';model.save_model(str(path))
                entries.append(dict(path=str(path.relative_to(ROOT)),sha256=digest(path)))
            result['models'].append(entries)
    return result


def raw_tails(model,rows):
    if 'constant' in model:
        return np.tile(np.array(model['constant'])[None,:,:],(len(rows),1,1))
    from catboost import CatBoostRegressor
    values=np.empty((len(rows),2,2))
    for j,entries in enumerate(model['models']):
        for k,entry in enumerate(entries):
            path=ROOT/entry['path']
            if digest(path)!=entry['sha256']: raise ValueError('Changed tail weights')
            m=CatBoostRegressor();m.load_model(str(path));values[:,j,k]=m.predict(features(rows))
    # Fix crossing without narrowing: min/max raw outputs, include zero residual.
    low=np.minimum(values.min(axis=2),0); high=np.maximum(values.max(axis=2),0)
    return np.stack([low,high],axis=2)


def calibrate_tails(model,rows):
    only2024(rows)
    tails=raw_tails(model,rows);residual=np.array([r['residual'] for r in rows]);w=date_weights(rows)
    adjustment=[];support=[]
    for j in range(2):
        low=max(0,weighted_quantile(tails[:,j,0]-residual[:,j],w,.95))
        high=max(0,weighted_quantile(residual[:,j]-tails[:,j,1],w,.95))
        mask=np.array([r['y'][j]-r['x'][j]<=-.10 for r in rows])
        event_rows=[r for r,m in zip(rows,mask) if m];dates=len({r['issue'] for r in event_rows})
        if model['spec']=='event_weighted' and event_rows:
            deficit=tails[mask,j,0]-residual[mask,j]
            event_margin=weighted_quantile(deficit,date_weights(event_rows),.90) if dates>=3 else float(deficit.max())
            low=max(low,event_margin)
        adjustment.append([low,high]);support.append(dict(decline_pairs=int(mask.sum()),decline_dates=dates,
            event_maximum_fallback=model['spec']=='event_weighted' and 0<dates<3,
            no_event_support=model['spec']=='event_weighted' and dates==0))
    return dict(adjustment=adjustment,pairs=len(rows),dates=len({r['issue'] for r in rows}),support=support,
                first_issue=min(r['issue'] for r in rows),max_target=max(r['target'] for r in rows))


def bounds(model,calibration,rows,points):
    tails=raw_tails(model,rows);adj=np.array(calibration['adjustment'])
    low=np.clip(points+tails[:,:,0]-adj[:,0],-1,1)
    high=np.clip(points+tails[:,:,1]+adj[:,1],-1,1)
    return low,high


def interval_metrics(rows,point,low,high):
    if not rows: return dict(pairs=0,dates=0,per_index={},weighted_interval_score=None)
    actual=np.array([r['y'] for r in rows]);w=date_weights(rows);total=[];out={}
    for j,name in enumerate(['ndvi','ndmi']):
        event=np.array([r['y'][j]-r['x'][j]<=-.10 for r in rows]);width=high[:,j]-low[:,j]
        covered=(actual[:,j]>=low[:,j])&(actual[:,j]<=high[:,j])
        score=width+20*np.maximum(low[:,j]-actual[:,j],0)+20*np.maximum(actual[:,j]-high[:,j],0)
        value=dict(mae=float(np.average(abs(point[:,j]-actual[:,j]),weights=w)),coverage=float(np.average(covered,weights=w)),
                   mean_width=float(np.average(width,weights=w)),interval_score=float(np.average(score,weights=w)))
        for label,mask in [('decline',event),('other',~event)]:
            rr=[r for r,m in zip(rows,mask) if m]
            value[label+'_pairs']=int(mask.sum());value[label+'_dates']=len({r['issue'] for r in rr})
            value[label+'_coverage']=float(np.average(covered[mask],weights=date_weights(rr))) if rr else None
            value[label+'_width']=float(np.average(width[mask],weights=date_weights(rr))) if rr else None
        total.append(float(np.average(score*np.where(event,3,1),weights=w)))
        out[name]=value
    return dict(pairs=len(rows),dates=len({r['issue'] for r in rows}),per_index=out,
                weighted_interval_score=float(np.mean(total)))


def select_candidate(table,p):
    reference=table['date_max'];accepted=[]
    for spec in p['interval_candidates']:
        row=table[spec];reasons=[]
        for j in ['ndvi','ndmi']:
            q=row['per_index'][j];b=reference['per_index'][j]
            if q['coverage']<p['minimum_overall_coverage']: reasons.append(j+': overall coverage')
            if q['decline_coverage'] is None or q['decline_coverage']<p['minimum_decline_coverage']: reasons.append(j+': decline coverage')
            if q['mean_width']>b['mean_width']*p['maximum_width_ratio_vs_date_max']: reasons.append(j+': width budget')
        row['selection_rejection_reasons']=reasons
        if not reasons: accepted.append(spec)
    choice=min(accepted,key=lambda s:table[s]['weighted_interval_score']) if accepted else 'date_max'
    return choice,bool(accepted)


def fit():
    if (D/'report.json').exists() or (D/'frozen-selection.json').exists():
        raise ValueError('Fit locked after selection/evaluation; no test-driven retries')
    p=protocol()
    if digest(SOURCE)!=p['source_2024_sha256'] or digest(OLD)!=p['old_point_sha256']:
        raise ValueError('Source changed after protocol freeze')
    rows=[r for r in read(SOURCE) if r['fid'] not in HOLDOUT];only2024(rows)
    oof,folds=causal_oof(rows);dump(D/'causal-oof-2024.json',dict(rows=oof,folds=folds))
    validation={s:[] for s in SPECS};coverage_folds=[]
    for start in p['interval_outer_starts']:
        month=int(start[5:7]);end=f'2024-{month+1:02d}-01'
        train=[r for r in rows if r['target']<start]
        val=[r for r in rows if start<=r['issue'] and r['target']<end]
        meta,calib=meta_split(oof,start)
        point=predict(train_point(train),val)
        for spec in SPECS:
            tail=fit_tails(meta,spec,p,D/'selection-models'/start)
            calibration=calibrate_tails(tail,calib)
            low,high=bounds(tail,calibration,val,point)
            validation[spec].extend(dict(r,point_validation=point[i].tolist(),low=low[i].tolist(),high=high[i].tolist()) for i,r in enumerate(val))
        coverage_folds.append(dict(start=start,point_fit_max_target=max(r['target'] for r in train),
            residual_fit_max_target=max(r['target'] for r in meta),calibration_first_issue=min(r['issue'] for r in calib),
            calibration_max_target=max(r['target'] for r in calib),validation_pairs=len(val)))
    table={}
    for spec,rr in validation.items():
        table[spec]=interval_metrics(rr,np.array([r['point_validation'] for r in rr]),np.array([r['low'] for r in rr]),np.array([r['high'] for r in rr]))
    choice,passed=select_candidate(table,p)
    dump(D/'selection-table.json',table);dump(D/'outer-predictions-2024.json',validation)
    meta,calib=meta_split(oof,'2025-01-01')
    tail=fit_tails(meta,choice,p,D/'selected-models');calibration=calibrate_tails(tail,calib)
    point=train_point(rows)
    final=dict(schema=1,created_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=digest(D/'protocol.json'),
        source_2024_sha256=digest(SOURCE),selected_interval=choice,selection_constraints_passed=passed,
        point=point,tail=tail,calibration=calibration,outer_folds=coverage_folds,
        fit_keys=[key(r) for r in rows],oof_sha256=digest(D/'causal-oof-2024.json'),
        selection_table_sha256=digest(D/'selection-table.json'),
        residual_fit_keys=[key(r) for r in meta],calibration_keys=[key(r) for r in calib],
        selected_before_test=True,detector_preserved=True,operational=False,
        fit_code_sha256=digest(__file__))
    dump(D/'frozen-selection.json',final)
    print('Frozen 2024 selection:',choice,'constraints',passed,'point',point['fit_pairs'],point['fit_dates'],point['fit_max_target'],flush=True)
    print('Selection table:',table,flush=True)
    return final


def evaluate():
    f=read(D/'frozen-selection.json');fsha=digest(D/'frozen-selection.json');p=read(D/'protocol.json')
    if digest(D/'protocol.json')!=f['protocol_sha256'] or digest(SOURCE)!=f['source_2024_sha256'] or digest(OLD)!=p['old_point_sha256']:
        raise ValueError('Changed frozen input/protocol')
    if digest(D/'causal-oof-2024.json')!=f['oof_sha256'] or digest(D/'selection-table.json')!=f['selection_table_sha256']:
        raise ValueError('Changed selection evidence')
    from .field_decline_experiment import confusion
    legacy=read(OLD);old=legacy['selected_model'];radius=np.array(legacy['interval']['radius'])
    future=read(ROOT/'data/farm-research/field-decline-model/data-2026.json')
    reserved=[r for r in read(SOURCE) if r['fid'] in HOLDOUT]
    groups=dict(spatial2024=reserved,all2026=future,
        temporal2026=[r for r in future if r['fid'] not in HOLDOUT],spatial2026=[r for r in future if r['fid'] in HOLDOUT])
    results={};predictions=[]
    for group,rr in groups.items():
        point=predict(f['point'],rr);previous=predict(old,rr)
        low,high=bounds(f['tail'],f['calibration'],rr,point)
        oldlow=np.clip(previous-radius,-1,1);oldhigh=np.clip(previous+radius,-1,1)
        before=interval_metrics(rr,previous,oldlow,oldhigh);after=interval_metrics(rr,point,low,high)
        result=dict(before=before,after=after,events={},interval_constraints={})
        for j,index in enumerate(['ndvi','ndmi']):
            actual=np.array([r['y'][j]-r['x'][j]<=-.10 for r in rr])
            detector=previous[:,j]-np.array([r['x'][j] for r in rr])<=-.10
            proposed=point[:,j]-np.array([r['x'][j] for r in rr])<=-.10
            prior=confusion(rr,actual,detector);raw=confusion(rr,actual,proposed)
            result['events'][index]=dict(preserved_detector=prior,full_season_point_raw=raw,
                preserved_detector_identical=True,raw_no_fp_growth=raw['fp']<=prior['fp'],raw_no_fp_date_growth=raw['fp_dates']<=prior['fp_dates'])
            a,b=after['per_index'][index],before['per_index'][index]
            result['interval_constraints'][index]=dict(
                decline_coverage_improved=a['decline_coverage'] is not None and b['decline_coverage'] is not None and a['decline_coverage']>b['decline_coverage'],
                overall_coverage=a['coverage']>=.90,width_budget=a['mean_width']<=b['mean_width']*1.10)
        results[group]=result
        for i,r in enumerate(rr):
            predictions.append(dict(split=group,fid=r['fid'],issue=r['issue'],target=r['target'],
                actual=r['y'],previous=previous[i].tolist(),point=point[i].tolist(),
                low=low[i].tolist(),high=high[i].tolist(),oldlow=oldlow[i].tolist(),oldhigh=oldhigh[i].tolist()))
    if digest(D/'frozen-selection.json')!=fsha: raise ValueError('Choice changed during test')
    point_guard=all(results[g]['events'][j]['raw_no_fp_growth'] and results[g]['events'][j]['raw_no_fp_date_growth']
                    for g in ['all2026','spatial2026'] for j in ['ndvi','ndmi'])
    interval_guard={j:all(all(results[g]['interval_constraints'][j].values()) for g in ['all2026','spatial2026']) for j in ['ndvi','ndmi']}
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),status='research_only',
        selected_interval=f['selected_interval'],selection_constraints_passed=f['selection_constraints_passed'],
        frozen_sha256=fsha,results=results,raw_point_warning_guard_passed=point_guard,
        interval_constraints_passed=interval_guard,preserved_detector_unchanged=True,
        production_promoted=False,tuning_years=[2024],test_years=[2026],
        point_fit_pairs=f['point']['fit_pairs'],point_fit_dates=f['point']['fit_dates'],point_max_target=f['point']['fit_max_target'],
        limitations=[p['selection_limit'],p['evaluation'],p['source_archive'],
            'Only clear-sky fixed original cohorts, not uninterrupted daily forecasts.',
            'Conditional coverage is descriptive. Tail calibration transfers from rolling OOF to full-season point fit; no distribution-free guarantee.',
            'No local agronomic labels. All areas approximate; R18 absent due insufficient core. Preserved detector FP does not prove raw point warning safety.'],
        evaluation_code_sha256=digest(__file__))
    dump(D/'report.json',out);print('Evaluation complete:',out['raw_point_warning_guard_passed'],interval_guard,flush=True)
    return out
