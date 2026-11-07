"""Independent NDMI interval around the unchanged legacy point and detector.

Only 2024 labels may enter fitting, calibration and selection. No diagnosis labels.
"""
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from .farm_context_data import ROOT,read,dump,digest
from .field_index_experiment import predict,date_weights
from .full_season_intervals import only2024,causal_oof,meta_split,weighted_quantile

D=ROOT/'data/farm-research/ndmi-error-only'
SRC=ROOT/'data/farm-research/field-decline-model/data-2024.json'
OLD=ROOT/'data/farm-research/field-index-model/frozen-model.json'
HOLDOUT=['R23','R24','R25','R26','R27']
SPECS=[dict(id='legacy',kind='legacy')]
SPECS += [dict(id=f'event_q{int(q*100):02d}_pad{int(pad*1000):03d}',kind='event_envelope',q=q,pad=pad)
          for q in [.05,.10] for pad in [0,.025,.05]]
SPECS += [dict(id='event_date_min',kind='event_date_min'),
          dict(id='conditional_weight3',kind='conditional',weight=3),
          dict(id='scenario_lower',kind='scenario')]


def key(r):return r['fid'],r['issue'],r['target']


def protocol():
    path=D/'protocol.json'
    if path.exists():return read(path)
    D.mkdir(parents=True,exist_ok=True)
    p=dict(schema=1,created_at_utc=datetime.now(timezone.utc).isoformat(),
        source_2024_sha256=digest(SRC),legacy_sha256=digest(OLD),holdout_ids=HOLDOUT,
        point='Legacy temporal_ridge_100 and both indices untouched. NDVI interval untouched. Legacy point-derived spectral detector untouched.',
        residual_archive='2024 only. Before/at legacy fit_max_target: causal issue-date OOF of same point algorithm, target<issue. After fit_max_target: exact frozen legacy point, never trained on these targets. No in-sample point errors.',
        outer_starts=['2024-08-01','2024-09-01','2024-10-01'],
        outer_policy='Fixed legacy point, issue in month and target before next month. Residual-fit/calibration strictly before outer start.',
        meta_split='Last 8 known issue dates for calibration; residual-fit target < first calibration issue. OOF transfer before July is approximate, not exact conditional coverage.',
        candidates=SPECS,tail_hyperparameters=dict(iterations=120,depth=2,learning_rate=.05,l2_leaf_reg=5,random_seed=202410,thread_count=2),
        features='Current and previous features only: legacy x[:7], absolute slopes, 7-day momentum. No future event/target/residual/phase.',
        tails='Event-envelope lower residual quantile .05/.10 with fixed pad0/.025/.05; upper overall .95. Date-min lower is minimum event residual. Conditional uses .05/.95 with eventweight3. Scenario lower uses .10 fitted on actual declines only, upper overall .95 constant.',
        calibration='Expand lower by max(0, weighted95th overall deficit, weighted90th event deficit); for <3 event dates use event maximum. Expand upper by weighted95th overall deficit. Never narrow on calibration.',
        coverage_floor=.90,decline_floor=.90,width_limit=1.10,
        selection='Only 2024: require pooled and every supported outer fold overall>=.90 and decline>=.90, mean width<=110% legacy. Minimize date-balanced 90% interval score with eventweight3; tie follows candidate order. Legacy fallback if no new candidate passes.',
        original_comparator='Legacy radius was itself calibrated on 2024; its 2024 coverage is optimistic and not independent. Fixed radius supplies product width budget, not a blind validation claim.',
        test_constraints='NDMI decline coverage improves vs legacy on all2026 and spatial2026, overall>=.90 and width<=110% legacy. Report mean/tail widths, event counts and exact legacy point/alarm identity.',
        test='One frozen choice only; 2026 already studied, not pristine blind. No 2025 data or post-test fitting. No production promotion.',
        event='NDMI future-current<=-0.10, spectral proxy not moisture, crop damage, disease or inspection utility',
        archive='Clear-sky fixed original cohort only. Reprocessed acquisition-date archive is not proven historical live availability.')
    dump(path,p);return p


def residual_archive(rows,legacy):
    only2024(rows)
    oof,folds=causal_oof(rows)
    mapped={key(r):r for r in oof}
    out=[]
    cutoff=legacy['selected_model']['fit_max_target']
    for r in rows:
        if r['issue']>cutoff:
            center=predict(legacy['selected_model'],[r])[0]
            out.append(dict(r,residual=(np.array(r['y'])-center).tolist(),
                            residual_origin='frozen_point_out_of_fit',point_oof=center.tolist()))
        elif key(r) in mapped:
            out.append(dict(mapped[key(r)],residual_origin='causal_reference_oof'))
    return out,folds


