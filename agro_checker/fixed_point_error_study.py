"""Select ONLY residual heads on2023/24; point and detector invariants enforced."""
from datetime import datetime
import json
from collections import Counter
import numpy as np
from .pipeline import ROOT,UTC,write_json
from .scenario_study import load_rows,nights,sha
from .minimum_model import fit_minimum,point_minimum,target_minimum
from .cold_interval_study import parent_assets,summarize,validation_key
from .conditional_error import calibrate_shape
from .conditional_error_study import pool_for,calibration_for,forecast_for
from .extended_archive import archive_config
from .extended_error_study import counts
from . import fixed_point_error as learner


def protocol(path):
    p=json.loads(path.read_text(encoding='utf-8'));ref=json.loads((ROOT/'config/regime_error_v2.json').read_text(encoding='utf-8'))
    for key in ['folds','extra_years','evaluation_years','final_calibration_year','crossfit_blocks','coverage','minimum_calibration_nights',
                'scale_floor_c','scale_ceiling_c','warm_width_ratio_limit','overall_width_ratio_limit','version_registry','policies','families','baselines']:
        if p[key]!=ref[key]:raise ValueError('Unexpected fixed residual registry/chronology')
    if p['windows']!=['recent_uniform','compatible_2021']:raise ValueError('Unexpected compatible training windows')
    p['candidates']=p['baselines']+[{**f,'family':f['name'],'window':'recent_uniform','policy_name':'uniform','name':'recent_uniform__'+f['name']} for f in p['families']]+[
        {**f,'family':f['name'],'window':'compatible_2021','policy_name':policy['name'],'name':policy['name']+'__'+f['name']} for policy in p['policies'] for f in p['families']]
    return p


def prior_samples(data_dir):
    rows,provenance=load_rows(data_dir,[2022,2023,2024]);base=nights(rows)
    directory=data_dir/'archive-2021';c=json.loads((directory/'source-config.json').read_text(encoding='utf-8'))
    if c!=archive_config():raise ValueError('Compatible2021 config changed')
    extra_rows,ep=load_rows(directory,[2021],c);extra=nights(extra_rows)
    if len(extra)!=70 or any(n['date']<'2021-03-23' or n['date']>'2021-05-31' for n in extra):raise ValueError('Need70 compatible2021 nights')
    # Early2021 errors are NOT generated with a surrogate point model.
    return base,extra,{'recent':provenance,'compatible2021':ep}


def choose(scores,p):
    legacy=scores['legacy_symmetric'];previous=scores['risk_5_lower_95'];gates={}
    for name,r in scores.items():
        if r['mae_c']!=legacy['mae_c']:raise ValueError('Point MAE changed between residual variants')
        gates[name]={'coverage_not_lower':r['covered']>=legacy['covered'],
           'warm_width_within_limit':r['warm_mean_width_c']<=legacy['warm_mean_width_c']*p['warm_width_ratio_limit'],
           'overall_width_within_limit':r['mean_width_c']<=previous['mean_width_c']*p['overall_width_ratio_limit']}
    eligible=[s for s in p['candidates'] if all(gates[s['name']].values())]
    best=lambda candidates:min(candidates,key=lambda spec:validation_key(scores[spec['name']]))['name'] if candidates else None
    winner=best(eligible) or 'legacy_symmetric'
    changed=[s for s in p['candidates'] if s.get('policy_name') not in [None,'uniform']]
    diagnostic=best([s for s in changed if all(gates[s['name']].values())]) or best(changed)
    matched='uniform__'+diagnostic.split('__',1)[1]
    return winner,diagnostic,matched,gates


