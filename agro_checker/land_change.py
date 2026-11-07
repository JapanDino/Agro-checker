"""Descriptive paired-pixel changes and explicit, unvalidated inspection filters.

No fitting, crop diagnosis, or write to the immutable observation archives.
"""
from datetime import datetime
import numpy as np


POLICY = {'schema_version':1, 'cell_m':20, 'block_m':60, 'required_pairs':9,
          'required_same_direction':8, 'shift_cells':1, 'required_shift_scenarios':9,
          'display_threshold_default':0.10, 'threshold_is_agronomic':False,
          'shift_range_is_confidence_interval':False, 'diagnoses_enabled':False}


def _time(s):
    t=datetime.fromisoformat(s['acquired_at_utc'].replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Время съёмки должно иметь часовой пояс')
    return t


def _grid(m, key):
    a=np.array(m[key],dtype=float)
    if a.shape!=(m['height'],m['width']) or np.isinf(a).any() or np.any(np.abs(a[np.isfinite(a)])>1):
        raise ValueError('Некорректный растр индекса')
    return a


def _integer(v):
    n=round(v)
    if abs(v-n)>1e-5: raise ValueError('Сетки не совмещены: сравнение без пересчёта невозможно')
    return n


def _values(a):
    return [[round(float(v),5) if np.isfinite(v) else None for v in row] for row in a]


def inspection(block, key, threshold=.1, comparable=True):
    """A display/review rule, never a statistical significance or agronomy test."""
    v=block[key]
    if not comparable or block['paired_cells']!=9 or v['shift_scenarios']!=9: return False
    lo,hi=v['shift_range']
    return bool((lo>=threshold and v['positive_cells']>=8) or
                (hi<=-threshold and v['negative_cells']>=8))


def compare(a, b, ma, mb, reference):
    from pyproj import Transformer
    if not a['point_window_usable'] or not b['point_window_usable'] or a.get('extraction_status')=='error' or b.get('extraction_status')=='error':
        raise ValueError('Выберите две съёмки, пригодные у ориентира')
    days=(_time(b)-_time(a)).total_seconds()/86400
    if days<=0: raise ValueError('Вторая съёмка должна быть позже первой')
    if ma['cell_m']!=20 or mb['cell_m']!=20: raise ValueError('Нужна исходная общая сетка 20 м')
    _integer((ma['west_m']-mb['west_m'])/20);_integer((ma['north_m']-mb['north_m'])/20)
    west=max(ma['west_m'],mb['west_m']);north=min(ma['north_m'],mb['north_m'])
    east=min(ma['west_m']+ma['width']*20,mb['west_m']+mb['width']*20)
    south=max(ma['north_m']-ma['height']*20,mb['north_m']-mb['height']*20)
    w,h=_integer((east-west)/20),_integer((north-south)/20)
    if min(w,h)<=0: raise ValueError('Нет общей территории съёмок')
    ra,ca=_integer((ma['north_m']-north)/20),_integer((west-ma['west_m'])/20)
    rb,cb=_integer((mb['north_m']-north)/20),_integer((west-mb['west_m'])/20)
    full_a={k:_grid(ma,k) for k in ['ndvi','ndmi']};full_b={k:_grid(mb,k) for k in ['ndvi','ndmi']}
    aa={k:v[ra:ra+h,ca:ca+w] for k,v in full_a.items()};bb={k:v[rb:rb+h,cb:cb+w] for k,v in full_b.items()}
    va=np.isfinite(aa['ndvi'])&np.isfinite(aa['ndmi']);vb=np.isfinite(bb['ndvi'])&np.isfinite(bb['ndmi']);paired=va&vb
    deltas={k:np.where(paired,bb[k]-aa[k],np.nan) for k in aa}
    coverage=np.where(paired,0,np.where(va,1,np.where(vb,2,3)))
    comparable=bool(a.get('processing_baseline') and a['processing_baseline']==b.get('processing_baseline'))
    warnings=['Маска облаков не проверена на месте; остаточные облака и тени могут влиять на разность.',
              'Сезонность, освещение, атмосфера и смена поверхности могут менять индексы. Причина по двум снимкам не установлена.',
              'Сдвиги B: −20/0/+20 м по каждой оси, включая диагонали до 28 м. Это сценарии чувствительности, не измеренная ошибка геопривязки и не доверительный интервал.']
    if not comparable: warnings.insert(0,'Версии обработки различаются или неизвестны. Разность показана справочно; окна для осмотра не выделяются.')
    if days>14: warnings.insert(0,'Между съёмками больше 14 дней: сравнение включает сезонные изменения. Это не сигнал о внезапном событии.')
    if a['id'][:3]!=b['id'][:3]: warnings.append('Разные спутники: геометрия наблюдения и калибровка могут различаться.')
    x0,y0=Transformer.from_crs(4326,32637,always_xy=True).transform(reference['longitude'],reference['latitude'])
    inverse=Transformer.from_crs(32637,4326,always_xy=True)
    blocks=[]
    # Nonoverlapping 60m blocks aligned to the global UTM grid, not to each crop edge.
    col0=_integer((np.ceil((west+x0)/60)*60-(west+x0))/20)
    row0=_integer(((north+y0)-np.floor((north+y0)/60)*60)/20)
    for r in range(row0,h-2,3):
        for c in range(col0,w-2,3):
            pp=(slice(r,r+3),slice(c,c+3));count=int(paired[pp].sum())
            block={'id':f'w{r}-{c}','west_m':west+c*20,'north_m':north-r*20,'paired_cells':count}
            lon,lat=inverse.transform(x0+block['west_m']+30,y0+block['north_m']-30)
            block['centre']={'latitude':round(lat,6),'longitude':round(lon,6)}
            for k in deltas:
                values=deltas[k][pp][paired[pp]];shifts=[]
                if count==9:
                    for dr in [-1,0,1]:
                        for dc in [-1,0,1]:
                            sr,sc=rb+r+dr,cb+c+dc
                            if sr<0 or sc<0 or sr+3>mb['height'] or sc+3>mb['width']: continue
                            bp=full_b[k][sr:sr+3,sc:sc+3]
                            # Both indices must remain valid in all shifted pixels.
                            other=full_b['ndmi' if k=='ndvi' else 'ndvi'][sr:sr+3,sc:sc+3]
                            if np.isfinite(bp).all() and np.isfinite(other).all():
                                shifts.append(float(np.median(bp-aa[k][pp])))
                block[k]={'median':round(float(np.median(values)),5) if count else None,
                          'spatial_range':[round(float(values.min()),5),round(float(values.max()),5)] if count else None,
                          'positive_cells':int((values>0).sum()),'negative_cells':int((values<0).sum()),
                          'shift_scenarios':len(shifts),'shift_range':[round(min(shifts),5),round(max(shifts),5)] if shifts else None}
            blocks.append(block)
    # Landmark summary is paired at identical coordinates, never difference of two
    # medians computed on different sets of pixels.
    pc,pr=int(np.floor(-west/20)),int(np.floor(north/20))
    point={'paired_cells':0,'usable':False,'ndvi':None,'ndmi':None}
    if 1<=pr<h-1 and 1<=pc<w-1:
        pp=(slice(pr-1,pr+2),slice(pc-1,pc+2));n=int(paired[pp].sum());point.update(paired_cells=n,usable=bool(paired[pr,pc] and n>=5))
        if point['usable']:
            for k in deltas: point[k]=round(float(np.median(deltas[k][pp][paired[pp]])),5)
    n=int(paired.sum());total_a=ma['width']*ma['height'];total_b=mb['width']*mb['height']
    if n/min(total_a,total_b)<.5: warnings.insert(0,'Общее пригодное покрытие меньше половины вырезки. Вывод на остальную окрестность не распространяется.')
    default_counts={k:sum(inspection(block,k,comparable=comparable) for block in blocks) for k in deltas}
    return {'policy':POLICY,'reference':reference,'before':{k:a.get(k) for k in ['id','map_key','acquired_at_utc','processing_baseline','retrieved_at_utc']},
            'after':{k:b.get(k) for k in ['id','map_key','acquired_at_utc','processing_baseline','retrieved_at_utc']},
            'days_between':days,'processing_comparable':comparable,'warnings':warnings,'orchard_boundary_known':False,
            'grid':{'width':w,'height':h,'west_m':west,'north_m':north,'cell_m':20},
            'delta':{k:_values(v) for k,v in deltas.items()},'coverage':coverage.tolist(),
            'statistics':{'paired_pixels':n,'paired_fraction_before':n/total_a,'paired_fraction_after':n/total_b,
                          'only_before_pixels':int((coverage==1).sum()),'only_after_pixels':int((coverage==2).sum()),
                          'neither_pixels':int((coverage==3).sum()),'overlap_pixels':w*h,
                          'complete_blocks':sum(v['paired_cells']==9 for v in blocks),'default_inspection_counts':default_counts},
            'point':point,'blocks':blocks}


def comparison(period, before, after):
    from . import land_view
    if period=='recent':
        from .sentinel_updates import load_latest
        snapshot,maps=load_latest()
        if not snapshot: raise ValueError('Свежий архив ещё не получен')
        lookup={s.get('map_key'):s for s in snapshot['scenes'] if s.get('map_key')}
        if before not in lookup or after not in lookup: raise KeyError('Съёмки отсутствуют в опубликованной версии; обновите список')
        a,b=lookup[before],lookup[after];ma,mb=maps[before],maps[after]
        ref=snapshot['reference'];version=snapshot['id']
    elif period=='archive':
        base=land_view.overview();lookup={s['id']:s for s in base['satellite']['scenes']}
        if before not in lookup or after not in lookup: raise KeyError('Нет такой архивной съёмки')
        a,b=lookup[before],lookup[after];ma,mb=land_view.scene(before),land_view.scene(after)
        ref=base['reference'];version=base['audit_sha256']
    else: raise ValueError('Неизвестный период')
    result=compare(a,b,ma,mb,ref);result['observation_version']=version
    result['maps']={'before':ma,'after':mb}
    return result
