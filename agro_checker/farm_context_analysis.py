"""Boundary-scenario summaries; descriptive remote observations, never predictions."""
import math
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from pyproj import Transformer
from shapely import transform as st, box, intersection, area
from shapely.geometry import shape
from shapely.affinity import translate
from .farm_context_data import D,R,ROOT,read,dump,digest,protocol,SOIL_CRS

def scenarios(poly,radius):
    out=[('base',poly)]
    for label,dx,dy in [('N',0,1),('NE',1,1),('E',1,0),('SE',1,-1),('S',0,-1),('SW',-1,-1),('W',-1,0),('NW',-1,1)]:
        norm=math.hypot(dx,dy);out.append((label,translate(poly,xoff=radius*dx/norm,yoff=radius*dy/norm)))
    return out+[('core',poly.buffer(-radius)),('outer',poly.buffer(radius))]

def masks(variants,tr,grid_shape):
    # Inscribed cell guard prevents boundary-crossing cells contributing as pure field pixels.
    guard=math.hypot(tr.a,tr.e)/2
    result={}
    for name,poly in variants:
        inner=poly.buffer(-guard)
        if inner.is_empty:result[name]=np.array([],dtype=np.int32);continue
        mask=rasterize([(inner,1)],out_shape=grid_shape,transform=tr,fill=0,dtype='uint8')
        result[name]=np.flatnonzero(mask).astype(np.int32)
    return result

def spectral_stats(a,valid,ix,minimum=10,coverage=.8):
    total=len(ix);v=valid.ravel()[ix]&np.isfinite(a.ravel()[ix]);count=int(v.sum())
    fraction=count/total if total else 0
    accepted=bool(count>=minimum and fraction>=coverage)
    return dict(total_cells=total,valid_cells=count,valid_fraction=fraction,value=float(np.median(a.ravel()[ix][v])) if accepted else None,usable=accepted)

def metric(values,tolerance,core_ok=True):
    shifts=[values.get(k) for k in ['base','N','NE','E','SE','S','SW','W','NW']]
    complete=all(v is not None and math.isfinite(v) for v in shifts)
    available=[v for v in shifts if v is not None and math.isfinite(v)]
    lo=min(available) if available else None;hi=max(available) if available else None
    shift_ok=bool(complete and hi-lo<=tolerance)
    edges=[values.get('core'),values.get('outer')]
    all_ok=complete and core_ok and all(v is not None and math.isfinite(v) for v in edges)
    lo_all=min(shifts+edges) if all_ok else None;hi_all=max(shifts+edges) if all_ok else None
    return dict(base=values.get('base'),shift_min=lo,shift_max=hi,all_shifts_available=complete,shift_stable=shift_ok,
                core=values.get('core'),outer=values.get('outer'),all_scenario_min=lo_all,all_scenario_max=hi_all,
                tolerance=tolerance,robust=bool(all_ok and hi_all-lo_all<=tolerance),core_supported=bool(core_ok))

def soil_weights(poly,r):
    native=st(poly,Transformer.from_crs(32637,SOIL_CRS,always_xy=True).transform,interleaved=False)
    if native.is_empty:return np.array([],dtype=int),np.array([])
    left,bottom,right,top=native.bounds;tr=r.transform
    c0=max(0,math.floor((left-tr.c)/250));c1=min(r.width,math.ceil((right-tr.c)/250))
    r0=max(0,math.floor((tr.f-top)/250));r1=min(r.height,math.ceil((tr.f-bottom)/250))
    if r1<=r0 or c1<=c0:return np.array([],dtype=int),np.array([])
    rows,cols=np.meshgrid(np.arange(r0,r1),np.arange(c0,c1),indexing='ij');rows=rows.ravel();cols=cols.ravel()
    x=tr.c+cols*250;y=tr.f-rows*250
    cells=box(x,y-250,x+250,y);weights=area(intersection(cells,native));good=weights>1e-6
    return (rows*r.width+cols)[good],weights[good]

