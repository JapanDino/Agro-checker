"""Freeze age/version choices on2023–24 before opening control snapshots."""
from datetime import datetime
import json
from collections import Counter
import numpy as np
from .pipeline import ROOT,UTC,write_json
from .scenario_study import load_rows,nights,sha
from .minimum_model import fit_minimum,target_minimum
from .cold_interval_study import parent_assets,summarize,validation_key
from .conditional_error import calibrate_shape
from .conditional_error_study import pool_for,calibration_for,forecast_for
from .early_cold_archive import rows as early_rows
from .extended_archive import archive_config
from .extended_error_study import counts
from . import regime_error as learner


def protocol(path):
    p=json.loads(path.read_text(encoding='utf-8'))
    ref=json.loads((ROOT/'config/extended_error.json').read_text(encoding='utf-8'))
    for key in ['folds','evaluation_years','final_calibration_year','crossfit_blocks','coverage','minimum_calibration_nights',
                'scale_floor_c','scale_ceiling_c','warm_width_ratio_limit','overall_width_ratio_limit']:
        if p[key]!=ref[key]:raise ValueError('Unexpected chronology or calibration gates')
    if p['extra_years']!=[2021] or p['baselines']!=ref['baseline_candidates']:raise ValueError('Unexpected archive/baselines')
    if p['families']!=[s for s in ref['conditional_families'] if s['name'] in ['scale_ridge_100','tails_ridge_10','quantile_trees']]:raise ValueError('Unexpected fixed families')
    if [s['name'] for s in p['policies']]!=['uniform','age_1','age_2','version_discount','age_version_1','version_offsets','age_version_offsets_2']:raise ValueError('Unexpected policy registry')
    registry=p['version_registry']
    if [r['name'] for r in registry]!=['pre_v16','v16_before_2022_update','after_2022_update'] or registry[0]['from']!='2021-01-01T00:00:00+00:00' or registry[-1]['until']!='2026-06-01T00:00:00+00:00':raise ValueError('Invalid version registry')
    for r in registry:
        if datetime.fromisoformat(r['from'])>=datetime.fromisoformat(r['until']):raise ValueError('Invalid version interval')
    if registry[0]['until']!=registry[1]['from'] or registry[1]['until']!=registry[2]['from']:raise ValueError('Version gap/overlap')
    p['candidates']=p['baselines']+[{**f,'family':f['name'],'policy_name':policy['name'],'name':policy['name']+'__'+f['name']} for policy in p['policies'] for f in p['families']]
    return p


def prior_samples(data_dir):
    rows,provenance=load_rows(data_dir,[2022,2023,2024]);base=nights(rows)
    directory=data_dir/'archive-2021';c=json.loads((directory/'source-config.json').read_text(encoding='utf-8'))
    if c!=archive_config():raise ValueError('Early config changed')
    later,lp=load_rows(directory,[2021],c);early,ep=early_rows(directory);extra=nights(early+later)
    if len(extra)!=92 or len({n['date'] for n in extra})!=92:raise ValueError('Need92 early forecast nights')
    return base,extra,{'recent':provenance,'extra_post_upgrade':lp,'extra_pre_upgrade':ep}


def choose(scores,p):
    base=scores['legacy_symmetric'];previous=scores['risk_5_lower_95'];gates={}
    for name,r in scores.items():
        gates[name]={'coverage_not_lower':r['covered']>=base['covered'],'point_mae_not_higher':r['mae_c']<=base['mae_c'],
          'warm_width_within_limit':r['warm_mean_width_c']<=base['warm_mean_width_c']*p['warm_width_ratio_limit'],
          'overall_width_within_limit':r['mean_width_c']<=previous['mean_width_c']*p['overall_width_ratio_limit']}
    eligible=[s for s in p['candidates'] if all(gates[s['name']].values())]
    best=lambda specs:min(specs,key=lambda s:validation_key(scores[s['name']]))['name'] if specs else None
    winner=best(eligible) or 'legacy_symmetric'
    alternatives=[s for s in p['candidates'] if s.get('policy_name') not in [None,'uniform']]
    diagnostic=best([s for s in alternatives if all(gates[s['name']].values())]) or best(alternatives)
    matched='uniform__'+diagnostic.split('__',1)[1]
    return winner,diagnostic,matched,gates


