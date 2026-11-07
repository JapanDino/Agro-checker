"""Age/version learning ONLY in residual head; immutable original Huber at inference."""
import math
import numpy as np
from .minimum_model import features,point_minimum,target_minimum
from .conditional_error import crossfit_residuals
from .regime_error_v2 import regime,fit_head as weighted_head


def error_features(n,policy,p):
    x=features(n).tolist();name=regime(n['rows'][0]['forecast_run_at_utc'],p)
    if policy['version_offsets']:x+=[float(name==r['name']) for r in p['version_registry'][:2]]
    return np.array(x)


def residuals(training,extra,point_model,policy,p,anchor_year):
    if anchor_year not in [2022,2023] or not training or any(n['year'] not in [2022,2023] or n['year']>anchor_year for n in training):raise ValueError('Only original prior training years')
    if any(n['year']!=2021 or not '2021-03-23'<=n['date']<='2021-05-31' for n in extra):raise ValueError('Extra inputs must have original instantaneous cloud')
    if any(int(d[:4]) not in [2022,2023] or int(d[:4])>anchor_year for d in point_model['training_dates']):raise ValueError('Point fit contains future targets')
    # This is the SAME unweighted fixed point family used in all earlier validation folds.
    base,audit=crossfit_residuals(training,point_model['spec'],p['crossfit_blocks'])
    by_date={n['date']:n for n in training};records=[]
    for r in base:records.append({**r,'x':error_features(by_date[r['date']],policy,p).tolist()})
    audit=[{**b,'anchor_year':anchor_year} for b in audit];held=[]
    for n in sorted(extra,key=lambda n:n['date']):
        # Validate even missing-target nights; no imputation or substitute point model.
        x=error_features(n,policy,p);actual=target_minimum(n)
        if actual is None:continue
        if n['date'] in point_model['training_dates']:raise ValueError('Extra target overlaps point fit')
        records.append({'date':n['date'],'x':x.tolist(),'error_c':actual-point_minimum(point_model,n)[0]});held.append(n['date'])
    if held:audit.append({'fit_dates':point_model['training_dates'],'held_dates':held,'anchor_year':anchor_year,'role':'extra2021 labels excluded from original point fit; retrospective'})
    if len({r['date'] for r in records})!=len(records):raise ValueError('Duplicate residual date')
    return sorted(records,key=lambda r:r['date']),audit


def fit_error(records,audit,spec,policy,p,anchor_year):
    return weighted_head(records,audit,spec,policy,p,anchor_year)


def shape(head,n,p):
    z=(error_features(n,head['policy'],p)-np.array(head['mean']))/np.array(head['scale']);values=[]
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


def pool(point_model,head,samples,p,year):
    if year not in [2023,2024] or not samples or any(n['year']!=year for n in samples):raise ValueError('Calibration accepts prior validation season only')
    dates={n['date'] for n in samples};fit_dates=set(point_model['training_dates'])|set(head['fit_dates'])
    if len(dates)!=len(samples) or fit_dates&dates or any(int(d[:4])>=year for d in fit_dates):raise ValueError('Calibration overlap/chronology')
    result=[]
    for n in samples:
        actual=target_minimum(n)
        if actual is None:continue
        error=actual-point_minimum(point_model,n)[0];s=shape(head,n,p)
        score=max(-error/-s['lower'],error/s['upper'],0.) if head['spec']['mode'] in ['scale','asymmetric'] else max(s['lower']-error,error-s['upper'],0.)
        result.append({'date':n['date'],'score':score})
    return result


def forecast(point_model,head,cal,n,p):
    # No new point fitter, no version offsets or weights in the original point call.
    point,z=point_minimum(point_model,n);s=shape(head,n,p);q=cal['value'];name=regime(n['rows'][0]['forecast_run_at_utc'],p)
    if not math.isfinite(q) or q<0:raise ValueError('Invalid calibration')
    if head['spec']['mode'] in ['scale','asymmetric']:lo,hi=s['lower']*q,s['upper']*q
    else:lo,hi=s['lower']-q,s['upper']+q
    return {'point_c':point,'lower_c':point+lo,'upper_c':point+hi,'max_abs_z':z,'residual_head_max_abs_z':s['max_abs_z'],
      'extrapolation':max(z,s['max_abs_z'])>4,'unseen_regime':name not in head['training_regimes'],'gfs_regime':name,
      'coverage_guaranteed':False,'probability_of_crop_damage':None,
      'threshold_state':'cold_range' if point+hi<0 else 'warm_range' if point+lo>=0 else 'crosses_zero'}


def safe_prediction(point_model,head,cal,n,p,station_id):
    if station_id!='34929':return {'status':'unsupported_site','prediction':None}
    try:r=forecast(point_model,head,cal,n,p)
    except (ValueError,KeyError,TypeError,IndexError):return {'status':'invalid_or_unsupported_inputs','prediction':None}
    if r['unseen_regime']:return {'status':'unseen_gfs_regime','prediction':None}
    if r['extrapolation']:return {'status':'extrapolation','prediction':None}
    return {'status':'research_only','prediction':r}
