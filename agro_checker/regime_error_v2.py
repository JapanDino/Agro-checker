"""Weighted OOF point/error training with a frozen coarse GFS update registry."""
from datetime import datetime
import math
import numpy as np
from .no_cloud_error import inputs as weather_inputs
from .minimum_model import target_minimum
from .conditional_error import tree_dump


def regime(run,p):
    t=datetime.fromisoformat(run)
    if t.tzinfo is None:raise ValueError('Version timestamp needs timezone')
    for entry in p['version_registry']:
        if datetime.fromisoformat(entry['from'])<=t<datetime.fromisoformat(entry['until']):return entry['name']
    raise ValueError('Unknown GFS version registry date')


def run_for_date(date):return date+'T06:00:00+00:00'


def inputs(n,policy,p):
    x=weather_inputs(n).tolist();name=regime(n['rows'][0]['forecast_run_at_utc'],p)
    if policy['version_offsets']:x+=[float(name==entry['name']) for entry in p['version_registry'][:2]]
    return np.array(x)


def weights(dates,policy,p,anchor_year):
    if not dates or anchor_year not in [2022,2023] or any(int(d[:4]) not in [2021,2022,2023] or int(d[:4])>anchor_year for d in dates):
        raise ValueError('Weights accept only prior training dates at fixed cutoff')
    if policy['half_life_seasons'] is not None and policy['half_life_seasons']<=0:raise ValueError('Invalid half-life')
    if len(policy['version_weights'])!=len(p['version_registry']) or any(not math.isfinite(w) or w<=0 for w in policy['version_weights']):raise ValueError('Invalid version weights')
    names=[v['name'] for v in p['version_registry']];result=[]
    for d in dates:
        age=anchor_year-int(d[:4]);decay=2**(-age/policy['half_life_seasons']) if policy['half_life_seasons'] else 1.
        result.append(decay*policy['version_weights'][names.index(regime(run_for_date(d),p))])
    raw=np.array(result);return raw/raw.mean()


def weighted_scale(x,w):
    if np.all(w==w[0]):mean=x.mean(axis=0);scale=x.std(axis=0)
    else:mean=np.average(x,axis=0,weights=w);scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w))
    scale[(scale==0) | (np.ptp(x,axis=0)==0)]=1
    return mean,scale


def fit_kwargs(w):return {} if np.all(w==w[0]) else {'sample_weight':w}


def audit_weights(dates,w,p):
    names=[regime(run_for_date(d),p) for d in dates]
    return {'effective_nights':float(w.sum()**2/(w@w)), 'normalized_weight_sum':float(w.sum()),
            'regimes':{name:{'nights':names.count(name),'weight_sum':float(sum(v for v,r in zip(w,names) if r==name))} for name in sorted(set(names))}}


def fit_point(samples,policy,p,anchor_year):
    if not samples or any(n['year'] not in [2021,2022,2023] or n['year']>anchor_year for n in samples):raise ValueError('Point fit accepts prior years only')
    if len({n['date'] for n in samples})!=len(samples):raise ValueError('Duplicate point night')
    usable=[n for n in samples if target_minimum(n) is not None]
    if len(usable)<20:raise ValueError('Too few complete point nights')
    dates=[n['date'] for n in usable];w=weights(dates,policy,p,anchor_year);x=np.array([inputs(n,policy,p) for n in usable]);mean,scale=weighted_scale(x,w)
    y=[target_minimum(n)-min(n['base']['temperature']) for n in usable]
    from sklearn.linear_model import HuberRegressor
    est=HuberRegressor(alpha=1.,max_iter=1000).fit((x-mean)/scale,y,**fit_kwargs(w))
    return {'kind':'weighted_huber_1_no_cloud','policy':policy,'anchor_year':anchor_year,'mean':mean.tolist(),'scale':scale.tolist(),
            'coef':est.coef_.tolist(),'intercept':float(est.intercept_),'training_dates':dates,
            'training_regimes':sorted({regime(run_for_date(d),p) for d in dates}), 'weight_audit':audit_weights(dates,w,p)}


def point(model,n,p):
    z=(inputs(n,model['policy'],p)-np.array(model['mean']))/np.array(model['scale'])
    value=min(n['base']['temperature'])+model['intercept']+float(z@np.array(model['coef']))
    if not math.isfinite(value):raise ValueError('Nonfinite point')
    return value,float(np.max(np.abs(z)))


def crossfit(samples,policy,p,anchor_year):
    if not samples or any(n['year'] not in [2021,2022,2023] or n['year']>anchor_year for n in samples):raise ValueError('OOF accepts prior years only')
    usable=sorted([n for n in samples if target_minimum(n) is not None],key=lambda n:n['date'])
    if len(usable)<40 or len({n['date'] for n in samples})!=len(samples):raise ValueError('Duplicate/insufficient OOF')
    records=[];audit=[]
    for indices in np.array_split(np.arange(len(usable)),p['crossfit_blocks']):
        held=set(int(i) for i in indices);model=fit_point([n for i,n in enumerate(usable) if i not in held],policy,p,anchor_year)
        audit.append({'fit_dates':model['training_dates'],'held_dates':[usable[i]['date'] for i in sorted(held)],'anchor_year':anchor_year})
        for i in sorted(held):
            n=usable[i];records.append({'date':n['date'],'x':inputs(n,policy,p).tolist(),'error_c':target_minimum(n)-point(model,n,p)[0]})
    return sorted(records,key=lambda r:r['date']),audit


