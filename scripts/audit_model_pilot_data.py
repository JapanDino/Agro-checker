"""Read-only evidence inventory; optional public STAC catalogue probe, never model fitting."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
MOSCOW=timezone(timedelta(hours=3))
COLLECTION='sentinel-2-c1-l2a'
SEARCH='https://earth-search.aws.element84.com/v1/search'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def stamp(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError('Timestamp needs an explicit timezone')
    return parsed.astimezone(timezone.utc)
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def journal_counts(directory):
    db=directory/'field.sqlite3'
    if not db.exists():return {'status':'not_available','tasks':None,'visits':None,'plans':None}
    before=sha(db)
    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
        rows=connection.execute('SELECT kind,COUNT(*) FROM events WHERE profile_id=? GROUP BY kind',('farm-scouting-01',)).fetchall()
    counts=dict(rows)
    result={'status':'inspected_read_only',**{name:counts.get(kind,0) for name,kind in [('tasks','farm_scouting_task'),('visits','farm_scouting_visit'),('plans','farm_scouting_plan')]}}
    result['sha256_before']=before;result['sha256_after']=sha(db)
    if result['sha256_after']!=before:raise ValueError('Journal changed during the read; repeat audit when idle')
    result['validated_training_labels']=None  # A visit count alone never establishes label validity.
    return result


def inventory(as_of,journal=None,root=ROOT):
    cutoff=datetime.combine(as_of,time.min,MOSCOW).astimezone(timezone.utc)
    directory=root/'data/farm-research/area-context'
    protocol=read(directory/'protocol.json');satellite=read(directory/'satellite.json')
    geometry_path=root/'data/farm-research/reconstruction/approximate-areas.geojson'
    geometry=read(geometry_path)
    ready=[s for s in satellite['scenes'] if s.get('status')=='ready' and stamp(s['acquired_at_utc'])<cutoff]
    latest=max((stamp(s['acquired_at_utc']) for s in ready),default=None)
    npz=[directory/'sentinel'/s['id']/'indices.npz' for s in ready]
    return dict(schema_version=1,as_of_moscow=as_of.isoformat(),cutoff_utc=cutoff.isoformat(),
        purpose='Data readiness only; no forecast, model selection or agronomic labels',
        geometry=dict(fragments=len(geometry['features']),confirmed_boundaries=False,confirmed_field_ids=None,
                      crop_labels_from_agromon=None,sha256=sha(geometry_path),bbox_wgs84=protocol['bbox_wgs84']),
        archive=dict(manifest_entries=len(satellite['scenes']),statuses=dict(Counter(s.get('status') for s in satellite['scenes'])),
                     ready_before_cutoff=len(ready),index_files_present=sum(p.is_file() for p in npz),
                     latest_ready_acquisition_utc=latest.isoformat() if latest else None,
                     age_days_at_cutoff=round((cutoff-latest).total_seconds()/86400,2) if latest else None,
                     per_fragment_usable_dates_not_recomputed=True,periods=protocol['periods'],
                     manifest_sha256=sha(directory/'satellite.json')),
        agromon=dict(access_status='not_available_user_confirmed_2026-11-09',farm_data_acquired=False,
                     official_api='https://agromon.ru/agreementapi',official_export_guidance='https://agromon.ru/education'),
        journal=journal_counts(journal) if journal else {'status':'not_inspected'},
        training=dict(selection_years=[2024],do_not_tune_years=[2025,2026],new_fit_performed=False,
                      production_model_replaced=False,historical_control_already_seen=True),
        local_sources={str(p.relative_to(root)).replace('\\','/'):sha(p) for p in [directory/'protocol.json',directory/'summary.json',directory/'satellite.json',geometry_path]})


def fetch(url,transport):
    if transport=='curl':
        result=subprocess.run(['curl','--http1.1','--retry','1','--connect-timeout','10','--max-time','30',
                               '--max-filesize','20000000','--fail','--silent','--show-error',url],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=65)
        if result.returncode:raise OSError(result.stderr.decode('utf-8',errors='replace').strip())
        raw=result.stdout
    else:
        with urlopen(Request(url,headers={'User-Agent':'AgroPulse-data-readiness/1.0'}),timeout=25) as response:
            raw=response.read(20_000_001)
    if len(raw)>20_000_000:raise ValueError('Oversized catalogue response')
    return raw


def probe_catalogue(report,start,output,transport='urllib'):
    end=stamp(report['cutoff_utc'])-timedelta(seconds=1)
    begin=datetime.combine(start,time.min,timezone.utc)
    if begin>end:raise ValueError('Catalogue start must precede the as-of cutoff')
    url=SEARCH+'?'+urlencode(dict(collections=COLLECTION,bbox=','.join(map(str,report['geometry']['bbox_wgs84'])),
                                  datetime=begin.isoformat()+'/'+end.isoformat(),limit=100))
    result=dict(collection=COLLECTION,transport=transport,from_utc=begin.isoformat(),through_utc=end.isoformat(),
                catalogue_complete=False,pages=[],items=[],spectral_assets_downloaded=0,
                field_cloud_quality_evaluated=False,usable_new_images=None,error=None)
    seen=set();items={}
    try:
        for index in range(20):
            parsed=urlparse(url)
            if (parsed.scheme,parsed.netloc,parsed.path)!=('https','earth-search.aws.element84.com','/v1/search') or url in seen:
                raise ValueError('Unexpected or cyclic catalogue link')
            seen.add(url)
            raw=fetch(url,transport)
            payload=json.loads(raw)
            if not isinstance(payload.get('features'),list):raise ValueError('Not a STAC item collection')
            path=output/f'stac-page-{index+1:02d}.json';path.write_bytes(raw)
            result['pages'].append(dict(url=url,file=path.name,sha256=sha(path),fetched_at_utc=datetime.now(timezone.utc).isoformat()))
            for item in payload['features']:
                props=item['properties'];acquired=stamp(props['datetime'])
                if item['collection']!=COLLECTION or not begin<=acquired<=end:raise ValueError('Unexpected scene collection or acquisition time')
                compact=dict(id=item['id'],acquired_at_utc=props['datetime'],
                             tile_cloud_cover_percent=props.get('eo:cloud_cover'),processing_baseline=props.get('s2:processing_baseline'),
                             provider_created_at=props.get('created'),provider_updated_at=props.get('updated'))
                if item['id'] in items and items[item['id']]!=compact:raise ValueError('Conflicting duplicate item')
                items[item['id']]=compact
            nxt=[v for v in payload.get('links',[]) if v.get('rel')=='next']
            if not nxt:result['catalogue_complete']=True;break
            if len(nxt)!=1 or nxt[0].get('method','GET')!='GET':raise ValueError('Unsupported pagination; catalogue remains incomplete')
            url=nxt[0]['href']
        if not result['catalogue_complete']:raise ValueError('Catalogue pagination was not exhausted')
    except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        result['error']=f'{type(error).__name__}: {error}'
    result['items']=sorted(items.values(),key=lambda v:(v['acquired_at_utc'],v['id']))
    result['catalogue_items_received']=len(items)
    result['notice']='Metadata only. Tile cloud percentage does not establish field suitability. Catalogue created/updated is not proven first availability.'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of',required=True,type=date.fromisoformat)
    parser.add_argument('--journal',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/pilot-dec04/data-audit')
    parser.add_argument('--live-catalogue',action='store_true')
    parser.add_argument('--transport',choices=('urllib','curl'),default='urllib')
    parser.add_argument('--catalogue-start',type=date.fromisoformat,default=date(2026,10,20))
    args=parser.parse_args();report=inventory(args.as_of,args.journal)
    args.output.mkdir(parents=True,exist_ok=True)
    report['new_catalogue']=probe_catalogue(report,args.catalogue_start,args.output,args.transport) if args.live_catalogue else {'status':'not_requested'}
    dump(args.output/'audit.json',report)
    print(json.dumps({k:report[k] for k in ('as_of_moscow','archive','agromon','journal')},ensure_ascii=False,indent=2))
    if args.live_catalogue:print(json.dumps({k:v for k,v in report['new_catalogue'].items() if k not in ('items','pages')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
