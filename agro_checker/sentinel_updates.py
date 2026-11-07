"""Versioned Sentinel refresh around the landmark; separate from frozen research data."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .pipeline import ROOT
from .product_data import sha

DIRECTORY = ROOT/'data/working-zone-01/sentinel-updates'
CONFIG = ROOT/'config/sentinel_refresh.json'
UTC = timezone.utc
_mutex = Lock()


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def stamp(value=None): return (value or datetime.now(UTC)).isoformat()


def instant(value):
    t = datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Timestamp must include timezone')
    return t.astimezone(UTC)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')


def checked_url(url, kind):
    p = urlparse(url)
    host = 'earth-search.aws.element84.com' if kind=='catalogue' else 'e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com'
    prefix = '/v1/search' if kind=='catalogue' else '/sentinel-2-c1-l2a/'
    if p.scheme!='https' or p.netloc!=host or p.username or p.password or not p.path.startswith(prefix):
        raise ValueError('Unregistered source URL')
    return url


def fetch_page(url):
    url = checked_url(url,'catalogue')
    with urlopen(Request(url,headers={'User-Agent':'Onlyfarms-Sentinel/1.0'}),timeout=30) as response:
        checked_url(response.url,'catalogue')
        raw = response.read(20_000_001)
        if len(raw)>20_000_000: raise ValueError('Catalogue page too large')
        return raw


def head(url):
    url=checked_url(url,'asset')
    with urlopen(Request(url,method='HEAD',headers={'User-Agent':'Onlyfarms-Sentinel/1.0'}),timeout=30) as r:
        checked_url(r.url,'asset')
        result={'url':url,'etag':r.headers.get('ETag'),'last_modified':r.headers.get('Last-Modified'),'bytes':int(r.headers.get('Content-Length','0'))}
    if not result['etag'] or not result['bytes']: raise ValueError('Missing remote version evidence')
    return result


def catalogue(cfg, start, end, folder, fetch=fetch_page):
    lon,lat=cfg['reference']['longitude'],cfg['reference']['latitude']
    url=cfg['catalogue']+'?'+urlencode({'collections':cfg['collection'],'bbox':f'{lon-.00001},{lat-.00001},{lon+.00001},{lat+.00001}',
                                    'datetime':stamp(start)+'/'+stamp(end),'limit':100})
    items={}; pages=[]; seen=set()
    for number in range(cfg['maximum_pages']):
        checked_url(url,'catalogue')
        if url in seen: raise ValueError('Catalogue pagination cycle')
        seen.add(url);raw=fetch(url);obj=json.loads(raw)
        path=folder/f'catalogue-page-{number+1}.json';path.write_bytes(raw)
        pages.append({'url':url,'path':path.name,'sha256':sha(path),'received_at_utc':stamp()})
        for f in obj['features']:
            t=instant(f['properties']['datetime'])
            if f['collection']!=cfg['collection'] or not start<=t<=end: raise ValueError('Wrong collection or acquisition date')
            sid=f['id']
            if not re.fullmatch(r'S2[ABC]_T37TDK_\d{8}T\d{6}_L2A',sid): raise ValueError('Unexpected landmark tile/scene identity')
            if sid in items and items[sid]!=f: raise ValueError('Conflicting catalogue duplicates')
            items[sid]=f
        nexts=[l for l in obj.get('links',[]) if l['rel']=='next']
        if not nexts or not obj['features']: break
        if len(nexts)!=1 or nexts[0].get('method','GET')!='GET': raise ValueError('Unsupported pagination')
        url=nexts[0]['href']
    else: raise ValueError('Catalogue pagination incomplete')
    return {'items':sorted(items.values(),key=lambda f:f['properties']['datetime']),'pages':pages,'pagination_completed':True}


def verified_object(folder):
    m=read(folder/'manifest.json')
    for name,h in m['files'].items():
        if name not in {'sample.json','map.json','red.tif','nir.tif','swir16.tif','scl.tif','item.json'} or sha(folder/name)!=h:
            raise ValueError('Saved satellite object failed integrity check')
    return read(folder/'sample.json'),read(folder/'map.json')


def extract(f, cfg, directory, algorithm_hash):
    import certifi
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    from pyproj import Transformer
    from .open_land_data import reflectance, to_twenty, indices
    from .land_view import _numbers
    canonical=json.dumps(f,sort_keys=True).encode()
    key=hashlib.sha256(canonical+json.dumps(cfg,sort_keys=True).encode()+algorithm_hash.encode()).hexdigest()
    target=directory/'objects'/key
    if (target/'manifest.json').exists():
        s,_=verified_object(target);return s
    # A failed extraction never becomes a published object, nor overwrites an older version.
    folder=directory/'staging'/(key+'-'+uuid4().hex);folder.mkdir(parents=True)
    dump(folder/'item.json',f)
    baseline=f['properties']['s2:processing_baseline']
    if float(baseline)<cfg['minimum_processing_baseline'] or baseline in cfg['rejected_processing_baselines']:
        raise ValueError('Unregistered/withdrawn processing baseline')
    if f['properties'].get('earthsearch:boa_offset_applied') is True: raise ValueError('Ambiguous already-applied offset')
    grids={};masks=[];versions={};transforms={};ref=cfg['reference'];n=cfg['window_cells']
    x,y=Transformer.from_crs(4326,32637,always_xy=True).transform(ref['longitude'],ref['latitude'])
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_CAINFO=certifi.where(),GDAL_HTTP_TIMEOUT='30',GDAL_HTTP_MAX_RETRY='1'):
        for band in ['scl','red','nir','swir16']:
            asset=f['assets'][band];url=checked_url(asset['href'],'asset');before=head(url)
            with rasterio.open(url) as r:
                if r.crs.to_epsg()!=32637 or list(r.shape)!=asset['proj:shape'] or not np.allclose(tuple(r.transform)[:6],asset['proj:transform'][:6],rtol=0,atol=1e-6):
                    raise ValueError('Asset CRS/shape/transform mismatch')
                if r.transform.b or r.transform.d or r.transform.a not in [10,20] or r.transform.e!=-r.transform.a: raise ValueError('Unsupported native grid')
                if band=='scl':
                    if r.transform.a!=20: raise ValueError('Expected 20m SCL')
                    row,col=r.index(x,y);common=Window(col-n//2,row-n//2,n,n);tr_common=r.window_transform(common)
                factor=int(20/r.transform.a);win=Window(common.col_off*factor,common.row_off*factor,n*factor,n*factor)
                crop=r.read(1,window=win);tr=r.window_transform(win)
                if crop.shape!=(n*factor,n*factor) or not np.allclose([tr.c,tr.f],[tr_common.c,tr_common.f],rtol=0,atol=1e-6): raise ValueError('Unaligned/incomplete band window')
                profile=r.profile.copy();profile.update(width=crop.shape[1],height=crop.shape[0],transform=tr,driver='GTiff')
                with rasterio.open(folder/(band+'.tif'),'w',**profile) as dst:
                    dst.write(crop,1);dst.scales=r.scales;dst.offsets=r.offsets
                a,valid=to_twenty(crop,factor,r.nodata)
                grids[band]=a if band=='scl' else reflectance(a,asset,r.scales[0],r.offsets[0]);masks.append(valid)
                transforms[band]=list(tr)[:6]
                versions[band]={**before,'checked_at_utc':stamp(),'native_scale':r.scales[0],'native_offset':r.offsets[0],
                                'declared_full_checksum_not_verified':asset.get('file:checksum')}
            if head(url)!=before: raise ValueError('Remote asset changed during extraction')
    ndvi,ndmi,valid=indices(grids['red'],grids['nir'],grids['swir16'],grids['scl'],np.logical_and.reduce(masks))
    pp=(slice(n//2-1,n//2+2),slice(n//2-1,n//2+2));count=int(valid[pp].sum());usable=bool(valid[n//2,n//2] and count>=cfg['minimum_valid_point_cells'])
    s={'id':f['id'],'map_key':key,'acquired_at_utc':f['properties']['datetime'],'processing_generated_at_utc':f['properties'].get('s2:generation_time'),
       'catalogue_created_at_utc':f['properties'].get('created'),'retrieved_at_utc':stamp(),'processing_baseline':baseline,
       'point_scl':int(grids['scl'][n//2,n//2]),'point_window_usable':usable,'valid_land_cells_in_point_60m_window':count,
       'context_valid_land_fraction':float(valid.mean()),'cloud_cover_tile_percent':f['properties']['eo:cloud_cover'],
       'spatial_range':{},'extraction_status':'ready','source_versions':versions,'source_transforms':transforms,
       'historic_first_availability_proven':False,'orchard_identity_known':False,'is_agronomic_prediction':False}
    for name,a in [('ndvi',ndvi),('ndmi',ndmi)]:
        vals=a[pp][valid[pp]]
        s[f'point_60m_{name}_median']=float(np.median(vals)) if usable else None
        s['spatial_range'][name]=[float(vals.min()),float(vals.max())] if usable else None
    m={'id':f['id'],'width':n,'height':n,'west_m':tr_common.c-x,'north_m':tr_common.f-y,'cell_m':20,'ndvi':_numbers(ndvi),'ndmi':_numbers(ndmi)}
    dump(folder/'sample.json',s);dump(folder/'map.json',m)
    dump(folder/'manifest.json',{'files':{p.name:sha(p) for p in folder.iterdir() if p.is_file()},'algorithm_sha256':algorithm_hash})
    target.parent.mkdir(parents=True,exist_ok=True);folder.rename(target)
    return s


def failed_scene(f):
    return {'id':f['id'],'acquired_at_utc':f['properties']['datetime'],'processing_generated_at_utc':f['properties'].get('s2:generation_time'),
            'catalogue_created_at_utc':f['properties'].get('created'),'retrieved_at_utc':None,'processing_baseline':f['properties'].get('s2:processing_baseline'),
            'point_scl':None,'point_window_usable':False,'valid_land_cells_in_point_60m_window':None,'context_valid_land_fraction':None,
            'cloud_cover_tile_percent':f['properties'].get('eo:cloud_cover'),'point_60m_ndvi_median':None,'point_60m_ndmi_median':None,
            'spatial_range':{'ndvi':None,'ndmi':None},'extraction_status':'error','map_key':None}


def statistics(rows):
    good=[s for s in rows if s['point_window_usable']];dates=[instant(s['acquired_at_utc']) for s in good]
    return {'catalogue_scenes':len(rows),'usable_point_dates':len(good),'rejected_point_dates':sum(s['extraction_status']=='ready' and not s['point_window_usable'] for s in rows),
            'extraction_error_dates':sum(s['extraction_status']=='error' for s in rows),
            'longest_gap_between_usable_acquisitions_days':max(((b-a).total_seconds()/86400 for a,b in zip(dates,dates[1:])),default=None),
            'latest_acquisition_at_utc':rows[-1]['acquired_at_utc'] if rows else None,'latest_usable_at_utc':good[-1]['acquired_at_utc'] if good else None}


@contextmanager
def update_lock(directory):
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'update.lock').open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except ImportError:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: yield
        finally:
            f.seek(0)
            try:
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            except ImportError:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def refresh(directory=DIRECTORY, now=None, progress=lambda message:None):
    from . import open_land_data
    cfg=read(CONFIG);now=now or datetime.now(UTC)
    if cfg['reference']!={k:open_land_data.protocol()['reference'][k] for k in ['latitude','longitude']} or cfg['orchard_boundary_known']:
        raise ValueError('Landmark identity changed')
    with update_lock(directory):
        start=now-timedelta(days=cfg['lookback_days']);rid=uuid4().hex;folder=directory/'runs'/rid;folder.mkdir(parents=True)
        # Store the actual code/policy bytes used by this extraction version.
        for name,path in [('extractor.py',Path(__file__)),('algorithm.py',open_land_data.SOURCE),('protocol.json',CONFIG)]:
            (folder/name).write_bytes(path.read_bytes())
        algorithm_hash=hashlib.sha256((folder/'extractor.py').read_bytes()+(folder/'algorithm.py').read_bytes()).hexdigest()
        progress('Проверяем каталог за последние 90 дней')
        cat=catalogue(cfg,start,now,folder);dump(folder/'catalogue.json',cat)
        results={};errors={};total=len(cat['items'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(extract,f,cfg,directory,algorithm_hash):f for f in cat['items']}
            for future in as_completed(futures):
                f=futures[future]
                try: results[f['id']]=future.result()
                except Exception as exc:
                    results[f['id']]=failed_scene(f);errors[f['id']]=type(exc).__name__+': '+str(exc)
                progress(f'Проверено съёмок: {len(results)} из {total}')
        rows=[results[f['id']] for f in cat['items']]
        snapshot={'id':rid,'query_start_utc':stamp(start),'query_end_utc':stamp(now),'checked_at_utc':stamp(),
                  'reference':cfg['reference'],'orchard_boundary_known':False,'collection':cfg['collection'],'pagination_completed':True,
                  'scenes':rows,'statistics':statistics(rows),'errors':errors,'model_fit_performed':False,'diagnoses_enabled':False}
        dump(folder/'snapshot.json',snapshot)
        dump(folder/'manifest.json',{'files':{p.name:sha(p) for p in folder.iterdir() if p.is_file()}})
        pointer=directory/('latest-'+rid+'.tmp');dump(pointer,{'run':rid,'manifest_sha256':sha(folder/'manifest.json')})
        pointer.replace(directory/'latest.json')
        return snapshot


@lru_cache(maxsize=2)
def _load(directory, run, manifest_hash):
    directory=Path(directory);folder=directory/'runs'/run
    if sha(folder/'manifest.json')!=manifest_hash: raise ValueError('Snapshot manifest changed')
    manifest=read(folder/'manifest.json')
    for name,h in manifest['files'].items():
        if Path(name).name!=name or sha(folder/name)!=h: raise ValueError('Snapshot artifact changed')
    snapshot=read(folder/'snapshot.json')
    maps={}
    for s in snapshot['scenes']:
        if s.get('map_key'):
            key=s['map_key']
            if not re.fullmatch('[a-f0-9]{64}',key): raise ValueError('Invalid object key')
            sample,m=verified_object(directory/'objects'/key)
            if sample!=s: raise ValueError('Snapshot and scene version disagree')
            maps[key]=m
    return snapshot,maps


def load_latest(directory=DIRECTORY):
    path=directory/'latest.json'
    if not path.exists(): return None,{}
    p=read(path)
    if not re.fullmatch('[a-f0-9]{32}',p['run']): raise ValueError('Invalid snapshot identity')
    return _load(str(directory),p['run'],p['manifest_sha256'])


if __name__=='__main__':
    s=refresh(progress=lambda s:print(s,flush=True));print(json.dumps(s['statistics'],ensure_ascii=False),flush=True)
