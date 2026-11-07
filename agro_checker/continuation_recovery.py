"""Past-only continuation/recovery veto research; immutable base detectors.

The response remains a spectral drop, not damage or inspection usefulness.
Only 2024 can train/select. A frozen choice is evaluated once, without retries.
"""
from datetime import date
from collections import defaultdict
import numpy as np

from .farm_context_data import ROOT, read, dump, digest
from . import seasonal_calibration as prior
from . import crop_season_experiment as crop
from .field_decline_experiment import source_index, chronological_folds, events, confusion
from .early_season_decline import selection2024
from .field_index_experiment import day, date_weights

D = ROOT/'data/farm-research/continuation-recovery'
NAMES = prior.NAMES
key = prior.key
TEMPORAL = [f'{n}_{j}' for n in ['level','last_week','prior_week','acceleration',
    'last_minus_30d','last_minus_60d','negative_run','relative_range','drawdown'] for j in NAMES]
TEMPORAL += ['logit_ndvi','logit_ndmi','lag_week','prior_lag_week','third_supported',
    'current_coverage','coverage_change','history_count','history_span','direction_disagreement']
PIXELS = [f'{n}_{j}' for n in ['common_last_week','common_prior_week','common_acceleration',
    'continuing_fraction','turning_up_fraction','decelerating_fraction','support_shift'] for j in NAMES]
PIXELS += ['triple_coverage','triple_supported']
FAMILIES = ['identity','temporal_logit','pixels_logit','pixels_extra','recovery_logit']


def protocol():
    if (D/'protocol.json').exists():
        return read(D/'protocol.json')
    bp = read(prior.D/'protocol.json')
    files = [prior.D/'causal-scores-2024.json', prior.D/'protocol.json', prior.D/'frozen-selection.json',
        prior.base.SOURCE, crop.INDEX/'observations.json', crop.INDEX/'protocol.json',
        ROOT/'data/farm-research/reconstruction/approximate-areas.geojson',
        ROOT/'agro_checker/seasonal_calibration.py', ROOT/'agro_checker/season_version_decline.py',
        ROOT/'agro_checker/crop_season_experiment.py', ROOT/'agro_checker/field_decline_experiment.py']
    for year in [2024,2026]:
        files += [crop.D/f'context-{year}/satellite.json']
    files += [crop.INDEX/'satellite.json', ROOT/'data/farm-research/area-context/satellite.json',
        prior.base.D/'protocol.json', prior.base.D/'frozen-selection.json']
    bf=read(prior.base.D/'frozen-selection.json')
    files += [ROOT/bf['models'][k]['path'] for k in ['0-season25','1-2024_only']]
    p=dict(schema=1, created_at_utc=prior.stamp(), families=FAMILIES, base_choices=bp['base_choices'],
        input_hashes={q.relative_to(ROOT).as_posix():digest(q) for q in files},
        holdout_ids=prior.base.HOLDOUT, thresholds=[0.,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,1.01],
        folds=bp['calibration_folds'], temporal_features=TEMPORAL, pixel_features=PIXELS,
        target='UNCHANGED: future median minus issue median <= -0.10 at 6-8 days; no crop/damage label',
        recovery_target='Separate recovery head only: future change >= +0.03. Other non-events are NOT labelled recovery.',
        history='Same fragment, year and processing baseline, date<=issue, max60days. Third scene is latest clear 2-21days before previous. Missing history encoded with support flags; no test cases dropped.',
        pixels='Only issue, previous, third scenes; same eroded approximate core and common three-scene valid cells. >=10 cells and >=80% coverage. No target raster read for features.',
        causal='Reuse immutable418 prequential base scores2024; each original base fit target<issue. Filter fits only rows target<month start; monthly validation target<nextmonth. No own-fit base scores.',
        fixed_parameters=dict(c=.1,extra_trees=256,max_depth=3,min_leaf=15,seed=20241021,threads=2),
        support=dict(min_pairs=20,min_dates=5,min_positive_dates=2,min_negative_dates=2),
        fallback='Constant keep-score1, retain every baseline alert and every cohort row; no fabricated outcomes',
        veto='baseline_alarm AND keep_score>=threshold; cannot add any alert or FP. Scores are NOT calibrated probabilities.',
        selection='2024 ONLY: >=2 baselineTP on>=2dates; preserve EVERY baselineTP case, add0 alarms, STRICTLY fewer FP pairs, no growth FP dates. Min FP, FP dates, listed family order, LOWER threshold. If no strictly beneficial choice, identity threshold0.',
        final_fit='All418 causal2024 rows excluding R23-R27; final base binary weights never retrained.',
        evaluation='Only selected frozen filter vs exact baseline and unchanged Ridge/persistence; all499 pairs2026 and reserved2024. No evaluation of rejected candidates on2026.',
        acceptance='All2026 and spatial2026: lose0 baselineTP, fewer FP pairs, no growth FP dates. Research only, no production promotion.',
        test_status='2026 already examined in earlier work, not pristine blind;2025 absent. Neither chooses features/models/thresholds.',
        caveats=['2024 reused across upstream model choices; optimistic selection risk.',
            'Nearby approximate fragments and overlapping windows are dependent; no confirmed boundaries or crop/operation labels.',
            'Filter learns from expanding base scores, then transfers to prior final base weights; scores may shift.',
            'Acquisition-date retrospective data; actual historical delivery latency unverified.'],
        operational=False)
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents:continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.cbm','.joblib','.csv']:continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def past_history(row,observations):
    return sorted([r for r in observations if r['fid']==row['fid'] and r['baseline']==row['baseline']
        and r['date'][:4]==row['issue'][:4] and 0<=(day(row['issue'])-day(r['date'])).days<=60],key=lambda r:r['date'])


