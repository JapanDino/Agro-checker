"""Separate wider-station archive. Frozen parent caches are read, never rewritten."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import datetime, timedelta
import csv
import io
import json
import math
from .pipeline import ROOT, UTC, cached_download, write_json
from .product_data import parse_isd, dates, night_times, sha
from .gfs_covariates import FIELDS
from .humidity_archive import RH_FIELDS

DIRECTORY = ROOT/'data/processed/regional-minimum'
PROTOCOL = ROOT/'config/regional_minimum.json'


def role(sid):
    import hashlib
    return 'reference' if sid=='349290-99999' else 'heldout_site' if int(hashlib.sha256(sid.encode()).hexdigest(),16)%4==0 else 'development'


def audit(offline=False):
    p=json.loads(PROTOCOL.read_text(encoding='utf-8'));lo,hi,west,east=p['bbox']
    raw,meta=cached_download('https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv',ROOT/'data/processed/product-stations/raw/isd-history.csv',True)
    sites=[]
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        try:lat,lon,elev=map(float,[r['LAT'],r['LON'],r['ELEV(M)']])
        except (ValueError,TypeError):continue
        if lo<=lat<=hi and west<=lon<=east and r['BEGIN']<='20220101' and r['END']>='20240531':
            sid=r['USAF']+'-'+r['WBAN'];sites.append({'id':sid,'name':r['STATION NAME'],'latitude':lat,'longitude':lon,'elevation_m':elev,'role':role(sid),'years':{}})
    write_json(DIRECTORY/'inventory-before-observations.json',{'sites':sites,'protocol_sha256':sha(PROTOCOL),'inventory':meta,'saved_at_utc':datetime.now(UTC).isoformat()})
    def fetch(job):
        s,y=job;sid=s['id'];url=f'https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/{y}/{sid}-{y}.gz'
        old=ROOT/'data/processed/product-stations/raw'/f'{sid}-{y}.gz';path=old if old.exists() else DIRECTORY/'raw'/f'{sid}-{y}.gz'
        if offline and not path.exists():return sid,str(y),{'complete_nights':0,'incomplete_nights':92,'error':'observation file absent in offline cache; no filling'}
        try:
            raw,m=cached_download(url,path,offline);obs=parse_isd(raw,y)
            full=sum(all(obs.get(t.isoformat(),{}).get('temperature_c') is not None for t in night_times(d)) for d in dates(y))
            return sid,str(y),{'complete_nights':full,'incomplete_nights':92-full,'path':str(path.relative_to(ROOT)),'provenance':m}
        except OSError as exc:return sid,str(y),{'complete_nights':0,'incomplete_nights':92,'error':str(exc)}
    index={s['id']:s for s in sites};done=0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sid,y,r in pool.map(fetch,[(s,y) for s in sites for y in p['years']]):
            index[sid]['years'][y]=r;done+=1
            if done%30==0:print('Station years audited',done,'/',len(sites)*3,flush=True)
    for s in sites:
        s['eligible']=all(s['years'][str(y)]['complete_nights']>=p['minimum_complete_nights_per_year'] for y in p['years'])
        s['exclusion']=None if s['eligible'] else 'fewer than 30 complete nights in at least one prior spring; no filling'
    fallback=[]
    if not any(s['eligible'] and s['role']=='heldout_site' for s in sites):
        import hashlib
        previously_used={'349290-99999','370010-99999','371710-99999'}
        unused=sorted((s for s in sites if s['eligible'] and s['id'] not in previously_used),key=lambda s:hashlib.sha256(s['id'].encode()).hexdigest())
        for s in unused[:2]:s['role']='heldout_site';fallback.append(s['id'])
    out={'stations':sites,'inventory':meta,'protocol_sha256':sha(PROTOCOL),'source_sha256':sha(ROOT/'agro_checker/regional_data.py'),
         'metadata_only_holdout_fallback':fallback,
         'eligibility_uses':'completeness only, not cold count or forecast errors','observations':'NOAA ISD-Lite; rounded hourly times, detailed QC flags unavailable; not orchard truth',
         'generated_at_utc':datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'station-audit.json',out);return out


def validate_field(ec,h,spec,run,t):
    expected={k:spec[k] for k in ['shortName','typeOfLevel','level','units']}
    expected.update(stepType='instant',dataDate=int(run.strftime('%Y%m%d')),dataTime=600,validityDate=int(t.strftime('%Y%m%d')),validityTime=t.hour*100,gridType='regular_ll',iDirectionIncrementInDegrees=.25,jDirectionIncrementInDegrees=.25)
    if any(ec.codes_get(h,k)!=v for k,v in expected.items()):raise ValueError('GFS schema/run/horizon/grid mismatch')


def decode_day(job):
    import eccodes as ec
    d,sites=job;run=datetime(d.year,d.month,d.day,6,tzinfo=UTC);issue=run+timedelta(hours=9)
    fields=[('temperature','gfs-single','',{'shortName':'2t','typeOfLevel':'heightAboveGround','level':2,'units':'K'})]
    fields += [(key,'gfs-covariates','_'+key,spec) for key,spec in FIELDS.items()]
    fields += [('rh','gfs-humidity','_rh',RH_FIELDS['rh'])]
    curves={s['id']:{key:[] for key,*_ in fields} for s in sites};manifest={};distance={s['id']:0. for s in sites}
    for t in night_times(d):
        lead=int((t-run).total_seconds()/3600);object_version=None;grids={s['id']:None for s in sites}
        for key,folder,suffix,spec in fields:
            path=ROOT/'data/raw'/folder/run.strftime('%Y%m%d%H')/f'f{lead:03}{suffix}.grib2';raw=path.read_bytes();m=json.loads(path.with_name(path.name+'.meta.json').read_text(encoding='utf-8'))
            if sha(path)!=m['sha256']:raise ValueError('GFS bytes changed')
            if datetime.fromisoformat(m['archive_last_modified_at_utc'])+timedelta(minutes=10)>issue:raise ValueError('GFS unavailable by issue proxy')
            version=tuple(m[k] for k in ['url','etag','archive_last_modified_at_utc'])
            if object_version is not None and version!=object_version:raise ValueError('Mixed GFS objects')
            object_version=version;manifest[str(path.relative_to(ROOT))]=m;h=ec.codes_new_from_message(raw)
            try:
                validate_field(ec,h,spec,run,t)
                points=ec.codes_grib_find_nearest_multiple(h,False,[s['latitude'] for s in sites],[s['longitude'] for s in sites])
                for s,point in zip(sites,points):
                    if ec.codes_get(h,'bitmapPresent') and ec.codes_get_element(h,'bitmap',point['index'])==0:raise ValueError('Missing grid point')
                    grid=(float(point['lat']),float(point['lon']))
                    if grids[s['id']] is not None and grid!=grids[s['id']]:raise ValueError('Different feature grids')
                    grids[s['id']]=grid;v=float(point['value'])-(273.15 if spec['units']=='K' else 0)
                    if not math.isfinite(v):raise ValueError('Nonfinite forecast')
                    curves[s['id']][key].append(v);distance[s['id']]=max(distance[s['id']],float(point['distance']))
            finally:ec.codes_release(h)
    return d,curves,manifest,distance


def extract():
    a=json.loads((DIRECTORY/'station-audit.json').read_text(encoding='utf-8'))
    if a['source_sha256']!=sha(ROOT/'agro_checker/regional_data.py') or a['protocol_sha256']!=sha(PROTOCOL):raise ValueError('Audit sources changed')
    sites=[s for s in a['stations'] if s['eligible']];obs={}
    for s in sites:
        obs[s['id']]={}
        for info in s['years'].values():
            path=ROOT/info['path']
            if sha(path)!=info['provenance']['sha256']:raise ValueError('Observation bytes changed')
            obs[s['id']].update(parse_isd(path.read_bytes(),int(path.stem[-4:])))
    partitions={'development':[],'heldout_site':[]};manifest={};grid_distance={s['id']:0. for s in sites}
    jobs=[(d,sites) for y in [2022,2023,2024] for d in dates(y)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for done,(d,curves,day_manifest,distance) in enumerate(pool.map(decode_day,jobs),1):
            y=d.year;run=datetime(d.year,d.month,d.day,6,tzinfo=UTC);issue=run+timedelta(hours=9);manifest.update(day_manifest)
            for sid,value in distance.items():grid_distance[sid]=max(grid_distance[sid],value)
            for s in sites:
                n={**{k:s[k] for k in ['id','name','role','latitude','longitude','elevation_m']},'date':d.isoformat(),'year':y,'run_at_utc':run.isoformat(),'issued_at_utc':issue.isoformat(),
                   'times':[t.isoformat() for t in night_times(d)],'gfs':curves[s['id']],
                   'target_temperature_c':[obs[s['id']].get(t.isoformat(),{}).get('temperature_c') for t in night_times(d)]}
                partitions['heldout_site' if s['role']=='heldout_site' else 'development'].append(n)
            if done%14==0 or d.day==1:print('Wider GFS decoded',d,'nights',done,'/276','sites',len(sites),flush=True)
    hashes={}
    for partition,records in partitions.items():
        path=DIRECTORY/(partition+'.json');write_json(path,records);hashes[partition]=sha(path)
    write_json(DIRECTORY/'gfs-manifest.json',manifest)
    m={'partitions_sha256':hashes,'audit_sha256':sha(DIRECTORY/'station-audit.json'),'manifest_sha256':sha(DIRECTORY/'gfs-manifest.json'),
       'source_sha256':sha(ROOT/'agro_checker/regional_data.py'),'protocol_sha256':sha(PROTOCOL),'records':{k:len(v) for k,v in partitions.items()},
       'sites':[{k:s[k] for k in ['id','name','role','latitude','longitude','elevation_m']} for s in sites],'max_grid_distance_km':grid_distance}
    write_json(DIRECTORY/'dataset.meta.json',m);return m


def load(partition='development'):
    if partition not in ['development','heldout_site']:raise ValueError('Unknown partition')
    m=json.loads((DIRECTORY/'dataset.meta.json').read_text(encoding='utf-8'))
    if m['source_sha256']!=sha(ROOT/'agro_checker/regional_data.py') or m['protocol_sha256']!=sha(PROTOCOL):raise ValueError('Regional source/protocol changed')
    for path,expected in [(DIRECTORY/(partition+'.json'),m['partitions_sha256'][partition]),(DIRECTORY/'station-audit.json',m['audit_sha256']),(DIRECTORY/'gfs-manifest.json',m['manifest_sha256'])]:
        if sha(path)!=expected:raise ValueError('Regional data changed')
    return json.loads((DIRECTORY/(partition+'.json')).read_text(encoding='utf-8')),m


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--audit',action='store_true');parser.add_argument('--extract',action='store_true');parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    if args.audit:
        a=audit(args.offline);print('Eligible',[(s['id'],s['name'],s['role']) for s in a['stations'] if s['eligible']],flush=True)
    if args.extract:print(json.dumps(extract(),ensure_ascii=False))
