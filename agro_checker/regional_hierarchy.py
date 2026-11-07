"""Partially pooled station offsets; fixed prior-only blending and freeze."""
from datetime import datetime
import json
import numpy as np
from .pipeline import ROOT,UTC,write_json
from . import regional_data as data
from . import regional_minimum as common
from . import regional_minimum_study as prior
from .minimum_model import features,fit_minimum
from .cold_interval_study import parent_assets
from .scenario_study import load_rows,nights
from .product_data import sha

PROTOCOL=ROOT/'config/regional_hierarchy.json'
DIRECTORY=ROOT/'models/research-regional-hierarchy'
LOCK=DIRECTORY/'selection-before-controls.json'


def fit(samples,spec):
    if any(n['year'] not in [2022,2023] or n.get('role')=='heldout_site' for n in samples):raise ValueError('Forbidden hierarchy training labels')
    usable=[n for n in samples if common.complete(n)]
    if len(usable)<20:raise ValueError('Too few training nights')
    w,_=common.weights(usable,spec['cold_weight'],'date');sites=sorted({n['id'] for n in usable});x=np.array([features(common.adapter(n)) for n in usable])
    mean=np.average(x,axis=0,weights=w);scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-12]=1
    indicators=np.array([[float(n['id']==sid)*spec['site_scale'] for sid in sites] for n in usable]);design=np.column_stack([(x-mean)/scale,indicators])
    y=[min(n['target_temperature_c'])-min(n['gfs']['temperature']) for n in usable]
    from sklearn.linear_model import HuberRegressor,Ridge
    from threadpoolctl import threadpool_limits
    est=HuberRegressor(alpha=spec['alpha'],max_iter=2000) if spec['kind']=='huber' else Ridge(alpha=spec['alpha'],solver='svd')
    with threadpool_limits(limits=1):est.fit(design,y,sample_weight=w)
    return {'spec':spec,'mean':mean.tolist(),'scale':scale.tolist(),'coef':est.coef_.tolist(),'intercept':float(est.intercept_),
            'training_sites':sites,'training_years':sorted({n['year'] for n in usable}),'training_dates':sorted({n['date'] for n in usable}),
            'training_nights':len(usable),'cold_site_nights':sum(min(n['target_temperature_c'])<0 for n in usable),'detector_unchanged':True}


def predict(model,base,n,weight):
    if weight not in [.25,.5,1]:raise ValueError('Unregistered blending')
    x=(features(common.adapter(n))-np.array(model['mean']))/np.array(model['scale'])
    z=np.r_[x,[float(n['id']==sid)*model['spec']['site_scale'] for sid in model['training_sites']]]
    point=min(n['gfs']['temperature'])+model['intercept']+float(z@np.array(model['coef']))
    return (1-weight)*common.predict(base,n)+weight*point


def records(ns,model,base,weight):
    return [{'id':n['id'],'date':n['date'],'actual':min(n['target_temperature_c']) if common.complete(n) else None,'point':predict(model,base,n,weight)} for n in ns]


