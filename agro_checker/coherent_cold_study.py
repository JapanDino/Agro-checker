"""Freeze a single-CDF experiment on 2023/24 before loading 2025/26 targets."""
from datetime import datetime
import json
import math
import numpy as np
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import coherent_cold as learner
from . import product_risk as analog
from .product_risk_study import station_nights, selection as previous_selection
from .scenario_study import load_rows
from .cold_interval_study import parent_assets
from .frost_study import fit_cold, predict_cold
from .model_study import load_protocol

DIRECTORY = ROOT / 'models/research-coherent-cold'
LOCK = DIRECTORY / 'selection-before-controls.json'


def metrics(records):
    result = {}
    for q in [-3, -2, -1, 0]:
        usable = [r for r in records if analog.complete(r['sample'])]
        hits = misses = false = negatives = 0
        brier, logs = [], []
        bins = [{'lower': a, 'upper': b, 'nights': 0, 'events': 0, 'sum_p': 0.}
                for a, b in zip([0, .1, .25, .5, .75], [.1, .25, .5, .75, 1.000001])]
        for r in usable:
            p = r['probabilities'][str(q)]
            if p is None:
                continue  # Legacy detector has no scores at lower thresholds.
            y = int(min(r['sample']['target_temperature_c']) < q)
            pred = r['legacy_event'] if 'legacy_event' in r else p >= r['decision_threshold']
            hits += bool(y and pred); misses += bool(y and not pred)
            false += bool(not y and pred); negatives += bool(not y and not pred)
            brier.append((p-y)**2)
            v = min(1-1e-12, max(1e-12, p)); logs.append(-y*math.log(v)-(1-y)*math.log(1-v))
            for b in bins:
                if b['lower'] <= p < b['upper']:
                    b['nights'] += 1; b['events'] += y; b['sum_p'] += p; break
        result[str(q)] = {'complete_nights': len(brier), 'excluded_nights': len(records)-len(usable),
                         'hits': hits, 'misses': misses, 'false_alarms': false, 'correct_negatives': negatives,
                         'brier': float(np.mean(brier)) if brier else None,
                         'log_loss': float(np.mean(logs)) if logs else None,
                         'reliability': [{k:v for k,v in b.items() if k!='sum_p'} |
                                         {'upper': min(b['upper'],1), 'mean_probability': b['sum_p']/b['nights'] if b['nights'] else None,
                                          'event_frequency': b['events']/b['nights'] if b['nights'] else None} for b in bins]}
    tested = [r for r in records if all(v is not None for v in r['probabilities'].values())]
    failures = sum(any(a>b+1e-10 for a,b in zip([r['probabilities'][str(q)] for q in [-3,-2,-1,0]],
                                               [r['probabilities'][str(q)] for q in [-2,-1,0]])) for r in tested)
    return {'thresholds': result, 'monotonicity_checked_nights': len(tested), 'nonmonotonic_nights': failures,
            'mean_threshold_brier': float(np.mean([m['brier'] for m in result.values() if m['brier'] is not None]))}


def detector_fit(rows):
    return fit_cold('event_duration_weather', rows,
                    json.loads((ROOT/'config/frost_study.json').read_text(encoding='utf-8')),
                    load_protocol(ROOT/'config/model_study.json'))


def oof_detector(rows, samples, p):
    complete = learner.prior_samples(samples)
    dates = sorted({n['date'] for n in complete})
    result = []
    # Exclude ALL hourly rows/sites for held station dates, not just target minima.
    for block in np.array_split(dates, p['date_blocks']):
        held = set(block.tolist())
        held_rows = [r for r in rows if r['date'] in held]
        fit_rows = [r for r in rows if r['date'] not in held]
        model = detector_fit(fit_rows)
        fit_dates = sorted({r['date'] for r in fit_rows})
        if set(fit_dates) & held:
            raise ValueError('OOF detector overlap')
        predictions = predict_cold(model, held_rows, .25)
        for n, pred in zip(station_nights(held_rows), predictions):
            if n['date'] in held:
                result.append({'date': n['date'], 'score': pred['night_event_model_score'], 'fit_dates': fit_dates})
    if {r['date'] for r in result} != set(dates):
        raise ValueError('OOF detector incomplete')
    return result


def candidates(p):
    return ([{'name':'shared_'+str(k), 'kind':'shared', 'neighbors':k} for k in p['analog_neighbors']] +
            [{'name': f'ordinal_{i}_{v:g}', 'kind':'ordinal', 'inputs':i, 'penalty':v}
             for i in p['ordinal_inputs'] for v in p['ordinal_penalties']])