def third_observation(row,history):
    previous=[r for r in history if 2<=(day(row['previous'])-day(r['date'])).days<=21]
    return max(previous,key=lambda r:r['date']) if previous else None


def temporal_features(row,history):
    h=past_history(row,history);lookup={r['date']:r for r in h}
    third=third_observation(row,h);last=np.asarray(row['x'][2:4])*7
    previous=lookup.get(row['previous']);supported=third is not None and previous is not None
    lag=(day(row['previous'])-day(third['date'])).days if supported else 0
    slope=(np.array(previous['values']['core']['value'])-third['values']['core']['value'])*7/lag if supported else np.zeros(2)
    acceleration=last-slope if supported else np.zeros(2)
    runs=np.zeros(2)
    for a,b in reversed(list(zip(h,h[1:]))):
        diff=np.array(b['values']['core']['value'])-a['values']['core']['value']
        if b['date']>row['issue']:raise ValueError('Future history')
        # Count consecutive drops independently, stopping at the first recovery.
        for j in range(2):
            if runs[j]>=0:runs[j]=runs[j]+1 if diff[j]<0 else -runs[j]-1
    runs=np.where(runs<0,-runs-1,runs)/10
    coverage=lookup[row['issue']]['values']['core']['coverage']
    delta_coverage=coverage-previous['values']['core']['coverage'] if previous else 0.
    blocks=[row['x'][:2],last,slope,acceleration,last-np.array(row['features'][7:9])*7,
        last-np.array(row['season'][15:17])*7,runs,row['season'][11:13],row['season'][9:11]]
    raw=np.clip(row['raw_score'],1e-5,1-1e-5);logit=np.log(raw)-np.log1p(-raw)
    return [float(v) for b in blocks for v in b]+[*logit,min(row['x'][4],21)/7,lag/7,float(supported),
        coverage,delta_coverage,len(h)/20,(day(row['issue'])-day(h[0]['date'])).days/60,
        float(last[0]*last[1]<0)]


