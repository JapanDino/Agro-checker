"""Separate 2024 Sentinel-1 RTC audit. No optical labels, model promotion or visits."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode, urlparse

import numpy as np
from .pipeline import ROOT
from . import sector_refresh as net,triage_outcomes as geometry

D=ROOT/'data/farm-research/sentinel1-2024'
PC='https://planetarycomputer.microsoft.com/api/stac/v1'
SOURCES={
    'pc_rtc':(PC,'sentinel-1-rtc'),
    'earth_grd':('https://earth-search.aws.element84.com/v1','sentinel-1-grd'),
}
DOCS={
    'rtc':'https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-rtc',
    'anonymous_access':'https://planetarycomputer.microsoft.com/docs/concepts/sas/',
    'interference':'https://land.copernicus.eu/en/production-updates/radio-frequency-interference-affecting-surface-soil-moisture-products',
    'quality_disclaimers':'https://sar-mpc.eu/disclaimers/',
}


def stamp():return datetime.now(timezone.utc).isoformat()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,value):net.dump(path,value)
def save_once(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def instant(v):return net.instant(v)


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    source=read(geometry.D/'protocol.json')
    p=dict(created_at_utc=stamp(),selection_years=[2024],start='2024-01-01T00:00:00Z',end_exclusive='2025-01-01T00:00:00Z',
        bbox_wgs84=source['bbox_wgs84'],bounds_utm=source['bounds_utm'],crs='EPSG:32637',cell_m=20,
        geometry_path='data/farm-research/reconstruction/approximate-areas.geojson',
        geometry_sha256=sha(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson'),
        sources=SOURCES,documentation=DOCS,
        acquisition_policy='Exhaust public PC RTC2024 catalogue. Cross-check GRD catalogue separately; no assertion RTC includes all acquisitions. One platform+relative orbit+direction time series. No joining different tracks into a change pair.',
        extraction='Actual raster georeference, not STAC transform. Read VV/VH linear gamma0 RTC. Average linear power to old20m optical grid. Both channels positive finite with>=.8 native support per cell. No speckle smoothing across dates.',
        quality='Same27 approximate outlines and11 scenarios. >=10 cells and>=80% joint valid core. Source hashes, sparse20m crops, per-scenario coverage and medians. Broad high-power/row-jump diagnostics only, not validated RFI screening.',
        rfi='Product-level ESA RFI clearance not provided by PC metadata. Record UNKNOWN for every scene; never treat absence of flag as absence of interference. Exploratory features allowed, production admission prohibited.',
        units='Linear gamma0 power in raw crop; 10log10(median power) in summaries. VHminusVV in dB. Neither NDMI nor measured soil water.',
        preservation='No optical target recovery, no changes to old models/detector, archived prospective registration/releases or human journal. No2025/2026 outcomes in training/selection.',
        code_sha256=sha(__file__))
    save_once(D/'protocol.json',p);return p


def check(p):
    if sha(ROOT/p['geometry_path'])!=p['geometry_sha256'] or sha(__file__)!=p['code_sha256']:
        raise ValueError('Frozen radar extraction code or geometry changed')


def catalogues():
    p=protocol();check(p)
    if (D/'catalogues.json').exists():return read(D/'catalogues.json')
    out={}
    for name,(endpoint,collection) in SOURCES.items():
        url=endpoint+'/search?'+urlencode(dict(collections=collection,bbox=','.join(map(str,p['bbox_wgs84'])),
            datetime=p['start']+'/2024-12-31T23:59:59.999999Z',limit=100))
        seen=set();items={};pages=[]
        for i in range(20):
            if url in seen or not url.startswith(endpoint+'/search?'):raise ValueError('Unsafe/cyclic pagination')
            seen.add(url);raw=net.curl_bytes(url)
            path=D/'catalogue'/name/f'page-{i+1:02d}.json';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw);page=json.loads(raw);pages.append(dict(path=path.relative_to(ROOT).as_posix(),sha256=sha(path)))
            for item in page['features']:
                at=instant(item['properties']['datetime'])
                if item['collection']!=collection or not instant(p['start'])<=at<instant(p['end_exclusive']):raise ValueError('Wrong catalogue year/collection')
                if item['id'] in items and items[item['id']]!=item:raise ValueError('Conflicting duplicate')
                items[item['id']]=item
            nexts=[v for v in page.get('links',[]) if v['rel']=='next']
            if not nexts:break
            if len(nexts)!=1 or not page['features'] or nexts[0].get('method','GET')!='GET':raise ValueError('Invalid continuation')
            url=nexts[0]['href']
        else:raise ValueError('Incomplete catalogue')
        out[name]=dict(items=sorted(items.values(),key=lambda f:(f['properties']['datetime'],f['id'])),pages=pages,pagination_complete=True)
        print(name,len(items),flush=True)
    save_once(D/'catalogues.json',out);return out


def track(item):
    p=item['properties']
    return f"{p['platform'].lower()}:{int(p['sat:relative_orbit'])}:{p['sat:orbit_state']}"


@lru_cache(maxsize=1)
def masks():
    g,shape,tr=geometry.grids();union=np.unique(np.concatenate([ix for variants in g.values() for ix in variants.values()]))
    return {fid:{s:np.searchsorted(union,ix) for s,ix in v.items()} for fid,v in g.items()},union,shape,tr


def summarise(vv,vh,g):
    valid=np.isfinite(vv)&np.isfinite(vh)&(vv>0)&(vh>0)
    fields={}
    for fid,variants in g.items():
        values={};supports={}
        for name,ix in variants.items():
            ok=valid[ix];n=int(ok.sum());coverage=n/len(ix) if len(ix) else 0
            supports[name]=dict(cells=n,total=len(ix),coverage=coverage)
            if n>=10 and coverage>=.8:
                a,b=vv[ix][ok],vh[ix][ok];x,y=10*np.log10(np.median(a)),10*np.log10(np.median(b))
                values[name]=dict(vv_db=float(x),vh_db=float(y),ratio_db=float(y-x),coverage=coverage,
                    vv_iqr_db=float(np.diff(np.percentile(10*np.log10(a),[25,75]))[0]),
                    vh_iqr_db=float(np.diff(np.percentile(10*np.log10(b),[25,75]))[0]))
        fields[fid]=dict(values=values,support=supports)
    return fields


def signed_assets(items):
    """Anonymous public SAS service; tokens stay in memory, never manifests/logs."""
    hosts={urlparse(item['assets']['vv']['href']).hostname for item in items}
    if hosts!={'sentinel1euwestrtc.blob.core.windows.net'}:raise ValueError('Unexpected RTC host')
    t=json.loads(net.curl_bytes('https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel1euwestrtc/sentinel1-grd-rtc'))
    if instant(t['msft:expiry'])<=datetime.now(timezone.utc):raise ValueError('Expired anonymous access token')
    return t['token']


def collect_scene(item,token):
    import certifi
    import rasterio
    from rasterio.windows import from_bounds,Window
    from rasterio.warp import reproject,transform_bounds,Resampling
    folder=D/'scenes'/item['id'];result_path=folder/'summary.json'
    if result_path.exists():
        value=read(result_path)
        if value.get('crop_sha256') and sha(folder/'radar.npz')!=value['crop_sha256']:raise ValueError('Changed cached radar crop')
        return value
    folder.mkdir(parents=True,exist_ok=True);save_once(folder/'item.json',item)
    props=item['properties'];out=dict(id=item['id'],at=props['datetime'],track=track(item),
        rfi_clearance='unknown',item_sha256=sha(folder/'item.json'),status='not_collected',fields={})
    if props.get('sar:instrument_mode')!='IW' or not {'vv','vh'}<=item['assets'].keys():
        out['status']='unsupported_mode_or_channels';save_once(result_path,out);return out
    g,union,shape,tr=masks();bounds=protocol()['bounds_utm'];arrays={};sources={}
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_CAINFO=certifi.where(),
                          GDAL_HTTP_TIMEOUT='35',GDAL_HTTP_MAX_RETRY='1',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tiff,.tif'):
            for name in ('vv','vh'):
                href=item['assets'][name]['href'];parsed=urlparse(href)
                if parsed.scheme!='https' or parsed.hostname!='sentinel1euwestrtc.blob.core.windows.net' or parsed.query:
                    raise ValueError('Unregistered raster host')
                with rasterio.open(href+'?'+token) as src:
                    box=transform_bounds('EPSG:32637',src.crs,*bounds,densify_pts=21)
                    window=from_bounds(*box,src.transform)
                    c0,r0=math.floor(window.col_off),math.floor(window.row_off)
                    window=Window(c0,r0,math.ceil(window.col_off+window.width)-c0,math.ceil(window.row_off+window.height)-r0)
                    if window.width*window.height>12_000_000:raise ValueError('Oversized chip')
                    raw=src.read(1,window=window,boundless=True,fill_value=src.nodata if src.nodata is not None else -32768)
                    good=np.isfinite(raw)&(raw>0)&(raw!=src.nodata)
                    power=np.where(good,raw,np.nan).astype('float32');dest=np.full(shape,np.nan,'float32');support=np.zeros(shape,'float32')
                    common=dict(src_transform=src.window_transform(window),src_crs=src.crs,dst_transform=tr,dst_crs='EPSG:32637',resampling=Resampling.average)
                    reproject(power,dest,src_nodata=np.nan,dst_nodata=np.nan,**common)
                    reproject(good.astype('float32'),support,dst_nodata=0,**common)
                    dest[support<.8]=np.nan;arrays[name]=dest.ravel()[union]
                    sources[name]=dict(href=href,crs=str(src.crs),transform=list(src.transform),nodata=src.nodata,
                        dtype=src.dtypes[0],crop_window=list(window.flatten()),unit='linear_gamma0',
                        acquisition_raster_tag=src.tags().get('Acquisition_DateTime'))
        np.savez_compressed(folder/'radar.npz',positions=union,vv=arrays['vv'],vh=arrays['vh'])
        out.update(status='ready',sources=sources,crop_sha256=sha(folder/'radar.npz'),
                   fields=summarise(arrays['vv'],arrays['vh'],g),received_at_utc=stamp(),
                   diagnostics=dict(high_power_fraction=float(np.mean((arrays['vv']>10)|(arrays['vh']>10))),
                       meaning='Descriptive only; not a validated RFI test'))
    except Exception as e:
        # GDAL exceptions may contain signed URLs; never persist their text.
        out.update(status='extraction_error',error_type=type(e).__name__,error='Asset extraction failed; no fabricated values')
    save_once(result_path,out);return out


def collect(workers=3):
    p=protocol();check(p)
    if (D/'satellite.json').exists():return read(D/'satellite.json')
    cat=catalogues();items=cat['pc_rtc']['items'];masks();token=signed_assets(items);results=[]
    with ThreadPoolExecutor(workers) as pool:
        futures={pool.submit(collect_scene,item,token):item['id'] for item in items}
        for future in as_completed(futures):
            v=future.result();results.append(v);print(v['id'],v['status'],flush=True)
    from collections import Counter
    result=dict(created_at_utc=stamp(),protocol_sha256=sha(D/'protocol.json'),catalogue_sha256=sha(D/'catalogues.json'),
        scenes=sorted(results,key=lambda v:(v['at'],v['id'])),counts=dict(Counter(v['status'] for v in results)))
    save_once(D/'satellite.json',result);return result
