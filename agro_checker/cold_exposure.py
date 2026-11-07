"""Hourly-sample cold index; explicitly not a biological dose/damage model."""
import math
import numpy as np


def exposure(temperatures,threshold=0):
    if not isinstance(threshold,(int,float)) or isinstance(threshold,bool) or not math.isfinite(threshold):raise ValueError('Finite weather threshold required')
    if len(temperatures)!=14 or any(v is None or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not -90<=v<=65 for v in temperatures):
        return {'status':'incomplete','degree_hours_c_h':None,'cold_bins_h':None}
    return {'status':'complete','degree_hours_c_h':float(sum(max(0.,threshold-t) for t in temperatures)),
            'cold_bins_h':sum(t<threshold for t in temperatures),'resolution_hours':1,
            'definition':'rectangular14-hour sample index, not tissue damage','crop_damage_probability':None}


def fit(samples,name,minimum=10):
    if not samples or any(n['year'] not in [2022,2023] for n in samples):raise ValueError('Only2022/23 fit')
    usable=[n for n in samples if n['complete']]
    if len({n['date'] for n in usable})!=len(usable):raise ValueError('Duplicate nights')
    selected=[n for n in usable if n['duration']>0] if name.startswith('cold_') else usable
    if name.startswith('cold_') and len(selected)<minimum:raise ValueError('Too few cold nights')
    model={'name':name,'training_nights':len(selected),'fit_dates':[n['date'] for n in selected],
           'training_cold_nights':sum(n['duration']>0 for n in selected)}
    if name in ['gfs','detector_gate_gfs']:return model
    y=np.array([exposure([r['target']['temperature'] for r in n['rows']])['degree_hours_c_h'] for n in selected])
    if name=='cold_mean':return model|{'mean':float(y.mean())}
    if name not in ['cold_ridge_10','cold_ridge_100','all_ridge_10','all_ridge_100']:raise ValueError('Unknown exposure head')
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    x=np.array([n['night_features'] for n in selected]);scaler=StandardScaler().fit(x)
    ridge=Ridge(alpha=float(name.rsplit('_',1)[1]),solver='svd').fit(scaler.transform(x),np.sqrt(y))
    return model|{'mean':scaler.mean_.tolist(),'scale':scaler.scale_.tolist(),'coef':ridge.coef_.tolist(),'intercept':float(ridge.intercept_)}


def predict(model,n,detector):
    x=n['night_features'];raw=exposure(n['curve'])['degree_hours_c_h'];name=model['name']
    if raw is None or any(not math.isfinite(v) for v in x):raise ValueError('Incomplete forecast features')
    event=any(detector['cold_mask']);p=detector['night_event_model_score']
    if not isinstance(p,(int,float)) or not 0<=p<=1:raise ValueError('Frozen detector score required')
    z=None
    if name=='gfs':value=raw
    elif name=='detector_gate_gfs':value=raw if event else 0.
    elif name=='cold_mean':value=model['mean']*p
    else:
        z=[(v-m)/s for v,m,s in zip(x,model['mean'],model['scale'])]
        root=max(0.,model['intercept']+sum(a*b for a,b in zip(z,model['coef'])))
        value=root*root*(p if name.startswith('cold_') else 1.)
    return {'degree_hours_c_h':min(1260.,value),'gfs_degree_hours_c_h':raw,'threshold_c':0,
            'extrapolation':z is not None and max(map(abs,z))>4,'research_only':True,
            'detector_event_unchanged':event,'crop_damage_probability':None,
            'definition':'14 hourly-sample weather index; no tissue/harvest model'}