def pixel_features(row,third,current,previous,earlier):
    if third is None or earlier is None:return [0.]*len(PIXELS)
    a,av=current;b,bv=previous;c,cv=earlier
    common=av&bv&cv;coverage=float(common.mean())
    if common.sum()<10 or coverage<.8:return [0.]*(len(PIXELS)-2)+[coverage,0.]
    lag1=(day(row['issue'])-day(row['previous'])).days
    lag2=(day(row['previous'])-day(third['date'])).days
    recent=(a[common]-b[common])*7/lag1;past=(b[common]-c[common])*7/lag2
    recent_median=np.median(recent,axis=0);past_median=np.median(past,axis=0)
    blocks=[recent_median,past_median,recent_median-past_median,
        ((recent<0)&(past<0)).mean(axis=0),((recent>0)&(past<0)).mean(axis=0),
        ((recent<0)&(recent>past)).mean(axis=0),recent_median-np.array(row['x'][2:4])*7]
    return [float(v) for b in blocks for v in b]+[coverage,1.]


def enrich(rows,year):
    """Only past scenes are requested, never target_scene; targets remain unchanged."""
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely import transform as st
    from .farm_context_analysis import masks,scenarios
    if any(r['issue'][:4]!=str(year) for r in rows):raise ValueError('Mixed years')
    obs=crop.observations_with_context(year);by_field=defaultdict(list)
    for r in obs:by_field[r['fid']].append(r)
    p=read(crop.INDEX/'protocol.json');x0,y0,x1,y1=p['bounds_utm']
    transform=from_origin(x0,y1,20,20);grid_shape=(int((y1-y0)/20),int((x1-x0)/20))
    project=Transformer.from_crs(4326,32637,always_xy=True);grids={}
    for f in read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')['features']:
        poly=st(shape(f['geometry']),project.transform,interleaved=False)
        grids[f['properties']['id']]=masks(scenarios(poly,f['properties']['working_band_m']),transform,grid_shape)['core']
    sources=source_index(year)
    for scene in read(crop.D/f'context-{year}/satellite.json')['scenes']:
        if scene['status']=='ready':sources[scene['id']]=(crop.D/f'context-{year}/sentinel'/scene['id'],scene)
    requests=set();thirds={}
    for r in rows:
        third=third_observation(r,past_history(r,by_field[r['fid']]));thirds[key(r)]=third
        requests.update([r['issue_scene'],r['previous_scene']])
        if third is not None:requests.add(third['scene_id'])
    cache={};hashes={}
    for sid in sorted(requests):
        folder,scene=sources[sid];path=folder/'indices.npz';sha=digest(path)
        if sha!=scene['crop_sha256']['indices.npz']:raise ValueError('Changed source pixels')
        hashes[path.relative_to(ROOT).as_posix()]=sha
        with np.load(path) as data:
            a={k:data[k].ravel() for k in ['ndvi','ndmi','valid']}
            cache[sid]={fid:(np.column_stack([a['ndvi'][ix],a['ndmi'][ix]]),a['valid'][ix]) for fid,ix in grids.items()}
    result=[]
    for r in rows:
        third=thirds[key(r)];fid=r['fid']
        px=pixel_features(r,third,cache[r['issue_scene']][fid],cache[r['previous_scene']][fid],
            cache[third['scene_id']][fid] if third else None)
        temporal=temporal_features(r,by_field[fid])
        if len(temporal)!=len(TEMPORAL) or len(px)!=len(PIXELS) or not np.isfinite(temporal+px).all():
            raise ValueError('Feature schema or nonfinite data')
        result.append(dict(r,continuation=temporal,pixel_continuation=px,
            feature_audit=dict(third_date=third['date'] if third else None,third_scene=third['scene_id'] if third else None,
                latest_input=r['issue'],input_scenes=[r['issue_scene'],r['previous_scene']]+([third['scene_id']] if third else []))))
    return result,dict(pixel_hashes=hashes,requested_scenes=len(requests),
        third_supported=sum(r['continuation'][22]>.5 for r in result),
        triple_supported=sum(r['pixel_continuation'][-1]>.5 for r in result))


