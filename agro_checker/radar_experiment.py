"""2024-only radar feature ablation; no production/prospective modifications."""
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import date,timedelta
from functools import lru_cache
import numpy as np
import joblib

from . import radar_data as data
from scripts import experiment_consensus_ndmi_guard as old

base=old.base
D=data.ROOT/'data/farm-research/radar-priority-2024'
SAR_FEATURES=['s1_vv_db','s1_vh_db','s1_vh_minus_vv_db','s1_dvv_per12d','s1_dvh_per12d',
    's1_dratio_per12d','s1_age_days','s1_pair_gap_days','s1_common_core_coverage',
    's1_boundary_dvv_span','s1_boundary_dvh_span','s1_missing','s1_boundary_missing','s1_descending']
METHODS=['baseline','fixed_candidate','optical_logit','optical_extra','radar_logit','radar_extra','radar_consensus']
NEW=['radar_logit','radar_extra','radar_consensus']


def protocol():
    if (D/'protocol.json').exists():return data.read(D/'protocol.json')
    paths=[base.D/'protocol.json',base.D/'pool-2024.json',base.D/'meta-oof.json',old.D/'protocol.json',old.D/'report.json',
           data.D/'protocol.json',data.D/'catalogues.json']
    p=dict(created_at_utc=data.stamp(),selection_years=[2024],prohibited_tuning_years=[2025,2026],
        input_hashes={p.relative_to(data.ROOT).as_posix():data.sha(p) for p in paths},code_sha256=data.sha(__file__),
        optical_parameters=base.read(base.D/'protocol.json'),budget_k=3,
        methods=METHODS,selection_candidates=NEW,radar_features=SAR_FEATURES,
        maximum_radar_age_days=18,previous_gap_days=[4,36],normalisation_days=12,
        feature_policy='Latest causal eligible same-platform/orbit/direction pair by UTC issue-day end. Actual common finite positive VV/VH cells>=10/.8. Latest<=18days old; previous4-36days. No interpolation, no pooling tracks, no future target required. Missing features=0 plus explicit indicators. All11 boundary sensitivity spans explicit, missing flagged.',
        input_availability='Retrospective acquired-by-issue-EOD data; actual historical RTC publication/receipt at that issue NOT proven. This is not a historical live forecast.',
        cohorts='Same699 original input candidates,368known/331unknown. Training original completed2024 pool excludingR23-R27. Known original targets unchanged; radar never fills unknown optical outcomes.',
        fitting='Same fixed LogisticC=.1 and ExtraTrees256/depth3/minleaf15/seed20241021. Optical-only controls must reproduce old OOF. For Jun-Oct fit on known nonreserved2024 target<monthstart, score every candidate in month. No future/current-month labels in fit or preprocessing.',
        selection='Development only: same min(3,N) slots. Retain EVERY known positive identity of baseline AND fixed candidate, knownFP<=both, worst-case paired negative growth<=0 vsboth and strict paired lower gain>0 vsboth. Choose largest minimum paired lower gain, then FP, declared order. Default fixed_candidate. No production/prospective promotion.',
        forward='Month-start selection using only prior OOF issue pools with completed target<monthstart; unknown candidates retained. Require20known/5dates else fixed_candidate. This inherited repeatedly inspected2024 is development, not independent validation.',
        quality_gate='ESA per-product RFI clearance UNKNOWN. Any statistical win remains exploratory; operational admission prohibited until independent source-QC and prospective validation.',
        source_models_changed=False,prospective_study_changed=False,detector_changed=False,point_forecast_changed=False,human_visits_created=0)
    data.save_once(D/'protocol.json',p);return p


def checked(p):
    if data.sha(__file__)!=p['code_sha256']:raise ValueError('Frozen radar experiment code changed')
    for name,value in p['input_hashes'].items():
        if data.sha(data.ROOT/name)!=value:raise ValueError('Changed frozen study input: '+name)
    data.check(data.protocol())


