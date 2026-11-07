"""Full issue-time pools for spectral triage; NOT inspection-usefulness labels.

Unknown future satellite outcomes remain in top-K denominators and comparisons.
No real visits are created; operational detectors and the journal stay immutable.
"""
from collections import defaultdict
from datetime import date
from functools import lru_cache
import hashlib
import math
import numpy as np
import joblib
from .farm_context_data import ROOT,read,dump,digest
from .field_index_experiment import D as INDEX,day,date_weights,make_pairs
from .field_decline_experiment import historical_vector,source_index,BASE_FEATURES,EXTRA_FEATURES
from . import seasonal_calibration as previous

D=ROOT/'data/farm-research/spectral-triage'
HOLDOUT=previous.base.HOLDOUT
METHODS=['recent_drop','robust_change','logit','extra']
SCENARIOS=['base','N','NE','E','SE','S','SW','W','NW','core','outer']
FEATURES=BASE_FEATURES+EXTRA_FEATURES+['common_ndvi_week','common_ndmi_week',
    'worst_boundary_ndvi_week','worst_boundary_ndmi_week','boundary_span_ndvi','boundary_span_ndmi','all_boundaries_supported']


def stamp():return previous.stamp()
def key(r):return r['fid'],r['issue']


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    files=[INDEX/'observations.json',INDEX/'protocol.json',INDEX/'satellite.json',
        ROOT/'data/farm-research/area-context/satellite.json',ROOT/'data/farm-research/reconstruction/approximate-areas.geojson',
        ROOT/'agro_checker/field_decline_experiment.py',ROOT/'agro_checker/field_index_experiment.py',
        ROOT/'agro_checker/farm_context_analysis.py',ROOT/'agro_checker/farm_context_data.py']
    p=dict(schema=1,created_at_utc=stamp(),input_hashes={q.relative_to(ROOT).as_posix():digest(q) for q in files},
        task='Rank satellite-observed fragments for prospective follow-up of future spectral declines, NOT usefulness of agronomist inspection.',
        outcome='Any NDVI or NDMI core median decline<=-0.10 on nearest same-baseline6-8day clear scene, same prior labels; unknown if unavailable.',
        human_labels='No human visits, crop/harvest/damage/usefulness labels. Cannot train an inspection-usefulness model.',
        budget_k=3,budget_status='Research comparison budget only; not an agreed field visit schedule.',
        methods=METHODS,features=FEATURES,holdout_ids=HOLDOUT,
        issue_pool='ALL eligible original archive observations with a prior same-baseline clear core2-21days earlier. Never require a future scene. Core>=10cells/.8clear. Same UTC-day dedup as frozen observations.',
        timing='Issue at end of acquisition UTC day; retrospective reprocessed archive availability at actual historical issue time unproven.',
        quality='Input pair only: common pixels and all11 boundary scenarios. Missing boundary support explicit; never exclude a candidate for lack of future outcome.',
        recent_drop='max(0,-NDVIlastslope*7,-NDMIlastslope*7)',
        robust_change='max(0, worst decline persisting across ALL11 common-pixel boundary scenarios)*7/pastlag. Missing complete support => score0, candidate kept.',
        fixed_parameters=dict(c=.1,trees=256,depth=3,min_leaf=15,seed=20241021,threads=2),
        support=dict(min_pairs=20,min_dates=5,min_positive_dates=2,min_negative_dates=2),
        fallback='recent_drop ordering when training support insufficient. No synthetic event examples.',
        folds=['2024-06-01','2024-07-01','2024-08-01','2024-09-01','2024-10-01'],
        folds_rule='Train completed known2024 target<monthstart, nonreserved. Rank EVERY input candidate in validation month. Targets>=nextmonth masked unknown for scoring, NOT dropped from ranking. No field/date splitting within a round.',
        selection='2024 ONLY. Same topmin(K,poolsize) slots. Retain every known baselineTP identity, strictly positive mean paired precision lower bound and no possible increase of negatives, including unknown outcomes. Max lower difference, then knownTP, listed method order. Else recent_drop retained.',
        missing='Known positive=1, known negative=0, unavailable=None. Precision lower=TP/slots, upper=(TP+unknown)/slots. Shared choices cancel in paired difference; unmatched unknowns take worst/best values. NOT statistical confidence intervals.',
        tie='sha256 fixed seed + fragmentID only for equal scores; no observed outcome or target used.',
        final_fit='All known nonreserved2024 labels only. Validation estimates optimistic due repeated upstream development with2024.',
        test='After frozen choice, selected method vs fixed recent_drop and robust_change on all2026 and reserved2024. Rejected learned methods NOT evaluated2026.2025 absent;2026 previously seen, not blind.',
        deployment='Research artifact only; no task scheduling, no production promotion, no change to old point forecasts/detectors/intervals/UI/journal.',
        limitations=['Future target availability is not guaranteed and may be informative.',
            'Nearby approximate fragments are not confirmed fields; dates and overlapping windows dependent.',
            'Future decline is not crop damage, yield loss or inspection usefulness.'])
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents:continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.joblib','.cbm','.csv']:continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def issue_pool(observations):
    """No search for target scenes: deleting future observations cannot remove an issue."""
    grouped=defaultdict(list)
    for r in observations:grouped[r['fid']].append(r)
    result=[]
    for fid,rr in grouped.items():
        for now in sorted(rr,key=lambda r:r['date']):
            past=[r for r in rr if r['baseline']==now['baseline'] and 2<=(day(now['date'])-day(r['date'])).days<=21]
            if not past:continue
            before=max(past,key=lambda r:r['date']);lag=(day(now['date'])-day(before['date'])).days
            value=np.array(now['values']['core']['value']);old=np.array(before['values']['core']['value']);slopes=(value-old)/lag
            angle=2*math.pi*day(now['date']).timetuple().tm_yday/365.25
            result.append(dict(fid=fid,issue=now['date'],previous=before['date'],issue_scene=now['scene_id'],
                previous_scene=before['scene_id'],baseline=now['baseline'],x=[*value.tolist(),*slopes.tolist(),lag,math.sin(angle),math.cos(angle),*now['terrain']]))
    return sorted(result,key=lambda r:(r['issue'],r['fid']))


