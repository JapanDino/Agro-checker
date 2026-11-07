"""2024-only weather ablation for NDMI bounds; frozen point and alarms preserved."""
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from .farm_context_data import ROOT,read,dump,digest
from .field_index_experiment import predict,date_weights
from . import ndmi_error_experiment as ref
from .full_season_intervals import only2024,meta_split,weighted_quantile
from .sector_weather_data import D,enrich,RAIN_NAMES,SOIL_NAMES

FAMILIES=['none','rain','soil','both']
STYLES=['envelope','quantile','scenario']


def protocol():
    path=D/'protocol.json'
    if path.exists():return read(path)
    if not (D/'weather-summary-2024.json').exists():raise ValueError('Collect and QC 2024 weather first')
    p=dict(schema=1,created_at_utc=datetime.now(timezone.utc).isoformat(),source_2024_sha256=digest(ref.SRC),
        legacy_sha256=digest(ref.OLD),locations_sha256=digest(D/'weather-locations.json'),
        weather_2024_summary_sha256=digest(D/'weather-summary-2024.json'),
        point='Legacy point coefficients, both spectral detectors and NDVI bounds unchanged. No future-weather target inputs.',
        weather='Open-Meteo historical gfs_global, explicit source. precipitation mm and modeled soil moisture0-10cm m3/m3. Not sensors, ECMWF or ERA5. Stitched first hours of successive forecasts; not single run.',
        timing='Inputs timestamp < issue day00UTC minus48h. Proxy, not verified historical first delivery. No forecast-window weather or target-day information.',
        feature_names=dict(rain=RAIN_NAMES,soil=SOIL_NAMES),
        residuals='Same causal reference OOF before July and exact frozen out-of-fit point residuals after2024-07-21 as NDMI-only study. No in-sample errors.',
        families=FAMILIES,styles=STYLES,outer_starts=['2024-08-01','2024-09-01','2024-10-01'],
        split='Residual-fit target<first calibration issue; latest8 known issue dates calibrate, all targets<outer month; month validation targets before next month. R23-R27 outside all fit/select/calibration.',
        tail_hyperparameters=dict(iterations=120,depth=2,learning_rate=.05,l2_leaf_reg=5,random_seed=202410,thread_count=2),
        models='For each family: causal refitted event-envelopeq.05 pad.05 and upperallq.95; quantile.05/.95 weighted3 on events; scenario lower.10 onlyevents and upperallq.95. Envelope has no weather and is identically available to all families.',
        calibration='Expand by overall95th one-sided deficits, plus lower90th event deficit (eventmaximum if<3dates). No narrowing.',
        missing='Keep every scored pair. Fit learned models only on complete required weather; missing inference uses fitted global envelope and remains in metrics. Never null rain=>0.',
        selection='For each family require overall/decline coverage>=90% pooled and every supported 2024 fold; width<=110% legacy. Best weighted90% interval score. If no family candidate passes, freeze best score diagnostic with failed flag. Primary=min score among all passing; ties prefer none and listed order. Only primary can pass final study guard.',
        coverage_floor=.90,decline_floor=.90,width_limit=1.10,
        preregistered_test='Four per-family choices and primary frozen before collecting2026 weather; evaluate all four as declared ablations, never choose by2026. Primary must use weather and improve NDMI decline coverage vs legacy on all2026/spatial2026, overall>=90%, width<=110%.',
        comparisons='Report same fixed499pair cohort with missing kept, baseline radius, no-weather equal architecture, prior NDMI-only range. Preserve alarms exactly; lower bound not an alarm.',
        caveats='2024 selection reuse optimistic, few event dates, legacy radius calibrated on2024. 2026 already studied, not blind. Weather-grid background not field-scale measurements. No automatic promotion.')
    dump(path,p);return p


def matrix(rows,family):
    base=ref.features(rows)
    if family=='none':return base
    indices=list(range(4)) if family=='rain' else list(range(4,8)) if family=='soil' else list(range(8))
    values=np.array([[np.nan if r['weather'][j] is None else r['weather'][j] for j in indices] for r in rows],float)
    return np.column_stack([base,values])


def complete(rows,family):
    return np.isfinite(matrix(rows,family)).all(axis=1)


