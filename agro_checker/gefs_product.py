"""Bounded original GEFS member extraction; separate coarse-time research input."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from email.utils import parsedate_to_datetime
import hashlib
from http.client import HTTPSConnection
import json
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from urllib.error import URLError
import base64
import os
import re
import subprocess
from .pipeline import ROOT,UTC,cached_download,write_json

HOST='noaa-gefs-pds.s3.amazonaws.com'


def fetch(url,byte_range=None):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!=HOST:raise ValueError('NOAA GEFS bucket only')
    transport_url=url.replace('https://'+HOST+'/','https://noaa-gefs-pds.s3.us-east-1.amazonaws.com/')
    headers={'Range':f'bytes={byte_range[0]}-{byte_range[1]}'} if byte_range else {}
    limit=byte_range[1]-byte_range[0]+1 if byte_range else 2_000_000
    try:
        if os.name=='nt':raise OSError('Use verified native Windows HTTPS transport')
        with urlopen(Request(transport_url,headers=headers),timeout=20) as r:
            status=r.status;response_headers=dict(r.headers);b=r.read(limit+1)
    except (URLError,OSError):
        if os.name!='nt':raise
        if parsed.query or not re.fullmatch(r'/[a-zA-Z0-9._/-]+',parsed.path):raise ValueError('Unsupported archive path')
        # Native Windows TLS retains certificate checks; no browser or credentials.
        add_range=f'$archiveRequest.AddRange({byte_range[0]},{byte_range[1]})' if byte_range else ''
        script=f"""$ErrorActionPreference='Stop'
