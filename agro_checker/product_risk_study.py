"""Prior-only selection; frozen controls and legacy point/detector kept intact."""
from collections import Counter
from datetime import datetime
import json
import math
from .pipeline import ROOT, UTC, write_json
from .product_data import load, sha
from . import product_risk as learner
from .scenario_study import load_rows, nights
from .cold_interval_study import parent_assets
from .frost_study import fit_cold, predict_cold
from .model_study import load_protocol
from .minimum_model import point_minimum


def station_nights(rows):
    return [{'site_id':'349290-99999','site_name':'Krasnodar original ISD-Lite/METAR','latitude':45.0333,'longitude':39.15,'elevation_m':33,
      'date':n['date'],'year':n['year'],'times':[r['valid_at_utc'] for r in n['rows']],
      'run_at_utc':n['rows'][0]['forecast_run_at_utc'],'issued_at_utc':n['rows'][0]['issued_at_utc'],
      'gfs_temperature_c':n['base']['temperature'],'gfs_dewpoint_c':[r['features_weather'][2] for r in n['rows']],
      'target_temperature_c':n['target']['temperature']} for n in nights(rows)]


def event_prediction(f,threshold):
    return {'probability':f['probability'],'mask':f['conditional_representative_mask'] if f['probability']>=threshold else [False]*14,
            'early_onset_index':f['early_onset_index'] if f['probability']>=threshold else None,'onset_left_censored':f['onset_left_censored'],
            'conditional_duration_range_h':f['conditional_duration_range_h'],'details':f}


def score(records):
    usable=[r for r in records if learner.complete(r['sample'])];counts=Counter(hits=0,misses=0,false_alarms=0,correct_negatives=0)
    errors=[];brier=[];cold_brier=[];warm_brier=[];onset=[];late=[];covered=[];width=[];cold_width=[];iscore=[]
    bins=[{'lower':a,'upper':b,'nights':0,'events':0,'probability_sum':0.} for a,b in zip([0,.1,.25,.5,.75],[.1,.25,.5,.75,1.000001])]
    for r in usable:
        n=r['sample'];f=r['forecast'];actual=n['target_temperature_c'];cold=min(actual)<0;pred=any(f['mask']);p=f['probability']
        counts['hits' if cold and pred else 'misses' if cold else 'false_alarms' if pred else 'correct_negatives']+=1
        v=(p-int(cold))**2;brier.append(v);(cold_brier if cold else warm_brier).append(v)
        for b in bins:
            if b['lower']<=p<b['upper']:b['nights']+=1;b['events']+=cold;b['probability_sum']+=p;break
        if cold:
            errors.append(abs(sum(f['mask'])-sum(t<0 for t in actual)))
            idx=next(i for i,t in enumerate(actual) if t<0)
            if pred and idx>0 and f['early_onset_index'] is not None and not f.get('onset_left_censored'):
                onset.append(abs(f['early_onset_index']-(idx-1)));late.append(f['early_onset_index']>idx-1)
        details=f.get('details',{})
        if 'minimum_range_c' in details:
            lo,hi=details['minimum_range_c'];value=min(actual);covered.append(lo<=value<=hi);w=hi-lo;width.append(w)
            if cold:cold_width.append(w);iscore.append(w+20*max(lo-value,value-hi,0))
    mean=lambda x:sum(x)/len(x) if x else None
    return {'complete_nights':len(usable),'excluded_nights':len(records)-len(usable),**dict(counts),
      'brier':mean(brier),'cold_brier':mean(cold_brier),'warm_brier':mean(warm_brier),
      'cold_duration_mae_h':mean(errors),'early_onset_edge_mae_h':mean(onset),'onset_pairs':len(onset),'late_edge_fraction':mean(late),
      'range_coverage':mean(covered),'mean_range_width_c':mean(width),'cold_range_width_c':mean(cold_width),'cold_interval_score':mean(iscore),
      'reliability':[{'lower':b['lower'],'upper':min(b['upper'],1),'nights':b['nights'],'events':b['events'],
        'mean_probability':b['probability_sum']/b['nights'] if b['nights'] else None,'event_frequency':b['events']/b['nights'] if b['nights'] else None} for b in bins]}


def legacy(pred):
    mask=pred['cold_mask'];first=next((i for i,v in enumerate(mask) if v),None)
    return {'probability':pred['night_event_model_score'],'mask':mask,'early_onset_index':max(0,first-1) if first is not None else None,
            'onset_left_censored':first==0,'conditional_duration_range_h':None}


