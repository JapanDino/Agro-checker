"""Separate event-target experiment with chronological 2024-only model selection.

Spectral declines are observed proxies, never agronomic diagnoses. The previous
point forecast, weather detector and operational journal remain immutable.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import math

import numpy as np

from .farm_context_data import ROOT, read, dump, digest
from .field_index_experiment import D as PREVIOUS, make_pairs, day, date_weights, predict as predict_point

D = ROOT/'data/farm-research/field-decline-model'
NAMES = ['ndvi','ndmi']
BASE_FEATURES = ['ndvi','ndmi','ndvi_slope','ndmi_slope','past_lag','doy_sin','doy_cos']
EXTRA_FEATURES = ['ndvi_30d_slope','ndmi_30d_slope','ndvi_below_30d_peak','ndmi_below_30d_peak',
    'ndvi_history_sd','ndmi_history_sd','history_count','history_days',
    'ndvi_iqr','ndmi_iqr','coverage','ndvi_fraction_fallen_010','ndmi_fraction_fallen_010',
    'common_past_coverage','ndvi_iqr_change','ndmi_iqr_change']


def freeze_protocol():
    D.mkdir(parents=True,exist_ok=True)
    p = dict(schema=1,target='core NDVI/NDMI decline <= -0.10 on original same-baseline 6-8-day labels; no disease label',
        created_at_utc=datetime.now(timezone.utc).isoformat(), source_observations_sha256=digest(PREVIOUS/'observations.json'),
        old_model_sha256=digest(PREVIOUS/'frozen-model.json'),
        holdout_ids=read(PREVIOUS/'spatial-split.json')['holdout_ids'],
        diagnosis='2024 non-holdout only; source support and boundary checks are audits, never new post-test target filters',
        folds=['2024-06-01','2024-07-01','2024-08-01','2024-09-01','2024-10-01'],
        fit_rule='non-holdout 2024 records whose target < validation month start; validate issue >= month start, target < next month; crossing pairs purged',
        candidates=[dict(id=f'{features}_d{depth}_w{weight}',features=features,depth=depth,positive_weight=weight)
            for features in ['basic','history_pixels'] for depth in [2,3] for weight in [1,3]],
        iterations=120,learning_rate=.04,l2_leaf_reg=10,random_seed=1729,thread_count=2,
        thresholds=[.5,.6,.7,.8,.9,.95,.975,.99,1.01],
        selection='per index: zero added pooled false-positive rows AND false-positive issue dates vs original fixed model across all chronological OOF folds; >=2 TP rows on >=2 issue dates; maximize date-balanced recall then minimize Brier then prefer listed candidate and higher threshold',
        negative_alarm_budget=0, final_fit='all eligible non-holdout 2024 pairs only, no 2025/2026 labels/features used to select or fit',
        minimum_event_dates=2, minimum_true_positives=2,
        zero_event_training='smoothed observed prevalence (constant); no synthetic positive examples',
        features=dict(basic=BASE_FEATURES,history_pixels=BASE_FEATURES+EXTRA_FEATURES),
        historical_features='same fragment/baseline, past 30 calendar days including issue; raw spatial features only issue and previous scenes; no future scene, future acquisition lead, ID or target cloud feature',
        test='2026 only after frozen selection and models; 2024 five southern fragments also held out spatially; neither may influence tuning',
        test_status='2026 previously examined; new predictions evaluated without tuning, not a pristine external test',
        deployment='no promotion or operational write; retain previous detector regardless of test result',
        score='class-weighted event score, not a calibrated probability or treatment instruction',
        historical_live_backtest=False,
        sources='immutable same-source processed Sentinel archive; no local disease/operation truth',
        no_guarantee='Zero empirical false alarms on 2024 cannot guarantee zero on a different season.')
    path=D/'protocol.json'
    if path.exists():
        old=read(path)
        if old['source_observations_sha256']!=p['source_observations_sha256'] or old['old_model_sha256']!=p['old_model_sha256']:
            raise ValueError('Frozen source changed')
        return old
    dump(path,p)
    # Freeze input/operational file hashes BEFORE the new experiment.
    protected=[q for folder in ['models','data/field','data/farm-research/field-index-model',
        'data/farm-research/area-context','data/farm-research/reconstruction'] for q in (ROOT/folder).rglob('*') if q.is_file()]
    dump(D/'preservation-before.json',{str(q.relative_to(ROOT)):digest(q) for q in protected})
    return p


def source_index(year):
    archive=PREVIOUS if year==2024 else ROOT/'data/farm-research/area-context'
    out={}
    for scene in read(archive/'satellite.json')['scenes']:
        if scene['status']=='ready' and day(scene['acquired_at_utc']).year==year:
            out[scene['id']]=(ROOT/scene['local_folder'] if scene.get('local_folder') else archive/'sentinel'/scene['id'],scene)
    return out


def freeze_gate_revision(base):
    """A second 2024-only stage; preserve the first rejection and its protocol."""
    path=D/'gate-protocol.json'
    if path.exists():
        return read(path)
    p={**base,'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'supersedes_protocol_sha256':digest(D/'protocol.json'),
        'revision_reason':'All 8 initial candidates rejected on chronological 2024 OOF; no 2025/2026 features, predictions or outcomes examined in this experiment. Test fixed past-only momentum/level gates before evaluating other years.',
        'gates':['none','above_floor','joint_momentum','pixel_momentum'],
        'gate_definitions':dict(above_floor='current NDVI >=0.40 for NDVI alert, current NDMI >=0.00 for NDMI alert',
            joint_momentum='both last-pair slopes *7 <= -0.05 AND above_floor',
            pixel_momentum='>=40% common past/issue core pixels fell by 0.10 for this index, previous lag<=10 days AND above_floor'),
        'first_stage_frozen_sha256':digest(D/'frozen-selection.json')}
    for filename in ['frozen-selection.json','selection-table.json']:
        initial=D/('initial-'+filename)
        if not initial.exists():
            initial.write_bytes((D/filename).read_bytes())
    dump(path,p)
    return p


def historical_vector(pair,observations,current_pixels,current_valid,past_pixels,past_valid,current_coverage):
    """Future observations cannot enter history even when supplied by a caller."""
    history=[r for r in observations if r['fid']==pair['fid'] and r['baseline']==pair['baseline']
        and 0<=(day(pair['issue'])-day(r['date'])).days<=30]
    if not history:
        raise ValueError('No past history')
    values=np.array([r['values']['core']['value'] for r in history])
    age=np.array([(day(r['date'])-day(pair['issue'])).days for r in history])
    centered=age-age.mean()
    slope=(centered[:,None]*(values-values.mean(axis=0))).sum(axis=0)/max(1,float((centered**2).sum()))
    now=np.array(pair['x'][:2])
    common=current_valid & past_valid
    iqr=np.ptp(np.quantile(current_pixels[current_valid],[.25,.75],axis=0),axis=0)
    past_iqr=np.ptp(np.quantile(past_pixels[past_valid],[.25,.75],axis=0),axis=0)
    fallen=((current_pixels[common]-past_pixels[common])<=-.10).mean(axis=0) if common.any() else np.array([0.,0.])
    return [*pair['x'][:7],*slope,*(now-values.max(axis=0)),*values.std(axis=0),len(history),int(-age.min()),
        *iqr,current_coverage,*fallen,float(common.mean()),*(iqr-past_iqr)]


def build_data(year):
    """Build each year independently. 2026 is called only by evaluate()."""
    import rasterio
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely import transform as st
    from shapely.geometry import shape
    from .farm_context_analysis import masks,scenarios
    p=read(PREVIOUS/'protocol.json')
    observations=[r for r in read(PREVIOUS/'observations.json')['rows'] if day(r['date']).year==year]
    pairs=make_pairs(observations)
    index={(r['fid'],r['date']):r for r in observations}
    grouped=defaultdict(list)
    for r in observations:
        grouped[r['fid']].append(r)
    x0,y0,x1,y1=p['bounds_utm'];tr=from_origin(x0,y1,20,20);sh=(int((y1-y0)/20),int((x1-x0)/20))
    project=Transformer.from_crs(4326,32637,always_xy=True)
    grids={}
    for f in read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')['features']:
        poly=st(shape(f['geometry']),project.transform,interleaved=False)
        grids[f['properties']['id']]=masks(scenarios(poly,f['properties']['working_band_m']),tr,sh)['core']
    sources=source_index(year);cache={}
    # Restrict memory to pixels actually required by the small fixed cores.
    for sid in {r[k] for r in pairs for k in ['issue_scene','previous_scene','target_scene']}:
        folder,scene=sources[sid]
        path=folder/'indices.npz'
        if digest(path)!=scene['crop_sha256']['indices.npz']:
            raise ValueError('Source pixel bytes changed')
        with np.load(path) as compressed:
            arr={k:compressed[k].ravel() for k in ['ndvi','ndmi','valid']}
            cache[sid]={fid:(np.column_stack([arr['ndvi'][ix],arr['ndmi'][ix]]),arr['valid'][ix]) for fid,ix in grids.items()}
    for pair in pairs:
        current=index[pair['fid'],pair['issue']]
        current_pixels,current_valid=cache[pair['issue_scene']][pair['fid']]
        past_pixels,past_valid=cache[pair['previous_scene']][pair['fid']]
        pair['features']=historical_vector(pair,grouped[pair['fid']],current_pixels,current_valid,past_pixels,past_valid,current['values']['core']['coverage'])
        if not np.isfinite(pair['features']).all():
            raise ValueError('Non-finite input feature')
        # Target audit ONLY, never copied to model features or used to remove test cases.
        future_pixels,future_valid=cache[pair['target_scene']][pair['fid']]
        overlap=current_valid & future_valid
        common_delta=None
        if overlap.sum()>=10 and overlap.mean()>=.8:
            common_delta=(np.median(future_pixels[overlap],axis=0)-np.median(current_pixels[overlap],axis=0)).tolist()
        differences=[]
        target=index[pair['fid'],pair['target']]
        for variant in ['base','N','NE','E','SE','S','SW','W','NW','core','outer']:
            if variant in current['values'] and variant in target['values']:
                differences.append((np.array(target['values'][variant]['value'])-current['values'][variant]['value']).tolist())
        pair['audit']=dict(common_target_coverage=float(overlap.mean()),common_delta=common_delta,
            all_11_boundaries=len(differences)==11,delta_scenarios=differences)
    dump(D/f'data-{year}.json',pairs)
    return pairs


def events(rows,j):
    return np.array([r['y'][j]-r['x'][j] <= -.10 for r in rows])


def matrix(rows,candidate):
    n=7 if candidate['features']=='basic' else len(BASE_FEATURES+EXTRA_FEATURES)
    return np.array([r['features'][:n] for r in rows],dtype=float)


def chronological_folds(rows,starts):
    # Reject any future-year input even if passed accidentally by a caller.
    if any(day(r['issue']).year!=2024 or day(r['target']).year!=2024 for r in rows):
        raise ValueError('Selection/fitting accepts 2024 only')
    for start in starts:
        current=day(start)
        end=date(current.year+current.month//12,current.month%12+1,1).isoformat()
        train=[r for r in rows if r['target']<start]
        validation=[r for r in rows if start<=r['issue'] and r['target']<end]
        if train and validation:
            yield start,train,validation


def train(candidate,rows,j,protocol):
    from catboost import CatBoostClassifier
    if any(day(r['issue']).year!=2024 or day(r['target']).year!=2024 for r in rows):
        raise ValueError('Only 2024 labels allowed for fit')
    y=events(rows,j).astype(int)
    w=date_weights(rows)
    if len(np.unique(y))<2:
        return dict(constant=float((w@y+.5)/(w.sum()+1)))
    # Normalize total sample weight for stable regularization across expanding folds.
    w=w*len(rows)/w.sum()
    model=CatBoostClassifier(iterations=protocol['iterations'],depth=candidate['depth'],learning_rate=protocol['learning_rate'],
        l2_leaf_reg=protocol['l2_leaf_reg'],class_weights=[1,candidate['positive_weight']],loss_function='Logloss',
        random_seed=protocol['random_seed'],thread_count=protocol['thread_count'],verbose=False,allow_writing_files=False)
    model.fit(matrix(rows,candidate),y,sample_weight=w)
    return model


def score(model,candidate,rows):
    if not rows:
        return np.array([])
    if isinstance(model,dict):
        return np.full(len(rows),model['constant'])
    return model.predict_proba(matrix(rows,candidate))[:,1]


def confusion(rows,y,alarm,prob=None):
    y=np.asarray(y,dtype=bool);alarm=np.asarray(alarm,dtype=bool)
    w=date_weights(rows)
    tp=alarm&y;fp=alarm&~y;fn=~alarm&y
    denom=float(w[y].sum())
    out=dict(tp=int(tp.sum()),fp=int(fp.sum()),fn=int(fn.sum()),tn=int((~alarm&~y).sum()),
        recall=float(tp.sum()/y.sum()) if y.any() else None,precision=float(tp.sum()/alarm.sum()) if alarm.any() else None,
        date_balanced_recall=float(w[tp].sum()/denom) if denom else None,
        tp_dates=len({r['issue'] for r,v in zip(rows,tp) if v}),fp_dates=len({r['issue'] for r,v in zip(rows,fp) if v}),
        event_dates=len({r['issue'] for r,v in zip(rows,y) if v}),pairs=len(rows),dates=len({r['issue'] for r in rows}))
    if prob is not None:
        out['brier']=float(np.average((prob-y)**2,weights=w))
    return out


def gate(rows,j,name):
    if name=='none':
        return np.ones(len(rows),dtype=bool)
    above=np.array([r['x'][j]>=(.40 if j==0 else .0) for r in rows])
    if name=='above_floor':
        return above
    if name=='joint_momentum':
        return above & np.array([r['x'][2]*7<=-.05 and r['x'][3]*7<=-.05 for r in rows])
    if name=='pixel_momentum':
        return above & np.array([r['features'][18+j]>=.4 and r['x'][4]<=10 for r in rows])
    raise ValueError('Unknown gate')


def choose(rows,records,protocol):
    """All arguments are 2024 OOF only. No test-budget relaxation or threshold fallback."""
    if any(day(r['issue']).year!=2024 or day(r['target']).year!=2024 for r in rows):
        raise ValueError('Selection accepts 2024 only')
    selected=[];table=[]
    for j in range(2):
        y=events(rows,j);eligible=[]
        for order,record in enumerate(records):
            prob=np.array(record['scores'][j])
            for gate_order,gate_name in enumerate(protocol.get('gates',['none'])):
                condition=gate(rows,j,gate_name)
                for threshold in protocol['thresholds']:
                    m=confusion(rows,y,(prob>=threshold)&condition,prob)
                    ok=(m['fp']<=protocol['negative_alarm_budget'] and m['fp_dates']==0
                        and m['tp']>=protocol['minimum_true_positives'] and m['tp_dates']>=protocol['minimum_event_dates'])
                    row=dict(index=NAMES[j],candidate=record['candidate']['id'],gate=gate_name,threshold=threshold,eligible=ok,metrics=m)
                    table.append(row)
                    if ok:
                        eligible.append((-(m['date_balanced_recall'] or 0),m['brier'],order,gate_order,-threshold,row))
        if eligible:
            best=min(eligible,key=lambda x:x[:-1])[-1]
            selected.append(dict(index=NAMES[j],candidate=best['candidate'],gate=best['gate'],threshold=best['threshold'],oof_metrics=best['metrics'],enabled=True))
        else:
            selected.append(dict(index=NAMES[j],candidate=None,threshold=None,enabled=False,
                reason='No candidate catches >=2 events on >=2 dates with zero OOF false positives; retain old detector'))
    return selected,table


def diagnose(rows,model):
    output={}
    for j,name in enumerate(NAMES):
        y=events(rows,j);ev=[r for r,yes in zip(rows,y) if yes]
        primary_delta=np.array([r['y'][j]-r['x'][j] for r in ev])
        common=[r for r in ev if r['audit']['common_delta'] is not None]
        boundaries=[r for r in ev if r['audit']['all_11_boundaries']]
        predicted=predict_point(model,rows)[:,j]-np.array([r['x'][j] for r in rows])
        output[name]=dict(pairs=len(rows),events=len(ev),event_dates=len({r['issue'] for r in ev}),
            initial_fit_events=sum(r['target']<'2024-06-01' for r in ev),
            predicted_delta_min=float(predicted.min()),predicted_delta_max=float(predicted.max()),
            events_with_prior_weekly_decline_005=int(sum(r['x'][j+2]*7<=-.05 for r in ev)),
            common_support_auditable_events=len(common),events_still_declines_on_common_support=sum(r['audit']['common_delta'][j]<=-.10 for r in common),
            boundary_complete_events=len(boundaries),events_declining_in_all_11_scenarios=sum(max(d[j] for d in r['audit']['delta_scenarios'])<=-.10 for r in boundaries),
            primary_event_median_delta=float(np.median(primary_delta)) if ev else None)
    return output


def fit_experiment(gated=False):
    if (D/'report.json').exists():
        raise ValueError('2026 already evaluated: this frozen experiment cannot be retuned in place')
    protocol=freeze_protocol()
    if gated:
        protocol=freeze_gate_revision(protocol)
    allrows=build_data(2024)
    rows=[r for r in allrows if r['fid'] not in protocol['holdout_ids']]
    old=read(PREVIOUS/'frozen-model.json')['selected_model']
    dump(D/'diagnosis-2024.json',diagnose(rows,old))
    folds=list(chronological_folds(rows,protocol['folds']))
    validation=[r for _,_,vv in folds for r in vv]
    records=[]
    for candidate in protocol['candidates']:
        scores=[[],[]]
        for start,tt,vv in folds:
            for j in range(2):
                model=train(candidate,tt,j,protocol)
                scores[j].extend(score(model,candidate,vv).tolist())
        records.append(dict(candidate=candidate,scores=scores))
        print('OOF ready',candidate['id'],flush=True)
    selected,table=choose(validation,records,protocol)
    dump(D/'selection-table.json',table)
    dump(D/'oof-predictions.json',dict(rows=[dict(fid=r['fid'],issue=r['issue'],target=r['target'],event=[bool(events([r],j)[0]) for j in range(2)]) for r in validation],records=records))
    paths=[]
    for j,choice in enumerate(selected):
        if not choice['enabled']:
            paths.append(None);continue
        candidate=next(c for c in protocol['candidates'] if c['id']==choice['candidate'])
        model=train(candidate,rows,j,protocol)
        if isinstance(model,dict):
            raise ValueError('Enabled model cannot be constant')
        path=D/f'{NAMES[j]}.cbm';model.save_model(str(path));paths.append(dict(path=str(path.relative_to(ROOT)),sha256=digest(path)))
    # Freeze selection and all final fitted models before opening any 2026 data.
    active_protocol=D/('gate-protocol.json' if gated else 'protocol.json')
    frozen=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),protocol_path=str(active_protocol.relative_to(ROOT)),protocol_sha256=digest(active_protocol),
        selected=selected,models=paths,fit_pairs=len(rows),fit_dates=len({r['issue'] for r in rows}),fit_max_target=max(r['target'] for r in rows),
        fit_ids=sorted({r['fid'] for r in rows}),fitting_keys=[dict(fid=r['fid'],issue=r['issue'],target=r['target']) for r in rows],
        folds=[dict(start=start,fit_pairs=len(tt),fit_max_target=max(r['target'] for r in tt),validation_pairs=len(vv),validation_min_issue=min(r['issue'] for r in vv)) for start,tt,vv in folds],
        source_features_sha256=digest(D/'data-2024.json'),selection_table_sha256=digest(D/'selection-table.json'),
        evaluated_2026=False,operational=False,fit_code_sha256=digest(__file__))
    dump(D/'frozen-selection.json',frozen)
    print('Frozen before 2026:',selected,flush=True)
    return frozen


def predict_events(rows,protocol,frozen):
    from catboost import CatBoostClassifier
    old=read(PREVIOUS/'frozen-model.json')['selected_model']
    point=predict_point(old,rows)
    scores=np.full((len(rows),2),np.nan)
    alarms=np.zeros((len(rows),2),dtype=bool)
    # Replace spectral-event classification only where 2024 guard passed. Point series never changed.
    for j,choice in enumerate(frozen['selected']):
        if not choice['enabled']:
            alarms[:,j]=point[:,j]-np.array([r['x'][j] for r in rows])<=-.10
            continue
        entry=frozen['models'][j];path=ROOT/entry['path']
        if digest(path)!=entry['sha256']:
            raise ValueError('Model changed')
        model=CatBoostClassifier();model.load_model(str(path))
        candidate=next(c for c in protocol['candidates'] if c['id']==choice['candidate'])
        scores[:,j]=score(model,candidate,rows)
        alarms[:,j]=(scores[:,j]>=choice['threshold']) & gate(rows,j,choice.get('gate','none'))
    return alarms,scores,point


def evaluate():
    frozen=read(D/'frozen-selection.json');sha=digest(D/'frozen-selection.json')
    protocol_path=ROOT/frozen.get('protocol_path',str((D/'protocol.json').relative_to(ROOT)))
    if digest(protocol_path)!=frozen['protocol_sha256']:
        raise ValueError('Protocol changed')
    protocol=read(protocol_path)
    # No fit or choose call exists below this point.
    data=build_data(2026)
    spatial=[r for r in read(D/'data-2024.json') if r['fid'] in protocol['holdout_ids']]
    splits=dict(spatial2024=spatial,all2026=data,
        temporal2026=[r for r in data if r['fid'] not in protocol['holdout_ids']],
        spatial2026=[r for r in data if r['fid'] in protocol['holdout_ids']])
    results={};predictions=[]
    for name,rr in splits.items():
        alarm,prob,point=predict_events(rr,protocol,frozen)
        results[name]={}
        for j,index in enumerate(NAMES):
            actual=events(rr,j);previous=point[:,j]-np.array([r['x'][j] for r in rr])<=-.10
            old_metrics=confusion(rr,actual,previous)
            new_metrics=confusion(rr,actual,alarm[:,j],prob[:,j] if frozen['selected'][j]['enabled'] else None)
            results[name][index]=dict(old=old_metrics,new=new_metrics,no_fp_growth=new_metrics['fp']<=old_metrics['fp'],
                no_fp_date_growth=new_metrics['fp_dates']<=old_metrics['fp_dates'],tp_gain=new_metrics['tp']-old_metrics['tp'])
        for i,r in enumerate(rr):
            predictions.append(dict(split=name,fid=r['fid'],issue=r['issue'],target=r['target'],
                events=[bool(events([r],j)[0]) for j in range(2)],alarms=alarm[i].tolist(),
                scores=[float(v) if math.isfinite(v) else None for v in prob[i]]))
    dump(D/'test-predictions.json',predictions)
    protected=read(D/'preservation-before.json')
    changed=[p for p,h in protected.items() if not (ROOT/p).is_file() or digest(ROOT/p)!=h]
    if changed:
        raise ValueError('Previous bytes changed: '+str(changed[:5]))
    if digest(D/'frozen-selection.json')!=sha:
        raise ValueError('Selection changed while evaluating')
    failed=[name for name in NAMES if any(not v[name]['no_fp_growth'] or not v[name]['no_fp_date_growth']
        for k,v in results.items() if k in ['all2026','spatial2026'])]
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),status='research_only',protocol_sha256=digest(protocol_path),
        frozen_sha256=sha,selected=frozen['selected'],results=results,prior_files_verified_unchanged=len(protected),
        outcome='rejected_false_alarm_growth' if failed else 'research_only_not_locally_validated',
        failed_test_constraints=failed,production_promoted=False,evaluation_code_sha256=digest(__file__),
        diagnosis=read(D/'diagnosis-2024.json'),no_operational_changes=True,
        limitations=['2026 already seen in previous work; this evaluation cannot be called pristine blind.',
            '2024 OOF predictions are used for selection, not final independent quality estimates.',
            'Zero 2024 false alarms is a constraint, not a guarantee for 2026 or a future season.',
            'No crop/harvest/treatment truth; index declines are not disease or soil moisture labels.',
            'Only existing clear-sky 6-8-day pairs scored; availability/latency not validated live.',
            'Nearby fragments and scenes are correlated; five held-out fragments are not an external farm.',
            'Event score is not a calibrated probability. Original point forecast remains unchanged.'])
    dump(D/'report.json',out)
    print('Evaluation complete',results['all2026'],flush=True)
    return out
