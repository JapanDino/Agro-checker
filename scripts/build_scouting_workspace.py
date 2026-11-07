"""Public context for the map workspace. Never writes models or field observations."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import hashlib,json,math,shutil
from datetime import datetime,timezone
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import reproject,Resampling,transform_bounds
from pyproj import Transformer
from shapely.geometry import shape,Point
from shapely import transform as shape_transform
from agro_checker.farm_context_analysis import masks,scenarios

D=ROOT/'data/farm-research/scouting-workspace-2026-10-22'
W=ROOT/'web/scouting/assets'
OLD=ROOT/'data/farm-research/area-context'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
def download(url,path):
    meta=path.with_suffix(path.suffix+'.meta.json')
    if path.exists() and meta.exists():
        m=read(meta)
        if m['url']==url and m['sha256']==sha(path):return m
    with urlopen(Request(url,headers={'User-Agent':'AgroPulse-public-context-prototype/1.0'}),timeout=45) as r:body=r.read()
    path.write_bytes(body);m=dict(url=url,sha256=sha(path),retrieved_at_utc=datetime.now(timezone.utc).isoformat(),bytes=len(body));dump(meta,m);return m

def weather():
    out={};sources=[]
    for period,start,end in [('spring2024','2024-02-01','2024-05-31'),('recent2026','2026-06-21','2026-10-19')]:
        q=dict(latitude=45.05,longitude=37.96,start_date=start,end_date=end,models='era5',
               hourly='temperature_2m,relative_humidity_2m,precipitation,soil_moisture_0_to_7cm',timezone='UTC',elevation='nan')
        url='https://archive-api.open-meteo.com/v1/archive?'+urlencode(q);path=D/f'era5-{period}.json'
        try:
            meta=download(url,path);v=read(path);h=v['hourly'];units=v['hourly_units']
            assert units['temperature_2m']=='°C' and units['precipitation']=='mm' and units['relative_humidity_2m']=='%' and units['soil_moisture_0_to_7cm']=='m³/m³'
            assert h['time']==sorted(set(h['time']))
            assert all(len(h[k])==len(h['time']) for k in h)
            days={}
            for i,t in enumerate(h['time']):days.setdefault(t[:10],[]).append(i)
            daily=[]
            for day,ix in days.items():
                row={'date':day,'hours':len(ix)}
                for key,fun in [('precipitation',sum),('temperature_2m',min),('relative_humidity_2m',lambda x:sum(z>=90 for z in x)),('soil_moisture_0_to_7cm',lambda x:sum(x)/len(x))]:
                    vals=[h[key][i] for i in ix];good=all(isinstance(z,(int,float)) and math.isfinite(z) for z in vals)
                    if good and key=='relative_humidity_2m':good=all(0<=z<=100 for z in vals)
                    if good and key in ('soil_moisture_0_to_7cm','precipitation'):good=all(z>=0 for z in vals)
                    if good and key=='soil_moisture_0_to_7cm':good=all(z<=1 for z in vals)
                    row[key]=fun(vals) if good and len(ix)==24 else None
                daily.append(row)
            complete=[r['date'] for r in daily if all(r[k] is not None for k in ['precipitation','temperature_2m','relative_humidity_2m','soil_moisture_0_to_7cm'])]
            out[period]={'status':'available','grid_latitude':v['latitude'],'grid_longitude':v['longitude'],'daily':daily,'source':meta,
                         'complete_days':len(complete),'missing_days':len(daily)-len(complete),'last_complete_date':max(complete) if complete else None,
                         'model':'ERA5','grid_degrees':.25,'kind':'retrospective_model_not_sensor','timezone':'UTC'};sources.append(meta)
        except Exception as e:
            out[period]={'status':'unavailable','reason':str(e),'model':'ERA5','kind':'retrospective_model_not_sensor'}
    return out,sources

def basemap(bbox,stem='basemap'):
    tr=Transformer.from_crs(4326,3857,always_xy=True)
    x0,y0=tr.transform(bbox[0]-.018,bbox[1]-.012);x1,y1=tr.transform(bbox[2]+.018,bbox[3]+.012)
    url='https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?'+urlencode(dict(bbox=','.join(map(str,[x0,y0,x1,y1])),bboxSR=3857,imageSR=3857,size='2300,2800',format='jpg',f='pjson',dpi=96))
    try:
        m=download(url,D/(stem+'-response.json'));response=read(D/(stem+'-response.json'))
        if 'error' in response:raise ValueError(str(response['error']))
        image=download(response['href'],D/(stem+'.jpg'));extent=response['extent']
        assert Image.open(D/(stem+'.jpg')).size==(response['width'],response['height'])
        path=D/(stem+'.jpg');source={'request':m,'image':image,'newly_collected':True}
    except Exception as e:
        path=ROOT/'data/farm-research/reconstruction/satellite-reference.jpg'
        extent=read(ROOT/'data/farm-research/reconstruction/satellite-reference.json')['extent']
        source={'fallback_reason':str(e),'newly_collected':False,'image_sha256':sha(path)}
    shutil.copyfile(path,W/(stem+'.jpg'))
    inv=Transformer.from_crs(3857,4326,always_xy=True)
    west,south=inv.transform(extent['xmin'],extent['ymin']);east,north=inv.transform(extent['xmax'],extent['ymax'])
    return {'bounds':[[south,west],[north,east]],'source':source,'capture_date':None,
            'attribution':'Esri, Vantor, Earthstar Geographics, GIS User Community; дата съёмки мозаики неизвестна'}

def layer(path,bbox,name,lo,hi,colors,divide=1,cell=30):
    box3857=transform_bounds('EPSG:4326','EPSG:3857',*bbox)
    resolution=cell/math.cos(math.radians(45));width=math.ceil((box3857[2]-box3857[0])/resolution);height=math.ceil((box3857[3]-box3857[1])/resolution)
    out=np.full((height,width),np.nan,dtype='float32');tr=from_bounds(*box3857,width,height)
    with rasterio.open(path) as r:
        reproject(rasterio.band(r,1),out,src_transform=r.transform,src_crs=r.crs,src_nodata=r.nodata,dst_transform=tr,dst_crs='EPSG:3857',dst_nodata=np.nan,resampling=Resampling.nearest)
    out/=divide;valid=np.isfinite(out);t=np.clip((np.nan_to_num(out)-lo)/(hi-lo),0,1)
    rgb=np.array(colors,float);rgba=np.zeros((*out.shape,4),dtype='uint8')
    for i in range(3):rgba[:,:,i]=np.interp(t,[0,.5,1],rgb[:,i])
    rgba[:,:,3]=np.where(valid,235,0);Image.fromarray(rgba).save(W/f'{name}.png')
    return {'bounds':[[bbox[1],bbox[0]],[bbox[3],bbox[2]]],'min':lo,'max':hi,'native_cell_m':cell}

def main():
    D.mkdir(parents=True,exist_ok=True);W.mkdir(parents=True,exist_ok=True)
    geo=read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson');context=read(ROOT/'web/farm-reconstruction/context-data.json');bbox=context['protocol']['bbox_wgs84']
    protected={p:sha(ROOT/p) for p in ['data/farm-research/reconstruction/approximate-areas.geojson','data/farm-research/area-context/summary.json','data/farm-research/area-context/satellite.json']}
    weather_data,weather_sources=weather();base=basemap(bbox)
    wide=basemap([bbox[0]-.35,bbox[1]-.20,bbox[2]+.35,bbox[3]+.20],stem='wide-basemap')
    soil=read(OLD/'soil/manifest.json')['layers']
    def soil_path(prop):return ROOT/next(v['path'] for v in soil if v['property']==prop and v['depth']=='0-5cm' and v['statistic']=='mean')
    layers={
        'terrain':layer(OLD/'dem/surface.tif',bbox,'terrain',0,100,[[223,229,204],[163,179,133],[92,101,73]]),
        'clay':layer(soil_path('clay'),bbox,'clay',15,55,[[240,224,185],[187,151,99],[109,82,56]],10,250),
        'ph':layer(soil_path('phh2o'),bbox,'ph',5.5,8.5,[[210,163,121],[233,231,203],[109,158,162]],10,250)}
    to_utm=Transformer.from_crs(4326,32637,always_xy=True);to_geo=Transformer.from_crs(32637,4326,always_xy=True)
    with rasterio.open(OLD/'dem/surface.tif') as r:
        assert r.crs.to_epsg()==32637
        z=r.read(1,masked=True).filled(np.nan);transform=r.transform;grid=r.shape
    result={}
    for feature in geo['features']:
        fid=feature['properties']['id'];data=context['features'][fid];poly=shape_transform(shape(feature['geometry']),to_utm.transform,interleaved=False)
        ix=masks(scenarios(poly,feature['properties']['working_band_m']),transform,grid)['core'];vals=z.ravel()[ix];good=np.isfinite(vals);ix=ix[good];vals=vals[good];candidates=[]
        if len(vals)>=10 and np.quantile(vals,.9)-np.quantile(vals,.1)>=1:
            used=[]
            for code,q in [('low',.1),('high',.9)]:
                for j in np.argsort(abs(vals-np.quantile(vals,q))):
                    row,col=np.unravel_index(ix[j],grid);x,y=rasterio.transform.xy(transform,row,col)
                    if any(math.hypot(x-a,y-b)<100 for a,b in used):continue
                    lon,lat=to_geo.transform(x,y);used.append((x,y));candidates.append({'id':fid+'-'+code,'kind':code,'latitude':lat,'longitude':lon,'utm_x':x,'utm_y':y,'surface_height_m':float(vals[j]),'status':'candidate_for_site_visit_not_sensor_installation'});break
        pairs={}
        for item in data['changes']:
            pair=item.get('same_baseline_comparison',item)
            if pair.get('available'):pairs[item['period']]={k:pair[k] for k in ['before','after','common_coverage','same_processing_baseline','ndvi','ndmi']}
        result[fid]={'dsm':data['dsm'],'soil':data['soil'],'candidate_sites':candidates,'pairs':pairs,
                     'working_band_m':feature['properties']['working_band_m'],'crop':None,'confirmed_boundary':False}
    bundle={'schema':1,'prepared_at_utc':datetime.now(timezone.utc).isoformat(),'basemap':base,'wide_basemap':wide,'layers':layers,'geometry':geo,'features':result,'weather':weather_data,
            'sources':{'weather_docs':'https://open-meteo.com/en/docs/historical-weather-api','weather_terms':'https://open-meteo.com/en/pricing',
                       'soil_docs':'https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html','dem_docs':'https://registry.opendata.aws/copernicus-dem/',
                       'sentinel_docs':'https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html'},
            'source_hashes':protected,'models_changed':False,'observations_created':0}
    dump(W/'public-context.json',bundle);dump(D/'manifest.json',{'protected':protected,'weather_sources':weather_sources,'basemap':base,'output_sha256':sha(W/'public-context.json')})
    for p,h in protected.items():assert sha(ROOT/p)==h
    expected={'leaflet.js':'20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=','leaflet.css':'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY='}
    import base64
    for name,h in expected.items():
        download('https://unpkg.com/leaflet@1.9.4/dist/'+name,W/name)
        assert base64.b64encode(hashlib.sha256((W/name).read_bytes()).digest()).decode()==h
    download('https://raw.githubusercontent.com/Leaflet/Leaflet/v1.9.4/LICENSE',W/'LEAFLET-LICENSE.txt')
    print(json.dumps({'features':len(result),'candidate_sites':sum(len(v['candidate_sites']) for v in result.values()),'weather':{k:v['status'] for k,v in weather_data.items()},'new_basemap':base['source']['newly_collected']},ensure_ascii=False))

if __name__=='__main__':main()
