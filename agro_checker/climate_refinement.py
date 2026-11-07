"""Prior-only cold forecast refinement; no new crossings on anchor-warm nights."""
from datetime import datetime
import json
from .pipeline import ROOT,UTC,write_json
from . import climate_minimum_study as prior
from . import climate_minimum as learner
from . import regional_minimum as common
from . import terrain_archive as terrain_data
from . import regional_data as data
from .regional_minimum_study import primary_records
from .scenario_study import load_rows,nights
from .minimum_model import fit_minimum
from .cold_interval_study import parent_assets
from .product_data import sha

PROTOCOL=ROOT/'config/climate_refinement.json'
DIRECTORY=ROOT/'models/research-climate-refinement'
LOCK=DIRECTORY/'selection-before-controls.json'


def refine(anchor,expert,weight):
    if weight not in [.25,.5,1]:raise ValueError('Unregistered refinement')
    import math
    if not math.isfinite(anchor) or not math.isfinite(expert):raise ValueError('Invalid minima')
    return (1-weight)*anchor+weight*expert if anchor<0 else anchor


def records(ns,bundle,base,terrain,weight,contexts=None):
    out=[]
    for n in ns:
        anchor=common.predict(base,n) if n['id']=='349290-99999' else min(n['gfs']['temperature'])
        expert=learner.predict(bundle,base,n,terrain,1,(contexts or {}).get(n['id'])) if weight is not None else None
        point=refine(anchor,expert,weight) if weight is not None else anchor
        out.append({'id':n['id'],'date':n['date'],'actual':min(n['target_temperature_c']) if common.complete(n) else None,'point':point,
                    'anchor':anchor,'refinement_used':weight is not None and anchor<0})
    return out


