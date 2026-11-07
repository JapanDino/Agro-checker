"""Bounded, cached public-source audit; no manufactured field labels."""
import sys
import hashlib
from pathlib import Path
from urllib.request import urlopen,Request
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.seasonal_calibration import stamp
D=ROOT/'data/farm-research/open-crop-context'
S=D/'sources'


def fetch(entry):
    name,url,limit=entry;path=S/name
    if path.exists():
        meta=read(path.with_name(path.name+'.meta.json'))
        if digest(path)!=meta['sha256']:raise ValueError('Changed cached source')
        return meta
    try:
        with urlopen(Request(url,headers={'User-Agent':'Onlyfarms-crop-source-audit/1.0'}),timeout=40) as response:
            content=response.read(limit+1);headers=dict(response.headers);status=response.status
            if len(content)>limit:raise ValueError('Bounded download exceeds expected size')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        meta=dict(name=name,url=url,retrieved_at_utc=stamp(),status=status,bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),last_modified=headers.get('Last-Modified'),etag=headers.get('ETag'))
        dump(path.with_name(path.name+'.meta.json'),meta);return meta
    except Exception as exc:
        return dict(name=name,url=url,error=str(exc),retrieved_at_utc=stamp())


if __name__=='__main__':
    if (D/'frozen-selection.json').exists():raise SystemExit('Sources locked after model freeze')
    bbox=read(ROOT/'data/farm-research/area-context/protocol.json')['bbox_wgs84']
    entries=[
        ('worldcereal-global.html','https://esa-worldcereal.org/en/products/global-maps',2_000_000),
        ('worldcereal-calendar.html','https://esa-worldcereal.org/en/products/crop-calendars',2_000_000),
        ('geoglam-v1-record.json','https://zenodo.org/api/records/10949972',1_000_000),
        ('geoglam-v1.zip','https://zenodo.org/api/records/10949972/files/GEOGLAM_CM4EW_Calendars_V1.0.zip/content',83_000_000),
        ('asap-download.html','https://agricultural-production-hotspots.ec.europa.eu/download.php',2_000_000),
        ('asap-calendar0.zip','https://agricultural-production-hotspots.ec.europa.eu/files/crop_calendar_gaul0.zip',1_000_000),
        ('asap-calendar1.zip','https://agricultural-production-hotspots.ec.europa.eu/files/crop_calendar_gaul1.zip',1_000_000),
        ('rdm-openapi.json','https://ewoc-rdm-api.iiasa.ac.at/swagger/OGC/swagger.json',3_000_000),
        ('kubgau-2024-06.pdf','https://kubsau.ru/upload/iblock/784/zva8xyd45afwkqtd7l71r4spijgwx5it.pdf',15_000_000),
        ('krasnodar-2024-06-27.pdf','https://ki-news.ru/wp-content/uploads/2024/06/2024-06-27-072.pdf',20_000_000),
    ]
    for label,extent,dates in [('sector2024',bbox,True),('region2024',[37.4,44.6,38.5,45.5],True),('sector-any-date',bbox,False)]:
        params=[('Bbox',x) for x in extent]+[('SkipCount',0),('MaxResultCount',500)]
        if dates:params += [('ValidityTime.Start','2024-01-01T00:00:00Z'),('ValidityTime.End','2024-12-31T23:59:59Z')]
        entries.append((f'rdm-{label}.json','https://ewoc-rdm-api.iiasa.ac.at/collections/search?'+urlencode(params),5_000_000))
    manifest=[]
    with ThreadPoolExecutor(4) as pool:
        for future in as_completed([pool.submit(fetch,e) for e in entries]):
            meta=future.result();manifest.append(meta);print(meta['name'],meta.get('bytes',meta.get('error')),flush=True)
    dump(D/'source-manifest.json',dict(created_at_utc=stamp(),bbox=bbox,entries=sorted(manifest,key=lambda e:e['name']),
        policy='Metadata/raw source audit only. No field crop or actual harvest date assigned; current/final maps cannot establish issue-time2024 truth.'))
    # Collection bounding boxes alone do NOT establish local samples.
    local=[]
    for collection in read(S/'rdm-sector-any-date.json'):
        cid=collection['collectionId']
        params=[('Bbox',x) for x in bbox]+[('SkipCount',0),('MaxResultCount',500)]
        local.append((f'rdm-items-{cid}.json',
            f'https://ewoc-rdm-api.iiasa.ac.at/collections/{cid}/items?'+urlencode(params),5_000_000))
    with ThreadPoolExecutor(4) as pool:
        for meta in pool.map(fetch,local):
            manifest.append(meta);print(meta['name'],meta.get('bytes',meta.get('error')),flush=True)
    dump(D/'source-manifest.json',dict(created_at_utc=stamp(),bbox=bbox,entries=sorted(manifest,key=lambda e:e['name']),
        policy='Collection AND item level checked. No field crop or actual harvest date assigned; final maps cannot establish issue-time2024 truth.'))