def selection():
    first=prior.selection();p=json.loads(PROTOCOL.read_text(encoding='utf-8'))
    if p['folds']!=first['protocol']['folds'] or p['final_train_years']!=[2022,2023] or p['evaluation_years']!=[2025,2026]:raise ValueError('Hierarchy chronology changed')
    extra,meta=data.load('development');rows,prov=load_rows(ROOT/'data',[2022,2023,2024]);old=nights(rows);primary=prior.primary_records(old);regional=[n for n in extra if n['role']=='development']
    state={'protocol_sha256':sha(PROTOCOL),'first_selection_sha256':sha(prior.LOCK),'source_sha256':sha(ROOT/'agro_checker/regional_hierarchy.py'),
           'parent_hashes':parent_assets(ROOT/'data')['hashes'],'data_meta':meta,'primary_provenance':prov}
    if LOCK.exists():
        s=json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen hierarchy sources/data/parents changed')
        for f,h in s['artifact_sha256'].items():
            if sha(DIRECTORY/f)!=h:raise ValueError('Hierarchy artifact changed')
        return s
    specs=[{'name':f"{a['kind']}_site{k}_cold{c}",**a,'site_scale':k,'cold_weight':c} for a in p['algorithms'] for k in p['site_feature_scales'] for c in p['cold_weights']]
    candidates=[z['name']+'@'+str(w) for z in specs for w in p['blend_weights']];names=['legacy','gfs']+candidates
    rr={group:{name:[] for name in names} for group in ['primary','regional']};folds=[]
    write_json(DIRECTORY/'protocol-lock.json',{**state,'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    for fold in p['folds']:
        used=[n for n in primary+regional if n['year'] in fold['train_years']];groups={'primary':[n for n in primary if n['year']==fold['validation_year']],
                                                                                'regional':[n for n in regional if n['year']==fold['validation_year']]}
        base=fit_minimum({'name':'huber_1','kind':'huber','alpha':1},[n for n in old if n['year'] in fold['train_years']])
        fr={g:{'legacy':prior.records(ns,base),'gfs':prior.records(ns,gfs=True)} for g,ns in groups.items()}
        for spec in specs:
            model=fit(used,spec)
            if set(model['training_dates'])&{n['date'] for ns in groups.values() for n in ns}:raise ValueError('Future validation dates leaked')
            for w in p['blend_weights']:
                name=spec['name']+'@'+str(w)
                for group,ns in groups.items():fr[group][name]=records(ns,model,base,w)
        for g in groups:
            for n,values in fr[g].items():rr[g][n].extend(values)
        folds.append({'year':fold['validation_year'],'groups':{g:{n:common.score(values) for n,values in by.items()} for g,by in fr.items()}})
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
    files={};hashes={}
    for name in dict.fromkeys([winner,diagnostic]):
        if name=='legacy':continue
        spec=next(s for s in specs if s['name']==name.split('@')[0]);m=fit([n for n in primary+regional if n['year'] in [2022,2023]],spec)
        file=spec['name']+'.json';write_json(DIRECTORY/file,m);files[name]=file;hashes[file]=sha(DIRECTORY/file)
    s={**state,'protocol':p,'winner':winner,'diagnostic':diagnostic,'validation':scores,'fold_scores':folds,'gates':gates,'models':files,'artifact_sha256':hashes,
       'selection_saved_at_utc':datetime.now(UTC).isoformat(),'heldout_targets_loaded_in_selection':False,'observations_loaded_years':[2022,2023,2024]}
    write_json(LOCK,s);return s


def evaluate():
    s=selection();base=parent_assets(ROOT/'data')['point_model'];models={n:json.loads((DIRECTORY/f).read_text(encoding='utf-8')) for n,f in s['models'].items()}
    def compare(ns):
        rr={'legacy':prior.records(ns,base),'gfs':prior.records(ns,gfs=True)}
        for label,name in [('selected',s['winner']),('diagnostic',s['diagnostic'])]:rr[label]=rr['legacy'] if name=='legacy' else records(ns,models[name],base,float(name.split('@')[1]))
        return rr,{'scores':{label:common.score(rs) for label,rs in rr.items()},'paired_vs_legacy':{label:common.paired_blocks(rs,rr['legacy']) for label,rs in rr.items() if label in ['selected','diagnostic']}}
    # First study verifies all original points and complete detector objects once.
    first=prior.evaluate();heldout,_=data.load('heldout_site')
    if {n['id'] for n in heldout}&{sid for m in models.values() for sid in m['training_sites']}:raise ValueError('Hierarchy spatial leakage')
    _,spatial=compare([n for n in heldout if n['year']==2024]);seasons={};frames=[]
    for y in [2025,2026]:
        src=ROOT/'data' if y==2025 else ROOT/'data/scenario-2026';cfg=None if y==2025 else json.loads((src/'source-config.json').read_text(encoding='utf-8'));rows,prov=load_rows(src,[y],cfg)
        ns=prior.primary_records(nights(rows));rr,z=compare(ns);z['provenance']=prov;seasons[str(y)]=z
        for i,n in enumerate(ns):frames.append({'date':n['date'],'points':{label:rs[i]['point'] for label,rs in rr.items()},'actual_minimum_c':rr['legacy'][i]['actual'],'detector_unchanged':True})
    if selection()!=s:raise ValueError('Hierarchy changed after controls')
    file=data.DIRECTORY/'hierarchy-control-forecasts.json';write_json(file,frames)
    out={'selection':s,'selection_sha256':sha(LOCK),'first_study_summary_sha256':sha(ROOT/'docs/REGIONAL_MINIMUM_SUMMARY.json'),
         'spatial_2024':spatial,'seasons':seasons,'forecast_sha256':sha(file),'generated_at_utc':datetime.now(UTC).isoformat(),
         'original_detector_verified':sum(z['original_points_and_detectors_verified'] for z in first['seasons'].values()),'deployment':'research only; no detector or field replacement'}
    write_json(ROOT/'docs/REGIONAL_HIERARCHY_SUMMARY.json',out);return out


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');a=parser.parse_args();r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'passed':[n for n,g in s['gates'].items() if all(g.values())],
                      'prior_primary':{n:s['validation']['primary'][n] for n in ['legacy',s['diagnostic']]},'seasons':r.get('seasons')},ensure_ascii=False))
