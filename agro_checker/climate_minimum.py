"""Climate/height-aware residuals from training-season forecast backgrounds only."""
from datetime import datetime
import math
import numpy as np
from . import regional_minimum as common
from .minimum_model import features

CONTEXT_FIELDS=['mean_gfs_min_c','p10_gfs_min_c','std_gfs_min_c','mean_gfs_range_c','mean_gfs_wind_ms','mean_gfs_rh_pct','mean_gfs_cloud_pct','mean_gfs_spread_c']


def backgrounds(samples,metadata,years):
    """Reads forecasts, never observation values or observed event frequencies."""
    if not years or not set(years)<={2022,2023} or any(n['year'] not in years for n in samples):raise ValueError('Backgrounds accept training-year forecasts only')
    by={}
    for n in samples:by.setdefault(n['id'],[]).append(n)
    result={}
    for sid,ns in by.items():
        if sid not in metadata or len({n['date'] for n in ns})!=len(ns) or len(ns)<20:raise ValueError('Missing metadata or sparse/duplicate background')
        x=np.array([features(common.adapter(n)) for n in ns]);v=[float(x[:,0].mean()),float(np.quantile(x[:,0],.1)),float(x[:,0].std()),float(x[:,6].mean()),
                                                                  float(x[:,11].mean()),float(x[:,8].mean()),float(x[:,12].mean()),float(x[:,10].mean())]
        meta=metadata[sid]
        for key,lo,hi in [('latitude',-90,90),('longitude',-180,180),('elevation_m',-500,9000)]:
            z=meta[key]
            if isinstance(z,bool) or not isinstance(z,(int,float)) or not math.isfinite(z) or not lo<=z<=hi:raise ValueError('Invalid station metadata')
        result[sid]={'id':sid,**{k:meta[k] for k in ['latitude','longitude','elevation_m']},**dict(zip(CONTEXT_FIELDS,v)),
                     'training_years':sorted(years),'forecast_nights':len(ns),'knowledge_cutoff_utc':max(n['issued_at_utc'] for n in ns),
                     'scope':'typical forecast spring of training years, not long-term climatic normals','observed_targets_used':False}
    return result


def distance(a,b,scales):
    return math.sqrt(sum(((a[key]-b[key])/scale)**2 for key,scale in scales.items()))


def neighbourhood(profile,catalog,k,scales):
    values=sorted([(distance(profile,c,scales),sid) for sid,c in catalog.items()]);chosen=values[:min(k,len(values))]
    weights=np.array([math.exp(-.5*min(d,12)**2) for d,_ in chosen]);weights/=weights.sum()
    return [{'id':sid,'distance':d,'weight':float(w)} for (d,sid),w in zip(chosen,weights)]


def context_features(n,profile,terrain,family):
    if profile['id']!=n['id']:raise ValueError('Wrong station background')
    t=terrain[n['date']][n['id']];h=t['gfs_surface_height'];e=profile['elevation_m'];x=features(common.adapter(n))
    if t['units'] not in ['m','gpm'] or isinstance(h,bool) or not isinstance(h,(int,float)) or not math.isfinite(h) or not -500<=h<=9000:raise ValueError('Invalid model surface height')
    # HGT may be geopotential metres: difference is a statistical proxy, no physical lapse correction.
    delta=(e-h)/1000
    if family in ['matched3_weather','matched5_weather']:return x
    geo=[profile['latitude']/10,profile['longitude']/10,e/1000,h/1000,delta,delta*x[11],delta*x[12]/100]
    if family=='geo_huber':return np.r_[x,geo]
    climate=[profile[k] for k in CONTEXT_FIELDS]
    anomaly=[x[0]-profile['mean_gfs_min_c'],x[0]-profile['p10_gfs_min_c'],x[11]-profile['mean_gfs_wind_ms'],x[12]-profile['mean_gfs_cloud_pct']]
    return np.r_[x,geo,climate,anomaly]