def selection(path,data_dir):
    p=protocol(path);parent=parent_assets(data_dir);directory=data_dir.parent/'models/research-regime-errors';lock=directory/'selection-before-controls.json'
    previous=data_dir.parent/'models/research-extended-errors/selection-before-controls.json'
    sources={n:sha(ROOT/'agro_checker'/n) for n in ['regime_error.py','regime_error_study.py','no_cloud_error.py','extended_error.py','extended_error_study.py',
        'extended_archive.py','early_cold_archive.py','conditional_error.py','conditional_error_study.py','minimum_model.py','cold_interval.py',
        'cold_interval_study.py','scenario_study.py','single_run.py','gfs_archive.py','gfs_covariates.py','humidity_archive.py','hourly_study.py','frost_study.py']}
    old=json.loads(lock.read_text(encoding='utf-8')) if lock.exists() else None
    if old:
        if old['protocol_sha256']!=sha(path) or old['source_sha256']!=sources or old['parent_hashes']!=parent['hashes'] or old['previous_selection_sha256']!=sha(previous):raise ValueError('Frozen protocol/source/parent changed')
        for file,digest in old['artifact_sha256'].items():
            if sha(directory/file)!=digest:raise ValueError('Frozen weighted model changed')
    else:write_json(directory/'protocol-lock.json',{'protocol':p,'protocol_sha256':sha(path),'source_sha256':sources,'saved_at_utc':datetime.now(UTC).isoformat()})
    base,extra,provenance=prior_samples(data_dir)
    if old:
        if old['input_provenance']!=provenance:raise ValueError('Frozen weighted inputs changed')
        return old
    records={s['name']:[] for s in p['candidates']};folds=[];final_fits=None
    for fold in p['folds']:
        train=[n for n in base if n['year'] in fold['train_years']];valid=[n for n in base if n['year']==fold['validation_year']]
        baseline_point=fit_minimum(parent['point_model']['spec'],train);anchor=max(fold['train_years']);fits={}
        for policy in p['policies']:
            point=learner.fit_point(extra+train,policy,p,anchor);residuals,audit=learner.crossfit(extra+train,policy,p,anchor)
            fits[policy['name']]={'point':point,'records':residuals,'audit':audit}
        if fold['validation_year']==2024:final_fits=fits
        scores={};unseen={}
        for spec in p['candidates']:
            name=spec['name'];policy_name=spec.get('policy_name');rr=[]
            if policy_name:
                fit=fits[policy_name];point=fit['point'];head=learner.fit_head(fit['records'],fit['audit'],spec,point['policy'],p,anchor)
                pool=learner.pool(point,head,valid,p,fold['validation_year'])
            else:point=baseline_point;head={'spec':spec};pool=pool_for(point,head,valid,p,fold['validation_year'])
            for n in valid:
                if target_minimum(n) is None:continue
                cal=calibrate_shape(pool,p,n['date']) if policy_name else calibration_for(pool,head,p,n['date'])
                forecast=learner.forecast(point,head,cal,n,p) if policy_name else forecast_for(point,head,cal,n,p)
                rr.append({'date':n['date'],'forecast':forecast,'actual_minimum_c':target_minimum(n)})
            scores[name]=summarize(rr);unseen[name]=sum(r['forecast'].get('unseen_regime',False) for r in rr);records[name].extend(rr)
        folds.append({**fold,'scores':scores,'unseen_fitted_regime_nights':unseen,'training_weight_audit':{name:f['point']['weight_audit'] for name,f in fits.items()}})
    scores={name:summarize(rr) for name,rr in records.items()}
    # Unit weights must reproduce the previous prior-only no-cloud experiment.
    reference=json.loads(previous.read_text(encoding='utf-8'))
    for name in scores:
        ref_name=name if name in ['legacy_symmetric','risk_5_lower_95'] else 'extended__'+name.split('__')[1] if name.startswith('uniform__') else None
        if ref_name:
            for key,value in reference['pooled_validation'][ref_name].items():
                current=scores[name][key]
                if isinstance(value,(int,float)) and value is not None:
                    if not np.isclose(current,value,rtol=0,atol=1e-9):raise ValueError('Unweighted prior baseline changed')
                elif current!=value:raise ValueError('Unweighted prior baseline changed')
    winner,diagnostic,matched,gates=choose(scores,p);models={};artifacts={}
    valid=[n for n in base if n['year']==2024]
    for name in dict.fromkeys([winner,diagnostic,matched]):
        spec=next(s for s in p['candidates'] if s['name']==name);policy_name=spec.get('policy_name')
        if policy_name:
            fit=final_fits[policy_name];point=fit['point'];head=learner.fit_head(fit['records'],fit['audit'],spec,point['policy'],p,2023)
            cal=calibrate_shape(learner.pool(point,head,valid,p,2024),p)
        else:point=parent['point_model'];head={'spec':spec};cal=calibration_for(pool_for(point,head,valid,p,2024),head,p)
        models[name]={}
        for label,value in [('point',point),('head',head),('calibration',cal)]:
            file=name+'-'+label+'.json';write_json(directory/file,value);models[name][label]=file;artifacts[file]=sha(directory/file)
    result={'protocol':p,'protocol_sha256':sha(path),'source_sha256':sources,'parent_hashes':parent['hashes'],
       'previous_selection_sha256':sha(previous),'winner':winner,'diagnostic':diagnostic,'matched_uniform':matched,'gates':gates,
       'pooled_validation':scores,'folds':folds,'models':models,'artifact_sha256':artifacts,'input_provenance':provenance,
       'observations_loaded_years':[2021,2022,2023,2024],'selection_saved_at_utc':datetime.now(UTC).isoformat(),
       'archive_counts':{str(y):counts(extra if y==2021 else [n for n in base if n['year']==y]) for y in [2021,2022,2023,2024]}}
    write_json(lock,result);return result


