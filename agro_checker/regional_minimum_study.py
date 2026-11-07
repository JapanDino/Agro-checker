"""Chronological multi-site minimum study; all choices precede heldout/2025–2026."""
from datetime import datetime
import json
import numpy as np
from .pipeline import ROOT,UTC,write_json
from . import regional_data as data
from . import regional_minimum as learner
from .product_data import sha
from .scenario_study import load_rows,nights
from .cold_interval_study import parent_assets
from .minimum_model import features,fit_minimum,point_minimum
from .frost_study import predict_cold

DIRECTORY=ROOT/'models/research-regional-minimum'
LOCK=DIRECTORY/'selection-before-controls.json'


def primary_records(samples):
    result=[]
    for n in samples:
        w=[r['features_weather'] for r in n['rows']]
        # Reconstruct wind magnitude without changing any model input.
        g={'temperature':n['base']['temperature'],'rh':[v[1] for v in w],'dewpoint':[v[2] for v in w],'u10':[v[4] for v in w],'v10':[0.]*14,'cloud':[v[5] for v in w]}
        result.append({'id':'349290-99999','name':'KRASNODAR original ISD/METAR','role':'reference','date':n['date'],'year':n['year'],
                       'run_at_utc':n['rows'][0]['forecast_run_at_utc'],'issued_at_utc':n['rows'][0]['issued_at_utc'],
                       'times':[r['valid_at_utc'] for r in n['rows']],'gfs':g,'target_temperature_c':n['target']['temperature']})
    return result


def records(samples,model=None,gfs=False):
    return [{'id':n['id'],'date':n['date'],'actual':min(n['target_temperature_c']) if learner.complete(n) else None,
             'point':min(n['gfs']['temperature']) if gfs else learner.predict(model,n)} for n in samples]


def registry(p):
    return [{'name':f"{scope}_{a['kind']}_{balance}_cold{cw}",'scope':scope,**a,'balance':balance,'cold_weight':cw}
            for scope in p['scopes'] for a in p['algorithms'] for balance in p['balancing'] for cw in p['cold_weights']]


def source_state(p,meta,provenance):
    names=['regional_data.py','regional_minimum.py','regional_minimum_study.py','minimum_model.py','frost_study.py','scenario_study.py','hourly_study.py','model_study.py','single_run.py','cold_interval_study.py','product_data.py','gfs_covariates.py','humidity_archive.py']
    return {'protocol_sha256':sha(data.PROTOCOL),'source_sha256':{n:sha(ROOT/'agro_checker'/n) for n in names},
            'parent_hashes':parent_assets(ROOT/'data')['hashes'],'dataset_meta':meta,'primary_provenance':provenance,
            'parent_config_hashes':{n:sha(ROOT/'config'/n) for n in ['single_run.json','model_study.json','frost_study.json']}}


