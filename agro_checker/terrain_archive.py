"""Same-object GFS surface height for a separate climate experiment."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta
import json
import math
from .pipeline import ROOT,UTC,cached_download,write_json
from .gfs_archive import object_url,range_download
from .gfs_covariates import field_range,check_object_version
from .product_data import sha
from .regional_data import dates

DIRECTORY=ROOT/'data/processed/climate-minimum'
SPEC={'terrain':{'index_parameter':'HGT','index_level':'surface'}}


def fetch_day(job):
    source,d,offline=job;run=datetime(d.year,d.month,d.day,6,tzinfo=UTC);url=object_url(run,10)
    original=source/'raw/gfs-single'/run.strftime('%Y%m%d%H')/'f010'
    index,_=cached_download(url+'.idx',original.with_suffix('.idx'),True)
    reference=json.loads(original.with_name(original.name+'.grib2.meta.json').read_text(encoding='utf-8'))
    start,end=field_range(index,run,10,'terrain',SPEC)
    path=DIRECTORY/'raw'/run.strftime('%Y%m%d%H')/'f010_terrain.grib2';raw,m=range_download(url,path,start,end,offline)
    check_object_version(m,reference)
    if datetime.fromisoformat(m['archive_last_modified_at_utc'])+timedelta(minutes=10)>run+timedelta(hours=9):raise ValueError('Terrain unavailable by issue proxy')
    return d,raw,m,reference


def decode(raw,d,sites):
    import eccodes as ec
    h=ec.codes_new_from_message(raw)
    try:
        expected={'typeOfLevel':'surface','level':0,'parameterCategory':3,'parameterNumber':5,'discipline':0,'stepType':'instant','centre':'kwbc',
                  'dataDate':int(d.strftime('%Y%m%d')),'dataTime':600,'startStep':10,'endStep':10,'validityDate':int(d.strftime('%Y%m%d')),'validityTime':1600,
                  'gridType':'regular_ll','iDirectionIncrementInDegrees':.25,'jDirectionIncrementInDegrees':.25,
                  'latitudeOfFirstGridPointInDegrees':90.,'longitudeOfFirstGridPointInDegrees':0.,'Ni':1440,'Nj':721}
        if any(ec.codes_get(h,k)!=v for k,v in expected.items()):raise ValueError('Terrain GRIB schema/time/grid mismatch')
        units=ec.codes_get(h,'units');short=ec.codes_get(h,'shortName')
        if units not in ['m','gpm'] or short not in ['orog','gh']:raise ValueError('Unknown surface-height units/identity')
        pts=ec.codes_grib_find_nearest_multiple(h,False,[s['latitude'] for s in sites],[s['longitude'] for s in sites]);out={}
        for s,pt in zip(sites,pts):
            if ec.codes_get(h,'bitmapPresent') and not ec.codes_get_element(h,'bitmap',pt['index']):raise ValueError('Missing terrain')
            value=float(pt['value'])
            if not math.isfinite(value) or not -500<=value<=9000:raise ValueError('Invalid surface height')
            out[s['id']]={'gfs_surface_height':value,'units':units,'short_name':short,'grid_latitude':float(pt['lat']),'grid_longitude':float(pt['lon']),
                          'grid_distance_km':float(pt['distance']),'station_elevation_m':s['elevation_m']}
        return out
    finally:ec.codes_release(h)


def collect(controls=False,offline=False):
    file=DIRECTORY/('terrain-controls.json' if controls else 'terrain-prior.json')
    meta=json.loads((ROOT/'data/processed/regional-minimum/dataset.meta.json').read_text(encoding='utf-8'));sites=meta['sites']
    if controls:
        jobs=[(ROOT/'data' if y==2025 else ROOT/'data/scenario-2026',date(y,3,1)+timedelta(days=i),offline) for y in [2025,2026] for i in range(92)]
        sites=[s for s in sites if s['role']=='reference']
    else:jobs=[(ROOT/'data',d,offline) for y in [2022,2023,2024] for d in dates(y)]
    records={};manifest={};done=0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for d,raw,m,ref in pool.map(fetch_day,jobs):
            records[d.isoformat()]=decode(raw,d,sites);manifest[d.isoformat()]={'terrain':m,'temperature_object':ref};done+=1
            if done%30==0:print('Same-object terrain',done,'/',len(jobs),flush=True)
    result={'records':records,'manifest':manifest,'source_sha256':sha(ROOT/'agro_checker/terrain_archive.py'),
            'regional_meta_sha256':sha(ROOT/'data/processed/regional-minimum/dataset.meta.json'),'years':[2025,2026] if controls else [2022,2023,2024]}
    write_json(file,result);write_json(file.with_name(file.name+'.meta.json'),{'sha256':sha(file)});return result


def load(controls=False):
    path=DIRECTORY/('terrain-controls.json' if controls else 'terrain-prior.json');m=json.loads(path.with_name(path.name+'.meta.json').read_text(encoding='utf-8'))
    if sha(path)!=m['sha256']:raise ValueError('Terrain artifact changed')
    r=json.loads(path.read_text(encoding='utf-8'))
    if r['source_sha256']!=sha(ROOT/'agro_checker/terrain_archive.py') or r['regional_meta_sha256']!=sha(ROOT/'data/processed/regional-minimum/dataset.meta.json'):raise ValueError('Terrain source/regional metadata changed')
    return r,m


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--controls',action='store_true');p.add_argument('--offline',action='store_true');a=p.parse_args()
    r=collect(a.controls,a.offline);print('Terrain saved:',len(r['records']), 'nights; units',sorted({v['units'] for day in r['records'].values() for v in day.values()}))