@lru_cache(maxsize=1)
def sources():
    manifest=data.read(data.D/'satellite.json')
    if manifest['protocol_sha256']!=data.sha(data.D/'protocol.json') or manifest['catalogue_sha256']!=data.sha(data.D/'catalogues.json'):
        raise ValueError('Changed source manifest')
    if manifest['counts'].get('extraction_error',0):raise ValueError('Incomplete radar extraction; no silently selected successful-only archive')
    return {v['id']:v for v in manifest['scenes'] if v['status']=='ready'}


@lru_cache(maxsize=24)
def pixels(sid):
    row=sources()[sid];path=data.D/'scenes'/sid/'radar.npz'
    if data.sha(path)!=row['crop_sha256']:raise ValueError('Changed radar crop')
    with np.load(path,allow_pickle=False) as a:
        np.testing.assert_array_equal(a['positions'],data.masks()[1])
        return a['vv'],a['vh']


@lru_cache(maxsize=16384)
def common_pair(fid,current,previous):
    a,b=pixels(current),pixels(previous)
    valid=np.logical_and.reduce([np.isfinite(v)&(v>0) for v in (*a,*b)])
    values={}
    for scenario,ix in data.masks()[0][fid].items():
        ok=valid[ix];n=int(ok.sum());coverage=n/len(ix) if len(ix) else 0
        if n<10 or coverage<.8:continue
        x=[float(10*np.log10(np.median(v[ix][ok]))) for v in a]
        y=[float(10*np.log10(np.median(v[ix][ok]))) for v in b]
        values[scenario]=dict(now=x+[x[1]-x[0]],delta=[x[0]-y[0],x[1]-y[1],x[1]-x[0]-y[1]+y[0]],coverage=coverage)
    if 'core' not in values:return None
    complete=set(values)==set(base.SCENARIOS)
    return dict(core=values['core'],all_boundaries_supported=complete,
        spans=[max(v['delta'][j] for v in values.values())-min(v['delta'][j] for v in values.values()) for j in (0,1)] if complete else [0.,0.])


def features(fid,issue,p,available=None):
    """Only issue inputs. Outcome labels are not an argument and are never read."""
    if issue[:4]!='2024':raise ValueError('2024 only')
    end=data.instant((date.fromisoformat(issue)+timedelta(days=1)).isoformat()+'T00:00:00Z')
    candidates=[]
    for scene in (available or sources()).values():
        at=data.instant(scene['at']);age=(end-at).total_seconds()/86400
        if 0<age<=p['maximum_radar_age_days'] and 'core' in scene['fields'].get(fid,{}).get('values',{}):candidates.append(scene)
    candidates.sort(key=lambda s:(s['at'],s['id']),reverse=True)
    for current in candidates:
        at=data.instant(current['at'])
        past=[s for s in (available or sources()).values() if s['track']==current['track']
            and p['previous_gap_days'][0]<=(at-data.instant(s['at'])).total_seconds()/86400<=p['previous_gap_days'][1]
            and 'core' in s['fields'].get(fid,{}).get('values',{})]
        for previous in sorted(past,key=lambda s:(s['at'],s['id']),reverse=True):
            comparison=common_pair(fid,current['id'],previous['id'])
            if comparison is None:continue
            gap=(at-data.instant(previous['at'])).total_seconds()/86400;scale=p['normalisation_days']/gap
            core=comparison['core'];age=(end-at).total_seconds()/86400
            values=core['now']+[v*scale for v in core['delta']]+[age,gap,core['coverage']]+[v*scale for v in comparison['spans']]
            values += [0.,float(not comparison['all_boundaries_supported']),float(current['track'].endswith(':descending'))]
            if len(values)!=len(SAR_FEATURES) or not np.isfinite(values).all():raise ValueError('Invalid radar feature vector')
            return values,dict(current=current['id'],previous=previous['id'],track=current['track'],
                current_at=current['at'],previous_at=previous['at'],cutoff_exclusive_utc=end.isoformat(),
                common_core_coverage=core['coverage'],rfi_clearance='unknown',all_boundaries_supported=comparison['all_boundaries_supported'])
    missing=[0.]*len(SAR_FEATURES);missing[SAR_FEATURES.index('s1_missing')]=1.;missing[SAR_FEATURES.index('s1_boundary_missing')]=1.
    return missing,dict(current=None,previous=None,rfi_clearance='unknown',reason='No causally eligible same-track common-core pair')


