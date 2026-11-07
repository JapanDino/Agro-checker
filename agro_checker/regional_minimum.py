"""Weighted night-minimum residuals; event detector is not an input or output."""
from collections import Counter,defaultdict
from datetime import date
import math
import numpy as np
from .minimum_model import features, point_minimum


def complete(n):
    t=n['target_temperature_c']
    return len(t)==14 and all(not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v) and -80<=v<=60 for v in t)


def adapter(n):
    """Forecast-only view equivalent to the parent's first six weather features."""
    g=n['gfs'];t=g['temperature'];dew=g['dewpoint'];rh=g['rh'];wind=[math.hypot(u,v) for u,v in zip(g['u10'],g['v10'])];cloud=g['cloud']
    for key,lo,hi in [('temperature',-80,60),('dewpoint',-100,60),('rh',0,110),('u10',-110,110),('v10',-110,110),('cloud',0,100)]:
        if len(g[key])!=14 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi for v in g[key]):raise ValueError('Invalid weather inputs')
    if any(len(x)!=14 for x in [t,dew,rh,wind,cloud,n['times']]):raise ValueError('Incomplete weather profile')
    rows=[{'date':n['date'],'year':n['year'],'hour_index':i,'forecast_run_at_utc':n['run_at_utc'],'issued_at_utc':n['issued_at_utc'],
           'valid_at_utc':n['times'][i],'base':{'temperature':t[i]},'features_weather':[t[i],rh[i],dew[i],t[i]-dew[i],wind[i],cloud[i]]} for i in range(14)]
    return {'date':n['date'],'year':n['year'],'rows':rows,'base':{'temperature':t}}


def episode_ids(cold_dates,gap=2):
    result={};previous=None;start=None
    for day in sorted(set(cold_dates)):
        current=date.fromisoformat(day)
        if previous is None or (current-previous).days>gap:start=day
        result[day]=start;previous=current
    return result


def weights(samples,cold_weight,balance,gap=2):
    if balance not in ['date','episode'] or cold_weight not in [1,2]:raise ValueError('Unregistered weighting')
    if not samples or len({(n['id'],n['date']) for n in samples})!=len(samples):raise ValueError('Empty/duplicate station nights')
    counts=Counter(n['date'] for n in samples);cold_dates={n['date'] for n in samples if min(n['target_temperature_c'])<0}
    episodes=episode_ids(cold_dates,gap);lengths=Counter(episodes.values())
    w=np.array([(cold_weight if min(n['target_temperature_c'])<0 else 1)/counts[n['date']] /
                (lengths[episodes[n['date']]] if balance=='episode' and min(n['target_temperature_c'])<0 else 1) for n in samples],dtype=float)
    # Total mass is dates, not number of correlated sites. Keeps regularization comparable.
    return w*len(counts)/w.sum(),episodes


def fit(samples,spec):
    if any(n['year'] not in [2022,2023] for n in samples):raise ValueError('Fit accepts only2022/23, never validation or controls')
    if any(n.get('role')=='heldout_site' for n in samples):raise ValueError('Heldout site labels forbidden in fit')
    usable=[n for n in samples if complete(n)]
    if len(usable)<20:raise ValueError('Too few complete nights')
    w,episodes=weights(usable,spec['cold_weight'],spec['balance']);x=np.array([features(adapter(n)) for n in usable]);y=np.array([min(n['target_temperature_c'])-min(n['gfs']['temperature']) for n in usable])
    mean=np.average(x,axis=0,weights=w);scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-12]=1
    from sklearn.linear_model import HuberRegressor,Ridge
    est=HuberRegressor(alpha=spec['alpha'],max_iter=2000) if spec['kind']=='huber' else Ridge(alpha=spec['alpha'],solver='svd')
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):est.fit((x-mean)/scale,y,sample_weight=w)
    return {'spec':spec,'mean':mean.tolist(),'scale':scale.tolist(),'coef':est.coef_.tolist(),'intercept':float(est.intercept_),
            'training_nights':len(usable),'excluded_nights':len(samples)-len(usable),'training_sites':sorted({n['id'] for n in usable}),
            'training_years':sorted({n['year'] for n in usable}),'training_dates':sorted({n['date'] for n in usable}),
            'cold_site_nights':sum(min(n['target_temperature_c'])<0 for n in usable),'cold_dates':len(episodes),'cold_episode_groups':len(set(episodes.values())),
            'sample_weight_sum':float(w.sum()),'detector_unchanged':True,'scope':'research minimum only, no new alerts or orchard validity'}


