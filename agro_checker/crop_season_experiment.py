"""Causal seasonal proxies with explicit unknown crop labels and source provenance.

No phenological stages or crop species are manufactured from reflectance. The
2024-only fit path is separate from evaluation and never edits prior experiments.
"""
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import math
import numpy as np

from .farm_context_data import ROOT,read,dump,digest
from . import field_decline_experiment as previous
from .field_index_experiment import day,date_weights

D=ROOT/'data/farm-research/crop-season-model'
PRIOR=ROOT/'data/farm-research/field-decline-model'
INDEX=ROOT/'data/farm-research/field-index-model'
SEASON_FEATURES=['history_span_days','history_count','history_supported',
    'observed_peak_ndvi','observed_peak_ndmi','observed_floor_ndvi','observed_floor_ndmi',
    'days_since_observed_peak_ndvi','days_since_observed_peak_ndmi',
    'drawdown_from_observed_peak_ndvi','drawdown_from_observed_peak_ndmi',
    'relative_observed_range_ndvi','relative_observed_range_ndmi',
    'range_supported_ndvi','range_supported_ndmi','past60d_slope_ndvi','past60d_slope_ndmi']
CALENDAR_FEATURES=['winter_active_fraction','maize_active_fraction','winter_relative_end','maize_relative_end',
    'winter_progress_if_active','maize_progress_if_active','calendar_unambiguous']


def freeze():
    if (D/'report.json').exists():
        raise ValueError('Evaluated experiment cannot be retuned in place')
    path=D/'protocol.json'
    if path.exists():
        old=read(path)
        if old['training_features']['calendar_season'][-1]!='calendar_unambiguous':
            if (D/'oof-predictions.json').exists():
                raise ValueError('Cannot change source handling after selection')
            dump(D/'protocol-before-source-audit.json',old)
            old={**old,'source_amendment':'Before any model fit: observed overlapping AEZ geometries (sum of intersection weights=2 for R01). Overlap/coverage ambiguity is NOT normalized into probabilities; six calendar features missing-coded0 with calendar_unambiguous=0. No outcome-based rule change.',
                'pre_amendment_sha256':digest(path),
                'calendar_prior':'historical regional prior only where raw geometry coverage sums to1; overlapping alternatives retained as source uncertainty and excluded from calendar inputs',
                'training_features':{**old['training_features'],'calendar_season':previous.BASE_FEATURES+previous.EXTRA_FEATURES+SEASON_FEATURES+CALENDAR_FEATURES}}
            dump(path,old)
        return old
    p=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
        hypothesis='Past-only observed seasonal position and historical regional calendar priors may separate continuing spectral declines; no actual crop or BBCH labels are known.',
        crop_policy='crop=null and actual_phase=null for every fragment; user screenshot field20 not spatially matched; 2021 crop map never relabelled as 2024/2026',
        target='UNCHANGED core decline >=0.10 on original 6-8-day issue/target pairs; neither target masks nor cases filtered by new features',
        initial_sources={str(q.relative_to(ROOT)):digest(q) for q in [INDEX/'observations.json',PRIOR/'data-2024.json',PRIOR/'frozen-selection.json',PRIOR/'oof-predictions.json',D/'sources/worldcereal-aez.geojson']},
        past_context_2024='January-February 2024 only; original response cohort remains fixed',
        past_context_2026='March 1 to July21 2026; retrieve only AFTER frozen model; used only as past inputs, never train/tune',
        spectral_history='same fragment, same processing baseline, dates <= issue, max366days; observed peak/floor to date, NOT actual full-season maximum or agronomic stage',
        phase_support='at least4 clear observation dates spanning30days; supported flag is input, no automatic event filtering',
        calendar_prior='2023 WorldCereal AEZ seasonal calendar (regional prior for winter cereals and maize) with area-weighted overlap; both crop priors always supplied, no species assignment',
        candidates=[dict(id=f'{family}_w{weight}',family=family,depth=3,positive_weight=weight) for family in ['causal_season','calendar_season'] for weight in [1,3]],
        thresholds=[.6,.7,.8,.9,.95],gate='joint_momentum',
        hyperparameters=dict(iterations=120,learning_rate=.04,l2_leaf_reg=10,random_seed=1729,thread_count=2),
        selection='2024 chronological monthly OOF June-October; zero FP and zero FP dates; >=2TP on>=2dates; date-balanced recall not below previous 2024 OOF reference; maximize recall then minimize Brier then listed order, higher threshold',
        minimum_event_dates=2,minimum_true_positives=2,negative_alarm_budget=0,
        holdout_ids=read(INDEX/'spatial-split.json')['holdout_ids'],
        folds=['2024-06-01','2024-07-01','2024-08-01','2024-09-01','2024-10-01'],
        final_fit='all original 2024 pairs excluding 5 geographic holdout fragments only',
        test='original 2026 target pairs after freezing; previously seen data, not new blind test; 2025 not used',
        production_policy='research only; original spectral point model, event experiment, weather detector and FieldStore unchanged',
        training_features=dict(causal_season=previous.BASE_FEATURES+previous.EXTRA_FEATURES+SEASON_FEATURES,
            calendar_season=previous.BASE_FEATURES+previous.EXTRA_FEATURES+SEASON_FEATURES+CALENDAR_FEATURES),
        limitations='No local crop truth or independently validated agronomic phase; historical reprocessed image availability not proven; regional calendars are coarse and not field-specific')
    dump(path,p)
    paths=[q for folder in ['models','data/field','data/farm-research/field-index-model','data/farm-research/field-decline-model',
        'data/farm-research/area-context','data/farm-research/reconstruction'] for q in (ROOT/folder).rglob('*') if q.is_file()]
    dump(D/'preservation-before.json',{str(q.relative_to(ROOT)):digest(q) for q in paths})
    return p