def fit_model(samples, oof, spec, p):
    return learner.fit_shared(samples, spec['neighbors'], p) if spec['kind']=='shared' else learner.fit_ordinal(samples, oof, spec['inputs'], spec['penalty'], p)


def record(n, forecasts, threshold):
    return {'sample':n, 'probabilities':{q:f['probability'] for q,f in forecasts.items()}, 'decision_threshold':threshold}


def selection():
    path = ROOT/'config/coherent_cold.json'; p = json.loads(path.read_text(encoding='utf-8'))
    if p['folds'] != [{'train_years':[2022], 'validation_year':2023}, {'train_years':[2022,2023], 'validation_year':2024}] or p['final_train_years'] != [2022,2023] or p['evaluation_years'] != [2025,2026]:
        raise ValueError('Unexpected chronology')
    previous = previous_selection(); parent = parent_assets(ROOT/'data')
    rows, provenance = load_rows(ROOT/'data', [2022,2023,2024]); samples = station_nights(rows)
    state = {'protocol_sha256':sha(path), 'source_sha256':{name:sha(ROOT/'agro_checker'/name) for name in
             ['coherent_cold.py','coherent_cold_study.py','product_risk.py','product_risk_study.py','frost_study.py','minimum_model.py','scenario_study.py','cold_interval_study.py','model_study.py','product_data.py','hourly_study.py','single_run.py']},
             'parent_hashes':parent['hashes'], 'input_provenance':provenance,
             'previous_selection_sha256':sha(ROOT/'models/research-product-risk/selection-before-controls.json')}
    if LOCK.exists():
        s = json.loads(LOCK.read_text(encoding='utf-8'))
        if any(s[k]!=v for k,v in state.items()):
            raise ValueError('Frozen coherent experiment changed')
        for name, digest in s['artifact_sha256'].items():
            if sha(DIRECTORY/name)!=digest:
                raise ValueError('Frozen coherent artifact changed')
        return s
    write_json(DIRECTORY/'protocol-lock.json', state | {'protocol':p,'saved_at_utc':datetime.now(UTC).isoformat()})
    specs = candidates(p); names = [s['name']+'@'+str(t) for s in specs for t in p['decision_thresholds']]
    records = {name:[] for name in ['legacy','previous_candidate']+names}; fold_audits = []
    oldp = previous['protocol']
    for fold in p['folds']:
        print('Coherent CDF prior fold: '+str(fold), flush=True)
        train = [n for n in samples if n['year'] in fold['train_years']]
        valid = [n for n in samples if n['year']==fold['validation_year']]
        tr = [r for r in rows if r['year'] in fold['train_years']]
        vr = [r for r in rows if r['year']==fold['validation_year']]
        old = predict_cold(detector_fit(tr), vr, .25)
        if len(old)!=len(valid):raise ValueError('Station detector alignment')
        oof = oof_detector(tr, train, p)
        oldspec={'name':'station_32','neighbors':32}
        oldmodel=analog.fit(train, oldspec);oldcal=analog.calibrate(train, oldspec, oldp)
        for n, pred in zip(valid, old):
            records['legacy'].append({'sample':n, 'probabilities':{'-3':None,'-2':None,'-1':None,'0':pred['night_event_model_score']},'legacy_event':any(pred['cold_mask'])})
            f={str(q):analog.forecast(oldmodel,oldcal,n,q,oldp) for q in p['weather_thresholds_c']}
            records['previous_candidate'].append(record(n,f,.1))
        for spec in specs:
            model = fit_model(train,oof,spec,p)
            predictions = [learner.forecast(model,n,pred['night_event_model_score']) for n,pred in zip(valid,old)]
            for t in p['decision_thresholds']:
                records[spec['name']+'@'+str(t)].extend(record(n,f,t) for n,f in zip(valid,predictions))
        fold_audits.append({'fold':fold,'complete_train_dates':sorted({n['date'] for n in learner.prior_samples(train)}),
                            'validation_dates':[n['date'] for n in valid], 'detector_oof':oof})
    scores={name:metrics(rr) for name,rr in records.items()};base=scores['legacy']['thresholds']['0']
    gates={name:{'misses_not_more':v['thresholds']['0']['misses']<=base['misses'],
                 'false_alarms_not_more':v['thresholds']['0']['false_alarms']<=base['false_alarms'],
                 'brier_not_worse':v['thresholds']['0']['brier']<=base['brier'],
                 'thresholds_consistent':v['nonmonotonic_nights']==0} for name,v in scores.items()}
    key=lambda name:(scores[name]['thresholds']['0']['brier'],scores[name]['thresholds']['0']['false_alarms'],scores[name]['thresholds']['0']['misses'])
    eligible=[name for name in ['legacy']+names if all(gates[name].values())]
    winner=min(eligible,key=key);diagnostic=min(names,key=key)
    final=[n for n in samples if n['year'] in p['final_train_years']]
    tr=[r for r in rows if r['year'] in p['final_train_years']];oof=oof_detector(tr,final,p)
    artifacts={};models={}
    for name in dict.fromkeys([winner,diagnostic]):
        if name=='legacy':continue
        spec=next(s for s in specs if s['name']==name.split('@')[0]);model=fit_model(final,oof,spec,p)
        file=name.replace('@','-')+'.json';write_json(DIRECTORY/file,model);models[name]=file;artifacts[file]=sha(DIRECTORY/file)
    write_json(DIRECTORY/'validation-forecasts.json',records);artifacts['validation-forecasts.json']=sha(DIRECTORY/'validation-forecasts.json')
    s=state|{'protocol':p,'winner':winner,'diagnostic':diagnostic,'models':models,'artifact_sha256':artifacts,
             'validation':scores,'gates':gates,'fold_audits':fold_audits,
             'selection_saved_at_utc':datetime.now(UTC).isoformat(), 'observations_loaded_years':[2022,2023,2024],
             'original_detector_unchanged':True,'original_point_unchanged':True,
             'deployment':'research comparison, never field replacement'}
    write_json(LOCK,s);return s


