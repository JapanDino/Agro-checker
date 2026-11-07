"""Earlier seasons as an isolated training-data ablation, selected on 2024 only."""
from datetime import datetime, timezone
import numpy as np

from .farm_context_data import ROOT, read, dump, digest
from .early_season_data import D, ARCHIVE, HOLDOUT, archive_protocol
from .conservative_decline import matrix, gate, score, key, NAMES
from .field_decline_experiment import chronological_folds, confusion, events
from .field_index_experiment import date_weights, predict as point_predict

SOURCE = ROOT/'data/farm-research/crop-season-model/data-2024.json'
LEGACY = ROOT/'data/farm-research/field-index-model/frozen-model.json'
PRIOR = ROOT/'data/farm-research/conservative-decline'


def protocol():
    path = D/'experiment-protocol.json'
    if path.exists():
        return read(path)
    previous = read(PRIOR/'protocol.json')
    selected = read(PRIOR/'frozen-selection.json')['selected']
    specs = [next(c for c in previous['candidates'] if c['id']==s['candidate']) for s in selected]
    p = dict(schema=1, created_at_utc=datetime.now(timezone.utc).isoformat(),
        archive_protocol_sha256=digest(ARCHIVE/'protocol.json'), source_2024_sha256=digest(SOURCE),
        previous_freeze_sha256=digest(PRIOR/'frozen-selection.json'), legacy_sha256=digest(LEGACY),
        hypothesis='Do additional earlier seasons improve future spectral-decline detection without increasing false alarms?',
        algorithms=specs, fixed_hyperparameters=previous['fixed_hyperparameters'],
        algorithm_policy='Keep each index algorithm chosen by previous 2024-only OOF. No new model/feature/hyperparameter search. Isolate extra training years and recency weights.',
        regimes=['2024_only', 'nearest_early_plus_2024', 'all_early_plus_2024', 'all_early_decay2_plus_2024'],
        nearest_rule='Latest earlier year with any eligible nonreserved pairs, chosen from availability, not event counts or quality',
        weights='Equal total weight per issue date; normalize mean row weight to1. decay2 multiplies by2**((year-2024)/2); all others no age weighting.',
        folds=previous['folds'], gates=previous['gates'], thresholds=previous['probability_thresholds'],
        fitting='Only2019-2024; no reserved IDs. Full earlier years, plus2024 targets strictly before validation month. Final all eligible2024 plus same earlier set.',
        validation='Identical274 monthly2024 OOF pairs; June-October; crossing windows purged; do not select on earlier year metrics',
        selection='For each index/regime:0FP pairs/0FP dates,>=2TP on>=2 dates. Maximize date-balanced recall, TP dates, TP count; simpler gate then higher threshold. Primary best eligible regime by same ranking, ties prefer listed regime order.',
        ablation='Freeze ALL four regime operating points/models before2026. Evaluate each as predeclared ablation, primary cannot be replaced using test outcomes.',
        acceptance='Primary must improveTP and not growFP pairs orFP dates vs BOTH persistence and original Ridge, on all2026 and spatial2026; also report reserved2024. No production promotion without local validation.',
        target='Unchanged core median drop>=0.10 through6-8days; same processing baseline within each pair; not disease/damage labels',
        holdout_ids=HOLDOUT, source_policy='Original2024/2026 pairs remain fixed; oldC1 PB05.00 vs later PB handled as transfer limitation, not guessed offsets; original processing policy unchanged.',
        features='Same past-only seven/forty inputs; history never uses future observations; culture and operations unknown',
        test='Existing2026 after freeze only; already examined previously, not pristine blind. No2025 in original evaluation archive; not fabricated.',
        historical_live_backtest=False, operational=False,
        caveats=['Repeated2024 selection across prior studies is optimistic.',
            'Zero empirical false alarms is not a future guarantee.',
            'Fragments/dates/windows correlated; reserved fragments are not external farms.'])
    dump(path,p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents:
                continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.cbm','.csv','.joblib']:
                continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def validate_fit(rows):
    if not rows or any(not 2019<=int(r['issue'][:4])<=2024 or r['target'][:4]!=r['issue'][:4] for r in rows):
        raise ValueError('Fit accepts same-year2019-2024 pairs only')
    if any(r['fid'] in HOLDOUT for r in rows):
        raise ValueError('Spatial reserve in fit')
    if len({key(r) for r in rows})!=len(rows):
        raise ValueError('Duplicate training rows')
    if any(not r['previous']<r['issue']<r['target'] for r in rows):
        raise ValueError('Noncausal observation order')


def selection2024(rows):
    validate_fit(rows)
    if any(r['issue'][:4]!='2024' for r in rows):
        raise ValueError('Selection requires2024 only')


def weights(rows, regime):
    w=date_weights(rows)
    if regime=='all_early_decay2_plus_2024':
        w*=np.array([2**((int(r['issue'][:4])-2024)/2) for r in rows])
    return w*len(rows)/w.sum()


def training_rows(current, earlier, regime):
    selection2024(current)
    validate_fit(earlier)
    if any(r['issue'][:4]>='2024' for r in earlier):
        raise ValueError('Earlier archive must precede2024')
    latest=max(r['issue'][:4] for r in earlier)
    if regime=='2024_only':
        result=list(current)
    elif regime=='nearest_early_plus_2024':
        result=[r for r in earlier if r['issue'][:4]==latest]+current
    elif regime in ['all_early_plus_2024','all_early_decay2_plus_2024']:
        result=earlier+current
    else:
        raise ValueError('Unknown archive regime')
    validate_fit(result)
    return result


def train(rows, j, spec, regime, p):
    validate_fit(rows)
    x=matrix(rows,spec);y=events(rows,j).astype(int);w=weights(rows,regime)
    if len(np.unique(y))<2:
        return dict(kind='constant',value=float((w@y+.5)/(w.sum()+1)))
    hp=p['fixed_hyperparameters']
    if spec['family']=='logit':
        from sklearn.linear_model import LogisticRegression
        mean=np.average(x,axis=0,weights=w)
        scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-9]=1
        m=LogisticRegression(C=spec['c'],solver='lbfgs',max_iter=2000,random_state=hp['seed'])
        m.fit((x-mean)/scale,y,sample_weight=w)
        return dict(kind='logit',model=m,mean=mean,scale=scale)
    if spec['family']=='extra':
        from sklearn.ensemble import ExtraTreesClassifier
        m=ExtraTreesClassifier(n_estimators=hp['extra_trees'],max_depth=hp['extra_max_depth'],
            min_samples_leaf=spec['leaf'],max_features=1.,random_state=hp['seed'],n_jobs=hp['threads'])
        m.fit(x,y,sample_weight=w)
        return dict(kind='classifier',model=m)
    raise ValueError('Unregistered algorithm')