$archiveRequest=[System.Net.HttpWebRequest]::Create('{transport_url}')
$archiveRequest.Timeout=20000
{add_range}
$archiveResponse=$archiveRequest.GetResponse()
$archiveStream=$archiveResponse.GetResponseStream()
$archiveBytes=New-Object byte[] {limit+1}
$archiveRead=0
while($archiveRead -lt $archiveBytes.Length){{$archiveGot=$archiveStream.Read($archiveBytes,$archiveRead,$archiveBytes.Length-$archiveRead);if($archiveGot -eq 0){{break}};$archiveRead+=$archiveGot}}
$archiveHeaders=@{{}}
foreach($archiveKey in $archiveResponse.Headers.AllKeys){{$archiveHeaders[$archiveKey]=$archiveResponse.Headers[$archiveKey]}}
@{{status=[int]$archiveResponse.StatusCode;headers=$archiveHeaders;data=[Convert]::ToBase64String($archiveBytes,0,$archiveRead)}} | ConvertTo-Json -Compress
$archiveStream.Close()
$archiveResponse.Close()
"""
        proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=40,creationflags=0x08000000)
        if proc.returncode:raise URLError('Verified Windows HTTPS request failed')
        payload=json.loads(proc.stdout);status=payload['status'];response_headers=payload['headers'];b=base64.b64decode(payload['data'])
    if status!=(206 if byte_range else 200):raise ValueError('GEFS request failed '+str(status))
    if byte_range and not response_headers.get('Content-Range','').startswith(f'bytes {byte_range[0]}-{byte_range[1]}/'):raise ValueError('GEFS range not honoured')
    if len(b)>limit or (byte_range and len(b)!=limit):raise ValueError('GEFS response length mismatch')
    return b,response_headers


def temperature_range(index,run,lead,member):
    lines=index.decode().splitlines();matches=[i for i,l in enumerate(lines) if ':TMP:2 m above ground:' in l]
    if len(matches)!=1 or matches[0]+1>=len(lines):raise ValueError('Unique indexed temperature required')
    i=matches[0];f=lines[i].split(':')
    if f[2]!='d='+run.strftime('%Y%m%d%H') or f[5]!=f'{lead} hour fcst':raise ValueError('GEFS index run/lead mismatch')
    if member==0 and 'ctl' not in ':'.join(f[6:]):raise ValueError('GEFS control member mismatch')
    a,b=int(f[1]),int(lines[i+1].split(':')[1])-1
    if not 0<=a<b or b-a>5_000_000:raise ValueError('Invalid GEFS message range')
    return a,b


def collect(night,latitude,longitude,members=range(31),offline=False):
    d=datetime.fromisoformat(night).replace(tzinfo=UTC)
    if d.year not in [2023,2024]:raise ValueError('Prior-only collector2023/24')
    if not -90<=latitude<=90 or not -180<=longitude<=180:raise ValueError('Invalid location')
    members=list(members)
    if not members or len(set(members))!=len(members) or any(type(m) is not int or not 0<=m<=30 for m in members):raise ValueError('GEFS members0–30')
    import eccodes as ec
    run=d.replace(hour=6);directory=ROOT/'data/processed/product-gefs';curves=[];manifest=[];failures=[]
    def fetch_job(job):
        m,lead=job;member='gec00' if m==0 else f'gep{m:02}'
        url=f'https://{HOST}/gefs.{d:%Y%m%d}/06/atmos/pgrb2ap5/{member}.t06z.pgrb2a.0p50.f{lead:03}'
        path=directory/'raw'/run.strftime('%Y%m%d%H')/f'm{m:02}-f{lead:03}'
        idx,imeta=cached_download(url+'.idx',path.with_suffix('.idx'),offline,fetcher=fetch)
        a,b=temperature_range(idx,run,lead,m)
        def range_fetch(unused):
            raw,headers=fetch(url,(a,b))
            if raw[:4]!=b'GRIB' or raw[-4:]!=b'7777' or int.from_bytes(raw[8:16],'big')!=len(raw):raise ValueError('Incomplete GEFS message')
            return raw,headers
        raw,meta=cached_download(url,path.with_suffix('.grib2'),offline,fetcher=range_fetch)
        return m,lead,raw,meta,imeta
    def guarded_fetch(job):
        try:return fetch_job(job)
        except (OSError,ValueError,subprocess.SubprocessError) as exc:return job[0],job[1],None,{'error':str(exc)},None
    values={m:[] for m in members}
    with ThreadPoolExecutor(max_workers=4) as pool:
        # Network parallelism only; native GRIB decoding stays on this thread.
        for m,lead,raw,meta,imeta in pool.map(guarded_fetch,[(m,l) for m in members for l in [12,15,18,21]]):
            if raw is None:
                failures.append({'member':m,'lead_hours':lead,**meta});continue
            h=ec.codes_new_from_message(raw)
            try:
                valid=run+timedelta(hours=lead)
                expected={'shortName':'2t','level':2,'typeOfLevel':'heightAboveGround','units':'K','stepType':'instant',
                  'dataDate':int(run.strftime('%Y%m%d')),'dataTime':600,'validityDate':int(valid.strftime('%Y%m%d')),'validityTime':valid.hour*100,
                  'perturbationNumber':m,'gridType':'regular_ll'}
                if any(ec.codes_get(h,k)!=v for k,v in expected.items()):raise ValueError('GEFS metadata mismatch')
                if ec.codes_get(h,'iDirectionIncrementInDegrees')!=.5:raise ValueError('Expected half-degree GEFS grid')
                point=ec.codes_grib_find_nearest(h,latitude,longitude,npoints=1)[0]
                if ec.codes_get(h,'bitmapPresent') and ec.codes_get_element(h,'bitmap',point['index'])==0:raise ValueError('Missing GEFS point')
                value=float(point['value'])-273.15
                if not -90<=value<=65:raise ValueError('Invalid GEFS temperature')
                values[m].append({'valid_at_utc':valid.isoformat(),'temperature_c':value,'lead_hours':lead,
                  'grid_latitude':float(point['lat']),'grid_longitude':float(point['lon'])})
            finally:ec.codes_release(h)
            manifest.extend([imeta,meta])
            if lead==21:print('GEFS member',m,'of',len(members),flush=True)
    curves=[{'member':m,'hours':values[m]} for m in members if len(values[m])==4]
    result={'night':night,'run_at_utc':run.isoformat(),'members':curves,'member_count':len(curves),'requested_member_count':len(members),'requested_members':members,'failures':failures,
      'status':'complete_sample' if not failures else 'incomplete_sample','complete_operational_ensemble':len(curves)==31 and not failures,
      'temporal_step_hours':3,'grid_degrees':.5,'manifest':manifest,'historical_first_publication_unknown':True,
      'transport_endpoint':'verified HTTPS noaa-gefs-pds.s3.us-east-1.amazonaws.com; cache URLs identify same canonical bucket objects',
      'not_hourly_duration_evidence':True,'used_in_fitting':False,'crop_damage_probability':None}
    write_json(directory/(night+'.json'),result);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--night',default='2023-03-01');p.add_argument('--offline',action='store_true');a=p.parse_args()
    collect(a.night,45.0333,39.15,offline=a.offline)