def fit_tail(rows,family,style,p,folder,radius):
    only2024(rows);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    envelope_spec=dict(id='matched_envelope',kind='event_envelope',q=.05,pad=.05)
    fallback=ref.tail_fit(rows,envelope_spec,p,folder,radius)
    model=dict(family=family,style=style,fallback=fallback,fit_pairs=len(rows),fit_dates=len({r['issue'] for r in rows}),
        fit_max_target=max(r['target'] for r in rows))
    if style=='envelope':return model
    from catboost import CatBoostRegressor
    mask=complete(rows,family);rr=[r for r,m in zip(rows,mask) if m]
    model['complete_fit_pairs']=len(rr)
    if len(rr)<20:
        model['no_fit_support']=True;return model
    residual=np.array([r['residual'][1] for r in rr]);event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rr])
    model['models']=[]
    for j,alpha in enumerate([.05,.95]):
        if style=='scenario' and j==1:
            model['models'].append(dict(constant=max(weighted_quantile(residual,date_weights(rr),.95),0)));continue
        use=event if style=='scenario' else np.ones(len(rr),bool)
        fit=[r for r,m in zip(rr,use) if m]
        if len(fit)<3:
            model['models'].append(dict(constant=fallback['constant'][j],no_event_support=True));continue
        weights=date_weights(fit)
        if style=='quantile':weights=weights*np.where(event[use],3,1)
        weights=weights*len(fit)/weights.sum()
        q=.10 if style=='scenario' else alpha
        reg=CatBoostRegressor(**p['tail_hyperparameters'],loss_function=f'Quantile:alpha={q}',verbose=False,allow_writing_files=False)
        reg.fit(matrix(fit,family),residual[use],sample_weight=weights)
        path=folder/f'{family}-{style}-{j}.cbm';reg.save_model(str(path))
        model['models'].append(dict(path=str(path.relative_to(ROOT)),sha256=digest(path)))
    return model


def raw(model,rows):
    out=ref.raw(model['fallback'],rows)
    if 'models' not in model:return out
    from catboost import CatBoostRegressor
    mask=complete(rows,model['family']);rr=[r for r,m in zip(rows,mask) if m]
    if not rr:return out
    proposed=np.empty((len(rr),2))
    for j,entry in enumerate(model['models']):
        if 'constant' in entry:proposed[:,j]=entry['constant']
        else:
            path=ROOT/entry['path']
            if digest(path)!=entry['sha256']:raise ValueError('Changed weather-tail model')
            reg=CatBoostRegressor();reg.load_model(str(path));proposed[:,j]=reg.predict(matrix(rr,model['family']))
    out[mask]=np.column_stack([np.minimum(proposed.min(axis=1),0),np.maximum(proposed.max(axis=1),0)])
    return out


def calibrate(model,rows):
    only2024(rows);tails=raw(model,rows);target=np.array([r['residual'][1] for r in rows]);weights=date_weights(rows)
    event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rows]);ev=[r for r,m in zip(rows,event) if m]
    low=max(0,weighted_quantile(tails[:,0]-target,weights,.95));high=max(0,weighted_quantile(target-tails[:,1],weights,.95))
    dates=len({r['issue'] for r in ev})
    if ev:
        deficits=tails[event,0]-target[event]
        low=max(low,weighted_quantile(deficits,date_weights(ev),.90) if dates>=3 else float(deficits.max()))
    return dict(adjustment=[low,high],pairs=len(rows),dates=len({r['issue'] for r in rows}),event_pairs=len(ev),event_dates=dates,
        first_issue=min(r['issue'] for r in rows),max_target=max(r['target'] for r in rows))


def bounds(model,cal,rows,point):
    t=raw(model,rows);lo,hi=cal['adjustment']
    return np.clip(point[:,1]+t[:,0]-lo,-1,1),np.clip(point[:,1]+t[:,1]+hi,-1,1)


