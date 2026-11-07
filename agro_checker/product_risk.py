"""Whole-night residual scenarios and probability calibration on whole-date OOF."""
from datetime import datetime
import math
import numpy as np


def complete(n):
    t=n['target_temperature_c']
    return len(t)==14 and all(v is not None and math.isfinite(v) for v in t)


def validate(n):
    times=[datetime.fromisoformat(t) for t in n['times']];run=datetime.fromisoformat(n['run_at_utc']);issue=datetime.fromisoformat(n['issued_at_utc'])
    if any(t.tzinfo is None or t.utcoffset() is None for t in times+[run,issue]):raise ValueError('Aware times required')
    if len(times)!=14 or any((b-a).total_seconds()!=3600 for a,b in zip(times,times[1:])):raise ValueError('14 hourly timestamps')
    if run.hour!=6 or not run<=issue<times[0] or [(t-run).total_seconds()/3600 for t in times]!=list(range(10,24)):raise ValueError('Original run/lead/issue required')
    if not n['date'][5:7] in ['03','04','05']:raise ValueError('Spring domain only')
    for key in ['gfs_temperature_c','gfs_dewpoint_c']:
        if len(n[key])!=14 or any(v is None or not math.isfinite(v) or not -100<=v<=65 for v in n[key]):raise ValueError('Invalid forecast curve')
    if not math.isfinite(n['elevation_m']):raise ValueError('Elevation required')


def features(n):
    validate(n);t=n['gfs_temperature_c'];d=n['gfs_dewpoint_c']
    return np.array([min(t),max(t),np.mean(t),t[0],t[-1],t[-1]-t[0],max(t)-min(t),np.argmin(t)/13,
                     min(d),np.mean(d),np.mean(np.subtract(t,d)),n['elevation_m']/100])


def fit(samples,spec):
    usable=[n for n in samples if complete(n)]
    if not usable or any(n['year'] not in [2022,2023] for n in samples):raise ValueError('Only prior2022/23 fit')
    if len(usable)<spec['neighbors']:raise ValueError('Too few whole nights')
    if len({(n['site_id'],n['date']) for n in usable})!=len(usable):raise ValueError('Duplicate site/night')
    x=np.array([features(n) for n in usable]);scale=x.std(axis=0);scale[scale==0]=1
    return {'spec':spec,'mean':x.mean(axis=0).tolist(),'scale':scale.tolist(),'fit_dates':sorted({n['date'] for n in usable}),
      'sites':sorted({n['site_id'] for n in usable}),'training_nights':len(usable),
      'library':[{'date':n['date'],'site_id':n['site_id'],'x':features(n).tolist(),
                   'residual_c':np.subtract(n['target_temperature_c'],n['gfs_temperature_c']).tolist()} for n in usable]}


def members(model,n):
    x=features(n);scale=np.array(model['scale']);z=(x-np.array(model['mean']))/scale
    distances=[float(np.mean(((x-np.array(r['x']))/scale)**2)) for r in model['library']]
    indexes=sorted(range(len(distances)),key=lambda i:(distances[i],model['library'][i]['date'],model['library'][i]['site_id']))[:model['spec']['neighbors']]
    curves=[np.add(n['gfs_temperature_c'],model['library'][i]['residual_c']).tolist() for i in indexes]
    return curves,{'max_abs_z':float(np.max(np.abs(z))),'analog_keys':[(model['library'][i]['site_id'],model['library'][i]['date']) for i in indexes]}


def raw_probability(curves,threshold):return sum(min(t)<threshold for t in curves)/len(curves)


def logit(p,k):
    p=(p*k+.5)/(k+1);return math.log(p/(1-p))