def weighted(a,index,weights):
    vals=np.asarray(a).ravel()[index];valid=np.isfinite(vals)
    total=float(weights.sum());valid_area=float(weights[valid].sum())
    if not total or valid_area/total<.99:return None
    return float(np.average(vals[valid],weights=weights[valid]))

def static_context(features):
    p=protocol();out={};dem_path=D/'dem/surface.tif'
    with rasterio.open(dem_path) as r:
        elevation=r.read(1);tr=r.transform;sh=r.shape
    with rasterio.open(D/'dem/slope.tif') as r:slope=r.read(1)
    soil_manifest=read(D/'soil/manifest.json');soil_arrays={}
    for layer in soil_manifest['layers']:
        with rasterio.open(ROOT/layer['path']) as r:
            raw=r.read(1,masked=True).astype('float64');soil_arrays[(layer['property'],layer['depth'],layer['statistic'])]=raw.filled(np.nan)/10
    with rasterio.open(ROOT/soil_manifest['layers'][0]['path']) as soil_r:
        for fid,poly,radius in features:
            variants=scenarios(poly,radius);indices=masks(variants,tr,sh);core_ok=len(indices['core'])>=10
            heights={};slopes={}
            for name,ix in indices.items():
                z=elevation.ravel()[ix];sl=slope.ravel()[ix]
                heights[name]=float(np.median(z)) if len(z)>=10 and np.isfinite(z).all() else None
                slopes[name]=float(np.quantile(sl,.9)) if len(sl)>=10 and np.isfinite(sl).all() else None
            z=elevation.ravel()[indices['base']];z=z[np.isfinite(z)]
            dsm=dict(height=metric(heights,p['stability_tolerances']['dsm_median_m'],core_ok),slope=metric(slopes,p['stability_tolerances']['slope_p90_deg'],core_ok),base_cells=len(indices['base']),core_cells=len(indices['core']),base_p10_p90_m=np.quantile(z,[.1,.9]).tolist() if len(z) else None)
            weights={name:soil_weights(g,soil_r) for name,g in variants};layers=[]
            for prop in ['clay','sand','silt','phh2o','soc']:
                for depth in ['0-5cm','15-30cm']:
                    vals={name:weighted(soil_arrays[prop,depth,'mean'],ix,w) for name,(ix,w) in weights.items()}
                    ix,w=weights['base'];qix=np.unique(np.concatenate([indices for indices,weights_ in weights.values()]))
                    good=np.isfinite(soil_arrays[prop,depth,'Q0.05'].ravel()[qix])&np.isfinite(soil_arrays[prop,depth,'Q0.95'].ravel()[qix])
                    lows=soil_arrays[prop,depth,'Q0.05'].ravel()[qix][good];highs=soil_arrays[prop,depth,'Q0.95'].ravel()[qix][good]
                    if len(lows) and np.any(lows>highs):raise ValueError('Invalid soil quantile order')
                    layers.append(dict(property=prop,depth=depth,units='pH' if prop=='phh2o' else 'g/kg' if prop=='soc' else '%',
                        sensitivity=metric(vals,p['stability_tolerances'][prop],not dict(variants)['core'].is_empty),native_cells_intersected=len(ix),
                        effective_cell_count=float(w.sum()**2/np.sum(w*w)) if len(w) else 0,
                        provider_cell_quantile_span=[float(min(lows)),float(max(highs))] if len(lows) else None,
                        provider_span_interpretation='min Q05 / max Q95 among native cells touched in ANY of eleven scenarios, NOT interval for field mean'))
            out[fid]=dict(dsm=dsm,soil=layers,core_area_ha=dict(variants)['core'].area/10000,radius_m=radius)
    return out