def features(rows):
    x=np.array([r['x'][:7] for r in rows],float)
    return np.column_stack([x,abs(x[:,2:4]),7*x[:,2:4]])


def tail_fit(rows,spec,p,folder,radius):
    only2024(rows);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    residual=np.array([r['residual'][1] for r in rows]);w=date_weights(rows)
    event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rows])
    ev=[r for r,m in zip(rows,event) if m]
    model=dict(spec=spec,fit_pairs=len(rows),fit_dates=len({r['issue'] for r in rows}),
               fit_max_target=max(r['target'] for r in rows),event_pairs=int(event.sum()),event_dates=len({r['issue'] for r in ev}))
    kind=spec['kind']
    if kind=='legacy':model['constant']=[-radius,radius]
    elif kind in ['event_envelope','event_date_min']:
        source=residual[event] if ev else residual
        weights=date_weights(ev) if ev else w
        lower=float(source.min()) if kind=='event_date_min' else weighted_quantile(source,weights,spec['q'])-spec['pad']
        model['constant']=[min(lower,0),max(weighted_quantile(residual,w,.95),0)]
        model['no_event_fallback']=not bool(ev)
    else:
        from catboost import CatBoostRegressor
        model['models']=[]
        for j,alpha in enumerate([.05,.95]):
            if kind=='scenario' and j==1:
                model['models'].append(dict(constant=max(weighted_quantile(residual,w,.95),0)))
                continue
            mask=event if kind=='scenario' else np.ones(len(rows),dtype=bool)
            rr=[r for r,m in zip(rows,mask) if m]
            if not rr:
                model['models'].append(dict(constant=float(residual.min()) if j==0 else float(residual.max()),no_events=True))
                continue
            ww=date_weights(rr)
            if kind=='conditional':ww=ww*np.where(event[mask],spec['weight'],1)
            ww=ww*len(rr)/ww.sum()
            loss_alpha=.10 if kind=='scenario' and j==0 else alpha
            reg=CatBoostRegressor(**p['tail_hyperparameters'],loss_function=f'Quantile:alpha={loss_alpha}',verbose=False,allow_writing_files=False)
            reg.fit(features(rr),residual[mask],sample_weight=ww)
            path=folder/f'{spec["id"]}-{j}.cbm';reg.save_model(str(path))
            model['models'].append(dict(path=str(path.relative_to(ROOT)),sha256=digest(path)))
    return model


def raw(model,rows):
    if 'constant' in model:return np.tile(model['constant'],(len(rows),1))
    from catboost import CatBoostRegressor
    out=np.empty((len(rows),2))
    for j,entry in enumerate(model['models']):
        if 'constant' in entry:out[:,j]=entry['constant']
        else:
            path=ROOT/entry['path']
            if digest(path)!=entry['sha256']:raise ValueError('Changed NDMI tail weights')
            reg=CatBoostRegressor();reg.load_model(str(path));out[:,j]=reg.predict(features(rows))
    return np.column_stack([np.minimum(out.min(axis=1),0),np.maximum(out.max(axis=1),0)])


def calibrate(model,rows):
    only2024(rows)
    event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rows]);ev=[r for r,m in zip(rows,event) if m]
    support=dict(pairs=len(rows),dates=len({r['issue'] for r in rows}),event_pairs=int(event.sum()),event_dates=len({r['issue'] for r in ev}),
                 first_issue=min(r['issue'] for r in rows),max_target=max(r['target'] for r in rows))
    if model['spec']['kind']=='legacy':return dict(adjustment=[0,0],support=support)
    tails=raw(model,rows);y=np.array([r['residual'][1] for r in rows]);w=date_weights(rows)
    low=max(0,weighted_quantile(tails[:,0]-y,w,.95));high=max(0,weighted_quantile(y-tails[:,1],w,.95))
    if ev:
        margin=tails[event,0]-y[event]
        low=max(low,weighted_quantile(margin,date_weights(ev),.90) if support['event_dates']>=3 else float(margin.max()))
    return dict(adjustment=[low,high],support=support,event_maximum_fallback=0<support['event_dates']<3,no_event_support=not bool(ev))


