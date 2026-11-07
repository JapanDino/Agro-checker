"""Separate prior-only calibration with identity anchor and warm-season gates."""
from datetime import datetime
import json
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import cold_calibration as learner
from . import coherent_cold as ordinal
from . import coherent_cold_study as previous
from .product_risk import complete
from .product_risk_study import station_nights
from .scenario_study import load_rows
from .cold_interval_study import parent_assets

DIRECTORY=ROOT/'models/research-cold-calibration'
LOCK=DIRECTORY/'selection-before-controls.json'


def score(records):
    if any(complete(r['sample']) for r in records):m=previous.metrics(records)
    else:
        tested=[r for r in records if all(v is not None for v in r['probabilities'].values())]
        bad=sum(any(a>b+1e-10 for a,b in zip([r['probabilities'][str(q)] for q in [-3,-2,-1]],
                                          [r['probabilities'][str(q)] for q in [-2,-1,0]])) for r in tested)
        m={'thresholds':{str(q):{'complete_nights':0,'excluded_nights':len(records),'hits':0,'misses':0,'false_alarms':0,
                                 'correct_negatives':0,'brier':None,'log_loss':None,'reliability':[]} for q in [-3,-2,-1,0]},
           'monotonicity_checked_nights':len(tested),'nonmonotonic_nights':bad,'mean_threshold_brier':None}
    for q,metrics in m['thresholds'].items():
        for name,cold in [('cold',True),('warm',False)]:
            usable=[r for r in records if complete(r['sample']) and r['probabilities'][q] is not None and (min(r['sample']['target_temperature_c'])<float(q))==cold]
            metrics[name+'_nights']=len(usable)
            metrics[name+'_brier']=sum((r['probabilities'][q]-int(cold))**2 for r in usable)/len(usable) if usable else None
    return m


def specs(p):
    return ([{'name':'identity','kind':'identity'},{'name':'ordinal','kind':'ordinal'}]+
            [{'name':f'mixture_{w:g}','kind':'mixture','weight':w} for w in p['mixture_weights']]+
            [{'name':f'{loss}_{source}_{pen:g}','kind':'affine','loss':loss,'source':source,'penalty':pen}
             for loss,source in [('brier','oof'),('logloss','oof'),('brier','forward')] for pen in p['affine_penalties']])


def calibration_records(samples,oof):
    by_date={n['date']:n for n in samples if complete(n)}
    if set(by_date)!={r['date'] for r in oof}:raise ValueError('Calibration alignment')
    return [{'date':r['date'],'year':by_date[r['date']]['year'],'score':r['score'],
             'target':int(min(by_date[r['date']]['target_temperature_c'])<0),'fit_dates':r['fit_dates']} for r in oof]


def head(spec,records,p):
    h=spec.copy()
    if h['kind']=='affine':h['calibration']=learner.fit_affine(records,h['loss'],h['penalty'],p)
    h['status']=h.get('calibration',{}).get('status','fixed_prior_reference')
    return h


def forecast_record(n,f,p):
    return {'sample':n,'probabilities':{q:x['probability'] for q,x in f.items()},'decision_threshold':p['decision_threshold']}