def pixel_quality(row,current,prior):
    deltas={};coverages={}
    for name in SCENARIOS:
        a,av=current[name];b,bv=prior[name];common=av&bv
        coverage=float(common.mean()) if len(common) else 0.;coverages[name]=coverage
        if common.sum()<10 or coverage<.8:continue
        # Differences of medians on the SAME support, not independently changing masks.
        deltas[name]=(np.median(a[common],axis=0)-np.median(b[common],axis=0)).tolist()
    complete=len(deltas)==len(SCENARIOS)
    core=deltas.get('core');worst=np.max(list(deltas.values()),axis=0).tolist() if complete else None
    span=np.ptp(list(deltas.values()),axis=0).tolist() if complete else None
    return dict(common_coverage=coverages['core'],core_delta=core,boundary_delta=deltas,
        all_boundaries_supported=complete,worst_boundary_delta=worst,boundary_span=span)


def input_vector(row,history,current,prior,coverage):
    a,av=current['core'];b,bv=prior['core'];q=pixel_quality(row,current,prior)
    # Same whitelist as original historical vector: only dates<=issue.
    features=historical_vector(row,history,a,av,b,bv,coverage)
    scale=7/row['x'][4]
    features += [v*scale for v in (q['core_delta'] or [0.,0.])]
    features += [v*scale for v in (q['worst_boundary_delta'] or [0.,0.])]
    features += q['boundary_span'] or [0.,0.]
    features += [float(q['all_boundaries_supported'])]
    if len(features)!=len(FEATURES) or not np.isfinite(features).all():raise ValueError('Bad input feature schema')
    return features,q