def satellite_context(features):
    p=protocol();snapshot=read(D/'satellite.json');x0,y0,x1,y1=p['bounds_utm'];tr=from_origin(x0,y1,20,20);sh=(int((y1-y0)/20),int((x1-x0)/20))
    grids={fid:masks(scenarios(poly,r),tr,sh) for fid,poly,r in features}
    result={fid:[] for fid,_,_ in features}
    for scene in snapshot['scenes']:
        data=None
        if scene['status']=='ready':
            with np.load(D/'sentinel'/scene['id']/'indices.npz') as source:data={k:source[k] for k in ['ndvi','ndmi','valid','scl']}
        for fid,_,_ in features:
            row={k:scene.get(k) for k in ['id','period','acquired_at_utc','processing_baseline','cloud_tile_percent']};row['source_status']=scene['status']
            if data is None:
                row.update(reason=scene.get('reason'),ndvi=None,ndmi=None);result[fid].append(row);continue
            ids=grids[fid];core_ok=len(ids['core'])>=p['minimum_core_cells']
            quality={}
            for name in ['ndvi','ndmi']:
                stats={key:spectral_stats(data[name],data['valid'],ix,p['minimum_valid_cells'],p['coverage_min']) for key,ix in ids.items()}
                sensitivity=metric({k:v['value'] for k,v in stats.items()},p['stability_tolerances'][name],core_ok and stats['core']['usable'])
                row[name]=sensitivity
                if name=='ndvi':quality=stats
            ix=ids['base'];scl=data['scl'].ravel()[ix]
            row.update(coverage=quality['base']['valid_fraction'],base_cells=quality['base']['total_cells'],valid_cells=quality['base']['valid_cells'],core_cells=len(ids['core']),minimum_shift_coverage=min(quality[k]['valid_fraction'] for k in list(ids)[:9]),
                       scl_cloud_shadow_fraction=float(np.isin(scl,[3,8,9,10]).mean()) if len(scl) else None,
                       scl_nodata_fraction=float((scl==0).mean()) if len(scl) else None,
                       scl_snow_water_fraction=float(np.isin(scl,[6,11]).mean()) if len(scl) else None)
            result[fid].append(row)
    # Comparisons use common valid pixels and fixed first/last sufficiently covered dates.
    comparisons={fid:[] for fid,_,_ in features}
    def pair(a,b,ids,period_id):
        with np.load(D/'sentinel'/a['id']/'indices.npz') as aa,np.load(D/'sentinel'/b['id']/'indices.npz') as bb:
            common=aa['valid']&bb['valid']
            same=bool(a['processing_baseline'] and a['processing_baseline']==b['processing_baseline'])
            row=dict(period=period_id,available=True,before=a['acquired_at_utc'],after=b['acquired_at_utc'],before_id=a['id'],after_id=b['id'],same_processing_baseline=same,before_baseline=a['processing_baseline'],after_baseline=b['processing_baseline'],selection='first and last base-covered dates; no selection by change size',common_coverage=float(common.ravel()[ids['base']].mean()) if len(ids['base']) else 0)
            for name in ['ndvi','ndmi']:
                delta=bb[name]-aa[name]
                stats={key:spectral_stats(delta,common,ix,p['minimum_valid_cells'],p['coverage_min']) for key,ix in ids.items()}
                m=metric({k:v['value'] for k,v in stats.items()},p['stability_tolerances'][name],len(ids['core'])>=10 and stats['core']['usable'])
                # Direction may be stable even when the magnitude varies by >0.05.
                supported=same and m['all_scenario_min'] is not None
                m['direction']='increase' if supported and m['all_scenario_min']>.05 else 'decrease' if supported and m['all_scenario_max']<-.05 else 'small' if supported and max(abs(m['all_scenario_min']),abs(m['all_scenario_max']))<=.05 else 'uncertain'
                if not same:m['robust']=False
                row[name]=m
        return row
    for fid,_,_ in features:
        ids=grids[fid]
        for period in p['periods']:
            eligible=[r for r in result[fid] if r['period']==period['id'] and r.get('ndvi') and r['ndvi']['base'] is not None and r['ndmi']['base'] is not None]
            if len({r['acquired_at_utc'][:10] for r in eligible})<2:comparisons[fid].append(dict(period=period['id'],available=False,reason='Fewer than two sufficiently covered calendar dates'));continue
            row=pair(eligible[0],eligible[-1],ids,period['id'])
            if not row['same_processing_baseline']:
                groups={baseline:[r for r in eligible if r['processing_baseline']==baseline] for baseline in {r['processing_baseline'] for r in eligible}}
                candidates=[group for group in groups.values() if len({r['acquired_at_utc'][:10] for r in group})>=2]
                if candidates:
                    group=max(candidates,key=lambda g:g[-1]['acquired_at_utc'])
                    window=pair(group[0],group[-1],ids,period['id'])
                    window['selection']='latest processing-baseline group with >=2 base-covered calendar dates; first/last scenes in group; no selection by index change'
                    row['same_baseline_comparison']=window
            comparisons[fid].append(row)
    return result,comparisons