def bounds(model,calib,rows,point):
    tails=raw(model,rows);a,b=calib['adjustment']
    return np.clip(point[:,1]+tails[:,0]-a,-1,1),np.clip(point[:,1]+tails[:,1]+b,-1,1)


def metrics(rows,low,high):
    if not rows:return dict(pairs=0,coverage=None,decline_coverage=None,mean_width=None,weighted_score=None)
    y=np.array([r['y'][1] for r in rows]);event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rows]);w=date_weights(rows)
    cov=(y>=low)&(y<=high);width=high-low
    score=width+20*np.maximum(low-y,0)+20*np.maximum(y-high,0)
    result=dict(pairs=len(rows),dates=len({r['issue'] for r in rows}),coverage=float(np.average(cov,weights=w)),
        mean_width=float(np.average(width,weights=w)),interval_score=float(np.average(score,weights=w)),
        weighted_score=float(np.average(score*np.where(event,3,1),weights=w)),
        lower_misses=int((y<low).sum()),upper_misses=int((y>high).sum()))
    for name,mask in [('decline',event),('other',~event)]:
        rr=[r for r,m in zip(rows,mask) if m]
        result[name+'_pairs']=int(mask.sum());result[name+'_dates']=len({r['issue'] for r in rr})
        result[name+'_coverage']=float(np.average(cov[mask],weights=date_weights(rr))) if rr else None
        result[name+'_width']=float(np.average(width[mask],weights=date_weights(rr))) if rr else None
    return result


def choose(table,p):
    passed=[]
    for name,value in table.items():
        reasons=[]
        for label,q in [('pooled',value['pooled']),*value['folds'].items()]:
            reference=table['legacy']['pooled'] if label=='pooled' else table['legacy']['folds'][label]
            if q['coverage']<p['coverage_floor']:reasons.append(label+': overall')
            if q['decline_coverage'] is not None and q['decline_coverage']<p['decline_floor']:reasons.append(label+': decline')
            if q['mean_width']>reference['mean_width']*p['width_limit']:reasons.append(label+': width')
        value['rejection_reasons']=reasons
        if not reasons:passed.append(name)
    winner=min(passed,key=lambda n:table[n]['pooled']['weighted_score']) if passed else 'legacy'
    return winner,bool(passed)


def fit():
    if (D/'frozen-selection.json').exists() or (D/'report.json').exists():raise ValueError('Fit locked after selection/test')
    p=protocol()
    if digest(SRC)!=p['source_2024_sha256'] or digest(OLD)!=p['legacy_sha256']:raise ValueError('Changed frozen source')
    rows=[r for r in read(SRC) if r['fid'] not in HOLDOUT];only2024(rows);legacy=read(OLD)
    archive,reference_folds=residual_archive(rows,legacy);dump(D/'residuals-2024.json',dict(rows=archive,reference_folds=reference_folds))
    table={s['id']:dict(folds={},predictions=[]) for s in p['candidates']};folds=[]
    for start in p['outer_starts']:
        end=f'2024-{int(start[5:7])+1:02d}-01'
        val=[r for r in rows if start<=r['issue'] and r['target']<end]
        meta,calib=meta_split(archive,start)
        if legacy['selected_model']['fit_max_target']>=start:raise ValueError('Point fitted beyond validation cutoff')
        point=predict(legacy['selected_model'],val)
        for spec in p['candidates']:
            tail=tail_fit(meta,spec,p,D/'selection-models'/start,legacy['interval']['radius'][1])
            adjustment=calibrate(tail,calib);low,high=bounds(tail,adjustment,val,point)
            entry=table[spec['id']];entry['folds'][start]=metrics(val,low,high)
            entry['predictions'].extend(dict(r,low=float(low[i]),high=float(high[i])) for i,r in enumerate(val))
        folds.append(dict(start=start,point_max_target=legacy['selected_model']['fit_max_target'],
            residual_fit_max_target=max(r['target'] for r in meta),calibration_first_issue=min(r['issue'] for r in calib),
            calibration_max_target=max(r['target'] for r in calib),validation_pairs=len(val)))
    external={}
    for name,value in table.items():
        rr=value.pop('predictions');external[name]=rr
        value['pooled']=metrics(rr,np.array([r['low'] for r in rr]),np.array([r['high'] for r in rr]))
    selected,passed=choose(table,p);dump(D/'selection-table.json',table);dump(D/'outer-predictions-2024.json',external)
    meta,calib=meta_split(archive,'2025-01-01');spec=next(s for s in p['candidates'] if s['id']==selected)
    tail=tail_fit(meta,spec,p,D/'selected-models',legacy['interval']['radius'][1]);adjustment=calibrate(tail,calib)
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),protocol_sha256=digest(D/'protocol.json'),
        source_2024_sha256=digest(SRC),legacy_sha256=digest(OLD),selected=selected,selection_constraints_passed=passed,
        tail=tail,calibration=adjustment,outer_folds=folds,
        residual_fit_keys=[key(r) for r in meta],calibration_keys=[key(r) for r in calib],
        residual_archive_sha256=digest(D/'residuals-2024.json'),selection_table_sha256=digest(D/'selection-table.json'),
        fit_code_sha256=digest(__file__),point_retrained=False,detector_changed=False,ndvi_changed=False,operational=False)
    dump(D/'frozen-selection.json',out)
    print('Frozen choice:',selected,'passed',passed,flush=True)
    for name,value in table.items():print(name,value['pooled'],value['rejection_reasons'],flush=True)
    return out


