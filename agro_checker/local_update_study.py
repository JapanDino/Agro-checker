"""Historical shadow nowcast under an explicit observation-delay assumption."""
import json
from datetime import datetime,timedelta
from .pipeline import ROOT,write_json,UTC
from .scenario_study import load_rows,sha
from .product_risk_study import station_nights
from .product_advice import local_update


def simulate(n,decay,cap):
    times=[datetime.fromisoformat(t) for t in n['times']];asof=times[2]+timedelta(minutes=15)
    if any(v is None for v in n['target_temperature_c'][:3]):return None
    hours=[{'valid_at_utc':t,'temperature_c':v} for t,v in zip(n['times'],n['gfs_temperature_c'])]
    measurements=[{'id':n['date']+':'+str(i),'effective_status':'accepted','recorded_at_utc':(times[i]+timedelta(minutes=15)).isoformat(),
      'payload':{'sensor_id':'station','observed_at_utc':n['times'][i],'temperature_c':n['target_temperature_c'][i]}} for i in range(3)]
    r=local_update(hours,measurements,asof.isoformat(),{'id':'station','measurement_type':'air_2m','height_m':2,'calibration_status':'checked'},'not_used',decay,cap)
    if r['hours'] is None:return None
    indexes=[n['times'].index(h['valid_at_utc']) for h in r['hours']]
    if any(n['target_temperature_c'][i] is None for i in indexes):return None
    return {'date':n['date'],'asof':asof.isoformat(),'indexes':indexes,'truth':[n['target_temperature_c'][i] for i in indexes],
      'base':[n['gfs_temperature_c'][i] for i in indexes],'updated':[h['temperature_c'] for h in r['hours']],
      'assumption':'hourly observations usable15minutes later, checked2m sensor and no intervention; not recorded historical availability'}


def scores(records,key):
    mean=lambda v:sum(v)/len(v) if v else None
    all_errors=[];cold_errors=[];warm_errors=[];hits=misses=false=0
    for r in records:
        truth=r['truth'];values=r[key];cold=min(truth)<0;pred=min(values)<0
        if cold:hits+=pred;misses+=not pred
        else:false+=pred
        errors=[abs(a-b) for a,b in zip(truth,values)];all_errors+=errors
        (cold_errors if cold else warm_errors).extend(errors)
    return {'nights':len(records),'hours':len(all_errors),'mae_c':mean(all_errors),'cold_mae_c':mean(cold_errors),'warm_mae_c':mean(warm_errors),
      'hits':hits,'misses':misses,'false_alarms':false}


def selection():
    directory=ROOT/'models/research-local-update';path=directory/'selection-before-controls.json'
    state={'source_sha256':{f:sha(ROOT/'agro_checker'/f) for f in ['local_update_study.py','product_advice.py','product_risk_study.py','scenario_study.py']},
      'registry':[(d,c) for d in [1,3,6] for c in [1,3]],'validation_years':[2023,2024],'observation_delay_minutes':15,'asof_index':2,'prediction_hours':6}
    # Tuples are serialized canonically so the freeze survives a JSON round-trip.
    state=json.loads(json.dumps(state))
    rows,prov=load_rows(ROOT/'data',[2023,2024]);state['provenance']=prov
    if path.exists():
        s=json.loads(path.read_text(encoding='utf-8'))
        if s['state']!=state:raise ValueError('Frozen local-update experiment changed')
        return s
    ns=station_nights(rows);all_records={str(spec):[r for n in ns if (r:=simulate(n,*spec)) is not None] for spec in state['registry']}
    # Common cohort prevents failed QC from improving one candidate by removing hard cases.
    common=set.intersection(*(set(r['date'] for r in rr) for rr in all_records.values()))
    candidate_scores={k:scores([r for r in rr if r['date'] in common],'updated') for k,rr in all_records.items()}
    key=lambda name:((candidate_scores[name]['cold_mae_c']+candidate_scores[name]['warm_mae_c'])/2,candidate_scores[name]['mae_c'])
    best=min(candidate_scores,key=key);spec=json.loads(best)
    baseline=scores([r for r in all_records[best] if r['date'] in common],'base')
    s={'state':state,'selected_decay_cap':spec,'validation':candidate_scores,'baseline':baseline,'common_nights':len(common),
      'saved_at_utc':datetime.now(UTC).isoformat(),'status':'historical simulation only; never field validated; original GFS/point/detector unchanged'}
    write_json(path,s);return s


def evaluate():
    s=selection();seasons={};frames=[]
    for year in [2025,2026]:
        source=ROOT/'data' if year==2025 else ROOT/'data/scenario-2026';config=None if year==2025 else json.loads((source/'source-config.json').read_text(encoding='utf-8'))
        rows,prov=load_rows(source,[year],config);ns=station_nights(rows)
        records=[r for n in ns if (r:=simulate(n,*s['selected_decay_cap'])) is not None]
        seasons[str(year)]={'baseline':scores(records,'base'),'updated':scores(records,'updated'),'excluded_nights':len(ns)-len(records),'provenance':prov};frames+=records
    if selection()!=s:raise ValueError('Local choice changed')
    file=ROOT/'data/processed/local-update/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(ROOT/'models/research-local-update/selection-before-controls.json'),'seasons':seasons,'forecast_sha256':sha(file)}
    write_json(ROOT/'docs/LOCAL_UPDATE_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--select-only',action='store_true');a=p.parse_args()
    r=selection() if a.select_only else evaluate();print(json.dumps(r,ensure_ascii=False))