def predict(model,n):
    return point_minimum(model,adapter(n))[0]


def score(records):
    valid=[r for r in records if r['actual'] is not None];cold=[r for r in valid if r['actual']<0];warm=[r for r in valid if r['actual']>=0]
    mean=lambda rr:float(np.mean([abs(r['point']-r['actual']) for r in rr])) if rr else None
    groups=episode_ids([r['date'] for r in cold]);errors=defaultdict(list)
    for r in cold:errors[groups[r['date']]].append(abs(r['point']-r['actual']))
    counts=Counter('hits' if r['actual']<0 and r['point']<0 else 'misses' if r['actual']<0 else 'false_alarms' if r['point']<0 else 'correct_negatives' for r in valid)
    sites={sid:[r for r in valid if r['id']==sid] for sid in sorted({r['id'] for r in valid})}
    return {'complete_nights':len(valid),'excluded_nights':len(records)-len(valid),'cold_site_nights':len(cold),'cold_dates':len(groups),'cold_episode_groups':len(errors),
            'mae_c':mean(valid),'cold_mae_c':mean(cold),'warm_mae_c':mean(warm),'episode_macro_mae_c':float(np.mean([np.mean(v) for v in errors.values()])) if errors else None,
            **{k:counts[k] for k in ['hits','misses','false_alarms','correct_negatives']},
            'site_macro_mae_c':float(np.mean([mean(v) for v in sites.values()])) if sites else None,
            'site_macro_cold_mae_c':float(np.mean([mean([r for r in v if r['actual']<0]) for v in sites.values() if any(r['actual']<0 for r in v)])) if cold else None,
            'by_site':{sid:{'nights':len(v),'cold_nights':sum(r['actual']<0 for r in v),'mae_c':mean(v),'cold_mae_c':mean([r for r in v if r['actual']<0]),
                            'false_alarms':sum(r['actual']>=0 and r['point']<0 for r in v),'misses':sum(r['actual']<0 and r['point']>=0 for r in v)} for sid,v in sites.items()},
            'crossing_scope':'point <0 diagnostic, not original detector alerts'}


def paired_blocks(records,baseline,repetitions=1000):
    """Paired calendar-week blocks include all sites/dates; descriptive, no selection."""
    pairs={(r['id'],r['date']):r for r in baseline if r['actual'] is not None};blocks=defaultdict(list)
    for r in records:
        if r['actual'] is None:continue
        b=pairs[(r['id'],r['date'])]
        if b['actual']!=r['actual']:raise ValueError('Different comparison targets')
        blocks[date.fromisoformat(r['date']).toordinal()//7].append(abs(r['point']-r['actual'])-abs(b['point']-b['actual']))
    if len(blocks)<2:return {'status':'insufficient_blocks'}
    arr=list(blocks.values());rng=np.random.default_rng(904);d=[]
    for _ in range(repetitions):d.append(float(np.mean([v for i in rng.integers(0,len(arr),len(arr)) for v in arr[i]])))
    return {'status':'descriptive','mean_mae_delta_c':float(np.mean([v for a in arr for v in a])),
            'interval_95_c':np.quantile(d,[.025,.975]).tolist(),'calendar_week_blocks':len(arr),'repetitions':repetitions,
            'independence_limit':'weather may cross block boundaries; nearby sites share weather; not a prospective significance claim'}