def check_sources(p):
    for path,sha in p['initial_sources'].items():
        if digest(ROOT/path)!=sha:
            raise ValueError('Frozen input changed: '+path)


def calendar_prior():
    """Explicit uncertain regional prior in every boundary scenario, not crop truth."""
    from shapely.geometry import shape,box
    from shapely import transform as st
    from pyproj import Transformer
    from .farm_context_analysis import scenarios
    geo=read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')
    regions=read(D/'sources/worldcereal-aez.geojson')['features']
    project=Transformer.from_crs(4326,32637,always_xy=True)
    inv=Transformer.from_crs(32637,4326,always_xy=True)
    bbox=read(INDEX/'protocol.json')['bbox_wgs84'];clip=box(*bbox).buffer(.01)
    zones=[(f['properties'],shape(f['geometry']).intersection(clip)) for f in regions if shape(f['geometry']).intersects(clip)]
    out={}
    for f in geo['features']:
        fid=f['properties']['id'];poly=st(shape(f['geometry']),project.transform,interleaved=False)
        variant_weights={}
        for name,variant in scenarios(poly,f['properties']['working_band_m']):
            if variant.is_empty:
                continue
            ll=st(variant,inv.transform,interleaved=False)
            entries=[]
            for props,zone in zones:
                intersect=zone.intersection(ll)
                if intersect.is_empty:
                    continue
                weight=st(intersect,project.transform,interleaved=False).area/variant.area
                if weight>1e-7:
                    entries.append(dict(weight=float(weight),properties=props))
            variant_weights[name]=entries
        out[fid]=dict(crop=None,crop_year=None,actual_phase=None,confirmed_boundary=False,
            historical_prior_only=True,base_aez_ids=[e['properties']['aez_id'] for e in variant_weights['base']],
            raw_coverage_by_scenario={k:sum(x['weight'] for x in v) for k,v in variant_weights.items()},
            ambiguous_prior=any(abs(sum(x['weight'] for x in v)-1)>.001 for v in variant_weights.values()),
            scenarios=variant_weights)
    rdm=read(D/'sources/rdm-collections-2024.json')
    if not isinstance(rdm,list):
        raise ValueError('Unexpected public collection response; cannot claim no matches')
    output=dict(fragments=out,source_sha256=digest(D/'sources/worldcereal-aez.geojson'),
        worldcereal_rdm_2024_public_collections_returned=len(rdm),
        rdm_query_sha256=digest(D/'sources/rdm-collections-2024.json'),
        rdm_scope='saved bbox and 2024 validity query; empty public response is not proof no ground truth exists anywhere',
        year2021_map_used_as_current_crop=False,unlocated_user_evidence=dict(field_id='20',crop_2024='soybean',
            source='user screenshot',geographically_matched=False,used_as_training_label=False),
        source_policy='Annual completed-season phenology products not used as issue-time phase; crop-calendar Phase II 2025/26 products not used.')
    dump(D/'crop-evidence.json',output)
    return output