def matrix(rows,family):
    return np.array([r['continuation']+(r['pixel_continuation'] if family.startswith('pixels') else []) for r in rows],float)


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
    else:
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        # Normalization also uses date-balanced training-only weights.
        model=make_pipeline(StandardScaler(),LogisticRegression(C=hp['c'],max_iter=2000,random_state=hp['seed']))
        model.fit(x,label,standardscaler__sample_weight=w,logisticregression__sample_weight=w)
        return dict(model=model,family=family)
    model.fit(x,label,sample_weight=w)
    return dict(model=model,family=family)


def score_filter(model,rows):
    if 'constant' in model:return np.full(len(rows),model['constant'])
    q=model['model'].predict_proba(matrix(rows,model['family']))[:,1]
    return 1-q if model['family']=='recovery_logit' else q


def metrics(rows,j,old,new):
    y=events(rows,j);old=np.asarray(old,bool);new=np.asarray(new,bool)
    return dict(metrics=confusion(rows,y,new),lost_tp=int((old&y&~new).sum()),
        removed_fp=int((old&~y&~new).sum()),added_alerts=int((new&~old).sum()))


def choose(rows,records,p):
    selection2024(rows);selected=[];table=[]
    for j,name in enumerate(NAMES):
        old=prior.base_alarm(rows,[r['raw_score'][j] for r in rows],j,p)
        reference=confusion(rows,events(rows,j),old);eligible=[]
        for order,record in enumerate(records):
            for threshold in p['thresholds']:
                new=prior.veto(old,record['scores'][j],threshold);m=metrics(rows,j,old,new)
                passed=(reference['tp']>=2 and reference['tp_dates']>=2 and m['lost_tp']==0
                    and m['added_alerts']==0 and m['metrics']['fp']<reference['fp']
                    and m['metrics']['fp_dates']<=reference['fp_dates'])
                entry=dict(index=name,family=record['family'],threshold=threshold,eligible=passed,**m)
                table.append(entry)
                if passed:eligible.append(((m['metrics']['fp'],m['metrics']['fp_dates'],order,threshold),entry))
        best=min(eligible,key=lambda e:e[0])[1] if eligible else None
        selected.append(dict(index=name,family=best['family'] if best else 'identity',
            threshold=best['threshold'] if best else 0.,enabled=best is not None,reference=reference,selection=best))
    return selected,table


def fit():
    import joblib
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Frozen experiment cannot be retuned')
    p=protocol();prior.check_inputs(p)
    causal=read(prior.D/'causal-scores-2024.json');rows,audit=enrich(causal['rows'],2024)
    selection2024(rows);dump(D/'data-2024.json',rows);dump(D/'features-2024-audit.json',audit)
    validation=[];records={f:[[],[]] for f in p['families']};audits=[]
    for start,tt,vv in chronological_folds(rows,p['folds']):
        validation.extend(vv)
        for family in p['families']:
            for j in range(2):
                model=fit_filter(tt,j,family,p);records[family][j].extend(score_filter(model,vv).tolist())
                audits.append(dict(start=start,index=NAMES[j],family=family,fit_pairs=len(tt),
                    fit_max_target=max(r['target'] for r in tt),validation_pairs=len(vv),fallback=model.get('reason')))
    record_list=[dict(family=f,scores=records[f]) for f in p['families']]
    selected,table=choose(validation,record_list,p)
    dump(D/'meta-oof.json',dict(rows=validation,records=record_list,audits=audits));dump(D/'selection-table.json',table)
    models=[]
    for j,c in enumerate(selected):
        model=fit_filter(rows,j,c['family'],p);path=D/f"{NAMES[j]}-filter.joblib";joblib.dump(model,path)
        models.append(dict(path=path.relative_to(ROOT).as_posix(),sha256=digest(path)))
    frozen=dict(created_at_utc=prior.stamp(),selected=selected,models=models,fit_pairs=len(rows),
        fit_dates=len({r['issue'] for r in rows}),fit_keys=[list(key(r)) for r in rows],fit_max_target=max(r['target'] for r in rows),
        hashes={name:digest(D/name) for name in ['protocol.json','data-2024.json','features-2024-audit.json','meta-oof.json','selection-table.json']},
        code_sha256=digest(__file__),production_promoted=False)
    dump(D/'frozen-selection.json',frozen);print('Frozen2024 selection:',selected,flush=True)
    return frozen


