"""Prior-season selection of a new weather target, frozen detector preserved."""
import json
from datetime import datetime
from .pipeline import ROOT,UTC,write_json
from .product_data import sha
from .scenario_study import load_rows
from .frost_study import night_samples,predict_cold
from .coherent_cold_study import detector_fit
from .cold_calibration_study import selection as parent_selection
from .cold_interval_study import parent_assets
from . import cold_exposure as learner

DIRECTORY=ROOT/'models/research-cold-exposure';LOCK=DIRECTORY/'selection-before-controls.json'


def score(records):
    out={}
    for name,cold in [('all',None),('cold',True),('warm',False)]:
        rr=[r for r in records if r['actual'] is not None and (cold is None or (r['actual']>0)==cold)]
        out[name]={'nights':len(rr),'mae_c_h':sum(abs(r['prediction']['degree_hours_c_h']-r['actual']) for r in rr)/len(rr) if rr else None,
                   'mean_predicted_c_h':sum(r['prediction']['degree_hours_c_h'] for r in rr)/len(rr) if rr else None,
                   'mean_actual_c_h':sum(r['actual'] for r in rr)/len(rr) if rr else None}
    out['excluded_nights']=sum(r['actual'] is None for r in records);return out


def selection():
    path=ROOT/'config/cold_exposure.json';p=json.loads(path.read_text(encoding='utf-8'))
    if p['folds']!=[{'train_years':[2022],'validation_year':2023},{'train_years':[2022,2023],'validation_year':2024}] or p['final_train_years']!=[2022,2023] or p['evaluation_years']!=[2025,2026]:raise ValueError('Invalid chronology')
    parent_selection();parent=parent_assets(ROOT/'data');rows,provenance=load_rows(ROOT/'data',[2022,2023,2024])
    state={'protocol_sha256':sha(path),'input_provenance':provenance,'parent_hashes':parent['hashes'],
           'calibration_parent_sha256':sha(ROOT/'models/research-cold-calibration/selection-before-controls.json'),
           'source_sha256':{name:sha(ROOT/'agro_checker'/name) for name in ['cold_exposure.py','cold_exposure_study.py','cold_calibration_study.py','cold_calibration.py','coherent_cold.py','coherent_cold_study.py','frost_study.py','scenario_study.py','product_risk.py','product_risk_study.py','product_data.py','cold_interval_study.py','minimum_model.py','hourly_study.py','model_study.py','single_run.py']}}
    if LOCK.exists():
        s=json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen exposure experiment changed')
        for file,digest in s['artifact_sha256'].items():
            if sha(DIRECTORY/file)!=digest:raise ValueError('Frozen exposure artifact changed')
        return s
    write_json(DIRECTORY/'protocol-lock.json',state|{'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    records={name:[] for name in p['candidates']}
    for fold in p['folds']:
        train=[r for r in rows if r['year'] in fold['train_years']];valid=[r for r in rows if r['year']==fold['validation_year']]
        ns=night_samples(valid);events=predict_cold(detector_fit(train),valid,.25)
        for name in p['candidates']:
            model=learner.fit(night_samples(train),name,p['minimum_conditional_nights'])
            for n,event in zip(ns,events):
                actual=learner.exposure([r['target']['temperature'] for r in n['rows']])['degree_hours_c_h']
                records[name].append({'date':n['date'],'year':n['year'],'actual':actual,'prediction':learner.predict(model,n,event)})
    scores={name:score(rr) for name,rr in records.items()};base=scores['gfs'];gates={}
    for name,m in scores.items():
        gates[name]={group:m[group]['mae_c_h']<=base[group]['mae_c_h']+1e-12 for group in ['all','cold','warm']}
    eligible=[name for name in p['candidates'] if all(gates[name].values())]
    key=lambda name:(scores[name]['cold']['mae_c_h'],scores[name]['all']['mae_c_h'],p['candidates'].index(name))
    winner=min(eligible,key=key);diagnostic=min(p['candidates'][2:],key=key)
    models={};artifacts={};train=night_samples([r for r in rows if r['year'] in [2022,2023]])
    for name in dict.fromkeys([winner,diagnostic]):
        file=name+'.json';write_json(DIRECTORY/file,learner.fit(train,name,p['minimum_conditional_nights']));models[name]=file;artifacts[file]=sha(DIRECTORY/file)
    write_json(DIRECTORY/'validation-forecasts.json',records);artifacts['validation-forecasts.json']=sha(DIRECTORY/'validation-forecasts.json')
    s=state|{'protocol':p,'winner':winner,'diagnostic':diagnostic,'validation':scores,'gates':gates,'models':models,'artifact_sha256':artifacts,
             'selection_saved_at_utc':datetime.now(UTC).isoformat(),'observations_loaded_years':[2022,2023,2024],'deployment':'research only, detector unchanged'}
    write_json(LOCK,s);return s


def evaluate():
    s=selection();parent=parent_assets(ROOT/'data');models={name:json.loads((DIRECTORY/file).read_text(encoding='utf-8')) for name,file in s['models'].items()}
    oldsummary=json.loads((ROOT/'docs/COLD_CALIBRATION_SUMMARY.json').read_text(encoding='utf-8'));oldfile=ROOT/'data/processed/cold-calibration/night-forecasts.json'
    if sha(oldfile)!=oldsummary['forecast_sha256']:raise ValueError('Parent controls changed')
    old={f['date']:f for f in json.loads(oldfile.read_text(encoding='utf-8'))};frames=[];seasons={}
    for year in [2025,2026]:
        source=ROOT/'data' if year==2025 else ROOT/'data/scenario-2026';config=None if year==2025 else json.loads((source/'source-config.json').read_text(encoding='utf-8'))
        rows,provenance=load_rows(source,[year],config);ns=night_samples(rows);events=predict_cold(parent['detector'],rows,.25)
        rr={name:[] for name in ['gfs','selected','diagnostic']}
        for n,event in zip(ns,events):
            if event!=old[n['date']]['legacy_detector']:raise ValueError('Frozen detector/timing changed')
            actual=learner.exposure([r['target']['temperature'] for r in n['rows']])['degree_hours_c_h'];predictions={}
            for label,name in [('gfs','gfs'),('selected',s['winner']),('diagnostic',s['diagnostic'])]:
                model={'name':'gfs'} if label=='gfs' else models[name]
                f=learner.predict(model,n,event);predictions[label]=f;rr[label].append({'date':n['date'],'actual':actual,'prediction':f})
            frames.append({'date':n['date'],'year':year,'predictions':predictions,'actual_degree_hours_c_h':actual,
                           'legacy_detector':event,'legacy_unchanged':True})
        seasons[str(year)]={'scores':{name:score(records) for name,records in rr.items()},'provenance':provenance}
    if selection()!=s:raise ValueError('Exposure selection changed after controls')
    file=ROOT/'data/processed/cold-exposure/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(LOCK),'forecast_sha256':sha(file),'seasons':seasons,'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':'research, no crop-damage inference'}
    write_json(ROOT/'docs/COLD_EXPOSURE_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--select-only',action='store_true');a=p.parse_args();r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'validation':s['validation'],'seasons':r.get('seasons')},ensure_ascii=False))