def rank(m):
    return -(m['date_balanced_recall'] or 0),-m['tp_dates'],-m['tp']


def select(rows,records,p):
    selection2024(rows)
    table=[];choices=[];primary=[]
    for j,index in enumerate(NAMES):
        by_regime=[]
        for record in records:
            values=np.array(record['scores'][j])
            if values.shape!=(len(rows),) or not np.isfinite(values).all():
                raise ValueError('Scores must cover every selection row')
            eligible=[]
            for g_order,g in enumerate(p['gates']):
                for threshold in p['thresholds']:
                    m=confusion(rows,events(rows,j),(values>=threshold)&gate(rows,j,g))
                    ok=m['fp']==0 and m['fp_dates']==0 and m['tp']>=2 and m['tp_dates']>=2
                    entry=dict(index=index,regime=record['regime'],gate=g,threshold=threshold,metrics=m,enabled=ok)
                    table.append(entry)
                    if ok:eligible.append(((*rank(m),g_order,-threshold),entry))
            if eligible:
                by_regime.append(min(eligible,key=lambda v:v[0])[1])
            else:
                by_regime.append(dict(index=index,regime=record['regime'],enabled=False,
                    reason='No2024 operating point passes0FP and>=2TP on>=2 dates; retain original detector'))
        choices.append(by_regime)
        options=[(rank(c['metrics'])+(order,),c) for order,c in enumerate(by_regime) if c['enabled']]
        primary.append(min(options,key=lambda v:v[0])[1] if options else
            dict(index=index,regime=None,enabled=False,reason='No regime passes2024 guards'))
    return primary,choices,table


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():
        raise ValueError('Cannot refit a frozen experiment')
    p=protocol()
    if digest(SOURCE)!=p['source_2024_sha256'] or digest(LEGACY)!=p['legacy_sha256']:
        raise ValueError('Original sources changed')
    early=[r for r in read(D/'data-early.json') if r['fid'] not in HOLDOUT]
    current=[r for r in read(SOURCE) if r['fid'] not in HOLDOUT]
    validate_fit(early);selection2024(current)
    if max(r['issue'][:4] for r in early)>='2024':
        raise ValueError('Earlier source must precede2024')
    folds=list(chronological_folds(current,p['folds']))
    validation=[r for _,_,vv in folds for r in vv]
    records=[];fold_manifest=[]
    for regime in p['regimes']:
        scores=[[],[]]
        for start,tt,vv in folds:
            training=training_rows(tt,early,regime)
            if max(r['target'] for r in training)>=start:
                raise ValueError('Future validation targets leaked into training')
            fold_manifest.append(dict(regime=regime,start=start,fit_pairs=len(training),
                fit_max_target=max(r['target'] for r in training),validation_min_issue=min(r['issue'] for r in vv)))
            for j,spec in enumerate(p['algorithms']):
                m=train(training,j,spec,regime,p)
                scores[j].extend(score(m,vv,j,spec).tolist())
        records.append(dict(regime=regime,scores=scores))
        print('2024 OOF ready',regime,flush=True)
    primary,choices,table=select(validation,records,p)
    dump(D/'selection-table.json',table)
    dump(D/'oof-predictions.json',dict(rows=validation,records=records))
    import joblib
    models={};fitting={}
    for regime in p['regimes']:
        training=training_rows(current,early,regime)
        fitting[regime]=dict(keys=[list(key(r)) for r in training],pairs=len(training),
            dates=len({r['issue'] for r in training}),by_year=dict((str(y),sum(r['issue'][:4]==str(y) for r in training)) for y in range(2019,2025)),
            events=[int(events(training,j).sum()) for j in range(2)])
        for j in range(2):
            c=next(c for c in choices[j] if c['regime']==regime)
            if not c['enabled']:continue
            spec=p['algorithms'][j]
            m=train(training,j,spec,regime,p)
            path=D/f'{NAMES[j]}-{regime}.joblib';joblib.dump(m,path)
            models[f'{j}-{regime}']=dict(path=path.relative_to(ROOT).as_posix(),sha256=digest(path))
    f=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),primary=primary,choices=choices,models=models,
        fitting=fitting,folds=fold_manifest,protocol_sha256=digest(D/'experiment-protocol.json'),
        early_sha256=digest(D/'data-early.json'),source_2024_sha256=digest(SOURCE),legacy_sha256=digest(LEGACY),
        selection_sha256=digest(D/'selection-table.json'),oof_sha256=digest(D/'oof-predictions.json'),
        code_sha256=digest(__file__),data_code_sha256=digest(ROOT/'agro_checker/early_season_data.py'),
        production_promoted=False)
    dump(D/'frozen-selection.json',f)
    print('Primary frozen:',primary,flush=True)
    return f


