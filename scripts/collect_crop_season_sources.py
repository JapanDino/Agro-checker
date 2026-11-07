"""Read-only public-source audit; no authentication or field/crop labels inferred."""
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from datetime import datetime,timezone
import json,hashlib
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/farm-research/crop-season-model/sources';D.mkdir(parents=True,exist_ok=True)
sources=[
('worldcereal-global.html','https://esa-worldcereal.org/en/products/global-maps'),
('worldcereal-calendars.html','https://esa-worldcereal.org/en/products/crop-calendars'),
('worldcereal-aez.geojson','https://zenodo.org/records/7875105/files/WorldCereal_AEZ.geojson?download=1'),
('worldcereal-aez-record.json','https://zenodo.org/api/records/7875105'),
('rdm-swagger.html','https://ewoc-rdm-api.iiasa.ac.at/swagger/index.html'),
('rdm-index.js','https://ewoc-rdm-api.iiasa.ac.at/swagger/index.js'),
('rdm-openapi.json','https://ewoc-rdm-api.iiasa.ac.at/swagger/v1/swagger.json'),
('rdm-ogc-openapi.json','https://ewoc-rdm-api.iiasa.ac.at/swagger/OGC/swagger.json'),
('rdm-demo.ipynb','https://raw.githubusercontent.com/WorldCereal/worldcereal-classification/main/notebooks/worldcereal_RDM_demo.ipynb')]
extent=json.loads((ROOT/'data/farm-research/area-context/protocol.json').read_text(encoding='utf-8'))['bbox_wgs84']
query=[('Bbox',v) for v in extent]+[('ValidityTime.Start','2024-01-01T00:00:00Z'),('ValidityTime.End','2024-12-31T23:59:59Z'),('SkipCount',0),('MaxResultCount',500)]
sources.append(('rdm-collections-2024.json','https://ewoc-rdm-api.iiasa.ac.at/collections/search?'+urlencode(query)))
manifest=[]
for name,url in sources:
    path=D/name
    if path.exists():
        print('Cached',name,flush=True);continue
    try:
        with urlopen(Request(url,headers={'User-Agent':'Onlyfarms-public-source-audit/1.0'}),timeout=40) as response:
            content=response.read();headers=dict(response.headers);status=response.status
        path.write_bytes(content)
        meta=dict(url=url,retrieved_at_utc=datetime.now(timezone.utc).isoformat(),status=status,bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),etag=headers.get('ETag'),last_modified=headers.get('Last-Modified'),
            role='external source metadata or historical ecological prior, NOT confirmed farm crop or phase')
        path.with_name(name+'.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('Saved',name,len(content),flush=True)
    except Exception as exc:
        print('Unavailable',name,str(exc),flush=True)