def evaluate():
    from .field_decline_experiment import confusion
    f=read(D/'frozen-selection.json');sha=digest(D/'frozen-selection.json');p=read(D/'protocol.json')
    if digest(D/'protocol.json')!=f['protocol_sha256'] or digest(SRC)!=f['source_2024_sha256'] or digest(OLD)!=f['legacy_sha256']:
        raise ValueError('Changed source or frozen protocol')
    if digest(D/'selection-table.json')!=f['selection_table_sha256'] or digest(D/'residuals-2024.json')!=f['residual_archive_sha256']:
        raise ValueError('Changed selection evidence')
    old=read(OLD);radius=old['interval']['radius'];future=read(ROOT/'data/farm-research/field-decline-model/data-2026.json')
    reserved=[r for r in read(SRC) if r['fid'] in HOLDOUT]
    groups=dict(spatial2024=reserved,all2026=future,temporal2026=[r for r in future if r['fid'] not in HOLDOUT],spatial2026=[r for r in future if r['fid'] in HOLDOUT])
    results={};predictions=[]
    for label,rr in groups.items():
        point=predict(old['selected_model'],rr);low,high=bounds(f['tail'],f['calibration'],rr,point)
        oldlow=np.clip(point[:,1]-radius[1],-1,1);oldhigh=np.clip(point[:,1]+radius[1],-1,1)
        before=metrics(rr,oldlow,oldhigh);after=metrics(rr,low,high)
        event=np.array([r['y'][1]-r['x'][1]<=-.10 for r in rr]);alarm=point[:,1]-np.array([r['x'][1] for r in rr])<=-.10
        signals=confusion(rr,event,alarm)
        results[label]=dict(before=before,after=after,detector=signals,
            constraints=dict(decline_improved=after['decline_coverage']>before['decline_coverage'],
                overall=after['coverage']>=p['coverage_floor'],width=after['mean_width']<=before['mean_width']*p['width_limit']))
        for i,r in enumerate(rr):
            predictions.append(dict(split=label,fid=r['fid'],issue=r['issue'],target=r['target'],actual=r['y'],point=point[i].tolist(),
                low=float(low[i]),high=float(high[i]),oldlow=float(oldlow[i]),oldhigh=float(oldhigh[i]),
                ndvi_low=float(np.clip(point[i,0]-radius[0],-1,1)),ndvi_high=float(np.clip(point[i,0]+radius[0],-1,1)),
                detector=[bool(point[i,j]-r['x'][j]<=-.10) for j in range(2)]))
    passed=all(all(results[k]['constraints'].values()) for k in ['all2026','spatial2026'])
    if digest(D/'frozen-selection.json')!=sha:raise ValueError('Choice changed during test')
    dump(D/'test-predictions.json',predictions)
    out=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),status='research_only',selected=f['selected'],
        frozen_sha256=sha,results=results,test_constraints_passed=passed,production_promoted=False,
        point_unchanged=True,detector_unchanged=True,ndvi_interval_unchanged=True,tuning_years=[2024],
        evaluation_code_sha256=digest(__file__),limitations=[p['residual_archive'],p['original_comparator'],p['test'],p['archive'],
            'Selected on 194 pairs/14 dates with few independent event dates. No guaranteed conditional coverage.',
            'A larger downward allowance is not a probability or a new alert. Spectral changes have no verified agronomic cause.'])
    dump(D/'report.json',out);print('Test constraints:',passed,flush=True)
    print('all2026:',results['all2026'],flush=True);print('spatial2026:',results['spatial2026'],flush=True)
    return out