def infer(rows,p,f,regime=None):
    import joblib
    point=point_predict(read(LEGACY)['selected_model'],rows)
    old=point-np.array([r['x'][:2] for r in rows])<=-.10
    alarms=old.copy();scores=[[None,None] for _ in rows]
    for j in range(2):
        c=f['primary'][j] if regime is None else next(c for c in f['choices'][j] if c['regime']==regime)
        if not c['enabled']:continue
        entry=f['models'][f"{j}-{c['regime']}"];path=ROOT/entry['path']
        if digest(path)!=entry['sha256']:raise ValueError('Frozen model changed')
        m=joblib.load(path)
        values=score(m,rows,j,p['algorithms'][j])
        alarms[:,j]=(values>=c['threshold'])&gate(rows,j,c['gate'])
        for i,v in enumerate(values):scores[i][j]=float(v)
    return alarms,scores,point,old


def evaluate():
    p=read(D/'experiment-protocol.json');f=read(D/'frozen-selection.json')
    if (D/'report.json').exists():raise ValueError('Already evaluated; no test-driven retries')
    frozen_sha=digest(D/'frozen-selection.json')
    for path,value in [(D/'experiment-protocol.json',f['protocol_sha256']),(SOURCE,f['source_2024_sha256']),
        (LEGACY,f['legacy_sha256']),(D/'data-early.json',f['early_sha256']),
        (ROOT/'agro_checker/early_season_decline.py',f['code_sha256'])]:
        if digest(path)!=value:raise ValueError('Changed frozen input or code')
    # Test first read only AFTER every regime and primary operating point are frozen.
    test=read(ROOT/'data/farm-research/crop-season-model/data-2026.json')
    splits=dict(spatial2024=[r for r in read(SOURCE) if r['fid'] in HOLDOUT],all2026=test,
        temporal2026=[r for r in test if r['fid'] not in HOLDOUT],spatial2026=[r for r in test if r['fid'] in HOLDOUT])
    results={};predictions=[]
    for split,rr in splits.items():
        results[split]={}
        for version in ['primary']+p['regimes']:
            alarms,scores,point,old=infer(rr,p,f,None if version=='primary' else version)
            results[split][version]={}
            for j,index in enumerate(NAMES):
                y=events(rr,j)
                mm={label:confusion(rr,y,aa) for label,aa in [('persistence',np.zeros(len(rr),bool)),('legacy',old[:,j]),('new',alarms[:,j])]}
                mm['passes_vs_legacy']=mm['new']['tp']>mm['legacy']['tp'] and mm['new']['fp']<=mm['legacy']['fp'] and mm['new']['fp_dates']<=mm['legacy']['fp_dates']
                mm['passes_vs_persistence']=mm['new']['tp']>0 and mm['new']['fp']==0
                results[split][version][index]=mm
            for i,r in enumerate(rr):
                predictions.append(dict(split=split,version=version,fid=r['fid'],issue=r['issue'],target=r['target'],
                    actual=r['y'],persistence=r['x'][:2],point=point[i].tolist(),legacy_alarm=old[i].tolist(),
                    new_alarm=alarms[i].tolist(),scores=scores[i]))
    protected=read(D/'preservation-before.json')
    changed=[name for name,h in protected.items() if not (ROOT/name).is_file() or digest(ROOT/name)!=h]
    if changed or digest(D/'frozen-selection.json')!=frozen_sha:raise ValueError('Original files changed: '+repr(changed[:5]))
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),frozen_sha256=frozen_sha,primary=f['primary'],
        results=results,test_constraints={index:f['primary'][j]['enabled'] and all(
            results[s]['primary'][index]['passes_vs_legacy'] and results[s]['primary'][index]['passes_vs_persistence'] for s in ['all2026','spatial2026']) for j,index in enumerate(NAMES)},
        protected_files=len(protected),changed_files=changed,production_promoted=False,
        no2025='absent from existing evaluation archive',evaluation_code_sha256=digest(__file__))
    dump(D/'report.json',out)
    print('Primary2026:',results['all2026']['primary'],flush=True)
    return out
