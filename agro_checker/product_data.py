"""Audited prior-only regional data; no imputation of sparse station reports."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import csv
import gzip
import hashlib
import io
import json
from .pipeline import ROOT, UTC, cached_download, write_json

YEARS = [2022, 2023, 2024]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_isd(content, year):
    if year not in YEARS: raise ValueError('Only prior2022–2024 observations')
    result = {}
    for line in gzip.decompress(content).decode('ascii').splitlines():
        f = line.split()
        if len(f) != 12: raise ValueError('Unexpected ISD-Lite schema')
        t = datetime(*map(int, f[:4]), tzinfo=UTC)
        if t.year != year or t.isoformat() in result: raise ValueError('Duplicate/incorrect station timestamp')
        temp, dew = int(f[4]), int(f[5])
        result[t.isoformat()] = {'temperature_c': temp/10 if temp != -9999 and -800 <= temp <= 600 else None,
                                'dewpoint_c': dew/10 if dew != -9999 and -1000 <= dew <= 600 else None}
    return result


def dates(year):
    if year not in YEARS: raise ValueError('Prior seasons only')
    return [date(year,3,1)+timedelta(days=i) for i in range(92)]


def night_times(d):
    return [datetime(d.year,d.month,d.day,16,tzinfo=UTC)+timedelta(hours=h) for h in range(14)]


def counts(obs, year):
    full = cold = 0
    for d in dates(year):
        values = [obs.get(t.isoformat(),{}).get('temperature_c') for t in night_times(d)]
        if all(v is not None for v in values): full += 1; cold += min(values) < 0
    return {'expected_nights':92, 'complete_nights':full, 'incomplete_nights':92-full, 'cold_complete_nights':cold}


def audit(directory, offline=False):
    url='https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv'
    raw, meta=cached_download(url,directory/'raw/isd-history.csv',offline)
    candidates=[]
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        try: lat,lon,elev=map(float,[r['LAT'],r['LON'],r['ELEV(M)']])
        except (ValueError,TypeError): continue
        if 43<=lat<=47 and 36<=lon<=42 and r['BEGIN']<='20220101' and r['END']>='20240531':
            sid=r['USAF']+'-'+r['WBAN']
            candidates.append({'id':sid,'name':r['STATION NAME'],'latitude':lat,'longitude':lon,'elevation_m':elev,
              'begin':r['BEGIN'],'end':r['END'],'role':'reference' if sid=='349290-99999' else 'heldout_site' if int(hashlib.sha256(sid.encode()).hexdigest(),16)%4==0 else 'development'})
    def fetch(job):
        station,year=job;sid=station['id'];url=f'https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/{year}/{sid}-{year}.gz'
        try:
            raw,m=cached_download(url,directory/'raw'/f'{sid}-{year}.gz',offline)
            return sid,year,{'counts':counts(parse_isd(raw,year),year),'provenance':m}
        except OSError as exc:return sid,year,{'error':str(exc),'counts':{'complete_nights':0,'cold_complete_nights':0}}
    results={s['id']:{} for s in candidates}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for sid,year,r in pool.map(fetch,[(s,y) for s in candidates for y in YEARS]): results[sid][str(year)]=r
    for s in candidates:
        s['years']=results[s['id']]
        s['eligible']=all(s['years'][str(y)]['counts']['complete_nights']>=30 for y in YEARS)
        s['exclusion']=None if s['eligible'] else 'fewer than30 complete14hour nights in a prior season; no interpolation'
    result={'years':YEARS,'inventory':meta,'stations':candidates,'source_sha256':sha(ROOT/'agro_checker/product_data.py'),
      'station_split':'SHA256(id)%4==0 heldout; original station reference excluded from development; roles fixed before targets',
      'minimum_complete_nights_per_year':30,'observations':'primary NOAA ISD-Lite, QC subset lacks original detailed flags; research only',
      'independence':'Nearby sites/dates are dependent; group entire forecast dates for resampling and crossfit',
      'location_selected':False,'generated_at_utc':datetime.now(UTC).isoformat()}
    write_json(directory/'station-audit.json',result);write_json(ROOT/'docs/PRODUCT_DATA_AUDIT.json',result)
    return result


def extract(directory, source_dir):
    """Decode cached global GFS fields for every eligible station in one pass."""
    import eccodes as ec
    audit_record=json.loads((directory/'station-audit.json').read_text(encoding='utf-8'))
    if audit_record['source_sha256']!=sha(ROOT/'agro_checker/product_data.py'): raise ValueError('Audit code changed; rerun before freeze')
    sites=[s for s in audit_record['stations'] if s['eligible']]
    if not sites: raise ValueError('No eligible hourly stations')
    observations={}
    for s in sites:
        observations[s['id']]={}
        for year in YEARS:
            file=directory/'raw'/f"{s['id']}-{year}.gz"
            if sha(file)!=s['years'][str(year)]['provenance']['sha256']: raise ValueError('Observation bytes changed')
            observations[s['id']].update(parse_isd(file.read_bytes(),year))
    records=[];manifest={}
    for year in YEARS:
        for d in dates(year):
            run=datetime(d.year,d.month,d.day,6,tzinfo=UTC);issue=run+timedelta(hours=9);per_site={s['id']:[] for s in sites}
            for t in night_times(d):
                lead=int((t-run).total_seconds()/3600);decoded={};metadata={}
                for name,subdir,suffix in [('temperature_c','gfs-single',''),('dewpoint_c','gfs-covariates','_dewpoint')]:
                    path=source_dir/'raw'/subdir/run.strftime('%Y%m%d%H')/f'f{lead:03}{suffix}.grib2'
                    raw=path.read_bytes();m=json.loads(path.with_name(path.name+'.meta.json').read_text(encoding='utf-8'))
                    if hashlib.sha256(raw).hexdigest()!=m['sha256']: raise ValueError('Cached GFS changed')
                    if datetime.fromisoformat(m['archive_last_modified_at_utc'])+timedelta(minutes=10)>issue: raise ValueError('GFS unavailable by issue proxy')
                    manifest[str(path.relative_to(ROOT))]=m;metadata[name]=m
                    h=ec.codes_new_from_message(raw)
                    try:
                        expected={'shortName':'2t' if name=='temperature_c' else '2d','stepType':'instant','typeOfLevel':'heightAboveGround',
                          'level':2,'units':'K','dataDate':int(run.strftime('%Y%m%d')),'dataTime':600,
                          'validityDate':int(t.strftime('%Y%m%d')),'validityTime':t.hour*100,'gridType':'regular_ll'}
                        if any(ec.codes_get(h,k)!=v for k,v in expected.items()):raise ValueError('GFS field/run/lead/schema mismatch')
                        if ec.codes_get(h,'iDirectionIncrementInDegrees')!=.25 or ec.codes_get(h,'jDirectionIncrementInDegrees')!=.25:raise ValueError('Expected quarter-degree grid')
                        points=ec.codes_grib_find_nearest_multiple(h,False,[s['latitude'] for s in sites],[s['longitude'] for s in sites])
                        for s,point in zip(sites,points):
                            if ec.codes_get(h,'bitmapPresent') and ec.codes_get_element(h,'bitmap',point['index'])==0:raise ValueError('Missing grid value')
                            decoded.setdefault(s['id'],{}).update({name:float(point['value'])-273.15,name+'_grid':[float(point['lat']),float(point['lon'])]})
                    finally:ec.codes_release(h)
                a,b=metadata.values()
                if any(a[k]!=b[k] for k in ['url','etag','archive_last_modified_at_utc']):raise ValueError('Mixed GFS object versions')
                for s in sites:
                    v=decoded[s['id']]
                    if v['temperature_c_grid']!=v['dewpoint_c_grid']:raise ValueError('Different feature grids')
                    per_site[s['id']].append(v)
            for s in sites:
                obs=observations[s['id']]
                records.append({'site_id':s['id'],'site_name':s['name'],'role':s['role'],'latitude':s['latitude'],'longitude':s['longitude'],
                  'elevation_m':s['elevation_m'],'date':d.isoformat(),'year':year,'run_at_utc':run.isoformat(),'issued_at_utc':issue.isoformat(),
                  'times':[t.isoformat() for t in night_times(d)],'gfs_temperature_c':[v['temperature_c'] for v in per_site[s['id']]],
                  'gfs_dewpoint_c':[v['dewpoint_c'] for v in per_site[s['id']]],'target_temperature_c':[obs.get(t.isoformat(),{}).get('temperature_c') for t in night_times(d)]})
            if d.day==1: print('Decoded',d,'sites',len(sites),flush=True)
    file=directory/'dataset.json';write_json(file,records);write_json(directory/'gfs-manifest.json',manifest)
    write_json(directory/'dataset.meta.json',{'sha256':sha(file),'audit_sha256':sha(directory/'station-audit.json'),'manifest_sha256':sha(directory/'gfs-manifest.json'),
      'source_sha256':sha(ROOT/'agro_checker/product_data.py'),'records':len(records),'sites':len(sites),'years':YEARS,'no_imputation':True})
    return records


def load(directory):
    meta=json.loads((directory/'dataset.meta.json').read_text(encoding='utf-8'))
    for name,key in [('dataset.json','sha256'),('station-audit.json','audit_sha256'),('gfs-manifest.json','manifest_sha256')]:
        if sha(directory/name)!=meta[key]:raise ValueError('Product dataset changed')
    if meta['source_sha256']!=sha(ROOT/'agro_checker/product_data.py'):raise ValueError('Frozen extraction source changed')
    return json.loads((directory/'dataset.json').read_text(encoding='utf-8')),meta


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--audit',action='store_true');parser.add_argument('--extract',action='store_true');parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    directory=ROOT/'data/processed/product-stations'
    if args.audit:
        r=audit(directory,args.offline);print('Eligible:',[(s['id'],s['name'],s['role']) for s in r['stations'] if s['eligible']])
    if args.extract:extract(directory,ROOT/'data')
