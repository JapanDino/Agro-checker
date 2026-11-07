"""Where to survey before placing a sensor node: relief, tall objects, soil, recorded foci.

Every proposed point is a place for a human site visit, never an installation decision.
Rules are transparent scores, not a trained model. Inputs are committed archive files
(web/scouting/assets/public-context.json and terrain.png) plus the farm's own journal.
"""
from functools import lru_cache
import json
import math

import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely import transform as shp_transform
from shapely.ops import nearest_points

from .pipeline import ROOT

ASSETS = ROOT/'web/scouting/assets'
STREAM = 'farm-scouting-01'
IMPORT_KIND = 'farm_records_import'
MARK_KIND = 'field_inspection_mark'
STATUS = 'candidate_for_site_visit_not_sensor_installation'
# terrain.png: build_scouting_workspace.layer() maps 0..100 m linearly onto this 3-stop ramp.
RAMP = np.array([[223, 229, 204], [163, 179, 133], [92, 101, 73]], float)
TALL_M = 4.0          # cell stands this much above the field level and its 150 m neighbourhood
FLAT_SPREAD_M = 3.0   # field p10-p90 DSM spread above this: slope, bumps are not separable
ZONE_M = 150          # look for tall objects this far around the outline
MIN_GAP_M = 60
BASE_SCORE = {'focus': 3.0, 'low': 2.0, 'shelter': 2.0, 'high': 1.0}
ORDER = {'focus': 0, 'low': 1, 'shelter': 2, 'high': 3}
NAMES = {'low': 'Нижняя точка рельефа', 'high': 'Верхняя точка рельефа (контроль)',
         'shelter': 'У высоких объектов (возможная лесополоса)', 'focus': 'У записанного очага'}
TO_UTM = Transformer.from_crs(4326, 32637, always_xy=True)
TO_GEO = Transformer.from_crs(32637, 4326, always_xy=True)
MERC_TO_UTM = Transformer.from_crs(3857, 32637, always_xy=True)
GEO_TO_MERC = Transformer.from_crs(4326, 3857, always_xy=True)
METHOD = ('Прозрачные баллы, не обученная модель: очаг с координатами 3, нижняя точка DSM 2, '
          'точка у высоких объектов 2, верхняя (контрольная) точка 1; +2 точке в пределах 150 м '
          'от записанного очага. Точки ближе 60 м объединяются, остаётся до четырёх. '
          'Почва (SoilGrids, 250 м) сравнивает площади между собой и внутри площади точки не двигает.')


