"""Read-only repeat visits and local satellite evidence. No field inference or fitting."""
from datetime import datetime
import math
import numpy as np

from . import land_inspections as journal

MAX_LAG_DAYS = 7


def place(reference, window):
    """World-grid identity, independent of crop-relative block IDs or image dates."""
    from pyproj import Transformer
    x, y = Transformer.from_crs(4326, 32637, always_xy=True).transform(reference['longitude'], reference['latitude'])
    west, north = x+window['west_m'], y+window['north_m']
    if any(not math.isfinite(v) or abs(v/60-round(v/60))>1e-6 for v in [west,north]):
        raise ValueError('Окно не совпадает с общей сеткой мест')
    west, north = round(west), round(north)
    return {'key':f'utm32637:{west}:{north}:60', 'west_utm_m':west, 'north_utm_m':north,
            'window_m':60, 'centre':window['centre'], 'reference':reference}


def groups(store):
    result={}
    for record in journal.listing(store):
        p=record['payload']; site=place(p['reference'],p['selected_window'])
        group=result.setdefault(site['key'],{'place':site,'record_ids':[],'latest_observed_at_utc':p['observed_at_utc']})
        group['record_ids'].append(record['id'])
        group['latest_observed_at_utc']=max(group['latest_observed_at_utc'],p['observed_at_utc'])
    return sorted(result.values(),key=lambda g:g['latest_observed_at_utc'],reverse=True)