def fit_head(records,audit,spec,policy,p,anchor_year):
    dates=[r['date'] for r in records];w=weights(dates,policy,p,anchor_year)
    if len(set(dates))!=len(dates) or sorted(d for b in audit for d in b['held_dates'])!=sorted(dates):raise ValueError('Residual audit mismatch')
    for b in audit:
        if set(b['fit_dates'])&set(b['held_dates']) or b['anchor_year']!=anchor_year or any(int(d[:4])>anchor_year or int(d[:4]) not in [2021,2022,2023] for d in b['fit_dates']):raise ValueError('Point/residual overlap or chronology')
    x=np.array([r['x'] for r in records]);y=np.array([r['error_c'] for r in records])
    if not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('Nonfinite residuals')
    mean,scale=weighted_scale(x,w);z=(x-mean)/scale
    head={'spec':spec,'policy':policy,'anchor_year':anchor_year,'mean':mean.tolist(),'scale':scale.tolist(),'fit_dates':dates,
          'training_regimes':sorted({regime(run_for_date(d),p) for d in dates}),'weight_audit':audit_weights(dates,w,p),'crossfit_audit':audit,'heads':[]}
    targets=[np.log1p(np.abs(y))] if spec['mode']=='scale' else [np.log1p(np.maximum(-y,0)),np.log1p(np.maximum(y,0))] if spec['mode']=='asymmetric' else [y,y]
    for i,target in enumerate(targets):
        if spec['mode'] in ['scale','asymmetric']:
            from sklearn.linear_model import Ridge
            est=Ridge(alpha=spec['alpha'],solver='svd').fit(z,target,**fit_kwargs(w))
        elif spec['mode']=='trees':
            from sklearn.ensemble import GradientBoostingRegressor
            est=GradientBoostingRegressor(loss='quantile',alpha=[.05,.95][i],n_estimators=spec['estimators'],max_depth=spec['depth'],
                min_samples_leaf=spec['minimum_leaf'],learning_rate=spec['learning_rate'],random_state=spec['seed']).fit(z,target,**fit_kwargs(w))
        else:raise ValueError('Unknown residual family')
        if spec['mode']=='trees':head['heads'].append({'initial':float(est.init_.predict(z[:1])[0]),'trees':[tree_dump(t[0]) for t in est.estimators_]})
        else:head['heads'].append({'coef':est.coef_.tolist(),'intercept':float(est.intercept_)})
    return head


def shape(head,n,p):
    z=(inputs(n,head['policy'],p)-np.array(head['mean']))/np.array(head['scale']);values=[]
    for h in head['heads']:
        if 'coef' in h:value=h['intercept']+float(z@np.array(h['coef']))
        else:
            value=h['initial']
            for t in h['trees']:
                i=0
                while t['left'][i]!=-1:i=t['left'][i] if z[t['feature'][i]]<=t['threshold'][i] else t['right'][i]
                value+=head['spec']['learning_rate']*t['value'][i]
        if not math.isfinite(value):raise ValueError('Nonfinite residual head')
        values.append(value)
    if head['spec']['mode'] in ['scale','asymmetric']:
        s=[float(np.clip(np.expm1(np.clip(v,0,math.log1p(p['scale_ceiling_c']))),p['scale_floor_c'],p['scale_ceiling_c'])) for v in values]
        lo,hi=-s[0],s[-1]
    else:lo,hi=min(*values,0.),max(*values,0.)
    return {'lower':lo,'upper':hi,'max_abs_z':float(np.max(np.abs(z)))}


def pool(model,head,samples,p,year):
    if year not in [2023,2024] or not samples or any(n['year']!=year for n in samples):raise ValueError('Calibration accepts prior validation season only')
    dates={n['date'] for n in samples};fit_dates=set(model['training_dates'])|set(head['fit_dates'])
    if len(dates)!=len(samples) or fit_dates&dates or any(int(d[:4])>=year for d in fit_dates):raise ValueError('Calibration overlap/chronology')
    rows=[]
    for n in samples:
        actual=target_minimum(n)
        if actual is None:continue
        error=actual-point(model,n,p)[0];s=shape(head,n,p)
        score=max(-error/-s['lower'],error/s['upper'],0.) if head['spec']['mode'] in ['scale','asymmetric'] else max(s['lower']-error,error-s['upper'],0.)
        rows.append({'date':n['date'],'score':score})
    return rows


def forecast(model,head,cal,n,p):
    minimum,z=point(model,n,p);s=shape(head,n,p);q=cal['value'];name=regime(n['rows'][0]['forecast_run_at_utc'],p)
    if not math.isfinite(q) or q<0:raise ValueError('Invalid calibration')
    if head['spec']['mode'] in ['scale','asymmetric']:lo,hi=s['lower']*q,s['upper']*q
    else:lo,hi=s['lower']-q,s['upper']+q
    return {'point_c':minimum,'lower_c':minimum+lo,'upper_c':minimum+hi,'max_abs_z':z,'residual_head_max_abs_z':s['max_abs_z'],
            'extrapolation':max(z,s['max_abs_z'])>4,'gfs_regime':name,'unseen_regime':name not in model['training_regimes'] or name not in head['training_regimes'],
            'coverage_guaranteed':False,'probability_of_crop_damage':None,
            'threshold_state':'cold_range' if minimum+hi<0 else 'warm_range' if minimum+lo>=0 else 'crosses_zero'}


def safe_prediction(model,head,cal,n,p,station_id):
    if station_id!='34929':return {'status':'unsupported_site','prediction':None}
    try:r=forecast(model,head,cal,n,p)
    except (ValueError,KeyError,TypeError,IndexError):return {'status':'invalid_or_unsupported_inputs','prediction':None}
    if r['unseen_regime']:return {'status':'unseen_gfs_regime','prediction':None}
    if r['extrapolation']:return {'status':'extrapolation','prediction':None}
    return {'status':'research_only','prediction':r}