def selection():
    p=json.loads(data.PROTOCOL.read_text(encoding='utf-8'))
    if p['folds']!=[{'train_years':[2022],'validation_year':2023},{'train_years':[2022,2023],'validation_year':2024}] or p['final_train_years']!=[2022,2023] or p['evaluation_years']!=[2025,2026]:raise ValueError('Chronology changed')
    extra,meta=data.load('development');rows,provenance=load_rows(ROOT/'data',[2022,2023,2024]);old=nights(rows);primary=primary_records(old);state=source_state(p,meta,provenance)
    if LOCK.exists():
        s=json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen regional protocol/source/parent/data changed')
        for f,h in s['artifact_sha256'].items():
            if sha(DIRECTORY/f)!=h:raise ValueError('Frozen regional model changed')
        return s
    if any(n['year'] not in [2022,2023,2024] or n['role']=='heldout_site' for n in extra):raise ValueError('Forbidden labels in selection')
    reference={n['date']:n for n in extra if n['role']=='reference'}
    for n,o in zip(primary,old):
        if not np.array_equal(features(learner.adapter(n)),features(o)):raise ValueError('Adapter changes original inputs')
        if not np.allclose(features(learner.adapter(reference[n['date']])),features(o),rtol=0,atol=1e-12):raise ValueError('Global extraction differs from original GFS')
    regional=[n for n in extra if n['role']=='development'];specs=registry(p);all_names=['legacy','gfs']+[s['name'] for s in specs]
    write_json(DIRECTORY/'protocol-lock.json',{**state,'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    rr={group:{name:[] for name in all_names} for group in ['primary','regional']};fold_scores=[];training=[]
    for fold in p['folds']:
        train=[n for n in primary if n['year'] in fold['train_years']];valid=[n for n in primary if n['year']==fold['validation_year']]
        dev_train=[n for n in regional if n['year'] in fold['train_years']];dev_valid=[n for n in regional if n['year']==fold['validation_year']]
        legacy=fit_minimum({'name':'huber_1','kind':'huber','alpha':1},[n for n in old if n['year'] in fold['train_years']]);groups={'primary':valid,'regional':dev_valid}
        fold_records={group:{'legacy':records(ns,legacy),'gfs':records(ns,gfs=True)} for group,ns in groups.items()}
        for spec in specs:
            used=train+(dev_train if spec['scope']=='regional' else []);model=learner.fit(used,spec)
            if set(model['training_dates'])&{n['date'] for ns in groups.values() for n in ns}:raise ValueError('Validation dates leaked')
            training.append({'fold':fold,'name':spec['name'],**{k:model[k] for k in ['training_nights','training_sites','cold_site_nights','cold_dates','cold_episode_groups','sample_weight_sum']}})
            for group,ns in groups.items():fold_records[group][spec['name']]=records(ns,model)
        for group in groups:
            for name,rs in fold_records[group].items():rr[group][name].extend(rs)
        fold_scores.append({'year':fold['validation_year'],'groups':{g:{n:learner.score(rs) for n,rs in by.items()} for g,by in fold_records.items()}})
        print('Regional minimum validation',fold['validation_year'],flush=True)
    scores={g:{name:learner.score(rs) for name,rs in by.items()} for g,by in rr.items()};gates={}
    for spec in specs:
        name=spec['name'];checks={}
        for f in fold_scores:
            a=f['groups']['primary'][name];b=f['groups']['primary']['legacy'];c=f['groups']['regional'][name];d=f['groups']['regional']['gfs'];y=f['year']
            for key in ['mae_c','cold_mae_c','misses','false_alarms']:checks[f'primary_{y}_{key}']=a[key] is not None and a[key]<=b[key]+1e-12
            for key in ['site_macro_mae_c','site_macro_cold_mae_c','misses','false_alarms']:checks[f'regional_{y}_{key}']=c[key] is not None and c[key]<=d[key]+1e-12
        gates[name]=checks
    key=lambda name:(scores['primary'][name]['cold_mae_c'],scores['primary'][name]['mae_c'],name)
    eligible=[n for n,gg in gates.items() if all(gg.values())];winner=min(eligible,key=key) if eligible else 'legacy'
    diagnostic=min((s['name'] for s in specs if s['scope']=='regional'),key=key);matched=diagnostic.replace('regional_','station_',1)
    models={};hashes={}
    final_primary=[n for n in primary if n['year'] in p['final_train_years']];final_dev=[n for n in regional if n['year'] in p['final_train_years']]
    for name in dict.fromkeys([winner,diagnostic,matched]):
        if name=='legacy':continue
        spec=next(s for s in specs if s['name']==name);m=learner.fit(final_primary+(final_dev if spec['scope']=='regional' else []),spec)
        file=name+'.json';write_json(DIRECTORY/file,m);models[name]=file;hashes[file]=sha(DIRECTORY/file)
    s={**state,'protocol':p,'winner':winner,'diagnostic':diagnostic,'matched':matched,'validation':scores,'fold_scores':fold_scores,'gates':gates,
       'training':training,'models':models,'artifact_sha256':hashes,'observations_loaded_years':[2022,2023,2024],
       'heldout_targets_loaded_in_selection':False,'selection_saved_at_utc':datetime.now(UTC).isoformat(),
       'scope':'research only; original detector and field GFS preserved; point crossings never replace alerts'}
    write_json(LOCK,s);return s


def evaluate():
    s=selection();parent=parent_assets(ROOT/'data');models={n:json.loads((DIRECTORY/f).read_text(encoding='utf-8')) for n,f in s['models'].items()}
    get=lambda name:parent['point_model'] if name=='legacy' else models[name]
    def compare(ns):
        rr={label:records(ns,model) for label,model in [('legacy',parent['point_model']),('selected',get(s['winner'])),('diagnostic',get(s['diagnostic'])),('matched_station',get(s['matched']))]}
        rr['gfs']=records(ns,gfs=True)
        return rr,{'scores':{k:learner.score(v) for k,v in rr.items()},'paired_week_blocks_vs_legacy':{k:learner.paired_blocks(v,rr['legacy']) for k,v in rr.items() if k in ['selected','diagnostic']}}
    heldout,meta=data.load('heldout_site')
    if {n['id'] for n in heldout}&{sid for m in models.values() for sid in m['training_sites']}:raise ValueError('Spatial holdout used in training')
    _,spatial=compare([n for n in heldout if n['year']==2024]);spatial['years']=[2024];spatial['site_ids']=sorted({n['id'] for n in heldout});spatial['label_policy']='new stations excluded from selection/fitting; same calendar dates share regional weather, not independent weather test'
    original={r['date']:r for r in json.loads((ROOT/'data/processed/minimum-study/night-forecasts.json').read_text(encoding='utf-8'))}
    old_events={r['date']:r['legacy_detector'] for r in json.loads((ROOT/'data/processed/cold-exposure/night-forecasts.json').read_text(encoding='utf-8'))}
    frames=[];seasons={}
    for y in [2025,2026]:
        source=ROOT/'data' if y==2025 else ROOT/'data/scenario-2026';config=None if y==2025 else json.loads((source/'source-config.json').read_text(encoding='utf-8'))
        rows,prov=load_rows(source,[y],config);ns=primary_records(nights(rows));rr,result=compare(ns);events=predict_cold(parent['detector'],rows,.25)
        for i,(n,event) in enumerate(zip(ns,events)):
            if rr['legacy'][i]['point']!=original[n['date']]['selected']['point_c'] or event!=old_events[n['date']]:raise ValueError('Original point/detector/masks/timing changed')
            frames.append({'date':n['date'],'year':y,'points':{label:rs[i]['point'] for label,rs in rr.items()},'actual_minimum_c':rr['legacy'][i]['actual'],
                           'legacy_detector':event,'legacy_unchanged':True,'point_crossings_are_alerts':False})
        result['provenance']=prov;result['original_points_and_detectors_verified']=len(ns);seasons[str(y)]=result
    if selection()!=s:raise ValueError('Choice changed after controls')
    file=data.DIRECTORY/'control-forecasts.json';write_json(file,frames)
    out={'selection':s,'selection_sha256':sha(LOCK),'spatial_2024':spatial,'seasons':seasons,'forecast_sha256':sha(file),
         'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':'research only; no orchard or regional detector deployment; no retuning after heldout/controls'}
    write_json(ROOT/'docs/REGIONAL_MINIMUM_SUMMARY.json',out);return out


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');a=parser.parse_args();r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'eligible':[n for n,g in s['gates'].items() if all(g.values())],
                      'primary_validation':{k:{q:v[q] for q in ['mae_c','cold_mae_c','misses','false_alarms']} for k,v in s['validation']['primary'].items()},
                      'spatial_2024':r.get('spatial_2024'),'seasons':r.get('seasons')},ensure_ascii=False))