def attach(rows,p):
    out=[];audit=[]
    for row in rows:
        values,meta=features(row['fid'],row['issue'],p)
        out.append(dict(row,triage_features=row['triage_features']+values))
        audit.append(dict(fid=row['fid'],issue=row['issue'],features=values,**meta))
    return out,audit


def choices(rows,records,p):
    rule=old.read(old.D/'protocol.json');v=old.read(old.D/'report.json')['selected_repair']
    old_scores={k:np.asarray(records[k]) for k in ('recent_drop','logit','extra')}
    augmented={k:np.asarray(records[k]) for k in ('recent_drop',)}
    augmented.update(logit=np.asarray(records['radar_logit']),extra=np.asarray(records['radar_extra']))
    return dict(baseline=base.topk(rows,old_scores['recent_drop'],p),
        fixed_candidate=old.choose(rows,old_scores,v,rule),
        optical_logit=base.topk(rows,records['logit'],p),optical_extra=base.topk(rows,records['extra'],p),
        radar_logit=base.topk(rows,records['radar_logit'],p),radar_extra=base.topk(rows,records['radar_extra'],p),
        radar_consensus=old.choose(rows,augmented,v,rule))


def assess(rows,cc,p):
    table=[]
    for name,mask in cc.items():
        m=base.rank_metrics(rows,mask,p);paired={n:base.paired_metrics(rows,cc[n],mask) for n in ('baseline','fixed_candidate')}
        checks={n:dict(retain_every_known_event=v['lost_known_baseline_tp']==0,
            no_known_false_growth=m['fp']<=base.rank_metrics(rows,cc[n],p)['fp'],
            no_possible_false_growth=v['negative_growth_upper']<=0,strict_missing_robust_gain=v['difference_lower'] is not None and v['difference_lower']>1e-12)
                for n,v in paired.items()}
        passed=name in NEW and all(all(v.values()) for v in checks.values())
        table.append(dict(method=name,metrics=m,paired=paired,checks=checks,development_gate_passed=passed))
    return table


def pick(table):
    eligible=[v for v in table if v['development_gate_passed']]
    if not eligible:return 'fixed_candidate'
    return min(eligible,key=lambda v:(-min(v['paired'][n]['difference_lower'] for n in ('baseline','fixed_candidate')),
        v['metrics']['fp'],NEW.index(v['method'])))['method']