def phase_features(pair,observations):
    """Names refer to observed past data; actual seasonal peak and BBCH stay unknown."""
    past=[r for r in observations if r['fid']==pair['fid'] and r['baseline']==pair['baseline']
        and 0<=(day(pair['issue'])-day(r['date'])).days<=366]
    if not past:
        raise ValueError('No issue-time history')
    past=sorted(past,key=lambda r:r['date'])
    values=np.array([r['values']['core']['value'] for r in past])
    ages=np.array([(day(pair['issue'])-day(r['date'])).days for r in past])
    peak=values.max(axis=0);floor=values.min(axis=0);amplitude=peak-floor
    now=np.array(pair['x'][:2]);span=int(ages.max())
    # Prefer the most recent equal observed maximum, no future argmax or smoothing.
    since=[int(ages[np.flatnonzero(values[:,j]==peak[j])[-1]]) for j in range(2)]
    supported=amplitude>=.05
    relative=np.divide(now-floor,amplitude,out=np.zeros(2),where=supported)
    recent=ages<=60;t=-ages[recent];vv=values[recent];center=t-t.mean()
    slopes=(center[:,None]*(vv-vv.mean(axis=0))).sum(axis=0)/max(1,float((center**2).sum()))
    return [span,len(past),int(len(past)>=4 and span>=30),*peak,*floor,*since,*(now-peak),*relative,
        *supported.astype(int).tolist(),*slopes]


def calendar_features(issue,entries):
    doy=day(issue).timetuple().tm_yday
    # Static calendar uses 365-day years: normalize leap-year DOY without shifting March.
    if day(issue).year%4==0 and doy>59:
        doy-=1
    if abs(sum(e['weight'] for e in entries)-1)>.001:
        return [0.]*7
    out=[0.]*6
    for entry in entries:
        w=entry['weight'];props=entry['properties']
        for k,season in enumerate(['tc-wintercereals','tc-maize-main']):
            sos=props[season+'_sos'];eos=props[season+'_eos']
            if sos is None or eos is None:
                raise ValueError('Missing regional season prior')
            duration=(eos-sos)%365;elapsed=(doy-sos)%365
            active=elapsed<=duration
            out[k]+=w*float(active)
            out[k+2]+=w*(((doy-eos+182.5)%365-182.5)/182.5)
            out[k+4]+=w*(elapsed/max(1,duration) if active else 0.)
    return out+[1.]