def analyze():
    p=protocol();status=read(D/'collection-status.json')
    for key in ['soil','dem','satellite']:
        if status[key]['status']!='ready':raise ValueError('Incomplete source: '+key)
    transform=Transformer.from_crs(4326,32637,always_xy=True)
    features=[(f['properties']['id'],st(shape(f['geometry']),transform.transform,interleaved=False),f['properties']['working_band_m']) for f in read(R/'approximate-areas.geojson')['features']]
    static=static_context(features);history,changes=satellite_context(features)
    for fid in static:
        static[fid]['history']=history[fid];static[fid]['changes']=changes[fid]
        static[fid]['availability']={q['id']:dict(total_full_extent=sum(r['period']==q['id'] and r['source_status']=='ready' for r in history[fid]),base_usable=sum(r['period']==q['id'] and r.get('ndvi') is not None and r['ndvi']['base'] is not None for r in history[fid]),robust_both_indices=sum(r['period']==q['id'] and r.get('ndvi') is not None and r['ndvi']['robust'] and r['ndmi']['robust'] for r in history[fid])) for q in p['periods']}
        for q in p['periods']:
            rows=[r for r in history[fid] if r['period']==q['id'] and r['source_status']=='ready'];av=static[fid]['availability'][q['id']]
            av['full_extent_calendar_days']=len({r['acquired_at_utc'][:10] for r in rows})
            av['base_usable_calendar_days']=len({r['acquired_at_utc'][:10] for r in rows if r.get('ndvi') is not None and r['ndvi']['base'] is not None})
            av['robust_both_calendar_days']=len({r['acquired_at_utc'][:10] for r in rows if r.get('ndvi') is not None and r['ndvi']['robust'] and r['ndmi']['robust']})
    scene_summary=read(D/'satellite.json')['counts']
    output=dict(protocol=p,features=static,summary=dict(fragment_count=len(features),satellite_counts=scene_summary,
        stable_dsm_count=sum(v['dsm']['height']['robust'] for v in static.values()),stable_topsoil_clay_count=sum(v['soil'][0]['sensitivity']['robust'] for v in static.values()),
        no_satellite_core=[k for k,v in static.items() if max((r.get('core_cells',0) for r in history[k]),default=0)<p['minimum_core_cells']],confirmed_boundary=False,used_by_model=False),
        provenance=dict(protocol_sha256=digest(D/'protocol.json'),contours_sha256=digest(R/'approximate-areas.geojson'),collector_sha256=digest(Path(__file__).with_name('farm_context_data.py')),analysis_sha256=digest(__file__),borrowed_spectral_algorithm_sha256=digest(Path(__file__).with_name('open_land_data.py')),soil_manifest_sha256=digest(D/'soil/manifest.json'),dem_manifest_sha256=digest(D/'dem/manifest.json'),satellite_manifest_sha256=digest(D/'satellite.json')))
    dump(D/'summary.json',output);dump(ROOT/'web/farm-reconstruction/context-data.json',output)
    print('Analysis complete',output['summary'],flush=True);return output