def selection():
    first=prior.selection();p=json.loads(PROTOCOL.read_text(encoding='utf-8'))
    if p['folds']!=first['protocol']['folds'] or p['final_train_years']!=[2022,2023] or p['evaluation_years']!=[2025,2026] or p['weights']!=[.25,.5,1]:raise ValueError('Refinement chronology/registry changed')
    extra,meta=data.load('development');terrain,tmeta=terrain_data.load(False);rows,prov=load_rows(ROOT/'data',[2022,2023,2024]);old=nights(rows)
    primary=primary_records(old);regional=[n for n in extra if n['role']=='development'];metadata={n['id']:n for n in meta['sites']}
    state={'protocol_sha256':sha(PROTOCOL),'source_sha256':sha(ROOT/'agro_checker/climate_refinement.py'),'climate_selection_sha256':sha(prior.LOCK),
           'terrain_prior_meta':tmeta,'primary_provenance':prov,'parent_hashes':parent_assets(ROOT/'data')['hashes']}
    if LOCK.exists():
        s=json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen refinement changed')
        if sha(DIRECTORY/'expert.json')!=s['expert_sha256']:raise ValueError('Frozen refinement expert changed')
        return s
    family,cold=first['diagnostic'].split('@')[0].rsplit('_cold',1);spec={'name':family+'_cold'+cold,'family':family,'cold_weight':int(cold)}
    rr={g:{name:[] for name in ['anchor','gfs']+[str(w) for w in p['weights']]} for g in ['primary','regional']};folds=[]
    write_json(DIRECTORY/'protocol-lock.json',{**state,'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    for fold in p['folds']:
        train=[n for n in primary+regional if n['year'] in fold['train_years']];catalog=learner.backgrounds([n for n in extra if n['year'] in fold['train_years']],metadata,fold['train_years'])
        bundle=learner.fit_bundle(train,catalog,terrain['records'],spec,first['protocol']['matching_scales']);base=fit_minimum({'name':'huber_1','kind':'huber','alpha':1},[n for n in old if n['year'] in fold['train_years']])
        groups={'primary':[n for n in primary if n['year']==fold['validation_year']],'regional':[n for n in regional if n['year']==fold['validation_year']]};fr={}
        for group,ns in groups.items():
            fr[group]={'anchor':records(ns,bundle,base,terrain['records'],None),'gfs':prior.prior.records(ns,gfs=True)}
            for w in p['weights']:fr[group][str(w)]=records(ns,bundle,base,terrain['records'],w)
            for name,rs in fr[group].items():rr[group][name].extend(rs)
        folds.append({'year':fold['validation_year'],'groups':{g:{name:common.score(rs) for name,rs in by.items()} for g,by in fr.items()}})
    scores={g:{name:common.score(rs) for name,rs in by.items()} for g,by in rr.items()};gates={}
    for w in p['weights']:
        name=str(w);checks={}
        for f in folds:
            y=f['year'];a=f['groups']['primary'][name];b=f['groups']['primary']['anchor'];c=f['groups']['regional'][name];d=f['groups']['regional']['anchor']
            for k in ['mae_c','cold_mae_c','misses','false_alarms']:checks[f'primary_{y}_{k}']=a[k] is not None and a[k]<=b[k]+1e-12
            for k in ['site_macro_mae_c','site_macro_cold_mae_c','misses','false_alarms']:checks[f'regional_{y}_{k}']=c[k] is not None and c[k]<=d[k]+1e-12
        gates[name]=checks
    key=lambda name:(scores['primary'][name]['cold_mae_c'],scores['primary'][name]['mae_c'],name)
    passed=[n for n,g in gates.items() if all(g.values())];winner=min(passed,key=key) if passed else 'anchor';diagnostic=min([str(w) for w in p['weights']],key=key)
    bundle=json.loads((prior.DIRECTORY/first['models'][first['diagnostic']]).read_text(encoding='utf-8'));write_json(DIRECTORY/'expert.json',bundle)
    s={**state,'protocol':p,'expert_spec':spec,'expert_sha256':sha(DIRECTORY/'expert.json'),'winner':winner,'diagnostic':diagnostic,'gates':gates,
       'validation':scores,'fold_scores':folds,'selection_saved_at_utc':datetime.now(UTC).isoformat(),'heldout_targets_loaded_in_selection':False,
       'observations_loaded_years':[2022,2023,2024],'scope':'research refinement; cannot discover anchor misses; detector unchanged'}
    write_json(LOCK,s);return s


def evaluate():
    s=selection();base=parent_assets(ROOT/'data')['point_model'];bundle=json.loads((DIRECTORY/'expert.json').read_text(encoding='utf-8'))
    def compare(ns,terrain,contexts=None):
        rr={'anchor':records(ns,bundle,base,terrain,None),'gfs':prior.prior.records(ns,gfs=True)}
        for label,name in [('selected',s['winner']),('diagnostic',s['diagnostic'])]:rr[label]=records(ns,bundle,base,terrain,None if name=='anchor' else float(name),contexts)
        return rr,{'scores':{k:common.score(rs) for k,rs in rr.items()},'paired_vs_anchor':{k:common.paired_blocks(rs,rr['anchor']) for k,rs in rr.items() if k in ['selected','diagnostic']}}
    first=prior.evaluate();heldout,meta=data.load('heldout_site');contexts=learner.backgrounds([n for n in heldout if n['year'] in [2022,2023]],{n['id']:n for n in meta['sites']},[2022,2023])
    terrain,_=terrain_data.load(False);_,spatial=compare([n for n in heldout if n['year']==2024],terrain['records'],contexts)
    controls,cmeta=terrain_data.load(True);seasons={};frames=[]
    original={f['date']:f for f in json.loads((terrain_data.DIRECTORY/'control-forecasts.json').read_text(encoding='utf-8'))}
    for y in [2025,2026]:
        src=ROOT/'data' if y==2025 else ROOT/'data/scenario-2026';cfg=None if y==2025 else json.loads((src/'source-config.json').read_text(encoding='utf-8'));rows,prov=load_rows(src,[y],cfg)
        ns=primary_records(nights(rows));rr,z=compare(ns,controls['records']);z['provenance']=prov;seasons[str(y)]=z
        for i,n in enumerate(ns):
            if rr['anchor'][i]['point']!=original[n['date']]['points']['legacy']:raise ValueError('Original point changed')
            frames.append({'date':n['date'],'year':y,'points':{name:rs[i]['point'] for name,rs in rr.items()},'actual_minimum_c':rr['anchor'][i]['actual'],
                           'legacy_detector':original[n['date']]['legacy_detector'],'legacy_unchanged':True,'refinement_used':rr['diagnostic'][i]['refinement_used']})
    if selection()!=s:raise ValueError('Refinement choice changed after controls')
    file=terrain_data.DIRECTORY/'refinement-controls.json';write_json(file,frames)
    out={'selection':s,'selection_sha256':sha(LOCK),'climate_summary_sha256':sha(ROOT/'docs/CLIMATE_MINIMUM_SUMMARY.json'),'forecast_sha256':sha(file),
         'spatial_2024':spatial,'seasons':seasons,'terrain_controls_meta':cmeta,'original_points_and_detector_verified':184,
         'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':'research only; field GFS/original detector unchanged'}
    write_json(ROOT/'docs/CLIMATE_REFINEMENT_SUMMARY.json',out);return out


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--select-only',action='store_true');a=p.parse_args();r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'passed':[n for n,g in s['gates'].items() if all(g.values())],
                     'validation_primary':{n:s['validation']['primary'][n] for n in ['anchor',s['diagnostic']]},'seasons':r.get('seasons')},ensure_ascii=False))