def evaluate():
    s=selection();p=s['protocol'];parent=parent_assets(ROOT/'data')
    models={name:json.loads((DIRECTORY/file).read_text(encoding='utf-8')) for name,file in s['models'].items()}
    old_summary=json.loads((ROOT/'docs/PRODUCT_RISK_SUMMARY.json').read_text(encoding='utf-8'))
    old_file=ROOT/'data/processed/product-risk/night-forecasts.json'
    if sha(old_file)!=old_summary['forecast_sha256']:raise ValueError('Old controls changed')
    old_frames={f['date']:f for f in json.loads(old_file.read_text(encoding='utf-8'))}
    frames=[];seasons={}
    for year in p['evaluation_years']:
        source=ROOT/'data' if year==2025 else ROOT/'data/scenario-2026'
        config=None if year==2025 else json.loads((source/'source-config.json').read_text(encoding='utf-8'))
        rows,provenance=load_rows(source,[year],config);samples=station_nights(rows)
        old=predict_cold(parent['detector'],rows,.25)
        rr={name:[] for name in ['legacy','previous_candidate','selected','candidate','gfs']}
        for n,pred in zip(samples,old):
            ref=old_frames[n['date']]
            if pred!=ref['legacy_detector'] or n!=ref['sample']:raise ValueError('Original detector/sample changed')
            rr['legacy'].append({'sample':n,'probabilities':{'-3':None,'-2':None,'-1':None,'0':pred['night_event_model_score']},'legacy_event':any(pred['cold_mask'])})
            rr['previous_candidate'].append(record(n,ref['threshold_forecasts'],.1))
            rr['gfs'].append({'sample':n,'probabilities':{str(q):float(min(n['gfs_temperature_c'])<q) for q in p['weather_thresholds_c']},'decision_threshold':.5})
            predictions={}
            for label,name in [('selected',s['winner']),('candidate',s['diagnostic'])]:
                if name=='legacy':rr[label].append(rr['legacy'][-1]);continue
                f=learner.forecast(models[name],n,pred['night_event_model_score']);predictions[label]=f
                rr[label].append(record(n,f,float(name.split('@')[1])))
            frames.append({'date':n['date'],'year':year,'sample':n,'threshold_forecasts':predictions['candidate'],
                           'selected_is_legacy':s['winner']=='legacy','decision_threshold':float(s['diagnostic'].split('@')[1]),
                           'legacy_detector':pred,'legacy_point':ref['legacy_point'],'legacy_unchanged':True})
        seasons[str(year)]={'scores':{name:metrics(records) for name,records in rr.items()},'provenance':provenance,'legacy_verified_nights':len(samples)}
    if selection()!=s:raise ValueError('Choice changed after controls')
    file=ROOT/'data/processed/coherent-cold/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(LOCK),'seasons':seasons,'forecast_sha256':sha(file),
            'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':'research probabilities only; old detector/point/timing unchanged'}
    write_json(ROOT/'docs/COHERENT_COLD_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');a=parser.parse_args()
    r=selection() if a.select_only else evaluate();s=r if a.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],
                      'validation':{name:{'at0':m['thresholds']['0'],'nonmonotonic':m['nonmonotonic_nights']} for name,m in s['validation'].items()},
                      'seasons':r.get('seasons')},ensure_ascii=False))