def selection():
    path=ROOT/'config/cold_calibration.json';p=json.loads(path.read_text(encoding='utf-8'))
    if p['folds']!=[{'train_years':[2022],'validation_year':2023},{'train_years':[2022,2023],'validation_year':2024}] or p['final_train_years']!=[2022,2023] or p['evaluation_years']!=[2025,2026] or p['decision_threshold']!=.25:
        raise ValueError('Unexpected chronology or decision threshold')
    old=previous.selection();parents=parent_assets(ROOT/'data')
    rows,provenance=load_rows(ROOT/'data',[2022,2023,2024]);samples=station_nights(rows)
    oldfile=previous.DIRECTORY/'validation-forecasts.json'
    state={'protocol_sha256':sha(path),'source_sha256':{name:sha(ROOT/'agro_checker'/name) for name in
           ['cold_calibration.py','cold_calibration_study.py','coherent_cold.py','coherent_cold_study.py','product_risk.py','product_risk_study.py','frost_study.py','minimum_model.py','scenario_study.py','cold_interval_study.py','model_study.py','product_data.py','hourly_study.py','single_run.py']},
           'previous_selection_sha256':sha(previous.LOCK),'previous_validation_sha256':sha(oldfile),
           'parent_hashes':parents['hashes'],'input_provenance':provenance}
    if LOCK.exists():
        s=json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):raise ValueError('Frozen calibration sources/protocol/parents changed')
        for file,digest in s['artifact_sha256'].items():
            if sha(DIRECTORY/file)!=digest:raise ValueError('Frozen calibration artifact changed')
        return s
    write_json(DIRECTORY/'protocol-lock.json',state|{'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    validation=json.loads(oldfile.read_text(encoding='utf-8'))
    registry=specs(p);records={s['name']:[] for s in registry};folds=[]
    for fold,audit in zip(p['folds'],old['fold_audits']):
        if audit['fold']!=fold:raise ValueError('Parent fold mismatch')
        train=[n for n in samples if n['year'] in fold['train_years']]
        valid=[r for r in validation['legacy'] if r['sample']['year']==fold['validation_year']]
        oof=audit['detector_oof'];cal=calibration_records(train,oof)
        base=ordinal.fit_ordinal(train,oof,'score',p['ordinal_penalty'],p)
        source='whole_date_oof'
        forward=cal
        if 2023 in fold['train_years']:
            train_dates=old['fold_audits'][0]['complete_train_dates']
            forward=[{'date':r['sample']['date'],'year':2023,'score':r['probabilities']['0'],
                      'target':int(min(r['sample']['target_temperature_c'])<0),'fit_dates':train_dates}
                     for r in validation['legacy'] if r['sample']['year']==2023 and complete(r['sample'])]
            source='2022_fit_to_2023_forecasts'
        heads={}
        for spec in registry:
            h=head(spec,forward if spec.get('source')=='forward' else cal,p);heads[spec['name']]=h
            m={'ordinal_base':base,'head':h}
            for r in valid:
                n=r['sample'];f=learner.forecast(m,n,r['probabilities']['0']);records[spec['name']].append(forecast_record(n,f,p))
        folds.append({'fold':fold,'oof_calibration':cal,'forward_calibration':forward,'forward_source':source,'heads':heads})
    scores={name:score(rr) for name,rr in records.items()}
    season_scores={name:{str(y):score([r for r in rr if r['sample']['year']==y]) for y in [2023,2024]} for name,rr in records.items()}
    base=scores['identity']['thresholds']['0'];gates={}
    for name,m in scores.items():
        at0=m['thresholds']['0']
        gates[name]={'misses_not_more':at0['misses']<=base['misses'],'false_alarms_not_more':at0['false_alarms']<=base['false_alarms'],
                     'brier_not_worse':at0['brier']<=base['brier']+1e-14,
                     'cold_brier_not_worse':at0['cold_brier']<=base['cold_brier']+1e-14,
                     'warm_brier_not_worse_each_season':all(season_scores[name][str(y)]['thresholds']['0']['warm_brier']<=season_scores['identity'][str(y)]['thresholds']['0']['warm_brier']+1e-14 for y in [2023,2024]),
                     'coherent':m['nonmonotonic_nights']==0}
    key=lambda name:(scores[name]['thresholds']['0']['brier'],[s['name'] for s in registry].index(name))
    winner=min([name for name,g in gates.items() if all(g.values())],key=key);diagnostic=min(scores,key=key)
    final=[n for n in samples if n['year'] in [2022,2023]]
    oldmodel_path=previous.DIRECTORY/old['models'][old['diagnostic']]
    base=json.loads(oldmodel_path.read_text(encoding='utf-8'))
    if base['inputs']!='score' or base['penalty']!=p['ordinal_penalty']:raise ValueError('Unexpected ordinal parent')
    cal=calibration_records(final,base['oof_detector_audits'])
    forward=folds[-1]['forward_calibration'];artifacts={};models={}
    for name in dict.fromkeys([winner,diagnostic]):
        spec=next(s for s in registry if s['name']==name);h=head(spec,forward if spec.get('source')=='forward' else cal,p)
        file=name+'.json';write_json(DIRECTORY/file,{'ordinal_base':base,'head':h});models[name]=file;artifacts[file]=sha(DIRECTORY/file)
    write_json(DIRECTORY/'validation-forecasts.json',records);artifacts['validation-forecasts.json']=sha(DIRECTORY/'validation-forecasts.json')
    result=state|{'protocol':p,'winner':winner,'diagnostic':diagnostic,'validation':scores,'validation_seasons':season_scores,'gates':gates,'folds':folds,
                  'models':models,'artifact_sha256':artifacts,'ordinal_parent_model_sha256':sha(oldmodel_path),
                  'selection_saved_at_utc':datetime.now(UTC).isoformat(),'observations_loaded_years':[2022,2023,2024],
                  'deployment':'research probabilities; original detector/point/alarms/timing preserved'}
    write_json(LOCK,result);return result


def evaluate():
    s=selection();p=s['protocol'];parents=parent_assets(ROOT/'data')
    old_summary=json.loads((ROOT/'docs/COHERENT_COLD_SUMMARY.json').read_text(encoding='utf-8'))
    oldfile=ROOT/'data/processed/coherent-cold/night-forecasts.json'
    if sha(oldfile)!=old_summary['forecast_sha256'] or sha(previous.LOCK)!=old_summary['selection_sha256']:raise ValueError('Old coherent controls changed')
    oldframes=json.loads(oldfile.read_text(encoding='utf-8'))
    models={name:json.loads((DIRECTORY/file).read_text(encoding='utf-8')) for name,file in s['models'].items()}
    frames=[];records={str(y):{name:[] for name in ['legacy','previous_ordinal','selected','diagnostic','gfs']} for y in p['evaluation_years']}
    for old in oldframes:
        n=old['sample'];raw=old['legacy_detector']['night_event_model_score'];yr=str(n['year']);f={}
        for label,name in [('selected',s['winner']),('diagnostic',s['diagnostic'])]:
            f[label]=learner.forecast(models[name],n,raw);records[yr][label].append(forecast_record(n,f[label],p))
        records[yr]['legacy'].append({'sample':n,'probabilities':{'-3':None,'-2':None,'-1':None,'0':raw},'legacy_event':any(old['legacy_detector']['cold_mask'])})
        records[yr]['previous_ordinal'].append(forecast_record(n,old['threshold_forecasts'],p))
        records[yr]['gfs'].append({'sample':n,'probabilities':{str(q):float(min(n['gfs_temperature_c'])<q) for q in [-3,-2,-1,0]},'decision_threshold':.25})
        frames.append({'date':n['date'],'year':n['year'],'sample':n,'threshold_forecasts':f['selected'],'diagnostic_forecasts':f['diagnostic'],
                       'legacy_detector':old['legacy_detector'],'legacy_point':old['legacy_point'],'legacy_unchanged':True})
    if selection()!=s or parents['hashes']!=parent_assets(ROOT/'data')['hashes']:raise ValueError('Choice/parents changed after controls')
    file=ROOT/'data/processed/cold-calibration/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(LOCK),'seasons':{y:{'scores':{name:score(rr) for name,rr in data.items()}} for y,data in records.items()},
            'forecast_sha256':sha(file),'parent_control_forecast_sha256':sha(oldfile),'generated_at_utc':datetime.now(UTC).isoformat(),
            'deployment':'research only; original detector/point/masks/timing unchanged'}
    write_json(ROOT/'docs/COLD_CALIBRATION_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');a=parser.parse_args()
    r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'validation':{name:m['thresholds']['0'] for name,m in s['validation'].items()},'seasons':r.get('seasons')},ensure_ascii=False))