def choose(table,p):
    eligible=[];family_choices={}
    for name,value in table.items():
        reasons=[]
        for label,q in [('pooled',value['pooled']),*value['folds'].items()]:
            b=table['legacy']['pooled'] if label=='pooled' else table['legacy']['folds'][label]
            if q['coverage']<p['coverage_floor']:reasons.append(label+': overall')
            if q['decline_coverage'] is not None and q['decline_coverage']<p['decline_floor']:reasons.append(label+': decline')
            if q['mean_width']>b['mean_width']*p['width_limit']:reasons.append(label+': width')
        value['rejection_reasons']=reasons
        if not reasons and name!='legacy':eligible.append(name)
    for family in FAMILIES:
        options=[k for k in table if k.startswith(family+'__')];valid=[k for k in options if k in eligible]
        choice=min(valid or options,key=lambda k:table[k]['pooled']['weighted_score'])
        family_choices[family]=dict(candidate=choice,passed_2024=bool(valid))
    primary=min(eligible,key=lambda k:table[k]['pooled']['weighted_score']) if eligible else 'legacy'
    return family_choices,primary


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Fit locked after freeze/test')
    p=protocol()
    for path,expected in [(ref.SRC,p['source_2024_sha256']),(ref.OLD,p['legacy_sha256']),
                          (D/'weather-locations.json',p['locations_sha256']),(D/'weather-summary-2024.json',p['weather_2024_summary_sha256'])]:
        if digest(path)!=expected:raise ValueError('Changed protocol input')
    rows=enrich([r for r in read(ref.SRC) if r['fid'] not in ref.HOLDOUT],2024);only2024(rows)
    old=read(ref.OLD);archive,folds=ref.residual_archive(rows,old)
    dump(D/'training-2024.json',rows);dump(D/'residuals-2024.json',dict(rows=archive,reference_folds=folds))
    specs=[(family,style) for family in FAMILIES for style in STYLES]
    table={f'{family}__{style}':dict(family=family,style=style,folds={},predictions=[]) for family,style in specs}
    table['legacy']=dict(family='none',style='legacy',folds={},predictions=[]);outer=[]
    for start in p['outer_starts']:
        end=f'2024-{int(start[5:7])+1:02d}-01';val=[r for r in rows if start<=r['issue'] and r['target']<end]
        meta,cal=meta_split(archive,start);point=predict(old['selected_model'],val)
        for family,style in specs:
            name=f'{family}__{style}';model=fit_tail(meta,family,style,p,D/'selection-models'/start,old['interval']['radius'][1])
            adjustment=calibrate(model,cal);low,high=bounds(model,adjustment,val,point)
            entry=table[name];entry['folds'][start]=ref.metrics(val,low,high)
            entry['predictions'].extend(dict(r,low=float(low[i]),high=float(high[i])) for i,r in enumerate(val))
        lo=np.clip(point[:,1]-old['interval']['radius'][1],-1,1);hi=np.clip(point[:,1]+old['interval']['radius'][1],-1,1)
        table['legacy']['folds'][start]=ref.metrics(val,lo,hi)
        table['legacy']['predictions'].extend(dict(r,low=float(lo[i]),high=float(hi[i])) for i,r in enumerate(val))
        outer.append(dict(start=start,residual_fit_max_target=max(r['target'] for r in meta),
            calibration_first_issue=min(r['issue'] for r in cal),calibration_max_target=max(r['target'] for r in cal),validation_pairs=len(val)))
    predictions={}
    for name,value in table.items():
        rr=value.pop('predictions');predictions[name]=rr
        value['pooled']=ref.metrics(rr,np.array([r['low'] for r in rr]),np.array([r['high'] for r in rr]))
    choices,primary=choose(table,p);dump(D/'selection-table.json',table);dump(D/'outer-predictions-2024.json',predictions)
    meta,cal=meta_split(archive,'2025-01-01');frozen={}
    for family,choice in choices.items():
        style=table[choice['candidate']]['style'];model=fit_tail(meta,family,style,p,D/'selected-models',old['interval']['radius'][1])
        frozen[family]=dict(**choice,model=model,calibration=calibrate(model,cal),uses_weather=family!='none' and style!='envelope')
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=digest(D/'protocol.json'),
        legacy_sha256=digest(ref.OLD),weather_2024_summary_sha256=digest(D/'weather-summary-2024.json'),
        locations_sha256=digest(D/'weather-locations.json'),choices=frozen,primary=primary,
        primary_family=table[primary]['family'],primary_uses_weather=primary!='legacy' and table[primary]['family']!='none' and table[primary]['style']!='envelope',
        residual_fit_keys=[ref.key(r) for r in meta],calibration_keys=[ref.key(r) for r in cal],outer_folds=outer,
        selection_table_sha256=digest(D/'selection-table.json'),residuals_sha256=digest(D/'residuals-2024.json'),
        code_sha256=digest(__file__),weather_code_sha256=digest(ROOT/'agro_checker/sector_weather_data.py'),
        point_changed=False,detector_changed=False,operational=False)
    dump(D/'frozen-selection.json',out);print('Frozen primary',primary,'weather',out['primary_uses_weather'],flush=True)
    for family,value in frozen.items():print(family,value['candidate'],value['passed_2024'],table[value['candidate']]['pooled'],flush=True)
    return out


