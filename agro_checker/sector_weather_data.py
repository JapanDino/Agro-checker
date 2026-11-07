"""Cached historical GFS weather background for unconfirmed screenshot fragments."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,hashlib,time
from urllib.parse import urlencode
from urllib.request import urlopen,Request
from urllib.error import HTTPError
import numpy as np
from .farm_context_data import ROOT,read,dump,digest

D=ROOT/'data/farm-research/weather-ndmi'
GEO=ROOT/'data/farm-research/reconstruction/approximate-areas.geojson'
VARIABLES=['precipitation','soil_moisture_0_to_10cm']
HOST='https://historical-forecast-api.open-meteo.com/v1/forecast'
RAIN_NAMES=['rain_3d_mm','rain_7d_mm','rain_14d_mm','wet_days_14d']
SOIL_NAMES=['soil_24h_m3m3','soil_7d_m3m3','soil_change_7d_m3m3','soil_min_14d_m3m3']


def download(url,path):
    path=Path(path);meta=path.with_suffix('.meta.json')
    if path.exists() and meta.exists():
        m=read(meta)
        if m['url']!=url or m['sha256']!=digest(path):raise ValueError('Weather cache changed')
        return read(path),m
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'Onlyfarms-weather-research/1.0'}),timeout=60) as response:
                raw=response.read();status=response.status;headers=dict(response.headers)
            obj=json.loads(raw)
            if status!=200 or isinstance(obj,dict) and obj.get('error'):raise ValueError('Weather API error '+str(obj))
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            m=dict(url=url,status=status,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),
                   retrieved_at_utc=datetime.now(timezone.utc).isoformat(),last_modified=headers.get('Last-Modified'),
                   historical_first_delivery_proven=False)
            dump(meta,m);return obj,m
        except HTTPError as exc:
            if exc.code not in [429,500,502,503,504] or attempt==2:raise
            time.sleep(3*(attempt+1))
    raise RuntimeError('Weather download failed')


def point_samples():
    # Bounding-box midpoints are deterministic weather sampling points, not farm/sensor coordinates.
    samples=[]
    for f in read(GEO)['features']:
        xy=np.array(f['geometry']['coordinates'][0],float)
        lon,lat=(xy.min(axis=0)+xy.max(axis=0))/2
        samples.append(dict(fid=f['properties']['id'],latitude=round(float(lat),6),longitude=round(float(lon),6),
                            confirmed_boundary=False,sampling='bounding_box_midpoint_of_unconfirmed_fragment'))
    return samples


def query(points,start,end):
    return HOST+'?'+urlencode(dict(latitude=','.join(str(a['latitude']) for a in points),
        longitude=','.join(str(a['longitude']) for a in points),start_date=start,end_date=end,
        models='gfs_global',hourly=','.join(VARIABLES),timezone='GMT',elevation=','.join(['nan']*len(points)),cell_selection='nearest'))


def collect_2024():
    samples=point_samples()
    response,meta=download(query(samples,'2024-04-01','2024-04-01'),D/'sources/grid-lookup-2024.json')
    if not isinstance(response,list) or len(response)!=len(samples):raise ValueError('Coordinate lookup mismatch')
    mapping={};unique={}
    for request,actual in zip(samples,response):
        pair=(actual['latitude'],actual['longitude'])
        gid=unique.setdefault(pair,dict(id=f'G{len(unique)+1:02d}',request=request,returned=pair))['id']
        mapping[request['fid']]=dict(grid_id=gid,requested=request,returned_latitude=pair[0],returned_longitude=pair[1])
    dump(D/'weather-locations.json',dict(contours_sha256=digest(GEO),lookup_sha256=meta['sha256'],
        mapping=mapping,grids=list(unique.values()),warning='Returned API grid locations; not independently verified native support per variable, not field sensors.'))
    return collect_year(2024)


def qc(data,start,end):
    hour=data['hourly'];times=hour['time']
    expected=int(((datetime.fromisoformat(end)+timedelta(days=1))-datetime.fromisoformat(start)).total_seconds()/3600)
    if len(times)!=expected or len(set(times))!=expected:raise ValueError('Weather time count or duplicates')
    t0=datetime.fromisoformat(start)
    if any(datetime.fromisoformat(t)!=t0+timedelta(hours=i) for i,t in enumerate(times)):raise ValueError('Nonhourly weather axis')
    if data.get('utc_offset_seconds')!=0:raise ValueError('UTC weather required')
    if data['hourly_units']['precipitation']!='mm' or data['hourly_units']['soil_moisture_0_to_10cm']!='m³/m³':raise ValueError('Unexpected weather units')
    summary={}
    for name in VARIABLES:
        values=hour[name]
        if len(values)!=expected:raise ValueError('Weather variable length mismatch')
        finite=[v for v in values if v is not None]
        if any(not np.isfinite(v) or v<0 or name.startswith('soil_') and v>1 for v in finite):raise ValueError('Weather range invalid')
        summary[name]=dict(hours=expected,valid=len(finite),missing=expected-len(finite),
            minimum=min(finite) if finite else None,maximum=max(finite) if finite else None)
    return summary


def collect_year(year):
    if year==2026 and not (D/'frozen-selection.json').exists():raise ValueError('2026 weather may be collected only after choice freeze')
    if year not in [2024,2026]:raise ValueError('Unplanned year')
    loc=read(D/'weather-locations.json');start=f'{year}-01-01';end='2024-12-31' if year==2024 else '2026-10-19'
    summaries=[]
    for g in loc['grids']:
        data,meta=download(query([g['request']],start,end),D/f'sources/{year}/{g["id"]}.json')
        if isinstance(data,list):data=data[0]
        if [data['latitude'],data['longitude']]!=list(g['returned']):raise ValueError('Grid location changed')
        summary=qc(data,start,end);summaries.append(dict(grid_id=g['id'],source_sha256=meta['sha256'],variables=summary))
        print(year,g['id'],summary,flush=True)
    output=dict(year=year,start=start,end=end,model='gfs_global',kind='stitched_first_hours_of_successive_forecasts',
        lag_hours=48,lag_is_availability_proxy=True,historical_delivery_proven=False,measured_on_farm=False,
        soil_depth_cm=[0,10],fragment_count=len(loc['mapping']),returned_grid_count=len(loc['grids']),grids=summaries)
    dump(D/f'weather-summary-{year}.json',output);return output


def load_hourly(year):
    loc=read(D/'weather-locations.json');summary=read(D/f'weather-summary-{year}.json');expected={g['grid_id']:g['source_sha256'] for g in summary['grids']}
    out={}
    for g in loc['grids']:
        path=D/f'sources/{year}/{g["id"]}.json'
        if digest(path)!=expected[g['id']]:raise ValueError('Weather source changed')
        obj=read(path);obj=obj[0] if isinstance(obj,list) else obj
        hour=obj['hourly'];base=datetime.fromisoformat(hour['time'][0])
        out[g['id']]=dict(base=base,time=hour['time'],rain=np.array([np.nan if v is None else v for v in hour[VARIABLES[0]]]),
                        soil=np.array([np.nan if v is None else v for v in hour[VARIABLES[1]]]))
    return loc,out


def weather_vector(hourly,issue):
    cutoff=datetime.fromisoformat(issue)-timedelta(hours=48)
    end=int((cutoff-hourly['base']).total_seconds()/3600)
    if end<14*24 or end>len(hourly['rain']):return [None]*8,dict(cutoff_utc=cutoff.isoformat(),complete=False,reason='window_outside_archive')
    rain=hourly['rain'][end-14*24:end];soil=hourly['soil'][end-14*24:end]
    # Any missing input keeps the window unavailable. Never replace rain null by zero.
    if not np.isfinite(rain).all() or not np.isfinite(soil).all():
        return [None]*8,dict(cutoff_utc=cutoff.isoformat(),complete=False,reason='missing_weather_hours')
    values=[rain[-72:].sum(),rain[-168:].sum(),rain.sum(),(rain.reshape(14,24).sum(axis=1)>=1).sum(),
        soil[-24:].mean(),soil[-168:].mean(),soil[-24:].mean()-soil[-192:-168].mean(),soil.min()]
    return [float(v) for v in values],dict(cutoff_utc=cutoff.isoformat(),complete=True,
        newest_valid_utc=hourly['time'][end-1],oldest_valid_utc=hourly['time'][end-14*24],
        availability='48h proxy relative to issue day 00UTC; not verified first publication')


def enrich(rows,year):
    loc,data=load_hourly(year);result=[]
    for r in rows:
        if r['issue'][:4]!=str(year):raise ValueError('Weather-year mismatch')
        gid=loc['mapping'][r['fid']]['grid_id'];vector,audit=weather_vector(data[gid],r['issue'])
        result.append(dict(r,weather=vector,weather_audit=dict(audit,grid_id=gid)))
    return result
