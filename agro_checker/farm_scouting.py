"""Archive evidence -> human task -> reported visit. Never seeds field observations.

Screenshot fragments remain unconfirmed. Satellite maps describe the surface;
neither the display rule nor user reports are an agronomic diagnosis/training set.
"""
from datetime import datetime, date, timezone
from functools import lru_cache
import hashlib
import io
import json
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely import transform
from shapely.geometry import shape, Point, box

from .pipeline import ROOT
from .field_store import digest, encoded, stamp
from .land_inspections import photo_input, _blob
from .farm_context_analysis import scenarios, masks, spectral_stats, metric
from .sector_refresh import extend_archive

D = ROOT/'data/farm-research/area-context'
R = ROOT/'data/farm-research/reconstruction'
STREAM = 'farm-scouting-01'
TASK = 'farm_scouting_task'
VISIT = 'farm_scouting_visit'
TO_UTM = Transformer.from_crs(4326, 32637, always_xy=True)
TO_GEO = Transformer.from_crs(32637, 4326, always_xy=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def archive():
    p = json.loads((D/'protocol.json').read_text(encoding='utf-8'))
    s = json.loads((D/'summary.json').read_text(encoding='utf-8'))
    geo = json.loads((R/'approximate-areas.geojson').read_text(encoding='utf-8'))
    if p['contours_sha256'] != sha(R/'approximate-areas.geojson') or s['provenance']['satellite_manifest_sha256'] != sha(D/'satellite.json') or s['provenance']['protocol_sha256'] != sha(D/'protocol.json'):
        raise ValueError('Версия архива или контуров изменилась; требуется повторная проверка')
    scenes = json.loads((D/'satellite.json').read_text(encoding='utf-8'))['scenes']
    s['_scouting_version'] = sha(D/'summary.json')
    p, s, sources = extend_archive(p, s, {v['id']: v for v in scenes})
    return p, s, {f['properties']['id']: f for f in geo['features']}, sources


def fragment(fid):
    p, s, geo, scenes = archive()
    if not isinstance(fid, str) or fid not in geo:
        raise ValueError('Выберите площадь R01–R27')
    poly = transform(shape(geo[fid]['geometry']), TO_UTM.transform, interleaved=False)
    return p, s['features'][fid], geo[fid], poly


def period_id(value):
    if value not in {p['id'] for p in archive()[0]['periods']}:
        raise ValueError('Выберите архивный период')
    return value


@lru_cache(maxsize=8)
def raster(scene_id):
    source = archive()[3].get(scene_id)
    if not source or source['status'] != 'ready':
        raise ValueError('Съёмка недоступна в проверенном архиве')
    directory = source.get('observation_directory')
    path = (Path(directory) if directory else D)/'sentinel'/scene_id/'indices.npz'
    if sha(path) != source['crop_sha256']['indices.npz']:
        raise ValueError('Растр не прошёл проверку целостности')
    with np.load(path, allow_pickle=False) as a:
        result = {k: a[k].copy() for k in ['ndvi', 'ndmi', 'valid']}
    p = archive()[0];x0,y0,x1,y1 = p['bounds_utm']
    expected = (int((y1-y0)/20), int((x1-x0)/20))
    if any(a.shape != expected for a in result.values()):
        raise ValueError('Не совпадает сетка спутниковых наблюдений')
    return result


def overview():
    p, s, geo, scenes = archive()
    from .farm_workspace import catalog
    default_period = next((v['id'] for v in reversed(p['periods']) if catalog(v['id'])['default']), 'recent2026')
    items = []
    for fid, v in s['features'].items():
        items.append({'id': fid, 'working_band_m': geo[fid]['properties']['working_band_m'],
                      'core_supported': max((r.get('core_cells',0) for r in v['history']),default=0)>=10,
                      'core_area_ha': v['core_area_ha'], 'availability': v['availability'],
                      'latest_usable_acquisition_utc': max((r['acquired_at_utc'] for r in v['history']
                          if r.get('ndvi') and r['ndvi']['base'] is not None and r['ndmi']['base'] is not None), default=None)})
    return {'items': items, 'periods': p['periods'],
            'snapshot_at': max(v['acquired_at_utc'] for v in scenes.values() if v['status']=='ready'),
            'archive_updated_at': p['created_at_utc'],
            'default_period': default_period,
            'confirmed_boundaries': False, 'automatic_diagnoses': False,
            'model_training_enabled': False, 'version': s['_scouting_version']}


@lru_cache(maxsize=64)
def detail(fid, period, before=None, after=None):
    p, item, geo, poly = fragment(fid);period_id(period)
    rows = [v for v in item['history'] if v['period'] == period]
    available = [v for v in rows if v.get('ndvi') and v['ndvi']['base'] is not None and v['ndmi']['base'] is not None]
    choices = [{'id': v['id'], 'at': v['acquired_at_utc'], 'coverage': v.get('coverage', 0),
                'usable': v in available, 'baseline': v.get('processing_baseline'),
                'ndvi': v['ndvi']['base'] if v.get('ndvi') else None,
                'ndmi': v['ndmi']['base'] if v.get('ndmi') else None}
               for v in rows if v['source_status'] == 'ready']
    pair = next((v for v in item['changes'] if v['period'] == period), {})
    pair = pair.get('same_baseline_comparison', pair)
    result = {'fragment': fid, 'period': period, 'dates': choices,
              'working_band_m': geo['properties']['working_band_m'], 'pair': None,
              'height_m': item['dsm']['height']['base'], 'core_supported': item['core_area_ha'] >= .4}
    if not before and not after:
        if not pair.get('available'):return result
        before, after = pair['before_id'], pair['after_id']
    lookup = {v['id']: v for v in available}
    if before not in lookup or after not in lookup:
        raise ValueError('Для сравнения нужны две пригодные съёмки выбранной площади')
    a, b = lookup[before], lookup[after]
    if stamp(a['acquired_at_utc']) >= stamp(b['acquired_at_utc']):
        raise ValueError('Вторая дата должна быть позже первой')
    aa, bb = raster(before), raster(after);common = aa['valid'] & bb['valid']
    x0,y0,x1,y1 = p['bounds_utm'];tr = from_origin(x0,y1,20,20)
    ids = masks(scenarios(poly, geo['properties']['working_band_m']), tr, common.shape)
    count = len(ids['base']);covered = int(common.ravel()[ids['base']].sum())
    same = bool(a['processing_baseline'] and a['processing_baseline'] == b['processing_baseline'])
    metrics = {}
    for key in ['ndvi', 'ndmi']:
        delta = bb[key]-aa[key]
        stats = {name:spectral_stats(delta, common, ix) for name,ix in ids.items()}
        m = metric({name:v['value'] for name,v in stats.items()}, .05, len(ids['core']) >= 10 and stats['core']['usable'])
        m['robust'] = m['robust'] and same
        ix = ids['base'];valid = common.ravel()[ix];ix = ix[valid]
        m['before'] = float(np.median(aa[key].ravel()[ix])) if stats['base']['usable'] else None
        m['after'] = float(np.median(bb[key].ravel()[ix])) if stats['base']['usable'] else None
        metrics[key] = m
    bounds = poly.bounds
    c0 = max(0, math.floor((bounds[0]-x0-160)/20));c1 = min(common.shape[1], math.ceil((bounds[2]-x0+160)/20))
    r0 = max(0, math.floor((y1-bounds[3]-160)/20));r1 = min(common.shape[0], math.ceil((y1-bounds[1]+160)/20))
    result['pair'] = {'before': {'id':before,'at':a['acquired_at_utc'],'baseline':a['processing_baseline']},
                      'after': {'id':after,'at':b['acquired_at_utc'],'baseline':b['processing_baseline']},
                      'days': (stamp(b['acquired_at_utc'])-stamp(a['acquired_at_utc'])).total_seconds()/86400,
                      'same_baseline': same, 'coverage': covered/count if count else 0,
                      'paired_cells': covered, 'metrics': metrics,
                      'crop': {'west':x0+c0*20,'north':y1-r0*20,'width':c1-c0,'height':r1-r0,'cell_m':20},
                      'version': archive()[1]['_scouting_version'], 'contours_sha256': p['contours_sha256'],
                      'source_hashes': {'satellite_manifest':archive()[1]['provenance']['satellite_manifest_sha256'],
                                        'before_indices':archive()[3][before]['crop_sha256']['indices.npz'],
                                        'after_indices':archive()[3][after]['crop_sha256']['indices.npz'],
                                        'comparison_code':sha(ROOT/'agro_checker/farm_scouting.py')},
                      'selection': 'Date pair pinned by before/after scene IDs; archive comparison, not forecast. Shared overview date-selection policy is supplied separately.'}
    core = poly.buffer(-geo['properties']['working_band_m'])
    pt = (core if not core.is_empty else poly).representative_point()
    result['default_location'] = location(fid, pt.x, pt.y)
    result['default_place_comparison'] = window_comparison(result['default_location']['id'],before,after)
    return result


def location(fid, x=None, y=None, relative=None, crop=None):
    poly = fragment(fid)[3]
    if relative is not None:
        if not isinstance(relative, list) or len(relative) != 2 or any(type(v) not in [int,float] or not math.isfinite(v) or not 0 <= v <= 1 for v in relative):
            raise ValueError('Выберите место внутри площади на карте индекса')
        x = crop['west']+relative[0]*crop['width']*20;y = crop['north']-relative[1]*crop['height']*20
    if not poly.covers(Point(x,y)):
        raise ValueError('Выберите место внутри приблизительной обводки')
    cx,cy = math.floor(x/60),math.floor(y/60)
    cell = box(cx*60,cy*60,cx*60+60,cy*60+60)
    # Canonical representative point in this fragment/cell keeps repeat-visit identity stable.
    point = poly.intersection(cell).representative_point()
    lon,lat = TO_GEO.transform(point.x,point.y)
    return {'id':f'{fid}-{cx}-{cy}', 'fragment':fid, 'latitude':round(lat,7),'longitude':round(lon,7),
            'utm_x':point.x,'utm_y':point.y,'cell_m':60,'source':'selected_map_cell_not_measured_gps'}


def map_png(fid, period, before, after, key, side):
    if key not in ['ndvi','ndmi'] or side not in ['before','after','delta']:
        raise ValueError('Неизвестный слой карты')
    d = detail(fid,period,before,after);crop = d['pair']['crop'];p = archive()[0]
    c0 = round((crop['west']-p['bounds_utm'][0])/20);r0 = round((p['bounds_utm'][3]-crop['north'])/20)
    sl = (slice(r0,r0+crop['height']),slice(c0,c0+crop['width']))
    aa,bb = raster(before),raster(after)
    valid = (aa['valid'] & bb['valid'])[sl] if side == 'delta' else (aa if side == 'before' else bb)['valid'][sl]
    vals = (bb[key]-aa[key])[sl] if side == 'delta' else (aa if side == 'before' else bb)[key][sl]
    val = np.nan_to_num(vals)
    colours = np.array([[169,74,58],[242,236,208],[50,125,90]],dtype=float) if side == 'delta' else np.array([[163,122,79],[231,217,153],[40,113,78]],dtype=float) if key == 'ndvi' else np.array([[182,145,103],[223,224,198],[42,121,139]],dtype=float)
    t = np.clip((val+.3)/.6,0,1) if side == 'delta' else np.clip((val+1)/2,0,1)
    rgb = np.stack([np.interp(t,[0,.5,1],colours[:,i]) for i in range(3)],axis=-1).astype('uint8')
    rr,cc = np.indices(valid.shape);shade = np.where(((rr//2+cc//2)%2)==0,224,240).astype('uint8')
    rgb[~valid] = np.stack([shade]*3,axis=-1)[~valid]
    scale = max(1,min(5,720//max(crop['width'],crop['height'])))
    image = Image.fromarray(rgb).resize((crop['width']*scale,crop['height']*scale),Image.Resampling.NEAREST)
    poly = fragment(fid)[3];draw = ImageDraw.Draw(image)
    ring = [((x-crop['west'])/20*scale,(crop['north']-y)/20*scale) for x,y in poly.exterior.coords]
    draw.line(ring,fill='white',width=max(3,scale+2));draw.line(ring,fill='#213d2e',width=max(1,scale))
    out = io.BytesIO();image.save(out,format='PNG');return out.getvalue()


def listing(store):
    tasks = store.records(TASK,STREAM);visits = store.records(VISIT,STREAM)
    return {'tasks': tasks, 'visits': visits, 'diagnoses_enabled':False,'training_enabled':False}


def _text(value, limit, label, optional=False):
    if not isinstance(value,str) or len(value)>limit or (not optional and not value.strip()):raise ValueError(label)
    return value.strip()


def _append(store, kind, raw, payload, now, blobs=()):
    key = raw.get('request_id')
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Обновите форму перед сохранением')
    h = digest(raw)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for rowkey,body in c.execute('SELECT id,payload FROM events WHERE kind=? AND profile_id=?',(kind,STREAM)):
            old = json.loads(body)
            if old['request_id'] == key:
                if old['request_sha256'] != h:raise ValueError('Этот запрос уже сохранён с другим содержимым')
                return next(v for v in store.records(kind,STREAM) if v['id'] == rowkey)
        for folder,blobkey,suffix,content in blobs:_blob(store.directory/folder,blobkey,suffix,content)
        payload.update(request_id=key,request_sha256=h,schema_version=1,automatic_diagnosis=None,model_training_eligible=False,forecast_scoring_eligible=False)
        return store.append(kind,STREAM,payload,now,c)


def save_task(store, raw, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(raw,dict) or set(raw) != {'request_id','fragment','period','before','after','version','relative','actor','planned_date','objective','metric'}:
        raise ValueError('Нужны место, архивная пара и описание задачи')
    actor = _text(raw['actor'],120,'Укажите ответственного')
    objective = _text(raw['objective'],2000,'Опишите цель осмотра, до 2000 символов')
    planned = raw['planned_date']
    if planned:
        if not isinstance(planned,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',planned) or date.fromisoformat(planned).year < 2000:raise ValueError('Некорректная дата задачи')
    elif planned != '':raise ValueError('Некорректная дата задачи')
    if raw['metric'] not in ['ndvi','ndmi']:raise ValueError('Выберите индекс')
    d = detail(raw['fragment'],raw['period'],raw['before'],raw['after'])
    if not d['pair'] or raw['version'] != d['pair']['version']:raise ValueError('Архив обновлён. Повторите выбор места')
    loc = d['default_location'] if raw['relative'] is None else location(raw['fragment'],relative=raw['relative'],crop=d['pair']['crop'])
    payload = {'place':loc,'actor':actor,'planned_date':planned,'objective':objective,
               'evidence':d['pair'],'period':raw['period'],'metric':raw['metric'],
               'place_evidence':window_comparison(loc['id'],raw['before'],raw['after']),
               'purpose':'human_task_from_archive_not_current_alert','confirmed_boundary':False}
    return _append(store,TASK,raw,payload,now)


def save_visit(store, raw, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(raw,dict) or set(raw) != {'request_id','task_id','actor','observed_at','notes','outcome','follow_up','photos','real_visit_confirmed'}:
        raise ValueError('Заполните запись реального посещения')
    if raw['real_visit_confirmed'] is not True:raise ValueError('Подтвердите, что это реальное посещение')
    task = next((v for v in store.records(TASK,STREAM) if v['id'] == raw['task_id']),None)
    if not task:raise ValueError('Не найдена задача на осмотр')
    when = stamp(raw['observed_at'])
    if when > now or when.year < 2000:raise ValueError('Дата посещения должна быть в прошлом, с 2000 года')
    if raw['outcome'] not in ['observed_change','nothing_notable','unclear','not_checked']:raise ValueError('Выберите результат посещения')
    if not isinstance(raw['photos'],list) or len(raw['photos']) > 3:raise ValueError('Можно приложить до трёх фотографий')
    photos = [photo_input(v) for v in raw['photos']]
    if len({p['id'] for p,b in photos}) != len(photos):raise ValueError('Одна фотография выбрана несколько раз')
    payload = {'task_id':task['id'],'place':task['payload']['place'],
               'actor':_text(raw['actor'],120,'Укажите автора осмотра'),
               'observed_at_utc':when.isoformat(),'notes':_text(raw['notes'],10000,'Запишите фактические наблюдения'),
               'outcome':raw['outcome'],'follow_up':_text(raw['follow_up'],2000,'Следующий шаг: до 2000 символов',True),
               'photos':[p for p,b in photos],'observation_source':'manual_unverified_report',
               'retrospective_entry':when < stamp(task['recorded_at_utc']),
               'real_visit_confirmed':True}
    return _append(store,VISIT,raw,payload,now,[('farm-scouting-media',p['id'],'.bin',b) for p,b in photos])


def saved_photo(store, key, photo_id):
    visit = next((v for v in store.records(VISIT,STREAM) if v['id'] == key),None)
    photo = next((p for p in visit['payload']['photos'] if p['id'] == photo_id),None) if visit else None
    if not photo or not re.fullmatch('[a-f0-9]{64}',photo_id):raise KeyError('Нет такой фотографии')
    path = store.directory/'farm-scouting-media'/(photo_id+'.bin')
    if sha(path) != photo['sha256']:raise ValueError('Фотография не прошла проверку целостности')
    return path.read_bytes(),photo['mime']


@lru_cache(maxsize=128)
def cell_indices(place_id):
    match = re.fullmatch(r'(R\d{2})-(\d+)-(\d+)',place_id or '')
    if not match:raise ValueError('Неизвестное место')
    fid,cx,cy = match[1],int(match[2]),int(match[3])
    p,item,geo,poly = fragment(fid)
    cell = poly.intersection(box(cx*60,cy*60,cx*60+60,cy*60+60))
    if cell.is_empty:raise ValueError('Место вне площади')
    x0,y0,x1,y1 = p['bounds_utm'];width = int((x1-x0)/20)
    # Exact containment of the nine native cell footprints; the radial guard
    # used for arbitrary fragment polygons would unnecessarily remove edge cells.
    ix = []
    for yy in range(cy*60,cy*60+60,20):
        for xx in range(cx*60,cx*60+60,20):
            if x0<=xx<x1 and y0<=yy<y1 and poly.covers(box(xx,yy,xx+20,yy+20)):
                ix.append(int((y1-yy-20)/20)*width+int((xx-x0)/20))
    ix = np.array(ix,dtype=np.int32)
    return fid,ix


def window_comparison(place_id,before,after):
    fid,ix = cell_indices(place_id)
    aa,bb = raster(before),raster(after);common=aa['valid']&bb['valid']
    total=len(ix);paired=int(common.ravel()[ix].sum())
    result={'paired_cells':paired,'total_cells':total,'coverage':paired/total if total else 0}
    for key in ['ndvi','ndmi']:
        stat=spectral_stats(bb[key]-aa[key],common,ix,minimum=5,coverage=.8)
        result[key]=stat['value']
    return result


@lru_cache(maxsize=32)
def place_series(place_id):
    fid,ix=cell_indices(place_id);item=fragment(fid)[1]
    rows = []
    for row in item['history']:
        if row['source_status'] != 'ready':continue
        a = raster(row['id'])
        values = {key:spectral_stats(a[key],a['valid'],ix,minimum=5,coverage=.8) for key in ['ndvi','ndmi']}
        rows.append({'at':row['acquired_at_utc'],'period':row['period'],'scene':row['id'],
                     'baseline':row['processing_baseline'], 'ndvi':values['ndvi']['value'],
                     'ndmi':values['ndmi']['value'], 'coverage':values['ndvi']['valid_fraction']})
    return {'place_id':place_id,'rows':rows,'cell_m':60,'total_cells':len(ix),
            'policy':'fixed 60m map cell clipped to approximate fragment; wholly contained 20m cells; >=5 valid and >=80%; no interpolation'}