def evaluate():
    from .sector_weather_data import collect_year
    from .field_decline_experiment import confusion
    f=read(D/'frozen-selection.json');sha=digest(D/'frozen-selection.json');p=read(D/'protocol.json')
    if digest(D/'protocol.json')!=f['protocol_sha256'] or digest(ref.OLD)!=f['legacy_sha256']:raise ValueError('Changed fixed input')
    if digest(__file__)!=f['code_sha256'] or digest(ROOT/'agro_checker/sector_weather_data.py')!=f['weather_code_sha256']:raise ValueError('Changed fit/eval code')
    if digest(D/'selection-table.json')!=f['selection_table_sha256'] or digest(D/'residuals-2024.json')!=f['residuals_sha256']:raise ValueError('Changed selection evidence')
    collect_year(2026)
    future=enrich(read(ROOT/'data/farm-research/field-decline-model/data-2026.json'),2026)
    reserved=enrich([r for r in read(ref.SRC) if r['fid'] in ref.HOLDOUT],2024)
    old=read(ref.OLD);radius=old['interval']['radius'];groups=dict(spatial2024=reserved,all2026=future,
        temporal2026=[r for r in future if r['fid'] not in ref.HOLDOUT],spatial2026=[r for r in future if r['fid'] in ref.HOLDOUT])
    results={};predictions=[]
    for group,rr in groups.items():
        point=predict(old['selected_model'],rr);lo=np.clip(point[:,1]-radius[1],-1,1);hi=np.clip(point[:,1]+radius[1],-1,1)
        baseline=ref.metrics(rr,lo,hi);entry=dict(legacy=baseline,families={},missing_complete_weather=sum(not r['weather_audit']['complete'] for r in rr))
        for family,value in f['choices'].items():
            low,high=bounds(value['model'],value['calibration'],rr,point);q=ref.metrics(rr,low,high)
            entry['families'][family]=dict(candidate=value['candidate'],passed_2024=value['passed_2024'],uses_weather=value['uses_weather'],metrics=q,
                weather_fallback_pairs=int((~complete(rr,family)).sum()) if value['uses_weather'] else 0,
                constraints=dict(decline_improved=q['decline_coverage']>baseline['decline_coverage'],overall=q['coverage']>=.90,width=q['mean_width']<=baseline['mean_width']*1.10))
            for i,r in enumerate(rr):
                predictions.append(dict(split=group,family=family,fid=r['fid'],issue=r['issue'],target=r['target'],actual=r['y'],point=point[i].tolist(),
                    low=float(low[i]),high=float(high[i]),weather_audit=r['weather_audit'],
                    detector=[bool(point[i,j]-r['x'][j]<=-.10) for j in range(2)],
                    ndvi_low=float(np.clip(point[i,0]-radius[0],-1,1)),ndvi_high=float(np.clip(point[i,0]+radius[0],-1,1))))
        event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rr]);alarm=point[:,1]-np.array([r['x'][1] for r in rr])<=-.10
        entry['preserved_ndmi_detector']=confusion(rr,event,alarm);results[group]=entry
    primary=f['primary_family'];passed=f['primary_uses_weather'] and all(all(results[g]['families'][primary]['constraints'].values()) for g in ['all2026','spatial2026'])
    if digest(D/'frozen-selection.json')!=sha:raise ValueError('Choice changed during test')
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),status='research_only',frozen_sha256=sha,
        primary=f['primary'],primary_uses_weather=f['primary_uses_weather'],test_constraints_passed=bool(passed),results=results,
        tuning_years=[2024],point_unchanged=True,detector_unchanged=True,ndvi_unchanged=True,production_promoted=False,
        limitations=[p['caveats'],p['timing'],p['weather'],'Per-family ablations fixed before test; do not select a better-looking2026 family.'])
    dump(D/'report.json',out);print('Test constraints',passed,flush=True)
    for g in ['all2026','spatial2026']:
        print(g,results[g],flush=True)
    return out