def selection(path,data_dir):
    p=protocol(path);parent=parent_assets(data_dir);directory=data_dir.parent/'models/research-fixed-point-errors';lock=directory/'selection-before-controls.json'
    previous=data_dir.parent/'models/research-conditional-errors/selection-before-controls.json'
    archive_reference=data_dir.parent/'models/research-extended-errors/selection-before-controls.json'
    sources={name:sha(ROOT/'agro_checker'/name) for name in ['fixed_point_error.py','fixed_point_error_study.py','regime_error_v2.py','conditional_error.py',
      'minimum_model.py','conditional_error_study.py','cold_interval.py','cold_interval_study.py','scenario_study.py','single_run.py',
      'gfs_archive.py','gfs_covariates.py','humidity_archive.py','hourly_study.py','frost_study.py','extended_archive.py','extended_error_study.py']}
    old=json.loads(lock.read_text(encoding='utf-8')) if lock.exists() else None
    if old:
        if old['protocol_sha256']!=sha(path) or old['source_sha256']!=sources or old['parent_hashes']!=parent['hashes'] or old['previous_selection_sha256']!=sha(previous) or old['archive_reference_sha256']!=sha(archive_reference):raise ValueError('Frozen fixed-point protocol/source/parent changed')
        for file,digest in old['artifact_sha256'].items():
            if sha(directory/file)!=digest:raise ValueError('Frozen residual artifact changed')
    else:write_json(directory/'protocol-lock.json',{'protocol':p,'protocol_sha256':sha(path),'source_sha256':sources,'saved_at_utc':datetime.now(UTC).isoformat()})
    base,extra,provenance=prior_samples(data_dir)
    if old:
        if old['input_provenance']!=provenance:raise ValueError('Frozen residual inputs changed')
        return old
    records={s['name']:[] for s in p['candidates']};folds=[];final_residuals=None
    for fold in p['folds']:
        train=[n for n in base if n['year'] in fold['train_years']];valid=[n for n in base if n['year']==fold['validation_year']]
        # Same original unweighted point family, independent of every error policy.
        point=fit_minimum(parent['point_model']['spec'],train);anchor=max(fold['train_years']);residual_sets={}
        for policy in p['policies']:
            for window in ['recent_uniform','compatible_2021']:
                if window=='recent_uniform' and policy['name']!='uniform':continue
                residual_sets[(window,policy['name'])]=learner.residuals(train,extra if window=='compatible_2021' else [],point,policy,p,anchor)
        if fold['validation_year']==2024:final_residuals=residual_sets
        scores={};unseen={}
        for spec in p['candidates']:
            name=spec['name'];policy_name=spec.get('policy_name');rr=[]
            if policy_name:
                policy=next(s for s in p['policies'] if s['name']==policy_name)
                head=learner.fit_error(*residual_sets[(spec['window'],policy_name)],spec,policy,p,anchor)
                pool=learner.pool(point,head,valid,p,fold['validation_year'])
            else:head={'spec':spec};pool=pool_for(point,head,valid,p,fold['validation_year'])
            for n in valid:
                if target_minimum(n) is None:continue
                cal=calibrate_shape(pool,p,n['date']) if policy_name else calibration_for(pool,head,p,n['date'])
                f=learner.forecast(point,head,cal,n,p) if policy_name else forecast_for(point,head,cal,n,p)
                if f['point_c']!=point_minimum(point,n)[0]:raise ValueError('Validation point changed')
                rr.append({'date':n['date'],'forecast':f,'actual_minimum_c':target_minimum(n)})
            scores[name]=summarize(rr);records[name].extend(rr);unseen[name]=sum(r['forecast'].get('unseen_regime',False) for r in rr)
        folds.append({**fold,'scores':scores,'unseen_head_regime_nights':unseen,'recent_residual_nights':len(residual_sets[('recent_uniform','uniform')][0]),
                      'compatible_residual_nights':len(residual_sets[('compatible_2021','uniform')][0])})
    scores={name:summarize(rr) for name,rr in records.items()}
    # Recent uniform residual heads must reproduce the frozen original-point experiment.
    reference=json.loads(previous.read_text(encoding='utf-8'))
    for name in scores:
        ref_name=name if name in ['legacy_symmetric','risk_5_lower_95'] else name.split('__')[1] if name.startswith('recent_uniform__') else None
        if ref_name:
            for key,value in reference['pooled_validation'][ref_name].items():
                if isinstance(value,(int,float)) and value is not None:
                    if not np.isclose(scores[name][key],value,rtol=0,atol=1e-9):raise ValueError('Recent fixed-point comparison changed')
                elif scores[name][key]!=value:raise ValueError('Recent fixed-point comparison changed')
    winner,diagnostic,matched,gates=choose(scores,p);artifacts={};models={};valid=[n for n in base if n['year']==2024]
    for name in dict.fromkeys([winner,diagnostic,matched]):
        spec=next(s for s in p['candidates'] if s['name']==name);policy_name=spec.get('policy_name')
        if policy_name:
            policy=next(s for s in p['policies'] if s['name']==policy_name)
            head=learner.fit_error(*final_residuals[(spec['window'],policy_name)],spec,policy,p,2023)
            cal=calibrate_shape(learner.pool(parent['point_model'],head,valid,p,2024),p)
        else:head={'spec':spec};cal=calibration_for(pool_for(parent['point_model'],head,valid,p,2024),head,p)
        models[name]={}
        # Deliberately no point model file is written or fitted here.
        for label,value in [('head',head),('calibration',cal)]:
            file=name+'-'+label+'.json';write_json(directory/file,value);models[name][label]=file;artifacts[file]=sha(directory/file)
    extra_counts=counts(extra)
    full2021=json.loads(archive_reference.read_text(encoding='utf-8'))['archive_counts']['2021']
    result={'protocol':p,'protocol_sha256':sha(path),'source_sha256':sources,'parent_hashes':parent['hashes'],'previous_selection_sha256':sha(previous),
      'winner':winner,'diagnostic':diagnostic,'matched_uniform':matched,'gates':gates,'pooled_validation':scores,'folds':folds,'models':models,
      'artifact_sha256':artifacts,'input_provenance':provenance,'observations_loaded_years':[2021,2022,2023,2024],
      'selection_saved_at_utc':datetime.now(UTC).isoformat(),'compatible_extra_counts':extra_counts,
      'archive_reference_sha256':sha(archive_reference),'excluded_early2021_forecast_nights':full2021['forecast_nights']-extra_counts['forecast_nights'],
      'excluded_early2021_cold_nights':full2021['cold_nights']-extra_counts['cold_nights'],'point_unchanged':True}
    write_json(lock,result);return result