def observations_with_context(year):
    import rasterio
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely import transform as st
    from .farm_context_analysis import masks,scenarios,spectral_stats
    result=[r for r in read(INDEX/'observations.json')['rows'] if day(r['date']).year==year]
    context=D/f'context-{year}';snapshot=read(context/'satellite.json')
    if snapshot['counts']['extraction_error']:
        raise ValueError('Incomplete seasonal history; resolve source errors first')
    p=read(INDEX/'protocol.json');x0,y0,x1,y1=p['bounds_utm'];tr=from_origin(x0,y1,20,20);sh=(int((y1-y0)/20),int((x1-x0)/20))
    project=Transformer.from_crs(4326,32637,always_xy=True)
    grids={}
    for f in read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')['features']:
        poly=st(shape(f['geometry']),project.transform,interleaved=False)
        grids[f['properties']['id']]=masks(scenarios(poly,f['properties']['working_band_m']),tr,sh)['core']
    for scene in snapshot['scenes']:
        if scene['status']!='ready':
            continue
        folder=context/'sentinel'/scene['id'];path=folder/'indices.npz'
        if digest(path)!=scene['crop_sha256']['indices.npz']:
            raise ValueError('Context crop changed')
        sun=read(folder/'item.json')['properties'].get('view:sun_elevation')
        if sun is None or sun<20:
            continue
        with np.load(path) as compressed:
            a={k:compressed[k] for k in ['ndvi','ndmi','valid']}
        for fid,ix in grids.items():
            stats=[spectral_stats(a[k],a['valid'],ix) for k in ['ndvi','ndmi']]
            if all(s['usable'] for s in stats):
                result.append(dict(fid=fid,date=scene['acquired_at_utc'][:10],baseline=scene['processing_baseline'],
                    acquired_at_utc=scene['acquired_at_utc'],scene_id=scene['id'],
                    values=dict(core=dict(value=[s['value'] for s in stats],coverage=stats[0]['valid_fraction']))))
    unique={}
    for r in result:
        k=(r['fid'],r['date']);score=(r['values']['core']['coverage'],r['acquired_at_utc'])
        if k not in unique or score>(unique[k]['values']['core']['coverage'],unique[k]['acquired_at_utc']):
            unique[k]=r
    return list(unique.values())


def build_features(year,evidence):
    # Fixed response cohort copied read-only from the previous experiment.
    rows=read(PRIOR/f'data-{year}.json');obs=observations_with_context(year)
    by_field=defaultdict(list)
    for r in obs:
        by_field[r['fid']].append(r)
    for r in rows:
        r['season']=phase_features(r,by_field[r['fid']])
        variants=evidence['fragments'][r['fid']]['scenarios']
        r['calendar']=calendar_features(r['issue'],variants['base'])
        scenarios_values=np.array([calendar_features(r['issue'],e) for e in variants.values()])
        r['calendar_sensitivity_span']=np.ptp(scenarios_values,axis=0).tolist()
        if not np.isfinite(r['season']+r['calendar']).all():
            raise ValueError('Non-finite seasonal feature')
    dump(D/f'data-{year}.json',rows)
    return rows


def matrix(rows,candidate):
    return np.array([r['features']+r['season']+(r['calendar'] if candidate['family']=='calendar_season' else []) for r in rows],dtype=float)


def train(rows,j,candidate,p):
    from catboost import CatBoostClassifier
    if any(day(r['issue']).year!=2024 or day(r['target']).year!=2024 for r in rows):
        raise ValueError('2024 labels only')
    if set(r['fid'] for r in rows)&set(p['holdout_ids']):
        raise ValueError('Spatial holdout labels in fit')
    y=previous.events(rows,j).astype(int);w=date_weights(rows)
    if len(np.unique(y))<2:
        return dict(constant=float((w@y+.5)/(w.sum()+1)))
    hp=p['hyperparameters']
    m=CatBoostClassifier(**hp,depth=candidate['depth'],class_weights=[1,candidate['positive_weight']],
        loss_function='Logloss',verbose=False,allow_writing_files=False)
    m.fit(matrix(rows,candidate),y,sample_weight=w*len(rows)/w.sum())
    return m


def score(model,rows,candidate):
    if isinstance(model,dict):
        return np.full(len(rows),model['constant'])
    return model.predict_proba(matrix(rows,candidate))[:,1]


def select(rows,records,p,reference):
    if any(day(r['issue']).year!=2024 or day(r['target']).year!=2024 for r in rows):
        raise ValueError('Selection: 2024 only')
    output=[];table=[]
    for j,index in enumerate(['ndvi','ndmi']):
        eligible=[];y=previous.events(rows,j)
        min_recall=(reference[j].get('date_balanced_recall') or 0.)
        for order,record in enumerate(records):
            prob=np.array(record['scores'][j]);condition=previous.gate(rows,j,p['gate'])
            for threshold in p['thresholds']:
                metric=previous.confusion(rows,y,(prob>=threshold)&condition,prob)
                ok=(metric['fp']==0 and metric['fp_dates']==0 and metric['tp']>=2 and metric['tp_dates']>=2
                    and (metric['date_balanced_recall'] or 0)+1e-12>=min_recall)
                entry=dict(index=index,candidate=record['candidate']['id'],threshold=threshold,eligible=ok,metrics=metric)
                table.append(entry)
                if ok:
                    eligible.append((-(metric['date_balanced_recall'] or 0),metric['brier'],order,-threshold,entry))
        if eligible:
            best=min(eligible,key=lambda x:x[:-1])[-1]
            output.append(dict(index=index,enabled=True,candidate=best['candidate'],threshold=best['threshold'],oof_metrics=best['metrics']))
        else:
            output.append(dict(index=index,enabled=False,reason='No seasonal candidate satisfies zero OOF FP, >=2TP on>=2dates and reference recall floor; original point-event output retained'))
    return output,table