def attach_outcomes(inputs,pairs):
    """Outcome metadata is separate from candidate eligibility and prediction inputs."""
    lookup={key(r):r for r in pairs};result=[]
    for row in inputs:
        target=lookup.get(key(row));out=dict(row,target=None,target_scene=None,y=None)
        if target:
            if target['previous_scene']!=row['previous_scene'] or target['issue_scene']!=row['issue_scene']:
                raise ValueError('Original cohort/previous scene mismatch')
            np.testing.assert_allclose(target['x'],row['x'],rtol=0,atol=1e-12)
            out.update(target=target['target'],target_scene=target['target_scene'],y=target['y'])
        result.append(out)
    return result


def build(year):
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely import transform as st
    from shapely.geometry import shape
    from .farm_context_analysis import masks,scenarios
    observations=[r for r in read(INDEX/'observations.json')['rows'] if r['date'][:4]==str(year)]
    inputs=issue_pool(observations);by_field=defaultdict(list);by_key={key(dict(r,issue=r['date'])):r for r in observations}
    for r in observations:by_field[r['fid']].append(r)
    p=read(INDEX/'protocol.json');x0,y0,x1,y1=p['bounds_utm'];tr=from_origin(x0,y1,20,20)
    grid_shape=(int((y1-y0)/20),int((x1-x0)/20));project=Transformer.from_crs(4326,32637,always_xy=True);grids={}
    for f in read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')['features']:
        poly=st(shape(f['geometry']),project.transform,interleaved=False)
        grids[f['properties']['id']]=masks(scenarios(poly,f['properties']['working_band_m']),tr,grid_shape)
    sources=source_index(year);hashes={};requested=set()
    @lru_cache(maxsize=12)
    def pixels(sid):
        folder,scene=sources[sid];path=folder/'indices.npz';sha=digest(path)
        if sha!=scene['crop_sha256']['indices.npz']:raise ValueError('Changed source pixels')
        hashes[path.relative_to(ROOT).as_posix()]=sha;requested.add(sid)
        with np.load(path) as data:
            a={k:data[k].ravel() for k in ['ndvi','ndmi','valid']}
            return {fid:{n:(np.column_stack([a['ndvi'][ix],a['ndmi'][ix]]),a['valid'][ix]) for n,ix in gg.items()} for fid,gg in grids.items()}
    for row in inputs:
        now=pixels(row['issue_scene'])[row['fid']];old=pixels(row['previous_scene'])[row['fid']]
        obs=by_key[key(row)]
        row['triage_features'],row['quality']=input_vector(row,by_field[row['fid']],now,old,obs['values']['core']['coverage'])
        row['input_audit']=dict(max_input_date=row['issue'],scenes=[row['previous_scene'],row['issue_scene']],
            max_history_date=max(r['date'] for r in by_field[row['fid']] if r['baseline']==row['baseline'] and r['date']<=row['issue']))
    # Only now attach future evidence; it cannot change the ordered input pool.
    result=attach_outcomes(inputs,make_pairs(observations))
    audit=dict(year=year,pool_size=len(result),issue_dates=len({r['issue'] for r in result}),
        known_outcomes=sum(r['y'] is not None for r in result),unknown_outcomes=sum(r['y'] is None for r in result),
        source_scenes=len(requested),pixel_hashes=hashes,pool_keys=[list(key(r)) for r in result])
    dump(D/f'pool-{year}.json',result);dump(D/f'pool-audit-{year}.json',audit)
    return result


def labels(rows):
    return [None if r.get('y') is None else bool(any(a-b<=-.10 for a,b in zip(r['y'],r['x'][:2]))) for r in rows]


def only2024(rows,require_known=False):
    if not rows or any(r['issue'][:4]!='2024' or (r.get('target') and r['target'][:4]!='2024') for r in rows):raise ValueError('2024 only')
    if any(r['fid'] in HOLDOUT for r in rows):raise ValueError('Spatial reserve excluded')
    if require_known and any(r.get('y') is None or not r['previous']<r['issue']<r['target'] for r in rows):raise ValueError('Known completed outcomes only')