def calibrate(samples,spec,p):
    usable=[n for n in samples if complete(n)];dates=sorted({n['date'] for n in usable});records=[];audits=[]
    if any(n['year'] not in [2022,2023] for n in samples):raise ValueError('Controls cannot enter calibration')
    blocks=np.array_split(dates,p['crossfit_date_blocks'])
    for block in blocks:
        held=set(block.tolist());train=[n for n in usable if n['date'] not in held];validation=[n for n in usable if n['date'] in held]
        model=fit(train,spec)
        if set(model['fit_dates'])&held:raise ValueError('Cross-site/date leakage')
        audits.append({'fit_dates':model['fit_dates'],'held_dates':sorted(held)})
        for n in validation:
            curves,_=members(model,n)
            records.append({'site_id':n['site_id'],'date':n['date'],'actual_minimum_c':min(n['target_temperature_c']),
              'raw':{str(q):raw_probability(curves,q) for q in p['weather_thresholds_c']}})
    heads={}
    from sklearn.linear_model import LogisticRegression
    for q in p['weather_thresholds_c']:
        y=[int(r['actual_minimum_c']<q) for r in records];events=sum(y)
        if len(y)<p['minimum_calibration_nights'] or min(events,len(y)-events)<p['minimum_calibration_events']:
            heads[str(q)]={'status':'insufficient_events','events':events,'nights':len(y)};continue
        x=[[logit(r['raw'][str(q)],spec['neighbors'])] for r in records]
        m=LogisticRegression(C=p['calibration_c'],max_iter=1000).fit(x,y)
        if float(m.coef_[0,0])<=0:
            heads[str(q)]={'status':'no_monotonic_signal','events':events,'nights':len(y)};continue
        heads[str(q)]={'status':'fitted_oof','coef':float(m.coef_[0,0]),'intercept':float(m.intercept_[0]),'events':events,'nights':len(y)}
    return {'heads':heads,'audits':audits,'fit_dates':dates,'method':'Platt, excluded whole-date blocks; empirical support limits; no coverage guarantee'}


def weighted_quantile(values,weights,q):
    ordered=sorted(zip(values,weights));total=sum(weights)
    if total<=0:return None
    acc=0
    for v,w in ordered:
        acc+=w
        if acc>=q*total:return float(v)
    return float(ordered[-1][0])


def forecast(model,cal,n,threshold,p):
    if threshold not in p['weather_thresholds_c']:raise ValueError('Threshold not calibrated in registry')
    curves,audit=members(model,n);raw=raw_probability(curves,threshold);head=cal['heads'][str(threshold)];prob=raw;status=head['status']
    if status=='fitted_oof' and 0<raw<1:
        v=np.clip(head['intercept']+head['coef']*logit(raw,len(curves)),-40,40);prob=float(1/(1+math.exp(-v)))
    elif status=='fitted_oof':status='finite_scenario_support_boundary'
    cold=[min(t)<threshold for t in curves]
    weights=[prob/sum(cold) if c else (1-prob)/(len(cold)-sum(cold)) for c in cold]
    masks=[[v<threshold for v in t] for t in curves];durations=[sum(m) for m in masks];starts=[m.index(True) if any(m) else None for m in masks]
    conditional=[i for i,c in enumerate(cold) if c];cw=[weights[i] for i in conditional]
    start_values=[max(0,starts[i]-1) for i in conditional]
    onset=weighted_quantile(start_values,cw,.1) if conditional else None
    left_mass=sum(weights[i] for i in conditional if starts[i]==0)/prob if prob>0 else None
    early_unknown=left_mass is not None and left_mass>=.1
    duration=[weighted_quantile([durations[i] for i in conditional],cw,q) for q in p['duration_quantiles']] if conditional else None
    center_duration=weighted_quantile([durations[i] for i in conditional],cw,.5) if conditional else None
    medoid=min(conditional,key=lambda i:abs(durations[i]-center_duration)+abs(max(0,starts[i]-1)-(onset or 0))) if conditional else None
    return {'probability':prob,'raw_probability':raw,'calibration_status':status,'threshold_c':threshold,
      'hour_probabilities':[sum(w for w,m in zip(weights,masks) if m[h]) for h in range(14)],
      'conditional_duration_range_h':duration,'expected_cold_hours':sum(w*d for w,d in zip(weights,durations)),
      'early_onset_index':None if early_unknown else onset,'onset_left_censored':early_unknown,
      'conditional_representative_mask':masks[medoid] if medoid is not None else [False]*14,
      'minimum_range_c':[weighted_quantile([min(t) for t in curves],weights,q) for q in [.05,.95]],
      'coverage_guaranteed':False,'crop_damage_probability':None,'research_only':True,
      'extrapolation':audit['max_abs_z']>4,'max_abs_z':audit['max_abs_z'],'analog_keys':audit['analog_keys'],
      'known_site':n['site_id'] in model['sites']}
