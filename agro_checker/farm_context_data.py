"""Independent public-data snapshot for UNCONFIRMED screenshot fragments.

No operational state, forecast/model, or previous land snapshot is written here.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window
from rasterio.warp import reproject, Resampling
from pyproj import Transformer, CRS
from .pipeline import ROOT, cached_download
from .open_land_data import SOIL_CRS, soil_value, reflectance, to_twenty, indices
from .sentinel_updates import checked_url, head

D=ROOT/'data/farm-research/area-context'
R=ROOT/'data/farm-research/reconstruction'
UTC=timezone.utc

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,o):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(o,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')

def get(url,path):
    def fetch(u):
        with urlopen(Request(u,headers={'User-Agent':'Onlyfarms-unconfirmed-area-context/1.0'}),timeout=45) as r:return r.read(),dict(r.headers)
    return cached_download(url,Path(path),fetcher=fetch)

def protocol():
    path=D/'protocol.json'
    if path.exists():
        p=read(path)
        if p['contours_sha256']!=digest(R/'approximate-areas.geojson'):raise ValueError('Contours changed; new snapshot required')
        return p
    # Expanded extent encloses the largest working band and the cloud/edge guard.
    g=read(R/'approximate-areas.geojson');coords=np.array([xy for f in g['features'] for xy in f['geometry']['coordinates'][0]])
    t=Transformer.from_crs(4326,32637,always_xy=True)
    xy=np.array(t.transform(*coords.T)).T
    xmin,ymin=xy.min(axis=0)-350;xmax,ymax=xy.max(axis=0)+350
    bounds=[math.floor(xmin/20)*20,math.floor(ymin/20)*20,math.ceil(xmax/20)*20,math.ceil(ymax/20)*20]
    inv=Transformer.from_crs(32637,4326,always_xy=True)
    corners=np.array(inv.transform([bounds[0],bounds[2],bounds[2],bounds[0]],[bounds[1],bounds[1],bounds[3],bounds[3]])).T
    end=datetime.now(UTC);start=end-timedelta(days=90)
    p=dict(schema=1,created_at_utc=end.isoformat(),contours_sha256=digest(R/'approximate-areas.geojson'),confirmed_boundary=False,used_by_model=False,
           bounds_utm=bounds,bbox_wgs84=[*corners.min(axis=0),*corners.max(axis=0)],
           periods=[dict(id='spring2024',label='Весна 2024',start='2024-03-01T00:00:00Z',end='2024-05-31T23:59:59Z'),dict(id='recent2026',label='Последние 90 дней 2026',start=start.isoformat(),end=end.isoformat())],
           collection='sentinel-2-c1-l2a',satellite_cell_m=20,coverage_min=.8,minimum_valid_cells=10,minimum_core_cells=10,
           shifts='base plus eight radial shifts of 50/100m, diagonals normalized; separate erosion/dilation checks',
           stability_tolerances=dict(dsm_median_m=2,slope_p90_deg=1,clay=3,sand=3,silt=3,phh2o=.2,soc=5,ndvi=.05,ndmi=.05),
           policy='Sensitivity scenarios, not a calibrated confidence interval or agronomic diagnosis. No interpolation, training or operational writes.')
    dump(path,p);return p

def soil():
    p=protocol();folder=D/'soil';folder.mkdir(parents=True,exist_ok=True)
    xml=ROOT/'data/working-zone-01/open-land/raw/soilgrids-clay-description.xml'
    tree=ET.fromstring(xml.read_bytes());ns={'g':'http://www.opengis.net/gml/3.2'}
    origin=[float(v) for v in tree.find('.//g:origin/g:Point/g:pos',ns).text.split()]
    vectors=[[float(v) for v in n.text.split()] for n in tree.findall('.//g:offsetVector',ns)]
    if vectors!=[[250.,0.],[0.,-250.]]:raise ValueError('Soil native grid changed')
    left0,top0=origin[0]-125,origin[1]+125
    # Densify the UTM extent edges before changing to Homolosine.
    x0,y0,x1,y1=p['bounds_utm'];v=np.linspace(0,1,101)
    xs=np.r_[x0+v*(x1-x0),x0+v*(x1-x0),np.full(101,x0),np.full(101,x1)]
    ys=np.r_[np.full(101,y0),np.full(101,y1),y0+v*(y1-y0),y0+v*(y1-y0)]
    xx,yy=Transformer.from_crs(32637,SOIL_CRS,always_xy=True).transform(xs,ys)
    c0=math.floor((min(xx)-left0)/250);c1=math.ceil((max(xx)-left0)/250)
    r0=math.floor((top0-max(yy))/250);r1=math.ceil((top0-min(yy))/250)
    left,right=left0+c0*250,left0+c1*250;top,bottom=top0-r0*250,top0-r1*250
    def one(job):
        prop,depth,stat=job;key=f'{prop}_{depth}_{stat}'
        url='https://maps.isric.org/mapserv?'+urlencode([('map',f'/map/{prop}.map'),('SERVICE','WCS'),('VERSION','2.0.1'),('REQUEST','GetCoverage'),('COVERAGEID',key),('SUBSET',f'X({left:.1f},{right:.1f})'),('SUBSET',f'Y({bottom:.1f},{top:.1f})'),('FORMAT','GEOTIFF_INT16')])
        path=folder/(key+'.tif');_,meta=get(url,path)
        with rasterio.open(path) as r:
            if r.shape!=(r1-r0,c1-c0) or not CRS.from_user_input(r.crs).equals(SOIL_CRS,ignore_axis_order=True):raise ValueError('Soil native extent mismatch')
            if not np.allclose(list(r.transform)[:6],[250,0,left,0,-250,top],atol=.02,rtol=0):raise ValueError('Soil misaligned')
            raw=r.read(1,masked=True)
            for value in raw.compressed():soil_value(prop,float(value))
        print('soil ready',key,flush=True)
        return dict(property=prop,depth=depth,statistic=stat,path=str(path.relative_to(ROOT)),source=meta)
    jobs=[(a,b,c) for a in ['clay','sand','silt','phh2o','soc'] for b in ['0-5cm','15-30cm'] for c in ['mean','Q0.05','Q0.95']]
    with ThreadPoolExecutor(max_workers=3) as pool:layers=list(pool.map(one,jobs))
    result=dict(layers=layers,native_cell_m=250,shape=[r1-r0,c1-c0],description_path=str(xml.relative_to(ROOT)),description_sha256=digest(xml),source_type='external soil model, not field measurement')
    dump(folder/'manifest.json',result);return result

def dem():
    p=protocol();folder=D/'dem';folder.mkdir(parents=True,exist_ok=True)
    west,south,east,north=p['bbox_wgs84'];sources=[]
    for lat in range(math.floor(south),math.floor(north)+1):
        for lon in range(math.floor(west),math.floor(east)+1):
            key=f'Copernicus_DSM_COG_10_N{lat:02}_00_E{lon:03}_00_DEM'
            url=f'https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{key}/{key}.tif'
            if lat==45 and lon==37:
                path=ROOT/'data/working-zone-01/open-land/raw/dem-glo30-n45e037.tif';meta=read(path.with_name(path.name+'.meta.json'))
                if meta['url']!=url or digest(path)!=meta['sha256']:raise ValueError('Existing DEM integrity')
            else:
                path=folder/(key+'.tif');_,meta=get(url,path)
            sources.append(dict(path=str(path.relative_to(ROOT)),source=meta))
            print('DEM source ready',lat,lon,flush=True)
    x0,y0,x1,y1=p['bounds_utm'];left=math.floor(x0/30)*30;top=math.ceil(y1/30)*30
    width=math.ceil((x1-left)/30);height=math.ceil((top-y0)/30);tr=from_origin(left,top,30,30)
    out=np.full((height,width),np.nan,dtype='float32')
    for item in sources:
        with rasterio.open(ROOT/item['path']) as r:
            part=np.full_like(out,np.nan)
            reproject(rasterio.band(r,1),part,src_transform=r.transform,src_crs=r.crs,dst_transform=tr,dst_crs='EPSG:32637',src_nodata=r.nodata,dst_nodata=np.nan,resampling=Resampling.bilinear)
            valid=np.isfinite(part);out[valid]=part[valid]
    slope=np.degrees(np.arctan(np.hypot(*np.gradient(out,30,30))))
    profile=dict(driver='GTiff',height=height,width=width,count=1,dtype='float32',crs='EPSG:32637',transform=tr,nodata=np.nan,compress='deflate')
    for name,a in [('surface',out),('slope',slope)]:
        with rasterio.open(folder/(name+'.tif'),'w',**profile) as dst:dst.write(a,1)
    result=dict(sources=sources,analysis_cell_m=30,shape=[height,width],surface_sha256=digest(folder/'surface.tif'),slope_sha256=digest(folder/'slope.tif'),native_type='DSM including vegetation/buildings; analysis resampled bilinear, not added detail')
    dump(folder/'manifest.json',result);return result

def catalogue():
    p=protocol();folder=D/'catalogue';items={};pages=[]
    for period in p['periods']:
        url='https://earth-search.aws.element84.com/v1/search?'+urlencode(dict(collections=p['collection'],bbox=','.join(map(str,p['bbox_wgs84'])),datetime=period['start']+'/'+period['end'],limit=100))
        seen=set()
        for number in range(20):
            checked_url(url,'catalogue')
            if url in seen:raise ValueError('Catalogue cycle')
            seen.add(url);raw,meta=get(url,folder/f"{period['id']}-{number+1}.json");obj=json.loads(raw);pages.append(meta)
            for f in obj['features']:
                if f['collection']!=p['collection']:raise ValueError('Wrong collection')
                dt=datetime.fromisoformat(f['properties']['datetime'].replace('Z','+00:00'))
                if not datetime.fromisoformat(period['start'].replace('Z','+00:00'))<=dt<=datetime.fromisoformat(period['end'].replace('Z','+00:00')):raise ValueError('Wrong acquisition period')
                sid=f['id']
                if not re.fullmatch(r'S2[ABC]_T\d{2}[A-Z]{3}_\d{8}T\d{6}_L2A',sid):raise ValueError('Unregistered scene identifier')
                if sid in items and items[sid]['item']!=f:raise ValueError('Conflicting STAC duplicate')
                items[sid]=dict(period=period['id'],item=f)
            nxt=[l for l in obj.get('links',[]) if l['rel']=='next']
            if not nxt or not obj['features']:break
            if len(nxt)!=1 or nxt[0].get('method','GET')!='GET':raise ValueError('Unsupported pagination')
            url=nxt[0]['href']
        else:raise ValueError('Incomplete catalogue')
    result=dict(items=sorted(items.values(),key=lambda a:a['item']['properties']['datetime']),pages=pages,pagination_completed=True)
    dump(D/'catalogue.json',result);print('Catalogue complete',len(items),flush=True);return result

def scene(entry):
    import certifi
    p=protocol();f=entry['item'];sid=f['id']
    if not re.fullmatch(r'S2[ABC]_T\d{2}[A-Z]{3}_\d{8}T\d{6}_L2A',sid):raise ValueError('Unregistered scene identifier')
    folder=D/'sentinel'/sid;manifest=folder/'manifest.json'
    item_hash=hashlib.sha256(json.dumps(f,sort_keys=True).encode()).hexdigest()
    if manifest.exists():
        result=read(manifest)
        if result['item_sha256']!=item_hash:raise ValueError('Changed cached scene')
        for name,h in result['crop_sha256'].items():
            if digest(folder/name)!=h:raise ValueError('Changed cached crop')
        return result
    baseline=f['properties']['s2:processing_baseline']
    if float(baseline)<5 or baseline=='05.09' or f['properties'].get('earthsearch:boa_offset_applied') is True:raise ValueError('Ambiguous/withdrawn processing')
    folder.mkdir(parents=True,exist_ok=True);dump(folder/'item.json',f)
    x0,y0,x1,y1=p['bounds_utm'];width=int((x1-x0)/20);height=int((y1-y0)/20);common_tr=from_origin(x0,y1,20,20)
    arrays={};valids=[];sources={}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_CAINFO=certifi.where(),GDAL_HTTP_TIMEOUT='30',GDAL_HTTP_MAX_RETRY='1'):
        for key in ['scl','red','nir','swir16']:
            asset=f['assets'][key];url=checked_url(asset['href'],'asset');before=head(url)
            with rasterio.open(url) as r:
                if r.crs.to_epsg()!=32637 or list(r.shape)!=asset['proj:shape'] or not np.allclose(list(r.transform)[:6],asset['proj:transform'][:6],atol=1e-6,rtol=0):raise ValueError('Asset grid metadata mismatch')
                if r.transform.b or r.transform.d or r.transform.a not in [10,20] or r.transform.e!=-r.transform.a:raise ValueError('Unsupported grid')
                factor=int(20/r.transform.a)
                col=(x0-r.transform.c)/r.transform.a;row=(r.transform.f-y1)/r.transform.a
                if abs(col-round(col))>1e-6 or abs(row-round(row))>1e-6:raise ValueError('Unaligned COG')
                if col<0 or row<0 or col+width*factor>r.width or row+height*factor>r.height:
                    return dict(id=sid,period=entry['period'],status='outside_full_grid',acquired_at_utc=f['properties']['datetime'],reason='Tile does not cover full research extent; retained in catalogue, not used for partial-field comparisons')
                win=Window(round(col),round(row),width*factor,height*factor);raw=r.read(1,window=win)
                tr=r.window_transform(win);profile=r.profile.copy();profile.update(width=raw.shape[1],height=raw.shape[0],transform=tr,driver='GTiff',compress='deflate')
                with rasterio.open(folder/(key+'.tif'),'w',**profile) as dst:dst.write(raw,1);dst.scales=r.scales;dst.offsets=r.offsets
                a,valid=to_twenty(raw,factor,r.nodata)
                arrays[key]=a if key=='scl' else reflectance(a,asset,r.scales[0],r.offsets[0]);valids.append(valid)
                sources[key]={**before,'scale':r.scales[0],'offset':r.offsets[0],'declared_full_checksum_not_verified':asset.get('file:checksum')}
            if head(url)!=before:raise ValueError('Remote object changed')
    ndvi,ndmi,valid=indices(arrays['red'],arrays['nir'],arrays['swir16'],arrays['scl'],np.logical_and.reduce(valids))
    np.savez_compressed(folder/'indices.npz',ndvi=ndvi.astype('float32'),ndmi=ndmi.astype('float32'),valid=valid,scl=arrays['scl'].astype('uint8'))
    result=dict(id=sid,period=entry['period'],status='ready',acquired_at_utc=f['properties']['datetime'],processing_baseline=baseline,catalogue_created_at_utc=f['properties'].get('created'),processing_generated_at_utc=f['properties'].get('s2:generation_time'),retrieved_at_utc=datetime.now(UTC).isoformat(),cloud_tile_percent=f['properties'].get('eo:cloud_cover'),item_sha256=item_hash,source_versions=sources,common_transform=list(common_tr)[:6],shape=[height,width],crop_sha256={k:digest(folder/k) for k in ['scl.tif','red.tif','nir.tif','swir16.tif','indices.npz','item.json']})
    dump(manifest,result);print('scene ready',sid,flush=True);return result

def satellite():
    cat=catalogue();results=[]
    def one(entry):
        try:return scene(entry)
        except Exception as exc:
            return dict(id=entry['item']['id'],period=entry['period'],status='extraction_error',acquired_at_utc=entry['item']['properties']['datetime'],reason=f'{type(exc).__name__}: {exc}')
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(one,cat['items']):
            results.append(result)
            if result['status']!='ready':print('scene unavailable',result['id'],result['status'],result.get('reason',''),flush=True)
    out=dict(scenes=results,pagination_completed=True,counts={s:sum(r['status']==s for r in results) for s in ['ready','outside_full_grid','extraction_error']})
    dump(D/'satellite.json',out);return out

def collect():
    protocol();results={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(fn):name for name,fn in [('soil',soil),('dem',dem),('satellite',satellite)]}
        for future in as_completed(jobs):
            name=jobs[future]
            try:results[name]={'status':'ready','result':future.result()}
            except Exception as exc:results[name]={'status':'error','reason':f'{type(exc).__name__}: {exc}'};print(name,'ERROR',results[name]['reason'],flush=True)
    dump(D/'collection-status.json',results);print('Collection completed', {k:v['status'] for k,v in results.items()},flush=True)
    return results
