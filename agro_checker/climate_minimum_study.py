"""Locked prior-only climate/terrain comparison; detector and parents immutable."""
from datetime import datetime
import json
from .pipeline import ROOT,UTC,write_json
from . import regional_data as data
from . import terrain_archive as terrain_data
from . import regional_minimum as common
from . import regional_minimum_study as prior
from . import regional_hierarchy as hierarchy
from . import climate_minimum as learner
from .scenario_study import load_rows,nights
from .minimum_model import fit_minimum
from .cold_interval_study import parent_assets
from .frost_study import predict_cold
from .product_data import sha

PROTOCOL=ROOT/'config/climate_minimum.json'
DIRECTORY=ROOT/'models/research-climate-minimum'
LOCK=DIRECTORY/'selection-before-controls.json'


def score_records(samples,bundle,base,terrain,blend,query_contexts=None):
    return [{'id':n['id'],'date':n['date'],'actual':min(n['target_temperature_c']) if common.complete(n) else None,
             'point':learner.predict(bundle,base,n,terrain,blend,(query_contexts or {}).get(n['id']))} for n in samples]


def selection():
    # Read-only verification of earlier choices; never evaluates or rewrites controls.
    first=prior.selection();old_hierarchy=hierarchy.selection();p=json.loads(PROTOCOL.read_text(encoding='utf-8'))
    if p['folds']!=first['protocol']['folds'] or p['final_train_years']!=[2022,2023] or p['evaluation_years']!=[2025,2026]:raise ValueError('Climate chronology changed')
    if p['alpha_huber']!=1 or p['alpha_ridge']!=10 or p['families']!=['geo_huber','climate_huber','climate_ridge','matched3_weather','matched5_weather','matched3_climate']:raise ValueError('Climate registry changed')
    extra,meta=data.load('development');terrain,tmeta=terrain_data.load(False);rows,prov=load_rows(ROOT/'data',[2022,2023,2024]);old=nights(rows)
    primary=prior.primary_records(old);regional=[n for n in extra if n['role']=='development'];metadata={s['id']:s for s in meta['sites']}
    source_names=['climate_minimum.py','climate_minimum_study.py','terrain_archive.py','gfs_archive.py','gfs_covariates.py']
    state={'protocol_sha256':sha(PROTOCOL),'source_sha256':{n:sha(ROOT/'agro_checker'/n) for n in source_names},
           'prior_selection_sha256':sha(prior.LOCK),'hierarchy_selection_sha256':sha(hierarchy.LOCK),'parent_hashes':parent_assets(ROOT/'data')['hashes'],
           'dataset_meta':meta,'terrain_prior_meta':tmeta,'primary_provenance':prov}
    if LOCK.exists():
        s=json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen climate source/protocol/input/parent changed')
        for name,h in s['artifact_sha256'].items():
            if sha(DIRECTORY/name)!=h:raise ValueError('Frozen climate model changed')
        return s
    if any(n['role']=='heldout_site' or n['year'] not in [2022,2023,2024] for n in extra):raise ValueError('Forbidden selection labels')
    if terrain['years']!=[2022,2023,2024]:raise ValueError('Control terrain cannot enter prior choice')
    specs=[{'name':family+'_cold'+str(c),'family':family,'cold_weight':c} for family in p['families'] for c in p['cold_weights']]
    candidates=[z['name']+'@'+str(w) for z in specs for w in p['blend_weights']];names=['legacy','gfs']+candidates
    rr={g:{name:[] for name in names} for g in ['primary','regional']};folds=[];training=[]
    write_json(DIRECTORY/'protocol-lock.json',{**state,'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    for fold in p['folds']:
        used=[n for n in primary+regional if n['year'] in fold['train_years']]
        # Includes incomplete-label nights as forecast context, without reading their targets.
        context_samples=[n for n in extra if n['year'] in fold['train_years']];catalog=learner.backgrounds(context_samples,metadata,fold['train_years'])
        groups={'primary':[n for n in primary if n['year']==fold['validation_year']],'regional':[n for n in regional if n['year']==fold['validation_year']]}
        base=fit_minimum({'name':'huber_1','kind':'huber','alpha':1},[n for n in old if n['year'] in fold['train_years']])
        fr={g:{'legacy':prior.records(ns,base),'gfs':prior.records(ns,gfs=True)} for g,ns in groups.items()}
        for spec in specs:
            bundle=learner.fit_bundle(used,catalog,terrain['records'],spec,p['matching_scales'])
            if set(bundle['training_dates'])&{n['date'] for ns in groups.values() for n in ns}:raise ValueError('Validation dates leaked')
            training.append({'fold':fold,'spec':spec['name'],'training_nights':bundle['training_nights'],'cold_site_nights':bundle['cold_site_nights'],
                             'expert_training':{q:{k:v[k] for k in ['training_nights','cold_site_nights','neighbours','weight_sum']} for q,v in bundle['models'].items()}})
            for w in p['blend_weights']:
                name=spec['name']+'@'+str(w)
                for g,ns in groups.items():fr[g][name]=score_records(ns,bundle,base,terrain['records'],w)
        for g in groups:
            for name,rs in fr[g].items():rr[g][name].extend(rs)
        folds.append({'year':fold['validation_year'],'groups':{g:{name:common.score(rs) for name,rs in by.items()} for g,by in fr.items()},'backgrounds':catalog})
        print('Climate/height validation',fold['validation_year'],flush=True)
    scores={g:{name:common.score(rs) for name,rs in by.items()} for g,by in rr.items()};gates={}
    for name in candidates:
        checks={}
        for f in folds:
            a=f['groups']['primary'][name];b=f['groups']['primary']['legacy'];c=f['groups']['regional'][name];d=f['groups']['regional']['gfs'];y=f['year']
            for k in ['mae_c','cold_mae_c','misses','false_alarms']:checks[f'primary_{y}_{k}']=a[k] is not None and a[k]<=b[k]+1e-12
            for k in ['site_macro_mae_c','site_macro_cold_mae_c','misses','false_alarms']:checks[f'regional_{y}_{k}']=c[k] is not None and c[k]<=d[k]+1e-12
        gates[name]=checks
    key=lambda name:(scores['primary'][name]['cold_mae_c'],scores['primary'][name]['mae_c'],name)
    passed=[n for n,g in gates.items() if all(g.values())];winner=min(passed,key=key) if passed else 'legacy';diagnostic=min(candidates,key=key)
    final=[n for n in primary+regional if n['year'] in [2022,2023]];catalog=learner.backgrounds([n for n in extra if n['year'] in [2022,2023]],metadata,[2022,2023]);models={};hashes={}
    for name in dict.fromkeys([winner,diagnostic]):
        if name=='legacy':continue
        spec=next(z for z in specs if z['name']==name.split('@')[0]);bundle=learner.fit_bundle(final,catalog,terrain['records'],spec,p['matching_scales'])
        file=spec['name']+'.json';write_json(DIRECTORY/file,bundle);models[name]=file;hashes[file]=sha(DIRECTORY/file)
    s={**state,'protocol':p,'winner':winner,'diagnostic':diagnostic,'models':models,'artifact_sha256':hashes,'gates':gates,'validation':scores,
       'fold_scores':folds,'training':training,'selection_saved_at_utc':datetime.now(UTC).isoformat(),'observations_loaded_years':[2022,2023,2024],
       'heldout_targets_loaded_in_selection':False,'context_observed_targets_used':False,'scope':'research, no detector replacement or orchard support'}
    write_json(LOCK,s);return s


def evaluate():
    s=selection();parent=parent_assets(ROOT/'data');models={name:json.loads((DIRECTORY/file).read_text(encoding='utf-8')) for name,file in s['models'].items()};base=parent['point_model']
    def compare(ns,terrain,contexts=None):
        rr={'legacy':prior.records(ns,base),'gfs':prior.records(ns,gfs=True)}
        for label,name in [('selected',s['winner']),('diagnostic',s['diagnostic'])]:rr[label]=rr['legacy'] if name=='legacy' else score_records(ns,models[name],base,terrain,float(name.split('@')[1]),contexts)
        return rr,{'scores':{k:common.score(v) for k,v in rr.items()},'paired_vs_legacy':{k:common.paired_blocks(v,rr['legacy']) for k,v in rr.items() if k in ['selected','diagnostic']}}
    heldout,_=data.load('heldout_site');meta=s['dataset_meta'];metadata={c['id']:c for c in meta['sites']};qcontext=learner.backgrounds([n for n in heldout if n['year'] in [2022,2023]],metadata,[2022,2023])
    if {n['id'] for n in heldout}&{sid for bundle in models.values() for sid in bundle['training_sites']}:raise ValueError('Spatial labels in fit')
    prior_terrain,_=terrain_data.load(False);_,spatial=compare([n for n in heldout if n['year']==2024],prior_terrain['records'],qcontext)
    spatial['query_contexts']=qcontext;spatial['scope']='previously viewed spatial sites and shared weather dates; not a new blind test'
    controls,controls_meta=terrain_data.load(True)
    oldfile=ROOT/'data/processed/regional-minimum/control-forecasts.json';oldsummary=json.loads((ROOT/'docs/REGIONAL_MINIMUM_SUMMARY.json').read_text(encoding='utf-8'))
    if sha(oldfile)!=oldsummary['forecast_sha256']:raise ValueError('Reference replay changed')
    original={n['date']:n for n in json.loads(oldfile.read_text(encoding='utf-8'))};seasons={};frames=[]
    for y in [2025,2026]:
        src=ROOT/'data' if y==2025 else ROOT/'data/scenario-2026';cfg=None if y==2025 else json.loads((src/'source-config.json').read_text(encoding='utf-8'));rows,prov=load_rows(src,[y],cfg)
        ns=prior.primary_records(nights(rows));rr,z=compare(ns,controls['records']);events=predict_cold(parent['detector'],rows,.25)
        for i,(n,event) in enumerate(zip(ns,events)):
            if event!=original[n['date']]['legacy_detector'] or rr['legacy'][i]['point']!=original[n['date']]['points']['legacy']:raise ValueError('Original point/detector/masks/timing changed')
            frames.append({'date':n['date'],'year':y,'points':{label:v[i]['point'] for label,v in rr.items()},'actual_minimum_c':rr['legacy'][i]['actual'],
                           'legacy_detector':event,'legacy_unchanged':True,'point_crossings_are_alerts':False})
        z['provenance']=prov;z['original_points_and_detector_objects_verified']=len(ns);seasons[str(y)]=z
    if selection()!=s:raise ValueError('Climate choice changed after controls')
    file=terrain_data.DIRECTORY/'control-forecasts.json';write_json(file,frames)
    out={'selection':s,'selection_sha256':sha(LOCK),'spatial_2024':spatial,'seasons':seasons,'terrain_controls_meta':controls_meta,
         'reference_controls_sha256':sha(oldfile),'forecast_sha256':sha(file),'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':'research only; unchanged field GFS and original detector'}
    write_json(ROOT/'docs/CLIMATE_MINIMUM_SUMMARY.json',out);return out


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--select-only',action='store_true');a=p.parse_args();r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'passed':[n for n,g in s['gates'].items() if all(g.values())],
                      'validation_primary':{n:{k:s['validation']['primary'][n][k] for k in ['mae_c','cold_mae_c','false_alarms','misses']} for n in ['legacy',s['diagnostic']]},
                      'seasons':r.get('seasons')},ensure_ascii=False))