def fit():
    p=freeze();check_sources(p);evidence=calendar_prior();allrows=build_features(2024,evidence)
    rows=[r for r in allrows if r['fid'] not in p['holdout_ids']]
    folds=list(previous.chronological_folds(rows,p['folds']));validation=[r for _,_,vv in folds for r in vv]
    source_oof=read(PRIOR/'oof-predictions.json')
    old=next(v for v in source_oof['records'] if v['candidate']['id']=='history_pixels_d3_w1')
    scores_by_key={(r['fid'],r['issue'],r['target']):[old['scores'][j][i] for j in range(2)] for i,r in enumerate(source_oof['rows'])}
    reference=[]
    for j in range(2):
        prob=np.array([scores_by_key[r['fid'],r['issue'],r['target']][j] for r in validation])
        alarm=(prob>=.7)&previous.gate(validation,j,'joint_momentum') if j==0 else np.zeros(len(validation),dtype=bool)
        reference.append(previous.confusion(validation,previous.events(validation,j),alarm))
    records=[]
    for candidate in p['candidates']:
        predictions=[[],[]]
        for start,tt,vv in folds:
            for j in range(2):
                m=train(tt,j,candidate,p);predictions[j].extend(score(m,vv,candidate).tolist())
        records.append(dict(candidate=candidate,scores=predictions))
        print('Seasonal OOF ready',candidate['id'],flush=True)
    selected,table=select(validation,records,p,reference)
    dump(D/'selection-table.json',table)
    dump(D/'oof-predictions.json',dict(rows=[dict(fid=r['fid'],issue=r['issue'],target=r['target']) for r in validation],records=records,reference_metrics=reference))
    paths=[]
    for j,choice in enumerate(selected):
        if not choice['enabled']:
            paths.append(None);continue
        c=next(c for c in p['candidates'] if c['id']==choice['candidate'])
        m=train(rows,j,c,p);path=D/f'{choice["index"]}.cbm';m.save_model(str(path));paths.append(dict(path=str(path.relative_to(ROOT)),sha256=digest(path)))
    frozen=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=digest(D/'protocol.json'),
        choices=selected,models=paths,fit_pairs=len(rows),fit_dates=len({r['issue'] for r in rows}),
        fit_keys=[dict(fid=r['fid'],issue=r['issue'],target=r['target']) for r in rows],
        reference_oof_metrics=reference,features_2024_sha256=digest(D/'data-2024.json'),
        evidence_sha256=digest(D/'crop-evidence.json'),fit_code_sha256=digest(__file__),
        folds=[dict(start=s,fit_max_target=max(r['target'] for r in tt),validation_min_issue=min(r['issue'] for r in vv)) for s,tt,vv in folds],
        evaluated_2026=False,production_promoted=False)
    dump(D/'frozen-selection.json',frozen)
    print('Frozen before any new 2026 feature collection:',selected,flush=True)
    return frozen