def rule_score(rows,method):
    if method=='recent_drop':return np.array([max(0.,-r['x'][2]*7,-r['x'][3]*7) for r in rows])
    if method=='robust_change':return np.array([max(0.,*[-d*7/r['x'][4] for d in r['quality']['worst_boundary_delta']])
        if r['quality']['all_boundaries_supported'] else 0. for r in rows])
    raise ValueError('Unknown rule')


def fit_model(rows,method,p):
    only2024(rows,require_known=True)
    if method not in METHODS:raise ValueError('Unregistered method')
    if method in METHODS[:2]:return dict(method=method)
    y=np.array(labels(rows),bool);cfg=p['support']
    if len(rows)<cfg['min_pairs'] or len({r['issue'] for r in rows})<cfg['min_dates'] or any(len({r['issue'] for r,v in zip(rows,y) if bool(v)==k})<cfg['min_positive_dates' if k else 'min_negative_dates'] for k in [False,True]):
        return dict(method='recent_drop',requested=method,fallback='Insufficient class/date support')
    x=np.array([r['triage_features'] for r in rows]);w=date_weights(rows);w*=len(rows)/w.sum();hp=p['fixed_parameters']
    if method=='extra':
        from sklearn.ensemble import ExtraTreesClassifier
        model=ExtraTreesClassifier(n_estimators=hp['trees'],max_depth=hp['depth'],min_samples_leaf=hp['min_leaf'],random_state=hp['seed'],n_jobs=hp['threads'])
        model.fit(x,y,sample_weight=w);return dict(method=method,model=model)
    from sklearn.linear_model import LogisticRegression
    mean=np.average(x,axis=0,weights=w);scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1.
    model=LogisticRegression(C=hp['c'],max_iter=2000,random_state=hp['seed']);model.fit((x-mean)/scale,y,sample_weight=w)
    return dict(method=method,model=model,mean=mean,scale=scale)


def score(model,rows):
    if model['method'] in METHODS[:2]:return rule_score(rows,model['method'])
    x=np.array([r['triage_features'] for r in rows])
    if 'mean' in model:x=(x-model['mean'])/model['scale']
    return model['model'].predict_proba(x)[:,1]


def topk(rows,scores,p):
    scores=np.asarray(scores,float)
    if scores.shape!=(len(rows),) or not np.isfinite(scores).all():raise ValueError('Missing score or changed pool')
    if len({key(r) for r in rows})!=len(rows):raise ValueError('Duplicate candidate')
    grouped=defaultdict(list)
    for i,r in enumerate(rows):grouped[r['issue']].append(i)
    selected=np.zeros(len(rows),bool)
    for issue,ix in grouped.items():
        ordered=sorted(ix,key=lambda i:(-scores[i],hashlib.sha256(f"{p['fixed_parameters']['seed']}:{rows[i]['fid']}".encode()).hexdigest()))
        selected[ordered[:p['budget_k']]]=True
    return selected


def rank_metrics(rows,chosen,p):
    if not rows:return dict(dates=0,pool=0,slots=0,tp=0,fp=0,unknown=0,precision_lower=None,precision_upper=None,by_date=[],selected_cases=[],status='no_data')
    y=labels(rows);dates=[];cases=[]
    for issue in sorted({r['issue'] for r in rows}):
        ii=[i for i,r in enumerate(rows) if r['issue']==issue];ss=[i for i in ii if chosen[i]];n=len(ss)
        tp=sum(y[i] is True for i in ss);fp=sum(y[i] is False for i in ss);unknown=sum(y[i] is None for i in ss)
        dates.append(dict(issue=issue,pool=len(ii),slots=n,tp=tp,fp=fp,unknown=unknown,
            precision_lower=tp/n if n else None,precision_upper=(tp+unknown)/n if n else None,
            selected=[rows[i]['fid'] for i in ss]))
        cases.extend([dict(fid=rows[i]['fid'],issue=issue,outcome=y[i]) for i in ss])
    slots=sum(d['slots'] for d in dates)
    return dict(dates=len(dates),pool=len(rows),slots=slots,tp=sum(d['tp'] for d in dates),fp=sum(d['fp'] for d in dates),
        unknown=sum(d['unknown'] for d in dates),precision_lower=float(np.mean([d['precision_lower'] for d in dates])),
        precision_upper=float(np.mean([d['precision_upper'] for d in dates])),
        by_date=dates,selected_cases=cases,meaning='Future satellite events only; not useful visits. Bounds are missing-outcome scenarios, not confidence intervals.')