def evaluate(path,data_dir,report_dir):
    s=selection(path,data_dir);p=s['protocol'];parent=parent_assets(data_dir);directory=data_dir.parent/'models/research-fixed-point-errors'
    models={name:{k:json.loads((directory/file).read_text(encoding='utf-8')) for k,file in paths.items()} for name,paths in s['models'].items()}
    def snapshot(summary,file,key):
        meta=json.loads((report_dir/summary).read_text(encoding='utf-8'));source=data_dir/file
        if sha(source)!=meta[key]:raise ValueError('Frozen reference snapshot changed')
        return {f['date']:f for f in json.loads(source.read_text(encoding='utf-8'))},sha(source)
    # All choices/heads/calibration frozen before opening control years.
    old,ohash=snapshot('MINIMUM_STUDY_SUMMARY.json','processed/minimum-study/night-forecasts.json','forecast_sha256')
    masks,mhash=snapshot('SCENARIO_STUDY_SUMMARY.json','processed/scenario-study/night-forecasts.json','forecast_artifact_sha256')
    prior,phash=snapshot('COLD_INTERVAL_SUMMARY.json','processed/cold-interval-study/night-forecasts.json','forecast_sha256')
    from .frost_study import predict_cold
    frames=[];seasons={}
    for year in p['evaluation_years']:
        if year==2025:rows,provenance=load_rows(data_dir,[year])
        else:
            fresh=data_dir/'scenario-2026';rows,provenance=load_rows(fresh,[year],json.loads((fresh/'source-config.json').read_text(encoding='utf-8')))
        samples=nights(rows);events=predict_cold(parent['detector'],rows,.25)
        if len(samples)!=len(events):raise ValueError('Detector alignment mismatch')
        rr={name:[] for name in ['gfs','legacy_huber','previous_cold_range','selected','error_only_candidate','matched_uniform']};detector=Counter();unseen=Counter()
        for n,event in zip(samples,events):
            ref=old[n['date']]
            if event['date']!=n['date'] or event['cold_mask']!=masks[n['date']]['previous_detector_cold_mask'] or event['frost']!=ref['previous_detector']['frost']:raise ValueError('Detector mask/timing changed')
            predictions={}
            for name,model in models.items():
                f=learner.forecast(parent['point_model'],model['head'],model['calibration'],n,p) if model['head']['spec'].get('policy_name') else forecast_for(parent['point_model'],model['head'],model['calibration'],n,p)
                if f['point_c']!=ref['selected']['point_c']:raise ValueError('Frozen point forecast changed')
                predictions[name]=f
            forecasts={'gfs':ref['gfs'],'legacy_huber':ref['selected'],'previous_cold_range':prior[n['date']]['selected'],
              'selected':predictions[s['winner']],'error_only_candidate':predictions[s['diagnostic']],'matched_uniform':predictions[s['matched_uniform']]}
            actual=target_minimum(n)
            if actual is not None:
                cold,detected=actual<0,any(event['cold_mask']);detector['hits' if cold and detected else 'misses' if cold else 'false_alarms' if detected else 'correct_negatives']+=1
            for name,f in forecasts.items():rr[name].append({'date':n['date'],'forecast':f,'actual_minimum_c':actual});unseen[name]+=bool(f.get('unseen_regime',False))
            frames.append({**ref,'forecasts':forecasts,'cold_mask':event['cold_mask'],'detector_unchanged':True,'point_unchanged':True})
        seasons[str(year)]={'scores':{name:summarize(records) for name,records in rr.items()},'detector':dict(detector),'point_values_verified':len(samples),
                           'detector_masks_verified':len(samples),'unseen_regime_nights':dict(unseen),'input_provenance':provenance}
    if selection(path,data_dir)!=s:raise ValueError('Frozen selection changed during controls')
    file=data_dir/'processed/fixed-point-error-study/night-forecasts.json';write_json(file,frames)
    result={'selection':s,'selection_sha256':sha(directory/'selection-before-controls.json'),'seasons':seasons,'forecast_sha256':sha(file),
      'legacy_forecast_sha256':ohash,'legacy_mask_sha256':mhash,'previous_range_sha256':phash,'generated_at_utc':datetime.now(UTC).isoformat(),'deployment':p['deployment']}
    write_json(report_dir/'FIXED_POINT_ERROR_SUMMARY.json',result);return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--select-only',action='store_true');args=parser.parse_args()
    r=selection(ROOT/'config/fixed_point_error.json',ROOT/'data') if args.select_only else evaluate(ROOT/'config/fixed_point_error.json',ROOT/'data',ROOT/'docs')
    s=r if args.select_only else r['selection']
    print(json.dumps({'winner':s['winner'],'diagnostic':s['diagnostic'],'matched_uniform':s['matched_uniform'],'validation':s['pooled_validation'],'seasons':r.get('seasons')},ensure_ascii=False))