def _time(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('У времени должен быть часовой пояс')
    return d


def _sample(scene, raster, window):
    """Exactly the selected nine ground cells, with no resampling/fill."""
    info={k:scene.get(k) for k in ['id','map_key','acquired_at_utc','processing_baseline','retrieved_at_utc']}
    info.update(valid_cells=0, usable=False, reason='Нет проверенного растра: технический пропуск', ndvi=None, ndmi=None)
    if raster is None:return info,None
    if raster['cell_m']!=20:raise ValueError('Нужна общая сетка 20 м')
    rr=(raster['north_m']-window['north_m'])/20;cc=(window['west_m']-raster['west_m'])/20
    if abs(rr-round(rr))>1e-5 or abs(cc-round(cc))>1e-5:
        info['reason']='Сетка не совмещена с выбранным окном';return info,None
    r,c=round(rr),round(cc)
    if r<0 or c<0 or r+3>raster['height'] or c+3>raster['width']:
        info['reason']='Снимок не покрывает всё выбранное окно';return info,None
    arrays={k:np.array(raster[k],dtype=float)[r:r+3,c:c+3] for k in ['ndvi','ndmi']}
    for a in arrays.values():
        if a.shape!=(3,3) or np.isinf(a).any() or np.any(np.abs(a[np.isfinite(a)])>1):raise ValueError('Некорректный растр индекса')
    valid=np.isfinite(arrays['ndvi'])&np.isfinite(arrays['ndmi']);n=int(valid.sum())
    info['valid_cells']=n
    if n<5 or not valid[1,1]:
        info['reason']='Поверхность/облака: нужны центр и минимум 5 из 9 пригодных пикселей';return info,None
    info.update(usable=True,reason=None)
    for k,a in arrays.items():
        v=a[valid];info[k]={'median':round(float(np.median(v)),5),'spatial_range':[round(float(v.min()),5),round(float(v.max()),5)]}
    return info,arrays


def _visit_source(visit, samples):
    when=_time(visit['payload']['observed_at_utc'])
    candidates=[s for s,a in samples if s['usable'] and _time(s['acquired_at_utc'])<=when]
    if not candidates:return None
    source=max(candidates,key=lambda s:(_time(s['acquired_at_utc']),s['id']))
    lag=(when-_time(source['acquired_at_utc'])).total_seconds()/86400
    return {**source,'lag_days':round(lag,3),'within_lag_limit':lag<=MAX_LAG_DAYS}


def interval(a, b, samples):
    start,end=_visit_source(a,samples),_visit_source(b,samples)
    result={'from_id':a['id'],'to_id':b['id'],'days_between':(_time(b['payload']['observed_at_utc'])-_time(a['payload']['observed_at_utc'])).total_seconds()/86400,
            'before':start,'after':end,'paired_cells':0,'ndvi':None,'ndmi':None,'status':'unavailable','reason':None}
    if _time(a['payload']['observed_at_utc'])==_time(b['payload']['observed_at_utc']):
        result['reason']='Одинаковое время записей: отдельный интервал посещений не подтверждён'
    elif start is None or end is None:
        result['reason']='Нет пригодной съёмки до одного из посещений в выбранном архиве'
    elif not start['within_lag_limit'] or not end['within_lag_limit']:
        result['reason']='Снимок старше 7 дней на момент одного из посещений; изменение между посещениями не рассчитано'
    elif start['id']==end['id']:
        result['status']='same_scene';result['reason']='Обоим посещениям предшествует одна съёмка; спутниковое изменение не измерено'
    elif not start['processing_baseline'] or start['processing_baseline']!=end['processing_baseline']:
        result['reason']='Разные или неизвестные версии обработки: сопоставимое изменение не рассчитано'
    else:
        aa=next(v for s,v in samples if s['id']==start['id']);bb=next(v for s,v in samples if s['id']==end['id'])
        valid=np.isfinite(aa['ndvi'])&np.isfinite(aa['ndmi'])&np.isfinite(bb['ndvi'])&np.isfinite(bb['ndmi'])
        result['paired_cells']=int(valid.sum())
        if result['paired_cells']<5 or not valid[1,1]:result['reason']='Недостаточно общих пригодных пикселей для изменения между посещениями'
        else:
            result['status']='paired';result['reason']='B − A на общих пикселях; это изменение между указанными съёмками, а не измерение в момент посещения'
            for k in ['ndvi','ndmi']:
                v=(bb[k]-aa[k])[valid];result[k]={'median':round(float(np.median(v)),5),'spatial_range':[round(float(v.min()),5),round(float(v.max()),5)]}
    return result


def build(store, reference, window, scenes, maps, inspection_reference, catalogue_window=None):
    site=place(reference,window)
    visits=[r for r in journal.listing(store) if place(r['payload']['reference'],r['payload']['selected_window'])['key']==site['key']]
    visits.sort(key=lambda r:(_time(r['payload']['observed_at_utc']),_time(r['recorded_at_utc']),r['id']))
    samples=[_sample(s,maps.get(s.get('map_key') or s['id']),window) for s in sorted(scenes,key=lambda s:(_time(s['acquired_at_utc']),s['id']))]
    bounds=catalogue_window or ({'start':samples[0][0]['acquired_at_utc'],'end':samples[-1][0]['acquired_at_utc']} if samples else None)
    return {'place':site,'inspection_reference':inspection_reference,'catalogue_window':bounds,'scenes':[s for s,a in samples],'visits':visits,
            'intervals':[interval(a,b,samples) for a,b in zip(visits,visits[1:])],
            'policy':{'minimum_valid_cells':5,'central_cell_required':True,'max_pre_visit_lag_days':MAX_LAG_DAYS,
                      'interpolation':False,'same_world_window_only':True,'automatic_diagnosis':False,
                      'cloud_mask_locally_validated':False,'spatial_range_is_confidence_interval':False}}


def history(store, reference=None, window_id=None, record_id=None):
    if record_id is not None:
        d=journal.saved_comparison(store,record_id);p=journal._record(store,record_id)['payload']
        reference=p['comparison_reference'];window_id=p['selected_window']['id']
    else:d=journal.resolve(reference)
    window=next((b for b in d['blocks'] if b['id']==window_id),None)
    if window is None:raise ValueError('Нет выбранного окна в этой паре снимков')
    if reference['period']=='archive':
        from . import land_view
        base=land_view.overview()
        if base['audit_sha256']!=reference['observation_version']:raise ValueError('Архив изменён')
        scenes=base['satellite']['scenes'];maps={s['id']:land_view.scene(s['id']) for s in scenes}
        from .open_land_data import protocol
        policy=protocol()['satellite'];bounds={'start':policy['start'],'end':policy['end']}
    elif reference['period']=='recent':
        from .sentinel_updates import DIRECTORY,_load
        snapshot,maps=_load(str(DIRECTORY),reference['observation_version'],reference['manifest_sha256']);scenes=snapshot['scenes']
        bounds={'start':snapshot.get('query_start_utc',scenes[0]['acquired_at_utc'] if scenes else None),
                'end':snapshot.get('query_end_utc',scenes[-1]['acquired_at_utc'] if scenes else None)}
    else:raise ValueError('Неизвестный период')
    return build(store,d['reference'],window,scenes,maps,reference,bounds)