def infer(rows,p,f):
    import joblib
    raw,old,point,ridge=prior.frozen_raw(rows,p);new=np.empty_like(old);scores=np.empty_like(raw)
    for j,c in enumerate(f['selected']):
        ref=f['models'][j];path=ROOT/ref['path']
        if digest(path)!=ref['sha256']:raise ValueError('Changed filter model')
        scores[:,j]=score_filter(joblib.load(path),rows)
        new[:,j]=prior.veto(old[:,j],scores[:,j],c['threshold'])
    return dict(raw=raw,baseline=old,filtered=new,keep_score=scores,point=point,ridge=ridge)


def evaluate():
    if (D/'report.json').exists():raise ValueError('Already evaluated; no test-driven retries')
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');prior.check_inputs(p)
    if digest(__file__)!=f['code_sha256']:raise ValueError('Changed frozen code')
    for name,sha in f['hashes'].items():
        if digest(D/name)!=sha:raise ValueError('Changed frozen artifact')
    frozen_sha=digest(D/'frozen-selection.json')
    spatial=[r for r in read(prior.base.SOURCE) if r['fid'] in p['holdout_ids']]
    test=read(crop.D/'data-2026.json')
    # Add original frozen scores to feature whitelist, never target information.
    for rr,year in [(spatial,2024),(test,2026)]:
        raw,_,_,_=prior.frozen_raw(rr,p)
        for i,r in enumerate(rr):r['raw_score']=raw[i].tolist()
        enriched,audit=enrich(rr,year);dump(D/f'evaluation-features-{year}.json',enriched)
        dump(D/f'evaluation-feature-audit-{year}.json',audit)
        if year==2024:spatial=enriched
        else:test=enriched
    splits=dict(spatial2024=spatial,all2026=test,temporal2026=[r for r in test if r['fid'] not in p['holdout_ids']],
        spatial2026=[r for r in test if r['fid'] in p['holdout_ids']])
    results={};predictions=[]
    for split,rr in splits.items():
        values=infer(rr,p,f);results[split]={}
        for j,name in enumerate(NAMES):
            old=values['baseline'][:,j];new=values['filtered'][:,j]
            a=metrics(rr,j,old,old);b=metrics(rr,j,old,new)
            results[split][name]=dict(baseline=a,new=b,ridge=confusion(rr,events(rr,j),values['ridge'][:,j]),
                persistence=confusion(rr,events(rr,j),np.zeros(len(rr),bool)),
                accepted=b['lost_tp']==0 and b['added_alerts']==0 and b['metrics']['fp']<a['metrics']['fp']
                    and b['metrics']['fp_dates']<=a['metrics']['fp_dates'])
        for i,r in enumerate(rr):predictions.append(dict(split=split,fid=r['fid'],issue=r['issue'],target=r['target'],
            actual=r['y'],persistence=r['x'][:2],raw=values['raw'][i].tolist(),keep_score=values['keep_score'][i].tolist(),
            baseline_alarm=values['baseline'][i].tolist(),filtered_alarm=values['filtered'][i].tolist(),
            point=values['point'][i].tolist(),ridge_alarm=values['ridge'][i].tolist()))
    protected=read(D/'preservation-before.json')
    changed=[name for name,sha in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=sha]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha:raise ValueError('Original state changed')
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=prior.stamp(),results=results,selected=f['selected'],frozen_sha256=frozen_sha,
        protected_files=len(protected),changed_files=changed,production_promoted=False,
        acceptance={name:all(results[s][name]['accepted'] for s in ['all2026','spatial2026']) for name in NAMES})
    dump(D/'report.json',out);print('Frozen test results:',results['all2026'],flush=True)
    return out
