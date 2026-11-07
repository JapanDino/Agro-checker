"""Pre-v16 five-field archive; cloud remains absent, never averaged/imputed."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
import hashlib
import math
import json
from .pipeline import ROOT, UTC, cached_download, night_hours, season_dates, write_json, parse_observations
from .extended_archive import archive_config, historical_url
from .gfs_archive import aws_fetch, temperature_range, range_download, point_row, run_at
from .gfs_covariates import FIELDS, field_range, check_object_version, decode_hour
from .humidity_archive import RH_FIELDS


def config():
    c = archive_config(); c.update(season_start='03-01', season_end='03-22'); return c


def fetch(c, directory, d, t, offline):
    run = run_at(c,d); lead = int((t-run).total_seconds()/3600); url = historical_url(run,lead)
    raw = directory/'raw/gfs-single'/f'{run:%Y%m%d%H}'/f'f{lead:03}'
    index, imeta = cached_download(url+'.idx',raw.with_suffix('.idx'),offline,fetcher=aws_fetch)
    start,end = temperature_range(index,run,lead); content,tmeta = range_download(url,raw.with_suffix('.grib2'),start,end,offline)
    fields = {}
    for key, specs in [(k,FIELDS) for k in ['u10','v10','dewpoint']]+[('rh',RH_FIELDS)]:
        start,end = field_range(index,run,lead,key,specs)
        binary,meta = range_download(url,directory/'raw/early-fields'/f'{run:%Y%m%d%H}'/f'f{lead:03}_{key}.grib2',start,end,offline)
        check_object_version(meta,tmeta); fields[key]=(binary,meta)
    return content,tmeta,fields,imeta


def collect(directory,offline=False,workers=10):
    c=config(); file=directory/'raw/early-no-cloud-points.json'; saved=[]; downloads=[]
    if file.exists():
        payload=load(directory); saved=payload['rows']; downloads=payload['downloads']
    done={r['temperature']['valid_at_utc'] for r in saved}; failures=[]
    jobs=[(d,t) for d in season_dates(c,2021) for t in night_hours(c,d) if t.isoformat() not in done]; iterator=iter(jobs); attempted=0
    def seal():
        write_json(file,{'config':c,'rows':saved,'downloads':downloads,'failures':failures,
                         'cloud':'absent; excluded from both new point and residual features'})
        write_json(file.with_name(file.name+'.meta.json'),{'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'decoder':'eccodes'})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={}
        def submit():
            job=next(iterator,None)
            if job: pending[pool.submit(fetch,c,directory,*job,offline)]=job
        for _ in range(workers*2): submit()
        while pending:
            ready,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in ready:
                d,t=pending.pop(future)
                try:
                    content,tmeta,fields,imeta=future.result(); point=point_row(c,d,t,content,tmeta)
                    cov=decode_hour(c,d,t,{k:fields[k] for k in ['u10','v10','dewpoint']},point,{k:FIELDS[k] for k in ['u10','v10','dewpoint']})
                    rh=decode_hour(c,d,t,{'rh':fields['rh']},point,RH_FIELDS)
                    if not point['available_by_issue_proxy'] or any(not f['available_by_issue_proxy'] for f in cov['fields'].values()) or not rh['fields']['rh']['available_by_issue_proxy']:
                        raise ValueError('Too late archive availability proxy')
                    saved.append({'temperature':point,'weather':cov,'humidity':rh}); downloads.extend([imeta,tmeta]+[meta for _,meta in fields.values()])
                except (ValueError,OSError,KeyError) as exc: failures.append({'date':d.isoformat(),'valid_at_utc':t.isoformat(),'error':str(exc)})
                attempted+=1
                if attempted%40==0 or attempted==len(jobs):
                    seal(); print(f'Early2021 no cloud: {len(saved)}/308 hours; failures={len(failures)}',flush=True)
                submit()
    seal()
    if failures: raise ValueError('Early archive incomplete; rerun to retry')


def load(directory):
    file=directory/'raw/early-no-cloud-points.json'; meta=json.loads(file.with_name(file.name+'.meta.json').read_text(encoding='utf-8'))
    if hashlib.sha256(file.read_bytes()).hexdigest()!=meta['sha256']: raise ValueError('Early extraction hash mismatch')
    payload=json.loads(file.read_text(encoding='utf-8'))
    if payload['config']!=config(): raise ValueError('Early config mismatch')
    return payload


def rows(directory):
    payload=load(directory)
    if payload['failures'] or len(payload['rows'])!=308: raise ValueError('Need complete early forecast archive')
    c=config(); content,meta=cached_download('https://data.meteostat.net/hourly/2021/34929.csv.gz',directory/'raw/meteostat-34929-2021.csv.gz',True)
    obs=parse_observations(content,c['observation_sources']); result=[]
    for item in sorted(payload['rows'],key=lambda r:r['temperature']['valid_at_utc']):
        point=item['temperature']; cov=item['weather']['fields']; rh=item['humidity']['fields']['rh']['value']
        temp=point['forecast_temperature_c']; dew=cov['dewpoint']['value']; wind=math.hypot(cov['u10']['value'],cov['v10']['value'])
        truth=obs.get(datetime.fromisoformat(point['valid_at_utc']),{}); target=truth.get('temperature_c')
        if target is not None and not -60<=target<=60: target=None
        result.append({'date':point['night_date_local'],'year':2021,'hour_index':point['lead_hours']-10,'lead_hours':point['lead_hours'],
          'valid_at_utc':point['valid_at_utc'],'forecast_run_at_utc':point['forecast_run_at_utc'],'issued_at_utc':point['issued_at_utc'],
          'features_weather':[temp,rh,dew,temp-dew,wind],
          'base':{'temperature':temp,'humidity':min(100.,max(0.,rh)),'wind':wind},
          'target':{'temperature':target,'humidity':None,'wind':None},'source':{'temperature':truth.get('source','')}})
    return result,{'extraction_sha256':hashlib.sha256((directory/'raw/early-no-cloud-points.json').read_bytes()).hexdigest(),'observations':meta}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    collect(ROOT/'data/archive-2021',args.offline)