def evaluate():
    from catboost import CatBoostClassifier
    p=read(D/'protocol.json');check_sources(p);f=read(D/'frozen-selection.json');sha=digest(D/'frozen-selection.json')
    if digest(D/'protocol.json')!=f['protocol_sha256']:
        raise ValueError('Protocol changed after selection')
    evidence=read(D/'crop-evidence.json')
    if digest(D/'crop-evidence.json')!=f['evidence_sha256']:
        raise ValueError('Prior changed after selection')
    rows=build_features(2026,evidence)
    spatial=[r for r in read(D/'data-2024.json') if r['fid'] in p['holdout_ids']]
    old_frozen=read(PRIOR/'frozen-selection.json');old_protocol=read(ROOT/old_frozen['protocol_path'])
    results={};predictions=[]
    for name,rr in dict(spatial2024=spatial,all2026=rows,
        spatial2026=[r for r in rows if r['fid'] in p['holdout_ids']],
        temporal2026=[r for r in rows if r['fid'] not in p['holdout_ids']]).items():
        old_alarms,_,point=previous.predict_events(rr,old_protocol,old_frozen)
        original=point-np.array([r['x'][:2] for r in rr])<=-.10
        new=original.copy();scores=np.full((len(rr),2),np.nan)
        results[name]={}
        for j,index in enumerate(['ndvi','ndmi']):
            choice=f['choices'][j]
            if choice['enabled']:
                entry=f['models'][j];path=ROOT/entry['path']
                if digest(path)!=entry['sha256']:
                    raise ValueError('Changed seasonal model')
                m=CatBoostClassifier();m.load_model(str(path));c=next(c for c in p['candidates'] if c['id']==choice['candidate'])
                scores[:,j]=score(m,rr,c);new[:,j]=(scores[:,j]>=choice['threshold'])&previous.gate(rr,j,p['gate'])
            y=previous.events(rr,j)
            a=previous.confusion(rr,y,original[:,j]);b=previous.confusion(rr,y,old_alarms[:,j]);c=previous.confusion(rr,y,new[:,j])
            results[name][index]=dict(original=a,previous_rejected=b,seasonal=c,
                no_fp_growth_vs_original=c['fp']<=a['fp'],no_fp_date_growth_vs_original=c['fp_dates']<=a['fp_dates'],
                no_recall_loss_vs_previous_rejected=c['tp']>=b['tp'],
                supported_history_pairs=sum(bool(r['season'][2]) for r in rr))
        for i,r in enumerate(rr):
            predictions.append(dict(split=name,fid=r['fid'],issue=r['issue'],target=r['target'],baseline=r['baseline'],
                crop=None,actual_phase=None,proxy_history_supported=bool(r['season'][2]),
                original=original[i].tolist(),previous_rejected=old_alarms[i].tolist(),seasonal=new[i].tolist(),
                actual=[bool(previous.events([r],j)[0]) for j in range(2)]))
    dump(D/'test-predictions.json',predictions)
    saved=read(D/'preservation-before.json')
    changed=[k for k,v in saved.items() if not (ROOT/k).is_file() or digest(ROOT/k)!=v]
    if changed:
        raise ValueError('Protected old files changed: '+str(changed[:5]))
    if digest(D/'frozen-selection.json')!=sha:
        raise ValueError('Selection changed during evaluation')
    checks=results['all2026']
    passed={j:all(results[k][j]['no_fp_growth_vs_original'] and results[k][j]['no_fp_date_growth_vs_original']
        for k in ['all2026','spatial2026']) and checks[j]['seasonal']['tp']>checks[j]['original']['tp'] for j in ['ndvi','ndmi']}
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),status='research_only',
        frozen_sha256=sha,results=results,choices=f['choices'],test_constraints_met=passed,production_promoted=False,
        crop_labels_inferred=False,actual_phenophase_labels_inferred=False,prior_files_verified_unchanged=len(saved),
        code_sha256=digest(__file__),limitations=['Only 2024 settings/fit. 2026 previously studied; no pristine blind claim.',
            'Crop and agronomic phase remain unknown. Calendar and spectral features are proxies only.',
            'Historical calendars may be out of date and vary by boundary; no verified crop-specific calendar.',
            'Original clear-sky target cohort fixed. History gaps and processing-baseline resets are explicit.',
            'Sample rows/dates/nearby fragments correlated. No local damage or operation ground truth.',
            'Completed-season phenology products and 2025/2026 labels not used in tuning.'])
    dump(D/'report.json',out)
    print('Seasonal evaluation:',checks,flush=True)
    return out