def evaluate(path,data_dir,report_dir):
    s=selection(path,data_dir);p=s['protocol'];parent=parent_assets(data_dir);directory=data_dir.parent/'models/research-regime-errors'
    models={name:{k:json.loads((directory/file).read_text(encoding='utf-8')) for k,file in paths.items()} for name,paths in s['models'].items()}
    def snapshot(summary,file,key):
        meta=json.loads((report_dir/summary).read_text(encoding='utf-8'));source=data_dir/file
        if sha(source)!=meta[key]:raise ValueError('Frozen reference changed')
        return {f['date']:f for f in json.loads(source.read_text(encoding='utf-8'))},sha(source)
    # Read controls only after all winner and diagnostic artifacts are frozen.
    old,ohash=snapshot('MINIMUM_STUDY_SUMMARY.json','processed/minimum-study/night-forecasts.json','forecast_sha256')
    masks,mhash=snapshot('SCENARIO_STUDY_SUMMARY.json','processed/scenario-study/night-forecasts.json','forecast_artifact_sha256')
    previous,phash=snapshot('COLD_INTERVAL_SUMMARY.json','processed/cold-interval-study/night-forecasts.json','forecast_sha256')
    from .frost_study import predict_cold
    frames=[];seasons={}
    for year in p['evaluation_years']:
        if year==2025:rows,provenance=load_rows(data_dir,[year])
        else:
            fresh=data_dir/'scenario-2026';rows,provenance=load_rows(fresh,[year],json.loads((fresh/'source-config.json').read_text(encoding='utf-8')))
        samples=nights(rows);events=predict_cold(parent['detector'],rows,.25)
        if len(samples)!=len(events):raise ValueError('Detector alignment mismatch')
        rr={name:[] for name in ['gfs','legacy_huber','previous_cold_range','selected','weighted_candidate','matched_uniform']};detector=Counter();unseen=Counter()
        for n,event in zip(samples,events):
            ref=old[n['date']]
            if event['date']!=n['date'] or event['cold_mask']!=masks[n['date']]['previous_detector_cold_mask'] or event['frost']!=ref['previous_detector']['frost']:raise ValueError('Detector mask/timing changed')
            predictions={}
            for name,model in models.items():
                if model['head']['spec'].get('policy_name'):forecast=learner.forecast(model['point'],model['head'],model['calibration'],n,p)
                else:
                    forecast=forecast_for(parent['point_model'],model['head'],model['calibration'],n,p)
                    if forecast['point_c']!=ref['selected']['point_c']:raise ValueError('Original point changed')
                predictions[name]=forecast
            f={'gfs':ref['gfs'],'legacy_huber':ref['selected'],'previous_cold_range':previous[n['date']]['selected'],
               'selected':predictions[s['winner']],'weighted_candidate':predictions[s['diagnostic']],'matched_uniform':predictions[s['matched_uniform']]}
            actual=target_minimum(n)
            if actual is not None:
                cold,detected=actual<0,any(event['cold_mask']);detector['hits' if cold and detected else 'misses' if cold else 'false_alarms' if detected else 'correct_negatives']+=1
            for name,forecast in f.items():
                rr[name].append({'date':n['date'],'forecast':forecast,'actual_minimum_c':actual});unseen[name]+=bool(forecast.get('unseen_regime',False))
            frames.append({**ref,'forecasts':f,'cold_mask':event['cold_mask'],'detector_unchanged':True})
        seasons[str(year)]={'scores':{name:summarize(records) for name,records in rr.items()},'detector':dict(detector),
                           'detector_masks_verified':len(samples),'unseen_regime_nights':dict(unseen),'input_provenance':provenance}
    if selection(path,data_dir)!=s:raise ValueError('Selection changed during controls')
    file=data_dir/'processed/regime-error-study/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(directory/'selection-before-controls.json'),'seasons':seasons,'forecast_sha256':sha(file),
            'legacy_forecast_sha256':ohash,'legacy_mask_sha256':mhash,'previous_range_sha256':phash,'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':p['deployment']}
    write_json(report_dir/'REGIME_ERROR_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');args=parser.parse_args()
    r=selection(ROOT/'config/regime_error.json',ROOT/'data') if args.select_only else evaluate(ROOT/'config/regime_error.json',ROOT/'data',ROOT/'docs')
    s=r if args.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'matched_uniform':s['matched_uniform'],
                      'validation':s['pooled_validation'],'seasons':r.get('seasons')},ensure_ascii=False))