@lru_cache(maxsize=1)
def context():
    return json.loads((ASSETS/'public-context.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def surface():
    """Invert terrain.png back to surface height (m) and pixel-centre UTM coordinates."""
    image = np.array(Image.open(ASSETS/'terrain.png'))
    rgb, valid = image[..., :3].astype(float), image[..., 3] > 0
    best, t = np.full(valid.shape, np.inf), np.zeros(valid.shape)
    for k in range(2):
        a, b = RAMP[k], RAMP[k+1]
        v = b-a
        u = np.clip(((rgb-a) @ v)/(v @ v), 0, 1)
        residual = np.linalg.norm(rgb-(a+u[..., None]*v), axis=-1)
        better = residual < best
        best[better], t[better] = residual[better], (k+u[better])*.5
    spec = context()['layers']['terrain']
    lo, hi = spec['min'], spec['max']
    height = np.where(valid & (best <= 3), lo+t*(hi-lo), np.nan)
    (south, west), (north, east) = spec['bounds']
    x0, y0 = GEO_TO_MERC.transform(west, south)
    x1, y1 = GEO_TO_MERC.transform(east, north)
    rows, cols = height.shape
    mx = x0+(np.arange(cols)+.5)*(x1-x0)/cols
    my = y1-(np.arange(rows)+.5)*(y1-y0)/rows
    gx, gy = np.meshgrid(mx, my)
    ux, uy = MERC_TO_UTM.transform(gx, gy)
    pad = np.pad(height, 2, mode='edge')
    windows = sliding_window_view(pad, (5, 5))
    with np.errstate(all='ignore'):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            local = np.nanmedian(windows, axis=(-1, -2))
    return {'height': height, 'local': local, 'x': ux, 'y': uy,
            'residual_p99': float(np.percentile(best[valid], 99)),
            'step_m': float((hi-lo)*.5/np.abs(RAMP[0]-RAMP[1]).max())}


def outline(fid):
    if not isinstance(fid, str):
        raise ValueError('Неизвестная площадь')
    for feature in context()['geometry']['features']:
        if feature['properties']['id'] == fid:
            geo = shape(feature['geometry'])
            return feature, geo, shp_transform(geo, TO_UTM.transform, interleaved=False)
    raise ValueError('Неизвестная площадь')


def _site(kind, x, y, reasons, extra=None):
    lon, lat = TO_GEO.transform(x, y)
    return {'kind': kind, 'name': NAMES[kind], 'latitude': lat, 'longitude': lon, 'x': x, 'y': y,
            'score': BASE_SCORE[kind], 'reasons': list(reasons), 'status': STATUS, **(extra or {})}


def _inside(poly, x, y, margin=15):
    """Move a point into the outline (with a small margin) if it lies outside it."""
    point = Point(x, y)
    core = poly.buffer(-margin)
    target = core if not core.is_empty else poly
    if target.contains(point):
        return x, y
    p = nearest_points(target, point)[0]
    return p.x, p.y


# ── factors ──
def relief(fid, poly):
    feature = context()['features'][fid]
    spread = feature['dsm'].get('base_p10_p90_m')
    sites = []
    for c in feature.get('candidate_sites', []):
        x, y = _inside(poly, c['utm_x'], c['utm_y'])
        if c['kind'] == 'low':
            reasons = [f"Низкая часть поверхности DSM ({c['surface_height_m']:.1f} м): там может дольше держаться "
                       'влага и застаиваться холодный воздух (гипотеза, на месте не проверено).']
        else:
            reasons = [f"Высокая часть поверхности DSM ({c['surface_height_m']:.1f} м): контрольная точка "
                       'для сравнения с низиной.']
        sites.append(_site(c['kind'], x, y, reasons, {'surface_height_m': c['surface_height_m']}))
    if sites:
        summary = (f'Перепад поверхности внутри ядра обводки {spread[0]:.1f}–{spread[1]:.1f} м (p10–p90). '
                   'Предложены нижняя и верхняя точки.')
        status = 'used'
    else:
        summary = ('Перепад поверхности внутри ядра меньше 1 м или ядро слишком мало: '
                   'низину и возвышение по DSM 30 м не выделить.')
        status = 'no_data'
    return {'id': 'relief', 'name': 'Рельеф', 'status': status, 'summary': summary,
            'details': {'source': 'Copernicus DSM 30 м (поверхность, включает растительность и постройки)',
                        'p10_p90_m': spread, 'candidates': len(sites)}}, sites


def _components(mask):
    """8-connected components of a boolean grid; returns lists of (row, col)."""
    seen = np.zeros(mask.shape, bool)
    out = []
    for r, c in zip(*np.nonzero(mask)):
        if seen[r, c]:
            continue
        stack, comp = [(r, c)], []
        seen[r, c] = True
        while stack:
            i, j = stack.pop()
            comp.append((i, j))
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    a, b = i+di, j+dj
                    if 0 <= a < mask.shape[0] and 0 <= b < mask.shape[1] and mask[a, b] and not seen[a, b]:
                        seen[a, b] = True
                        stack.append((a, b))
        out.append(comp)
    return out


def tall_objects(fid, poly):
    spread = context()['features'][fid]['dsm'].get('base_p10_p90_m')
    base = {'id': 'tall_objects', 'name': 'Лесополосы и высокие объекты',
            'details': {'source': 'terrain.png — отображение Copernicus DSM 30 м, высота восстановлена из цветовой шкалы',
                        'threshold_m': TALL_M, 'search_zone_m': ZONE_M}}
    if not spread or spread[1]-spread[0] > FLAT_SPREAD_M:
        return {**base, 'status': 'unavailable',
                'summary': ('Площадь на склоне: по сетке 30 м выступ лесополосы не отличить от формы рельефа. '
                            'Лесополосы здесь нужно отметить на месте.')}, []
    s = surface()
    zone = poly.buffer(ZONE_M)
    minx, miny, maxx, maxy = zone.bounds
    box = (s['x'] >= minx) & (s['x'] <= maxx) & (s['y'] >= miny) & (s['y'] <= maxy) & np.isfinite(s['height'])
    rows, cols = np.nonzero(box)
    if not len(rows):
        return {**base, 'status': 'unavailable', 'summary': 'Площадь вне покрытия слоя поверхности.'}, []
    from shapely import contains_xy
    in_zone = contains_xy(zone, s['x'][rows, cols], s['y'][rows, cols])
    in_field = contains_xy(poly, s['x'][rows, cols], s['y'][rows, cols])
    field_level = float(np.median(s['height'][rows, cols][in_field])) if in_field.any() else float(np.median(s['height'][rows, cols]))
    h, local = s['height'][rows, cols], s['local'][rows, cols]
    tall = in_zone & (h-field_level >= TALL_M) & (h-local >= TALL_M*.75)
    mask = np.zeros(s['height'].shape, bool)
    mask[rows[tall], cols[tall]] = True
    strips = []
    for comp in _components(mask):
        if len(comp) < 2:
            continue
        r, c = np.array(comp).T
        xs, ys = s['x'][r, c], s['y'][r, c]
        pts = np.c_[xs-xs.mean(), ys-ys.mean()]
        ev = np.sort(np.linalg.eigvalsh(np.cov(pts.T))) if len(comp) > 2 else np.array([0, 1.])
        elong = math.sqrt(ev[1]/max(ev[0], 1.0))
        length = 30*len(comp)
        strips.append({'cells': len(comp), 'length_m': length, 'elongation': round(elong, 1),
                       'linear': elong >= 2 and len(comp) >= 3,
                       'rise_m': round(float(np.max(s['height'][r, c])-field_level), 1),
                       'xy': np.c_[xs, ys]})
    strips.sort(key=lambda v: (v['linear'], v['cells']), reverse=True)
    public = [{k: v for k, v in st.items() if k != 'xy'} for st in strips[:5]]
    details = {**base['details'], 'field_level_m': round(field_level, 1), 'objects': public,
               'height_step_m': round(s['step_m'], 2)}
    if not strips:
        return {**base, 'status': 'used', 'details': details,
                'summary': f'В пределах {ZONE_M} м от обводки выступов ≥ {TALL_M:.0f} м над уровнем поля по DSM 30 м не найдено. '
                           'Узкие лесополосы сетка 30 м может не показать.'}, []
    best = strips[0]
    core = poly.buffer(-45)
    target = core if not core.is_empty else poly.buffer(-15)
    if target.is_empty:
        target = poly
    from shapely.geometry import MultiPoint
    strip_geom = MultiPoint([tuple(p) for p in best['xy']]).buffer(15)
    # nearest point of the field core to the strip, kept at least ~45 m from it
    p = nearest_points(target, strip_geom)[0]
    kind_text = 'вытянутый, похож на лесополосу' if best['linear'] else 'компактный: роща, постройка или помеха'
    reasons = [f"Рядом выступ поверхности до +{best['rise_m']:.0f} м над уровнем поля, около {best['length_m']} м ({kind_text}). "
               'У полосы дольше держится роса и тише ветер (гипотеза); с какой стороны подветренная, по данным не определено.']
    sites = [_site('shelter', p.x, p.y, reasons, {'object_rise_m': best['rise_m'], 'object_linear': best['linear']})]
    n_lin = sum(v['linear'] for v in strips)
    summary = (f'Найдено высоких объектов: {len(strips)}, из них вытянутых: {n_lin}. '
               'Это выступы DSM — лесополоса, роща или постройка; проверить на месте. Узкие полосы внутри поля сетка 30 м обычно не показывает.')
    return {**base, 'status': 'used', 'details': details, 'summary': summary}, sites


def soil(fid):
    feats = context()['features']

    def value(f, prop):
        return next((v['sensitivity']['base'] for v in feats[f]['soil'] if v['property'] == prop and v['depth'] == '0-5cm'), None)
    clay, ph = value(fid, 'clay'), value(fid, 'phh2o')
    farm = sorted(v for v in (value(f, 'clay') for f in feats) if v is not None)
    if clay is None:
        return {'id': 'soil', 'name': 'Почва', 'status': 'no_data', 'summary': 'Нет оценки SoilGrids для площади.',
                'details': {}}
    share = sum(v <= clay for v in farm)/len(farm)
    heavier = share >= .5
    summary = (f'Глина 0–5 см ≈ {clay:.0f} % ({"тяжелее" if heavier else "легче"} половины площадей хозяйства), pH ≈ {ph:.1f}. '
               + ('На более глинистой почве поверхность дольше остаётся влажной (гипотеза): площадь стоит раньше в очереди на узел. '
                  if heavier else '')
               + 'Сетка 250 м: внутри площади место узла не уточняет.')
    return {'id': 'soil', 'name': 'Почва', 'status': 'used', 'summary': summary,
            'details': {'source': 'SoilGrids 2.0, модель 250 м, не лабораторный анализ', 'clay_0_5cm_pct': round(clay, 1),
                        'ph_0_5cm': round(ph, 2) if ph is not None else None, 'farm_clay_rank_share': round(share, 2),
                        'heavier_than_farm_median': heavier}}


def _foci(store, fid):
    out = []
    if store is None:
        return out
    try:
        imports = store.records(IMPORT_KIND)
    except Exception:
        imports = []
    if imports:
        latest = max(imports, key=lambda r: r.get('recorded_at_utc', ''))
        for row in (latest.get('payload') or {}).get('rows') or []:
            if isinstance(row, dict) and row.get('type') == 'focus' and row.get('fid') == fid:
                out.append({'source': 'import', 'date': row.get('date'), 'disease': row.get('disease'),
                            'latitude': row.get('latitude'), 'longitude': row.get('longitude')})
    try:
        marks = store.records(MARK_KIND)
    except Exception:
        marks = []
    for mark in marks:
        p = mark.get('payload') or {}
        if p.get('fid') == fid and p.get('result') == 'focus':
            out.append({'source': 'mark', 'date': p.get('observed_on'), 'disease': p.get('disease'),
                        'latitude': p.get('latitude'), 'longitude': p.get('longitude')})
    return out


def _coords(f):
    try:
        lat, lon = float(f['latitude']), float(f['longitude'])
    except (TypeError, ValueError, KeyError):
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def foci(store, fid, poly):
    items = _foci(store, fid)
    base = {'id': 'foci', 'name': 'Очаги', 'details': {'source': 'учёт хозяйства (импорт) и отметки осмотра «нашёл очаг»',
                                                     'count': len(items)}}
    if not items:
        return {**base, 'status': 'no_data',
                'summary': 'Очаги для этой площади не записаны. Их можно загрузить из учёта или отметить после осмотра.'}, [], []
    located = []
    for f in items:
        c = _coords(f)
        if c:
            x, y = TO_UTM.transform(c[1], c[0])
            if poly.distance(Point(x, y)) <= 200:
                located.append((f, x, y))
    sites = []
    if located:
        located.sort(key=lambda v: str(v[0].get('date') or ''), reverse=True)
        f, x, y = located[0]
        x, y = _inside(poly, x, y)
        label = f.get('disease') or 'болезнь не указана'
        sites.append(_site('focus', x, y, [f"Записанный очаг ({label}, {f.get('date') or 'дата не указана'}): "
                                           'условия здесь уже складывались — узел покажет, повторяются ли они.']))
    summary = (f'Записано очагов: {len(items)}' + (f', с координатами: {len(located)}.' if located else
               ', координат нет: площадь получает приоритет, место внутри выбирается по рельефу.'))
    return {**base, 'status': 'used', 'summary': summary,
            'details': {**base['details'], 'with_coordinates': len(located)}}, sites, [(x, y) for _, x, y in located]


def _merge(sites):
    sites.sort(key=lambda s: (-s['score'], ORDER[s['kind']]))
    kept = []
    for s in sites:
        twin = next((k for k in kept if math.hypot(k['x']-s['x'], k['y']-s['y']) < MIN_GAP_M), None)
        if twin:
            twin['reasons'] += s['reasons']
            twin['score'] = max(twin['score'], s['score'])
        else:
            kept.append(s)
    return kept[:4]


def siting(store, fid):
    feature, geo, poly = outline(fid)
    f_relief, sites = relief(fid, poly)
    f_tall, shelter = tall_objects(fid, poly)
    f_soil = soil(fid)
    f_foci, focus_sites, focus_xy = foci(store, fid, poly)
    sites = focus_sites+sites+shelter
    for s in sites:
        if s['kind'] != 'focus' and any(math.hypot(s['x']-x, s['y']-y) <= 150 for x, y in focus_xy):
            s['score'] += 2
            s['reasons'].append('В пределах 150 м от записанного очага.')
    sites = _merge(sites)
    sites.sort(key=lambda s: (-s['score'], ORDER[s['kind']]))
    out = []
    for i, s in enumerate(sites, 1):
        out.append({'id': f'{fid}-{s["kind"]}', 'rank': i, 'kind': s['kind'], 'name': s['name'],
                    'latitude': round(s['latitude'], 7), 'longitude': round(s['longitude'], 7),
                    'score': s['score'], 'reasons': s['reasons'], 'status': STATUS,
                    **{k: s[k] for k in ('surface_height_m', 'object_rise_m', 'object_linear') if k in s}})
    if out:
        first = out[0]
        recommendation = f'Сначала осмотреть точку «{first["name"].lower()}»: {first["reasons"][0][0].lower()}{first["reasons"][0][1:]}'
    else:
        recommendation = ('По открытым данным место внутри площади не выделяется: точку для узла выбирают на месте, '
                          'с учётом доступа, связи и записанных очагов.')
    return {'fid': fid, 'area_ha': feature['properties'].get('traced_area_ha'),
            'factors': [f_relief, f_tall, f_soil, f_foci], 'sites': out, 'recommendation': recommendation,
            'method': METHOD,
            'limitations': ['Обводки приблизительные, восстановлены по скриншоту; границы и культура не подтверждены.',
                            'DSM 30 м включает растительность и постройки; узкая лесополоса может не проявиться, а постройка — выглядеть как полоса.',
                            'Влияние низин, лесополос и почвы на увлажнение листа — гипотеза, её проверяет узел на пилоте.',
                            'Точки — места для осмотра, а не точки монтажа: доступ, связь, питание и безопасность проверяет человек.']}