def fit_bundle(samples,catalog,terrain,spec,scales):
    if spec['family'] not in ['geo_huber','climate_huber','climate_ridge','matched3_weather','matched5_weather','matched3_climate']:raise ValueError('Unregistered climate family')
    if not samples or any(n['year'] not in [2022,2023] or n.get('role')=='heldout_site' for n in samples):raise ValueError('Forbidden fit labels')
    usable=[n for n in samples if common.complete(n)]
    if len(usable)<20:raise ValueError('Too few complete training nights')
    years=sorted({n['year'] for n in samples})
    if set(catalog)!={n['id'] for n in samples} or any(c['training_years']!=years or c.get('observed_targets_used') is not False for c in catalog.values()):raise ValueError('Mismatched/target-derived training contexts')
    matched=spec['family'].startswith('matched');k=5 if spec['family']=='matched5_weather' else 3
    queries=sorted(catalog) if matched else ['global'];models={}
    from sklearn.linear_model import HuberRegressor,Ridge
    from threadpoolctl import threadpool_limits
    for query in queries:
        neighbours=neighbourhood(catalog[query],catalog,k,scales) if matched else [{'id':sid,'weight':1.,'distance':0.} for sid in catalog]
        site_w={r['id']:r['weight'] for r in neighbours};used=[n for n in usable if n['id'] in site_w]
        w,_=common.weights(used,spec['cold_weight'],'date');w*=np.array([site_w[n['id']] for n in used]);w*=len({n['date'] for n in used})/w.sum()
        x=np.array([context_features(n,catalog[n['id']],terrain,spec['family']) for n in used]);y=[min(n['target_temperature_c'])-min(n['gfs']['temperature']) for n in used]
        mean=np.average(x,axis=0,weights=w);scale=np.sqrt(np.average((x-mean)**2,axis=0,weights=w));scale[scale<1e-12]=1
        est=Ridge(alpha=10,solver='svd') if spec['family']=='climate_ridge' else HuberRegressor(alpha=1,max_iter=2000)
        with threadpool_limits(limits=1):est.fit((x-mean)/scale,y,sample_weight=w)
        models[query]={'mean':mean.tolist(),'scale':scale.tolist(),'coef':est.coef_.tolist(),'intercept':float(est.intercept_),
                       'training_nights':len(used),'cold_site_nights':sum(min(n['target_temperature_c'])<0 for n in used),'neighbours':neighbours,'weight_sum':float(w.sum())}
    return {'spec':spec,'catalog':catalog,'matching_scales':scales,'models':models,'training_years':years,
            'training_sites':sorted(catalog),'training_dates':sorted({n['date'] for n in usable}),'training_nights':len(usable),
            'cold_site_nights':sum(min(n['target_temperature_c'])<0 for n in usable),'detector_unchanged':True,'scope':'research minimum only; no orchard or alert validity'}


def predict(bundle,base,n,terrain,blend,query_context=None):
    if blend not in [.25,.5,1]:raise ValueError('Unregistered mixing weight')
    profile=query_context if query_context is not None else bundle['catalog'].get(n['id'])
    if profile is None or profile['training_years']!=bundle['training_years'] or profile.get('observed_targets_used') is not False:raise ValueError('Unknown/mismatched/target-derived past forecast context')
    if datetime.fromisoformat(profile['knowledge_cutoff_utc'])>=datetime.fromisoformat(n['issued_at_utc']):raise ValueError('Background not available before this issue')
    x=context_features(n,profile,terrain,bundle['spec']['family']);models=bundle['models']
    if 'global' in models:experts=[{'id':'global','weight':1}]
    elif n['id'] in models:experts=[{'id':n['id'],'weight':1}]
    else:
        k=5 if bundle['spec']['family']=='matched5_weather' else 3
        experts=neighbourhood(profile,bundle['catalog'],k,bundle['matching_scales'])
    correction=0.
    for expert in experts:
        m=models[expert['id']];z=(x-np.array(m['mean']))/np.array(m['scale']);correction+=expert['weight']*(m['intercept']+float(z@np.array(m['coef'])))
    point=min(n['gfs']['temperature'])+correction
    output=(1-blend)*common.predict(base,n)+blend*point
    if not math.isfinite(output):raise ValueError('Nonfinite minimum')
    return output