def run():
    p=protocol();checked(p)
    if (D/'report.json').exists():raise ValueError('Frozen 2024 development result; no outcome-driven reruns')
    rows,original=old.guard.dataset();base.only2024(rows)
    pool=[r for r in base.read(base.D/'pool-2024.json') if r['fid'] not in base.HOLDOUT];base.only2024(pool)
    extended_pool,pool_audit=attach(pool,p);extended_rows,row_audit=attach(rows,p)
    records={k:np.asarray(v) for k,v in original.items()};records.update(radar_logit=np.zeros(len(rows)),radar_extra=np.zeros(len(rows)))
    folds=[];optical=p['optical_parameters']
    for start in optical['folds']:
        d=date.fromisoformat(start);end=date(d.year+d.month//12,d.month%12+1,1).isoformat()
        tt=[r for r in extended_pool if r['y'] is not None and r['target']<start]
        ix=[i for i,r in enumerate(rows) if start<=r['issue']<end];vv=[extended_rows[i] for i in ix]
        audit=dict(start=start,fit_pairs=len(tt),fit_max_target=max(r['target'] for r in tt),validation_pool=len(ix),fallbacks={})
        for method in ('logit','extra'):
            # Fresh optical-only control must match the unchanged historical OOF.
            control=base.fit_model([r for r in pool if r['y'] is not None and r['target']<start],method,optical)
            np.testing.assert_allclose(base.score(control,[rows[i] for i in ix]),records[method][ix],rtol=1e-9,atol=1e-10)
            model=base.fit_model(tt,method,optical);records['radar_'+method][ix]=base.score(model,vv)
            audit['fallbacks'][method]=model.get('fallback')
        folds.append(audit)
    cc=choices(rows,records,{**p,'fixed_parameters':optical['fixed_parameters']})
    params={**p,'fixed_parameters':optical['fixed_parameters']};table=assess(rows,cc,params)
    forward=np.zeros(len(rows),bool);selection=[]
    for fold in folds:
        start=fold['start'];d=date.fromisoformat(start);end=date(d.year+d.month//12,d.month%12+1,1).isoformat()
        ti=[i for i,r in enumerate(rows) if r['issue']<start];vi=[i for i,r in enumerate(rows) if start<=r['issue']<end]
        prior=[dict(rows[i],y=rows[i]['y'] if rows[i]['target'] and rows[i]['target']<start else None) for i in ti]
        known=sum(r['y'] is not None for r in prior);method='fixed_candidate'
        if known>=20 and len({r['issue'] for r in prior})>=5:
            prior_scores={n:np.asarray(v)[ti] for n,v in records.items()};prior_choices=choices(prior,prior_scores,params)
            method=pick(assess(prior,prior_choices,params))
        forward[vi]=cc[method][vi]
        selection.append(dict(start=start,selected=method,prior_known=known,prior_pool=len(ti),validation_pool=len(vi),
            max_known_target=max((r['target'] for r in prior if r['y'] is not None),default=None)))
    final_models={}
    for method in ('logit','extra'):
        training=[r for r in extended_pool if r['y'] is not None];model=base.fit_model(training,method,optical)
        path=D/('experimental-'+method+'.joblib');joblib.dump(model,path)
        final_models[method]=dict(path=path.relative_to(data.ROOT).as_posix(),sha256=data.sha(path),fit_pairs=len(training),
            fit_max_target=max(r['target'] for r in training),features=base.FEATURES+SAR_FEATURES,production=False)
    counts=Counter(v['reason'] if v['current'] is None else 'available' for v in row_audit)
    data.save_once(D/'feature-audit.json',dict(pool=pool_audit,validation=row_audit))
    data.save_once(D/'oof.json',dict(rows=rows,records={k:v.tolist() for k,v in records.items()},folds=folds))
    report=dict(created_at_utc=data.stamp(),protocol_sha256=data.sha(D/'protocol.json'),radar_manifest_sha256=data.sha(data.D/'satellite.json'),
        input_count=len(rows),known=sum(r['y'] is not None for r in rows),unknown=sum(r['y'] is None for r in rows),
        feature_counts=dict(counts),table=table,selected_development=pick(table),folds=folds,
        forward=dict(selection=selection,metrics=base.rank_metrics(rows,forward,params),
            paired={n:base.paired_metrics(rows,cc[n],forward) for n in ('baseline','fixed_candidate')}),
        experimental_models=final_models,selection_years=[2024],test_years_evaluated=[],
        optical_controls_reproduced=True,production_promoted=False,prospective_study_changed=False,
        source_models_changed=False,detector_changed=False,point_forecast_changed=False,human_visits_created=0,
        quality_limitations=['Per-product RFI clearance unknown','Historical RTC publication availability unproven',
            'Repeated 2024 development, not independent validation','Satellite change is not crop harm or useful inspection'])
    data.save_once(D/'report.json',report)
    for entry in table:print(entry['method'],{k:entry['metrics'][k] for k in ('tp','fp','unknown','slots')},entry['development_gate_passed'])
    return report