def paired_metrics(rows,old,new):
    if not rows:return dict(difference_lower=None,difference_upper=None,negative_growth_upper=0,lost_known_baseline_tp=0,lost_cases=[],by_date=[])
    yy=labels(rows);by_date=[];lost=[]
    for issue in sorted({r['issue'] for r in rows}):
        ii=[i for i,r in enumerate(rows) if r['issue']==issue];a=[i for i in ii if old[i]];b=[i for i in ii if new[i]]
        if len(a)!=len(b):raise ValueError('Different comparison budgets')
        added=set(b)-set(a);removed=set(a)-set(b);k=len(a)
        pos=lambda inds:sum(yy[i] is True for i in inds)
        neg=lambda inds:sum(yy[i] is False for i in inds)
        unknown=lambda inds:sum(yy[i] is None for i in inds)
        lower=(pos(added)-pos(removed)-unknown(removed))/k
        upper=(pos(added)+unknown(added)-pos(removed))/k
        by_date.append(dict(issue=issue,precision_difference_lower=lower,precision_difference_upper=upper,
            negative_growth_upper=neg(added)+unknown(added)-neg(removed)))
        lost.extend([dict(fid=rows[i]['fid'],issue=issue) for i in removed if yy[i] is True])
    return dict(difference_lower=float(np.mean([d['precision_difference_lower'] for d in by_date])),
        difference_upper=float(np.mean([d['precision_difference_upper'] for d in by_date])),
        negative_growth_upper=sum(d['negative_growth_upper'] for d in by_date),lost_known_baseline_tp=len(lost),lost_cases=lost,by_date=by_date)


