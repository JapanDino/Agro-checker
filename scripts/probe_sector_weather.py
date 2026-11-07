"""Capability checks on 2024 weather only; retains exact responses, never ML labels."""
from pathlib import Path
import json,sys,hashlib
from urllib.parse import urlencode
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/farm-research/weather-ndmi/sources/preflight'
OUT.mkdir(parents=True,exist_ok=True)
queries=[
 ('gfs_previous','https://previous-runs-api.open-meteo.com/v1/forecast',dict(models='gfs_global',hourly='precipitation_previous_day1,soil_moisture_0_to_10cm_previous_day1')),
 ('gfs_historical','https://historical-forecast-api.open-meteo.com/v1/forecast',dict(models='gfs_global',hourly='precipitation,soil_moisture_0_to_10cm')),
 ('era5','https://archive-api.open-meteo.com/v1/archive',dict(models='era5',hourly='precipitation,soil_moisture_0_to_7cm')),
 ('era5_land','https://archive-api.open-meteo.com/v1/archive',dict(models='era5_land',hourly='precipitation,soil_moisture_0_to_7cm'))]
for name,base,extra in queries:
    url=base+'?'+urlencode(dict(latitude=45.024106,longitude=37.911914,start_date='2024-04-01',end_date='2024-04-03',timezone='GMT',elevation='nan',**extra))
    path=OUT/(name+'.json')
    try:
        with urlopen(Request(url,headers={'User-Agent':'Onlyfarms-research/1.0'}),timeout=45) as response:
            raw=response.read();status=response.status
    except HTTPError as exc:raw=exc.read();status=exc.code
    except Exception as exc:
        print(name,type(exc).__name__,str(exc));continue
    path.write_bytes(raw)
    data=json.loads(raw);meta=dict(url=url,status=status,sha256=hashlib.sha256(raw).hexdigest(),retrieved_at_utc=datetime.now(timezone.utc).isoformat())
    path.with_suffix('.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    hour=data.get('hourly',{})
    print(name,status,'location',data.get('latitude'),data.get('longitude'),'units',data.get('hourly_units'),
          'valid',{k:sum(v is not None for v in values) for k,values in hour.items() if k!='time'},'error',data.get('reason'))