def selection():
    path=ROOT/'config/product_risk.json';p=json.loads(path.read_text(encoding='utf-8'));directory=ROOT/'models/research-product-risk';lock=directory/'selection-before-controls.json'
    if p['folds']!=[{'train_years':[2022],'validation_year':2023},{'train_years':[2022,2023],'validation_year':2024}] or p['evaluation_years']!=[2025,2026]:raise ValueError('Unexpected chronology')
    parent=parent_assets(ROOT/'data');extra,data_meta=load(ROOT/'data/processed/product-stations')
    sources={name:sha(ROOT/'agro_checker'/name) for name in ['product_data.py','product_risk.py','product_risk_study.py','scenario_study.py','frost_study.py','model_study.py','hourly_study.py','single_run.py','minimum_model.py','cold_interval_study.py']}
    rows,provenance=load_rows(ROOT/'data',[2022,2023,2024]);primary=station_nights(rows)
    state={'protocol_sha256':sha(path),'source_sha256':sources,'parent_hashes':parent['hashes'],'regional_data':data_meta,
      'primary_provenance':provenance,'reference_config_sha256':{name:sha(ROOT/'config'/name) for name in ['frost_study.json','model_study.json','single_run.json']}}
    if lock.exists():
        s=json.loads(lock.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen product protocol/source/data/parent changed')
        for name,digest in s['artifact_sha256'].items():
            if sha(directory/name)!=digest:raise ValueError('Frozen product model changed')
        return s
    write_json(directory/'protocol-lock.json',{**state,'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    specs=[{'name':w+'_'+str(k),'window':w,'neighbors':k} for w in p['windows'] for k in p['neighbors']]
    candidates=[s['name']+'@'+str(t) for s in specs for t in p['decision_thresholds']]
    records={name:[] for name in ['legacy']+candidates};training_counts=[]
    frost_p=json.loads((ROOT/'config/frost_study.json').read_text(encoding='utf-8'));mp=load_protocol(ROOT/'config/model_study.json')
    for fold in p['folds']:
        train=[n for n in primary if n['year'] in fold['train_years']];valid=[n for n in primary if n['year']==fold['validation_year']]
        old=fit_cold('event_duration_weather',[r for r in rows if r['year'] in fold['train_years']],frost_p,mp)
        predictions=predict_cold(old,[r for r in rows if r['year']==fold['validation_year']],.25)
        records['legacy'].extend({'sample':n,'forecast':legacy(f)} for n,f in zip(valid,predictions))
        for spec in specs:
            used=train+([n for n in extra if n['role']=='development' and n['year'] in fold['train_years']] if spec['window']=='regional' else [])
            model=learner.fit(used,spec);cal=learner.calibrate(used,spec,p)
            if set(model['fit_dates'])&{n['date'] for n in valid}:raise ValueError('Validation target overlap')
            forecasts=[learner.forecast(model,cal,n,0,p) for n in valid]
            for t in p['decision_thresholds']:
                records[spec['name']+'@'+str(t)].extend({'sample':n,'forecast':event_prediction(f,t)} for n,f in zip(valid,forecasts))
            training_counts.append({'fold':fold,'spec':spec['name'],'nights':model['training_nights'],
              'cold_nights':sum(learner.complete(n) and min(n['target_temperature_c'])<0 for n in used),
              'cold_dates':len({n['date'] for n in used if learner.complete(n) and min(n['target_temperature_c'])<0}),
              'sites':model['sites'],'calibration':{q:{k:v for k,v in h.items() if k in ['status','events','nights']} for q,h in cal['heads'].items()}})
    scores={name:score(rr) for name,rr in records.items()};base=scores['legacy']
    gates={name:{'misses_not_more':r['misses']<=base['misses'],'false_alarms_not_more':r['false_alarms']<=base['false_alarms'],
      'brier_not_worse':r['brier']<=base['brier']} for name,r in scores.items()}
    key=lambda name:(scores[name]['brier'],scores[name]['cold_duration_mae_h'],scores[name]['early_onset_edge_mae_h'] if scores[name]['early_onset_edge_mae_h'] is not None else 1e9)
    eligible=[name for name in ['legacy']+candidates if all(gates[name].values())];winner=min(eligible,key=key);diagnostic=min(candidates,key=key)
    diag_spec,decision=diagnostic.split('@');matched='station_'+diag_spec.split('_')[-1]+'@'+decision
    models={};artifacts={}
    for name in dict.fromkeys([winner,diagnostic,matched]):
        if name=='legacy':continue
        spec=next(s for s in specs if s['name']==name.split('@')[0]);train=[n for n in primary if n['year'] in [2022,2023]]
        used=train+([n for n in extra if n['role']=='development' and n['year'] in [2022,2023]] if spec['window']=='regional' else [])
        model=learner.fit(used,spec);cal=learner.calibrate(used,spec,p);models[name]={}
        for label,value in [('model',model),('calibration',cal)]:
            file=name.replace('@','-')+'-'+label+'.json';write_json(directory/file,value);models[name][label]=file;artifacts[file]=sha(directory/file)
    result={**state,'protocol':p,'winner':winner,'diagnostic':diagnostic,'matched_station':matched,'models':models,'artifact_sha256':artifacts,
      'validation':scores,'gates':gates,'training_counts':training_counts,'primary_provenance':provenance,
      'selection_saved_at_utc':datetime.now(UTC).isoformat(),'observations_loaded_years':[2022,2023,2024],
      'scope':'research comparison only; no orchard validated; original point/detector preserved'}
    write_json(lock,result);return result


def evaluate():
    s=selection();p=s['protocol'];parent=parent_assets(ROOT/'data');directory=ROOT/'models/research-product-risk'
    models={name:{key:json.loads((directory/file).read_text(encoding='utf-8')) for key,file in paths.items()} for name,paths in s['models'].items()}
    oldfile=ROOT/'data/processed/minimum-study/night-forecasts.json';maskfile=ROOT/'data/processed/scenario-study/night-forecasts.json'
    oldmeta=json.loads((ROOT/'docs/MINIMUM_STUDY_SUMMARY.json').read_text(encoding='utf-8'));maskmeta=json.loads((ROOT/'docs/SCENARIO_STUDY_SUMMARY.json').read_text(encoding='utf-8'))
    if sha(oldfile)!=oldmeta['forecast_sha256'] or sha(maskfile)!=maskmeta['forecast_artifact_sha256']:raise ValueError('Reference controls changed')
    original={r['date']:r for r in json.loads(oldfile.read_text(encoding='utf-8'))};old_masks={r['date']:r for r in json.loads(maskfile.read_text(encoding='utf-8'))}
    frames=[];seasons={}
    for year in p['evaluation_years']:
        source=ROOT/'data' if year==2025 else ROOT/'data/scenario-2026'
        config=None if year==2025 else json.loads((source/'source-config.json').read_text(encoding='utf-8'))
        rows,provenance=load_rows(source,[year],config);primary=station_nights(rows);events=predict_cold(parent['detector'],rows,.25);point_samples={n['date']:n for n in nights(rows)}
        rr={name:[] for name in ['legacy','gfs','selected','candidate','matched_station']}
        for n,event in zip(primary,events):
            ref=original[n['date']]
            if point_minimum(parent['point_model'],point_samples[n['date']])[0]!=ref['selected']['point_c']:raise ValueError('Original point changed')
            if event['cold_mask']!=old_masks[n['date']]['previous_detector_cold_mask'] or event['frost']!=ref['previous_detector']['frost']:raise ValueError('Legacy detector/timing changed')
            forecasts={'legacy':legacy(event),'gfs':{'probability':float(min(n['gfs_temperature_c'])<0),'mask':[t<0 for t in n['gfs_temperature_c']],
              'early_onset_index':next((max(0,i-1) for i,t in enumerate(n['gfs_temperature_c']) if t<0),None),'onset_left_censored':n['gfs_temperature_c'][0]<0}}
            for label,name in [('selected',s['winner']),('candidate',s['diagnostic']),('matched_station',s['matched_station'])]:
                forecasts[label]=forecasts['legacy'] if name=='legacy' else event_prediction(learner.forecast(models[name]['model'],models[name]['calibration'],n,0,p),float(name.split('@')[1]))
            for label,f in forecasts.items():rr[label].append({'sample':n,'forecast':f})
            threshold_forecasts={str(q):learner.forecast(models[s['diagnostic']]['model'],models[s['diagnostic']]['calibration'],n,q,p) for q in p['weather_thresholds_c']}
            frames.append({'date':n['date'],'year':year,'sample':n,'forecasts':forecasts,'threshold_forecasts':threshold_forecasts,
              'legacy_point':ref['selected'],'legacy_detector':event,'legacy_unchanged':True})
        seasons[str(year)]={'scores':{name:score(records) for name,records in rr.items()},'provenance':provenance,'legacy_masks_verified':len(primary)}
    if selection()!=s:raise ValueError('Choice changed after controls')
    file=ROOT/'data/processed/product-risk/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(directory/'selection-before-controls.json'),'seasons':seasons,'forecast_sha256':sha(file),
      'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':'research only; field GFS and original point/detector unchanged'}
    write_json(ROOT/'docs/PRODUCT_RISK_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');args=parser.parse_args()
    r=selection() if args.select_only else evaluate();s=r if args.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'matched':s['matched_station'],'validation':s['validation'],'seasons':r.get('seasons')},ensure_ascii=False))
