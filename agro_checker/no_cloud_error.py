"""Compatible point/residual family: five GFS fields, no cloud imputation."""
import math
import numpy as np
from .minimum_model import features, target_minimum
from .extended_error import fit_extended_head


def inputs(n):
    temperature = features(n, temperature_only=True)
    weather = [r['features_weather'] for r in n['rows']]
    for w in weather:
        if len(w) < 5 or any(isinstance(w[i],bool) or not isinstance(w[i],(int,float)) or not math.isfinite(w[i]) for i in range(1,5)):
            raise ValueError('Missing instantaneous RH/dewpoint/wind')
        if not (0<=w[1]<=110 and -100<=w[2]<=60 and 0<=w[4]<=100): raise ValueError('Invalid no-cloud inputs')
    return np.asarray(temperature.tolist()+[sum(w[i] for w in weather)/14 for i in [1,2,3,4]]+[min(w[4] for w in weather)])


def fit_point(samples):
    if not samples or any(n['year'] not in [2021,2022,2023] for n in samples): raise ValueError('Point fit accepts only2021–2023')
    usable=[n for n in samples if target_minimum(n) is not None]
    if len({n['date'] for n in samples})!=len(samples) or len(usable)<20: raise ValueError('Duplicate/insufficient point fit')
    x=np.asarray([inputs(n) for n in usable]); y=[target_minimum(n)-min(n['base']['temperature']) for n in usable]
    mean=x.mean(axis=0);scale=x.std(axis=0);scale[scale==0]=1
    from sklearn.linear_model import HuberRegressor
    est=HuberRegressor(alpha=1.,max_iter=1000).fit((x-mean)/scale,y)
    return {'kind':'huber_1_no_cloud','mean':mean.tolist(),'scale':scale.tolist(),'coef':est.coef_.tolist(),'intercept':float(est.intercept_),
            'training_dates':[n['date'] for n in usable],'training_years':sorted({n['year'] for n in usable}),'excluded_training_nights':len(samples)-len(usable)}


def point(model,n):
    z=(inputs(n)-np.array(model['mean']))/np.array(model['scale'])
    value=min(n['base']['temperature'])+model['intercept']+float(z@np.array(model['coef']))
    if not math.isfinite(value): raise ValueError('Nonfinite point')
    return value,float(np.max(np.abs(z)))


def crossfit(samples):
    if not samples or any(n['year'] not in [2021,2022,2023] for n in samples): raise ValueError('OOF accepts only2021–2023')
    usable=sorted([n for n in samples if target_minimum(n) is not None],key=lambda n:n['date'])
    if len(usable)<40 or len({n['date'] for n in samples})!=len(samples): raise ValueError('Duplicate/insufficient OOF')
    records=[];audit=[]
    for indices in np.array_split(np.arange(len(usable)),6):
        held=set(int(i) for i in indices);model=fit_point([n for i,n in enumerate(usable) if i not in held])
        audit.append({'fit_dates':model['training_dates'],'held_dates':[usable[i]['date'] for i in sorted(held)]})
        for i in sorted(held):
            n=usable[i];records.append({'date':n['date'],'x':inputs(n).tolist(),'error_c':target_minimum(n)-point(model,n)[0]})
    return sorted(records,key=lambda r:r['date']),audit


def fit_head(records,audit,spec):
    return fit_extended_head(records,audit,spec,allowed_point_years=(2021,2022,2023))


def shape(head,n,p):
    # Reuse the JSON-only mathematics after supplying only the 13 validated inputs.
    x=inputs(n);z=(x-np.array(head['mean']))/np.array(head['scale']);values=[]
    for h in head['heads']:
        if 'coef' in h: value=h['intercept']+float(z@np.array(h['coef']))
        else:
            value=h['initial']
            for t in h['trees']:
                i=0
                while t['left'][i]!=-1: i=t['left'][i] if z[t['feature'][i]]<=t['threshold'][i] else t['right'][i]
                value+=head['spec']['learning_rate']*t['value'][i]
        if not math.isfinite(value): raise ValueError('Nonfinite residual shape')
        values.append(value)
    mode=head['spec']['mode']
    if mode=='constant': lo,hi=-1.,1.
    elif mode in ['scale','asymmetric']:
        s=[float(np.clip(np.expm1(np.clip(v,0,math.log1p(p['scale_ceiling_c']))),p['scale_floor_c'],p['scale_ceiling_c'])) for v in values]
        lo,hi=-s[0],s[-1]
    else: lo,hi=min(*values,0.),max(*values,0.)
    return {'lower':lo,'upper':hi,'max_abs_z':float(np.max(np.abs(z)))}


def pool(model,head,samples,p,year):
    if year not in [2023,2024] or not samples or any(n['year']!=year for n in samples): raise ValueError('Calibration accepts only prior validation season')
    dates={n['date'] for n in samples};fit_dates=set(model['training_dates'])|set(head['fit_dates'])
    if len(dates)!=len(samples) or fit_dates&dates or any(int(d[:4])>=year for d in fit_dates): raise ValueError('Calibration overlap/chronology')
    rows=[]
    for n in samples:
        actual=target_minimum(n)
        if actual is None:continue
        error=actual-point(model,n)[0];s=shape(head,n,p)
        score=max(-error/-s['lower'],error/s['upper'],0.) if head['spec']['mode'] in ['constant','scale','asymmetric'] else max(s['lower']-error,error-s['upper'],0.)
        rows.append({'date':n['date'],'score':score})
    return rows


def forecast(model,head,cal,n,p):
    minimum,z=point(model,n);s=shape(head,n,p);q=cal['value']
    if not math.isfinite(q) or q<0:raise ValueError('Invalid calibration')
    if head['spec']['mode'] in ['constant','scale','asymmetric']:lo,hi=s['lower']*q,s['upper']*q
    else:lo,hi=s['lower']-q,s['upper']+q
    return {'point_c':minimum,'lower_c':minimum+lo,'upper_c':minimum+hi,'max_abs_z':z,'residual_head_max_abs_z':s['max_abs_z'],
        'extrapolation':max(z,s['max_abs_z'])>4,'coverage_guaranteed':False,'probability_of_crop_damage':None,
        'threshold_state':'cold_range' if minimum+hi<0 else 'warm_range' if minimum+lo>=0 else 'crosses_zero'}


def safe_prediction(model,head,cal,n,p,station_id):
    if station_id!='34929':return {'status':'unsupported_site','prediction':None}
    try:r=forecast(model,head,cal,n,p)
    except (ValueError,KeyError,TypeError,IndexError):return {'status':'invalid_or_unsupported_inputs','prediction':None}
    if r['extrapolation']:return {'status':'extrapolation','prediction':None}
    return {'status':'research_only','prediction':r}
