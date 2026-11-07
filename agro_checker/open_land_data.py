"""Provenance-labelled public land context around a landmark, not an orchard.

No fitting or operational prediction. Source/crop bytes are cached immutably.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import numpy as np
import rasterio
from rasterio.windows import Window
from pyproj import CRS, Transformer, Geod
from .pipeline import ROOT, UTC, cached_download, write_json
from .product_data import sha

DIRECTORY = ROOT/'data/working-zone-01/open-land'
PROTOCOL = ROOT/'config/open_land_audit.json'
SOURCE = ROOT/'agro_checker/open_land_data.py'
SOIL_CRS = CRS.from_proj4('+proj=igh +datum=WGS84 +units=m +no_defs')


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def protocol():
    p = read(PROTOCOL)
    if p['reference'] != {'latitude': 45.024106, 'longitude': 37.911914, 'role': 'user-confirmed landmark, NOT orchard centre or boundary'}:
        raise ValueError('Unknown landmark identity')
    if p['orchard_boundary_known']: raise ValueError('Do not invent an orchard boundary')
    return p


def get(url, path):
    def fetch(u):
        with urlopen(Request(u, headers={'User-Agent': 'Onlyfarms-open-land-audit/0.1'}), timeout=30) as r:
            return r.read(), dict(r.headers)
    return cached_download(url, path, fetcher=fetch)


def soil_window(xml_bytes, lon, lat, cells=9):
    if cells<1 or cells % 2 != 1: raise ValueError('Odd native soil window required')
    tree = ET.fromstring(xml_bytes); ns = {'g': 'http://www.opengis.net/gml/3.2'}
    origin = [float(v) for v in tree.find('.//g:origin/g:Point/g:pos', ns).text.split()]
    vectors = [[float(v) for v in node.text.split()] for node in tree.findall('.//g:offsetVector', ns)]
    if vectors != [[250., 0.], [0., -250.]]: raise ValueError('Unexpected soil grid spacing/axes')
    envelope = tree.find('.//g:Envelope', ns)
    if not envelope.attrib['srsName'].endswith('/152160'): raise ValueError('Unexpected soil CRS')
    x, y = Transformer.from_crs(4326, SOIL_CRS, always_xy=True).transform(lon, lat)
    corner_x, corner_y = origin[0]-125, origin[1]+125
    col, row = math.floor((x-corner_x)/250), math.floor((corner_y-y)/250)
    radius = cells//2; left = corner_x+(col-radius)*250; top = corner_y-(row-radius)*250
    return {'x': x, 'y': y, 'bbox': [left, top-cells*250, left+cells*250, top],
            'centre_native_cell': [row, col], 'window_cells': cells}


def soil_value(prop, raw):
    if prop not in ['clay', 'sand', 'silt', 'phh2o', 'soc'] or not math.isfinite(raw) or raw<0:
        raise ValueError('Unknown/invalid soil value')
    value = float(raw)/10
    if prop in ['clay', 'sand', 'silt'] and value>100: raise ValueError('Invalid texture percent')
    if prop=='phh2o' and not 0<=value<=14: raise ValueError('Invalid soil pH')
    return value


def collect_soil():
    p = protocol(); cfg = p['soil']; ref = p['reference']
    desc = DIRECTORY/'raw/soilgrids-clay-description.xml'
    description = get('https://maps.isric.org/mapserv?'+urlencode({'map':'/map/clay.map', 'SERVICE':'WCS', 'VERSION':'2.0.1', 'REQUEST':'DescribeCoverage', 'COVERAGEID':'clay_0-5cm_mean'}), desc)[0]
    window = soil_window(description, ref['longitude'], ref['latitude'], cfg['window_cells']); left, bottom, right, top = window['bbox']
    def one(job):
        prop, depth, stat = job; key = f'{prop}_{depth}_{stat}'
        url = 'https://maps.isric.org/mapserv?'+urlencode([('map',f'/map/{prop}.map'), ('SERVICE','WCS'), ('VERSION','2.0.1'),
             ('REQUEST','GetCoverage'), ('COVERAGEID',key), ('SUBSET',f'X({left:.1f},{right:.1f})'), ('SUBSET',f'Y({bottom:.1f},{top:.1f})'), ('FORMAT','GEOTIFF_INT16')])
        path = DIRECTORY/'raw/soil-native'/(key+'.tif'); raw, meta = get(url, path)
        if not raw.startswith((b'II', b'MM')): raise ValueError('WCS did not return a TIFF')
        with rasterio.open(path) as r:
            if r.shape != (cfg['window_cells'], cfg['window_cells']) or not CRS.from_user_input(r.crs).equals(SOIL_CRS, ignore_axis_order=True):
                raise ValueError('Wrong native soil raster geometry')
            if not np.allclose(tuple(r.transform)[:6], [250,0,left,0,-250,top], rtol=0, atol=.02): raise ValueError('Misaligned soil window')
            grid = r.read(1, masked=True)
            centre = grid[cfg['window_cells']//2, cfg['window_cells']//2]
            if np.ma.is_masked(centre): raise ValueError('No soil value at landmark cell')
            value = soil_value(prop, float(centre))
            valid = [soil_value(prop, float(v)) for v in grid.compressed()]
            result = {'property': prop, 'depth': depth, 'statistic': stat, 'raw_centre': float(centre), 'centre_value': value,
                      'valid_context_cells': len(valid), 'context_value_min': min(valid), 'context_value_max': max(valid),
                      'units': '%' if prop in ['clay','sand','silt'] else 'g/kg' if prop=='soc' else 'pH',
                      'path': str(path.relative_to(ROOT)), 'sha256': sha(path), 'source': meta}
        print('Soil WCS', key, value, flush=True); return result
    jobs = [(a,b,c) for a in cfg['properties'] for b in cfg['depths'] for c in cfg['statistics']]
    with ThreadPoolExecutor(max_workers=3) as pool: values = list(pool.map(one, jobs))
    by = {(v['property'],v['depth'],v['statistic']): v for v in values}
    for prop in cfg['properties']:
        for depth in cfg['depths']:
            low, high = [by[(prop,depth,k)]['centre_value'] for k in ['Q0.05','Q0.95']]
            if low>high: raise ValueError('Soil quantile order invalid')
    out = {'window': window, 'values': values, 'description_sha256': sha(desc), 'source_type': 'external_model_prediction_not_field_measurement',
           'local_interval_coverage_validated': False, 'is_current_soil_moisture': False}
    write_json(DIRECTORY/'soil.json', out); return out


def collect_dem():
    from rasterio.warp import reproject, Resampling, calculate_default_transform
    p = protocol(); lat, lon = [p['reference'][k] for k in ['latitude','longitude']]; radius = p['dem']['half_width_m']
    path = DIRECTORY/'raw/dem-glo30-n45e037.tif'
    url = 'https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/Copernicus_DSM_COG_10_N45_00_E037_00_DEM/Copernicus_DSM_COG_10_N45_00_E037_00_DEM.tif'
    _, meta = get(url, path); geod = Geod(ellps='WGS84')
    west = geod.fwd(lon,lat,270,radius)[0]; east = geod.fwd(lon,lat,90,radius)[0]
    north = geod.fwd(lon,lat,0,radius)[1]; south = geod.fwd(lon,lat,180,radius)[1]
    with rasterio.open(path) as r:
        if r.crs.to_epsg()!=4326 or r.count!=1: raise ValueError('Unexpected DEM reference system/bands')
        w = rasterio.windows.from_bounds(west,south,east,north,r.transform).round_offsets().round_lengths()
        z = r.read(1, window=w, masked=True)
        if z.count()!=z.size or not np.all(np.isfinite(z)): raise ValueError('Missing DEM cells')
        transform = r.window_transform(w); profile = r.profile.copy(); profile.update(width=z.shape[1],height=z.shape[0],transform=transform)
        crop = DIRECTORY/'dem-context-native.tif'
        with rasterio.open(crop, 'w', **profile) as dst: dst.write(z.filled(r.nodata),1)
        bounds = rasterio.windows.bounds(w,r.transform); dx = abs(geod.inv(lon,lat,lon+abs(transform.a),lat)[2]); dy = abs(geod.inv(lon,lat,lon,lat+abs(transform.e))[2])
        tr, width, height = calculate_default_transform(r.crs,'EPSG:32637',z.shape[1],z.shape[0],*bounds,resolution=30)
        utm = np.full((height,width), np.nan, dtype='float32')
        reproject(z.filled(np.nan), utm, src_transform=transform,src_crs=r.crs,dst_transform=tr,dst_crs='EPSG:32637',src_nodata=np.nan,dst_nodata=np.nan,resampling=Resampling.bilinear)
        dzdy,dzdx = np.gradient(utm,30,30); slope = np.degrees(np.arctan(np.hypot(dzdx,dzdy)))
        point = float(next(r.sample([(lon,lat)]))[0])
        out = {'source': meta, 'native_crop': str(crop.relative_to(ROOT)), 'native_crop_sha256': sha(crop),
               'bbox_wgs84': list(bounds), 'native_grid_metres_at_landmark': [dx,dy], 'native_shape': list(z.shape),
               'height_at_landmark_m': point, 'context_min_m': float(z.min()), 'context_max_m': float(z.max()),
               'context_p10_p50_p90_m': np.quantile(z.compressed(), [.1,.5,.9]).tolist(),
               'apparent_dsm_slope_degrees_p50_p90': np.nanquantile(slope,[.5,.9]).tolist(),
               'surface_not_bare_ground': True, 'frost_pockets_validated': False, 'temperature_correction_derived': False}
    write_json(DIRECTORY/'dem.json',out); return out


def catalogue():
    p = protocol(); cfg = p['satellite']
    params = {'collections':cfg['collection'],'bbox':'37.9118,45.0240,37.9120,45.0242','datetime':cfg['start']+'/'+cfg['end'],'limit':100}
    url = 'https://earth-search.aws.element84.com/v1/search?'+urlencode(params); features = {}; pages = []; seen = set()
    for page in range(1,11):
        if url in seen: raise ValueError('Catalogue pagination cycle')
        seen.add(url); path = DIRECTORY/'raw'/('earth-search-c1-spring2024.json' if page==1 else f'earth-search-c1-spring2024-page{page}.json')
        raw, meta = get(url,path); obj = json.loads(raw); pages.append(meta)
        for f in obj['features']:
            if f['collection'] != cfg['collection'] or not cfg['start'] <= f['properties']['datetime'] <= cfg['end']: raise ValueError('Wrong collection/season')
            if f['id'] in features and f != features[f['id']]: raise ValueError('Conflicting duplicate STAC item')
            features[f['id']] = f
        nexts = [l for l in obj['links'] if l['rel']=='next']
        if not nexts or not obj['features']: break
        if len(nexts)!=1 or nexts[0].get('method','GET')!='GET': raise ValueError('Unknown pagination')
        url = nexts[0]['href']
    else: raise ValueError('Unfinished pagination')
    out = {'items': sorted(features.values(),key=lambda f:f['properties']['datetime']), 'pages': pages, 'pagination_completed': True}
    write_json(DIRECTORY/'catalogue.json',out); return out


def head(url):
    with urlopen(Request(url,method='HEAD',headers={'User-Agent':'Onlyfarms-open-land-audit/0.1'}),timeout=30) as r:
        result = {'url':url,'etag':r.headers.get('ETag'),'last_modified':r.headers.get('Last-Modified'),'bytes':int(r.headers.get('Content-Length','0'))}
    if not result['etag'] or not result['bytes']: raise ValueError('Missing source object version evidence')
    return result


def reflectance(raw, asset, actual_scale, actual_offset):
    band = asset['raster:bands'][0]; scale, offset = float(band['scale']), float(band.get('offset',0))
    if scale<=0 or not math.isfinite(scale+offset): raise ValueError('Invalid reflectance conversion')
    if abs(scale-actual_scale)>1e-10 or abs(offset-actual_offset)>1e-10:
        raise ValueError('STAC scale/offset disagree with TIFF; do not guess')
    return np.asarray(raw, dtype=float)*scale+offset


def to_twenty(grid, factor, nodata):
    a = np.asarray(grid)
    if factor not in [1,2] or a.ndim!=2 or any(v%factor for v in a.shape): raise ValueError('Unsupported aligned pixel aggregation')
    valid = np.isfinite(a) & (a!=nodata)
    if factor==1: return a.astype(float), valid
    shape = (a.shape[0]//2,2,a.shape[1]//2,2)
    return a.reshape(shape).mean(axis=(1,3)), valid.reshape(shape).all(axis=(1,3))


def indices(red,nir,swir,scl,raw_valid):
    if not (red.shape==nir.shape==swir.shape==scl.shape==raw_valid.shape): raise ValueError('Spectral/mask grids differ')
    land = np.isin(scl,[4,5]); eroded = np.lib.stride_tricks.sliding_window_view(np.pad(land,1,constant_values=False),(3,3)).all(axis=(-2,-1))
    valid = eroded & raw_valid & np.isfinite(red+nir+swir) & (red>0) & (nir>0) & (swir>0)
    ndvi = np.full(red.shape,np.nan); ndmi = np.full(red.shape,np.nan)
    np.divide(nir-red,nir+red,out=ndvi,where=valid); np.divide(nir-swir,nir+swir,out=ndmi,where=valid)
    return ndvi,ndmi,valid


def extract_scene(f):
    import certifi
    cfg = protocol()['satellite']; ref = protocol()['reference']; sid = f['id']; folder = DIRECTORY/'sentinel'/sid
    if f['collection']!='sentinel-2-c1-l2a' or float(f['properties']['s2:processing_baseline'])<5 or f['properties']['s2:processing_baseline']=='05.09':
        raise ValueError('Unregistered/withdrawn processing baseline')
    if f['properties'].get('earthsearch:boa_offset_applied') is True: raise ValueError('Ambiguous pre-applied offset')
    summary_path = folder/'sample.json'
    if summary_path.exists():
        old = read(summary_path)
        if old['item_sha256'] != __import__('hashlib').sha256(json.dumps(f,sort_keys=True).encode()).hexdigest(): raise ValueError('STAC item changed')
        for path,h in old['crop_sha256'].items():
            if sha(ROOT/path)!=h: raise ValueError('Cached scene crop changed')
        return old
    folder.mkdir(parents=True,exist_ok=True); extracted = {}; source_versions = {}; paths = {}; transforms = {}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_CAINFO=certifi.where(),GDAL_HTTP_TIMEOUT='30',GDAL_HTTP_MAX_RETRY='1'):
        for key in ['scl','red','nir','swir16']:
            asset = f['assets'][key]; url = asset['href']; version = head(url)
            with rasterio.open(url) as r:
                if r.crs.to_epsg()!=32637 or list(r.shape)!=asset['proj:shape'] or not np.allclose(tuple(r.transform)[:6],asset['proj:transform'][:6],rtol=0,atol=1e-6):
                    raise ValueError('Asset coordinate/grid metadata mismatch')
                if r.transform.b or r.transform.d or r.transform.a not in [10,20] or r.transform.e!=-r.transform.a: raise ValueError('Unsupported spectral grid')
                if key=='scl':
                    if r.transform.a!=20: raise ValueError('Expected native20m scene classification')
                    x,y = Transformer.from_crs(4326,r.crs,always_xy=True).transform(ref['longitude'],ref['latitude'])
                    row,col = r.index(x,y); radius = cfg['window_cells']//2
                    common = Window(col-radius,row-radius,cfg['window_cells'],cfg['window_cells']); common_transform = r.window_transform(common)
                factor = int(20/r.transform.a); win = Window(common.col_off*factor,common.row_off*factor,common.width*factor,common.height*factor)
                crop = r.read(1,window=win)
                if crop.shape!=(cfg['window_cells']*factor,cfg['window_cells']*factor): raise ValueError('Scene does not cover context window')
                tr = r.window_transform(win)
                if not np.allclose([tr.c,tr.f],[common_transform.c,common_transform.f],rtol=0,atol=1e-6): raise ValueError('Misaligned band origins')
                profile = r.profile.copy(); profile.update(width=crop.shape[1],height=crop.shape[0],transform=tr,driver='GTiff')
                path = folder/(key+'.tif')
                with rasterio.open(path,'w',**profile) as dst:
                    dst.write(crop,1); dst.scales=r.scales; dst.offsets=r.offsets
                grid,raw_valid = to_twenty(crop,factor,r.nodata)
                if key!='scl': grid = reflectance(grid,asset,r.scales[0],r.offsets[0])
                extracted[key] = (grid,raw_valid); transforms[key] = list(tr)[:6]; paths[str(path.relative_to(ROOT))] = sha(path)
            if head(url)!=version: raise ValueError('Remote COG changed during extraction')
            source_versions[key] = {**version,'declared_full_checksum_not_verified':asset.get('file:checksum'),
                                    'native_scale':float(asset['raster:bands'][0].get('scale',1)), 'native_offset':float(asset['raster:bands'][0].get('offset',0))}
        red,nir,swir = [extracted[k][0] for k in ['red','nir','swir16']]; scl = extracted['scl'][0]
        valid_raw = np.logical_and.reduce([extracted[k][1] for k in cfg['assets']])
        ndvi,ndmi,valid = indices(red,nir,swir,scl,valid_raw); centre = cfg['window_cells']//2; pp = (slice(centre-1,centre+2),slice(centre-1,centre+2))
        count = int(valid[pp].sum()); usable = bool(valid[centre,centre] and count>=cfg['minimum_valid_point_cells'])
        def median(a): return float(np.nanmedian(a[pp])) if usable else None
        out = {'id':sid,'acquired_at_utc':f['properties']['datetime'],'processing_generated_at_utc':f['properties'].get('s2:generation_time'),
               'catalogue_created_at_utc':f['properties'].get('created'),'retrieved_at_utc':datetime.now(UTC).isoformat(),
               'processing_baseline':f['properties']['s2:processing_baseline'], 'cloud_cover_tile_percent':f['properties']['eo:cloud_cover'],
               'common_transform_utm':list(common_transform)[:6], 'common_shape':list(valid.shape), 'common_cell_m':20,
               'source_versions':source_versions, 'crop_sha256':paths, 'source_transforms':transforms,
               'item_sha256':__import__('hashlib').sha256(json.dumps(f,sort_keys=True).encode()).hexdigest(),
               'context_scl_counts':{str(int(v)):int(n) for v,n in zip(*np.unique(scl,return_counts=True))},
               'context_valid_land_fraction':float(valid.mean()),'point_scl':int(scl[centre,centre]),
               'valid_land_cells_in_point_60m_window':count,'point_window_usable':usable,
               'point_60m_ndvi_median':median(ndvi),'point_60m_ndmi_median':median(ndmi),
               'point_ndvi':float(ndvi[centre,centre]) if usable else None,
               'point_ndmi':float(ndmi[centre,centre]) if usable else None,
               'historic_first_availability_proven':False,'orchard_identity_known':False,'is_agronomic_prediction':False}
    write_json(summary_path,out); print('Sentinel',sid,'usable',usable,'validland',round(valid.mean(),3),flush=True); return out


def collect_satellite():
    cat = catalogue()
    with ThreadPoolExecutor(max_workers=3) as pool: samples = list(pool.map(extract_scene,cat['items']))
    out = {'collection':protocol()['satellite']['collection'],'catalogue_items':len(cat['items']),'pages':len(cat['pages']),
           'pagination_completed':cat['pagination_completed'],'samples':samples,'usable_point_dates':sum(s['point_window_usable'] for s in samples),
           'context_not_farm_boundary':True,'historical_monitoring_not_forecasting':True}
    write_json(DIRECTORY/'satellite.json',out); return out


def collect():
    before = {'source_sha256':sha(SOURCE),'protocol_sha256':sha(PROTOCOL),'protocol':protocol(),'started_at_utc':datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'protocol-before-extraction.json',before)
    soil = collect_soil(); dem = collect_dem(); satellite = collect_satellite()
    parents = [ROOT/'agro_checker/pipeline.py', ROOT/'agro_checker/product_data.py', PROTOCOL, SOURCE]
    artifacts = ['soil.json','dem.json','satellite.json','catalogue.json','protocol-before-extraction.json','dem-context-native.tif']
    m = {'parent_sha256':{str(p.relative_to(ROOT)):sha(p) for p in parents},'artifacts_sha256':{n:sha(DIRECTORY/n) for n in artifacts},
         'source_file_sha256':{str(p.relative_to(ROOT)):sha(p) for p in (DIRECTORY/'raw').rglob('*') if p.is_file()},
         'generated_at_utc':datetime.now(UTC).isoformat(),'model_fit_performed':False,'production_forecasts_changed':False,
         'field_truth_added':False,'control_years_for_selection_used':False,
         'software':{'rasterio':rasterio.__version__,'numpy':np.__version__,'pyproj':__import__('pyproj').__version__}}
    write_json(DIRECTORY/'audit.meta.json',m); return m


def load():
    m = read(DIRECTORY/'audit.meta.json')
    checks = {ROOT/p:h for p,h in {**m['parent_sha256'],**m['source_file_sha256']}.items()}
    checks.update({DIRECTORY/p:h for p,h in m['artifacts_sha256'].items()})
    if any(sha(p)!=h for p,h in checks.items()): raise ValueError('Frozen public-land audit changed; create a separate extraction version')
    satellite = read(DIRECTORY/'satellite.json')
    for s in satellite['samples']:
        if any(sha(ROOT/p)!=h for p,h in s['crop_sha256'].items()): raise ValueError('Frozen scene crop changed')
    return read(DIRECTORY/'soil.json'),read(DIRECTORY/'dem.json'),satellite,m


if __name__=='__main__':
    if (DIRECTORY/'audit.meta.json').exists():
        a,b,c,m=load(); print('Frozen public-land audit verified',c['catalogue_items'],c['usable_point_dates'])
    else:
        m=collect(); print('Public-land audit complete; no ML fit or field writes')