def select(rows,records,p):
    only2024(rows);reference=topk(rows,rule_score(rows,'recent_drop'),p);entries=[]
    for record in records:
        selected=topk(rows,record['scores'],p);metrics=rank_metrics(rows,selected,p);paired=paired_metrics(rows,reference,selected)
        passed=paired['lost_known_baseline_tp']==0 and paired['difference_lower']>1e-12 and paired['negative_growth_upper']<=0
        entries.append(dict(method=record['method'],eligible=passed,metrics=metrics,paired=paired))
    options=[e for e in entries if e['eligible']]
    best=min(options,key=lambda e:(-e['paired']['difference_lower'],-e['metrics']['tp'],METHODS.index(e['method']))) if options else None
    return dict(method=best['method'] if best else 'recent_drop',enabled=best is not None,selection=best,
        reference=rank_metrics(rows,reference,p)),entries


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Frozen experiment cannot be retuned')
    p=protocol();previous.check_inputs(p);allrows=build(2024);rows=[r for r in allrows if r['fid'] not in HOLDOUT];only2024(rows)
    validation=[];audits=[];records={method:[] for method in METHODS}
    for start in p['folds']:
        d=day(start);end=date(d.year+d.month//12,d.month%12+1,1).isoformat()
        tt=[r for r in rows if r['y'] is not None and r['target']<start]
        # Keep every issue-time candidate; unknown evaluation outcomes do not affect ordering.
        vv=[dict(r,y=r['y'] if r['target'] is not None and r['target']<end else None) for r in rows if start<=r['issue']<end]
        if not tt or not vv:continue
        validation.extend(vv)
        for method in METHODS:
            model=fit_model(tt,method,p);records[method].extend(score(model,vv).tolist())
            audits.append(dict(start=start,method=method,fit_pairs=len(tt),fit_max_target=max(r['target'] for r in tt),
                validation_pool=len(vv),validation_known=sum(r['y'] is not None for r in vv),fallback=model.get('fallback')))
    record_list=[dict(method=method,scores=records[method]) for method in METHODS]
    selected,table=select(validation,record_list,p);dump(D/'meta-oof.json',dict(rows=validation,records=record_list,audits=audits))
    dump(D/'selection-table.json',table)
    training=[r for r in rows if r['y'] is not None];model=fit_model(training,selected['method'],p)
    path=D/'selected-ranker.joblib';joblib.dump(model,path)
    frozen=dict(created_at_utc=stamp(),selected=selected,model_path=path.relative_to(ROOT).as_posix(),model_sha256=digest(path),
        fit_pairs=len(training),fit_keys=[list(key(r)) for r in training],fit_max_target=max(r['target'] for r in training),
        hashes={n:digest(D/n) for n in ['protocol.json','pool-2024.json','pool-audit-2024.json','meta-oof.json','selection-table.json']},
        code_sha256=digest(__file__),production_promoted=False)
    dump(D/'frozen-selection.json',frozen);print('Frozen2024:',selected['method'],'pool',len(validation),'fit_known',len(training),flush=True)
    for e in table:print(e['method'],{k:e['metrics'][k] for k in ['slots','tp','fp','unknown','precision_lower','precision_upper']},
        {k:e['paired'][k] for k in ['difference_lower','difference_upper','negative_growth_upper','lost_known_baseline_tp']},flush=True)
    return frozen


def evaluate():
    if (D/'report.json').exists():raise ValueError('Already evaluated; no test-driven retries')
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');previous.check_inputs(p)
    if digest(__file__)!=f['code_sha256']:raise ValueError('Changed frozen code')
    for name,sha in f['hashes'].items():
        if digest(D/name)!=sha:raise ValueError('Changed frozen artifact')
    if digest(ROOT/f['model_path'])!=f['model_sha256']:raise ValueError('Changed model')
    frozen_sha=digest(D/'frozen-selection.json');test=build(2026);spatial=[r for r in read(D/'pool-2024.json') if r['fid'] in HOLDOUT]
    results={};predictions=[];model=joblib.load(ROOT/f['model_path'])
    for split,rr in dict(all2026=test,temporal2026=[r for r in test if r['fid'] not in HOLDOUT],spatial2026=[r for r in test if r['fid'] in HOLDOUT],spatial2024=spatial).items():
        reference=topk(rr,rule_score(rr,'recent_drop'),p);methods=dict(recent_drop=rule_score(rr,'recent_drop'),robust_change=rule_score(rr,'robust_change'),selected=score(model,rr));results[split]={}
        for method,scores in methods.items():
            chosen=topk(rr,scores,p);results[split][method]=dict(metrics=rank_metrics(rr,chosen,p),paired=paired_metrics(rr,reference,chosen))
            for i,row in enumerate(rr):predictions.append(dict(split=split,method=method,fid=row['fid'],issue=row['issue'],
                score=float(scores[i]),chosen=bool(chosen[i]),outcome=labels([row])[0]))
    protected=read(D/'preservation-before.json');changed=[n for n,sha in protected.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha:raise ValueError('Original state changed')
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=stamp(),results=results,frozen_sha256=frozen_sha,selected=f['selected'],
        protected_files=len(protected),changed_files=changed,production_promoted=False,human_visit_labels=0,
        acceptance={s:results[s]['selected']['paired']['lost_known_baseline_tp']==0 and results[s]['selected']['paired']['difference_lower']>0
            and results[s]['selected']['paired']['negative_growth_upper']<=0 for s in results})
    dump(D/'report.json',out);print('Fixed2026:',{m:{k:v['metrics'][k] for k in ['slots','tp','fp','unknown','precision_lower','precision_upper']} for m,v in results['all2026'].items()},flush=True)
    return out
