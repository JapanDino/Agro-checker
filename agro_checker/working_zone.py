"""Working point registration and a read-only spatial source audit.

This module does not fit models, create forecasts, change alert routing or
enable recurring collection. Historical global GRIBs are read, never changed.
"""
from copy import deepcopy
from datetime import datetime
import csv
import json
import math
from .pipeline import ROOT, UTC, write_json
from .field_store import FieldStore, numeric
from .product_data import sha

CONFIG = ROOT/'config/working_zone.json'
DIRECTORY = ROOT/'data/working-zone-01'


def load_config():
    p = json.loads(CONFIG.read_text(encoding='utf-8'))
    if p['schema_version'] != 1 or p['coordinate_role'] != 'user_supplied_reference_point':
        raise ValueError('Expected a user-confirmed working point')
    p['latitude'] = numeric(p['latitude'], -90, 90, 'Широта')
    p['longitude'] = numeric(p['longitude'], -180, 180, 'Долгота')
    if p['boundary'] is not None or p['area_ha'] is not None or p['surveyed_elevation_m'] is not None:
        raise ValueError('This registration contains a point only; do not infer a boundary or elevation')
    return p


def distance_km(lat, lon, other_lat, other_lon):
    a, b = map(math.radians, [lat, other_lat])
    dlat, dlon = b-a, math.radians(other_lon-lon)
    return 6371.0088*2*math.asin(math.sqrt(math.sin(dlat/2)**2+math.cos(a)*math.cos(b)*math.sin(dlon/2)**2))


def register():
    p = load_config(); store = FieldStore(ROOT/'data/field').init()
    current = store.profile_record(p['profile_id']); profile = deepcopy(current['payload'])
    zone = next(z for z in profile['zones'] if z['id'] == p['zone_id'])
    wanted = (p['latitude'], p['longitude']); existing = (zone['latitude'], zone['longitude'])
    if existing == wanted:
        return {'status': 'already_registered', 'profile_revision': current['id']}
    if existing != (None, None):
        raise ValueError('Existing coordinates differ; review a new location change explicitly before applying')
    # Preserve cultivar, phase, sensors, equipment, every old issue and journal.
    zone.update(latitude=p['latitude'], longitude=p['longitude'], name=p['label'])
    zone['coordinate_source'] = {'kind': p['coordinate_role'], 'confirmation_date_local': p['confirmation_date_local'],
                                 'working_zone_config_sha256': sha(CONFIG)}
    saved = store.save_profile(profile, expected_revision=current['id'])
    receipt = {'status': 'registered', 'previous_profile_revision': current['id'], 'profile_revision': saved['id'],
               'profile_sha256': saved['sha256'], 'config_sha256': sha(CONFIG), 'recorded_at_utc': saved['recorded_at_utc']}
    write_json(DIRECTORY/'registration.json', receipt)
    return receipt


def source_audit():
    p = load_config(); lat, lon = p['latitude'], p['longitude']
    inventory = ROOT/'data/processed/product-stations/raw/isd-history.csv'
    regional = ROOT/'data/processed/regional-minimum/station-audit.json'
    audited = {s['id']: s for s in json.loads(regional.read_text(encoding='utf-8'))['stations']}
    stations = []
    for row in csv.DictReader(inventory.open(encoding='utf-8-sig')):
        try: a, b = float(row['LAT']), float(row['LON'])
        except (ValueError, TypeError): continue
        d = distance_km(lat, lon, a, b); sid = row['USAF']+'-'+row['WBAN']
        if d > 110: continue
        info = audited.get(sid)
        stations.append({'id': sid, 'name': row['STATION NAME'], 'latitude': a, 'longitude': b,
                         'distance_km': d, 'inventory_begin': row['BEGIN'], 'inventory_end': row['END'],
                         'prior_complete_14h_nights': {y: v['complete_nights'] for y, v in info['years'].items()} if info else None,
                         'eligible_in_prior_audit': info['eligible'] if info else None,
                         'represents_orchard_truth': False})
    stations.sort(key=lambda s: s['distance_km'])
    import eccodes as ec
    # Example geometry from a frozen historical object, not a current forecast.
    temp = ROOT/'data/raw/gfs-single/2022030106/f010.grib2'
    tm = json.loads(temp.with_name(temp.name+'.meta.json').read_text(encoding='utf-8'))
    terrain = ROOT/'data/processed/climate-minimum/raw/2022030106/f010_terrain.grib2'
    hm = json.loads(terrain.with_name(terrain.name+'.meta.json').read_text(encoding='utf-8'))
    if sha(temp) != tm['sha256'] or sha(terrain) != hm['sha256']: raise ValueError('Frozen GRIB bytes changed')
    if any(tm[k] != hm[k] for k in ['url', 'etag', 'archive_last_modified_at_utc']): raise ValueError('Different GFS objects')
    grid = {}; surface = {}
    for path, kind in [(temp, 'temperature'), (terrain, 'terrain')]:
        h = ec.codes_new_from_message(path.read_bytes())
        try:
            if ec.codes_get(h, 'gridType') != 'regular_ll' or ec.codes_get(h, 'iDirectionIncrementInDegrees') != .25 or ec.codes_get(h, 'jDirectionIncrementInDegrees') != .25:
                raise ValueError('Unexpected grid geometry')
            if ec.codes_get(h, 'dataDate') != 20220301 or ec.codes_get(h, 'dataTime') != 600 or ec.codes_get(h, 'endStep') != 10:
                raise ValueError('Unexpected historical snapshot run')
            if kind == 'temperature' and (ec.codes_get(h, 'shortName') != '2t' or ec.codes_get(h, 'typeOfLevel') != 'heightAboveGround' or ec.codes_get(h, 'level') != 2):
                raise ValueError('Expected2m temperature geometry')
            if kind == 'terrain' and (ec.codes_get(h, 'typeOfLevel') != 'surface' or ec.codes_get(h, 'units') not in ['m', 'gpm']):
                raise ValueError('Expected surface-height message')
            point = ec.codes_grib_find_nearest_multiple(h, False, [lat], [lon])[0]
            if ec.codes_get(h, 'bitmapPresent') and not ec.codes_get_element(h, 'bitmap', point['index']): raise ValueError('Missing grid point')
            g = {'latitude': float(point['lat']), 'longitude': float(point['lon']), 'distance_km': float(point['distance']), 'resolution_degrees': .25}
            if grid and grid != g: raise ValueError('Different terrain and temperature grids')
            grid = g
            if kind == 'terrain': surface = {'value': float(point['value']), 'units': ec.codes_get(h, 'units'), 'is_surveyed_site_elevation': False}
        finally: ec.codes_release(h)
    osm_path = DIRECTORY/'raw/osm-reverse.json'
    osm = json.loads(osm_path.read_text(encoding='utf-8')) if osm_path.exists() else {}
    result = {'working_point': p, 'administrative_context': osm.get('address'),
              'administrative_context_source': {'url': 'https://nominatim.openstreetmap.org/reverse?lat=45.024106&lon=37.911914&format=jsonv2&zoom=10&addressdetails=1',
                                                'sha256': sha(osm_path) if osm_path.exists() else None,
                                                'scope': 'OSM reverse-geocoding administrative context, not orchard identification'},
              'nearby_stations': stations, 'historical_grid_example': {'run_at_utc': '2022-03-01T06:00:00+00:00',
                   'grid': grid, 'gfs_surface_height': surface,
                   'source_sha256': {str(path.relative_to(ROOT)): sha(path) for path in [temp, terrain]},
                   'is_current_forecast': False},
              'source_sha256': {str(path.relative_to(ROOT)): sha(path) for path in [inventory, regional, CONFIG]},
              'model_admission': {'original_krasnodar_point_supported': False, 'original_detector_supported': False,
                  'new_cold_range_field_validated': False, 'spring_model_valid_in_october': False,
                  'baseline': 'One original GFS issue decoded at the working coordinates; no learned correction without local validation'},
              'independent_field_truth': {'sensors_confirmed': False, 'cold_nights': 0, 'damage_labels': 0},
              'generated_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'source-audit.json', result); return result


def report(audit):
    p = audit['working_point']; grid = audit['historical_grid_example']['grid']; stations = audit['nearby_stations']
    nearest_eligible = next(s for s in stations if s['eligible_in_prior_audit'])
    ref = next(s for s in stations if s['id'] == '349290-99999')
    lines = ['# Рабочая зона Onlyfarms', '',
             f"Подтверждено пользователем17октября2026: **{p['latitude']}, {p['longitude']}**. Это опорная точка зоны; точный контур, площадь, высота, сорт, текущая фаза и наличие сада-партнёра не подтверждены.", '',
             f"[Точка на карте]({p['map_url']}). По reverse-geocoding OpenStreetMap: Киевское сельское поселение, Крымский район, Краснодарский край. Это административная привязка, а не определение конкретного хозяйства.", '',
             '## Что подключено', '',
             'Координаты добавлены новой ревизией полевого паспорта orchard-draft, участокz1. Пустой шаблон паспорта сохранён для других инсталляций. Вся история, demo-станция, фактические измерения и прежние прогнозные snapshots сохранены. Новый live-прогноз не создавался, ежедневный сбор не включался; достоверных измерений участка пока нет.', '',
             '## Наблюдения вокруг зоны', '',
             '| Источник | Расстояние по каталогу | Полных весенних ночей2022/2023/2024 | Роль |',
             '|---|---:|---|---|']
    for s in stations[:8]:
        counts = '/'.join(str(s['prior_complete_14h_nights'][str(y)]) for y in [2022, 2023, 2024]) if s['prior_complete_14h_nights'] else 'не проверено в прежнем prior-аудите'
        role = ('есть полные ряды для отдельной станционной проверки' if s['eligible_in_prior_audit'] else
                'метаданные есть; почасовая пригодность не проверена' if s['eligible_in_prior_audit'] is None else
                'в выбранном почасовом архиве нет пригодного полного ряда')
        lines.append(f"| {s['name']} · {s['id']} | {s['distance_km']:.1f}км | {counts} | {role} |")
    lines += ['', f"Среди уже проверенных пригодных источников ближайшее Витязево: {nearest_eligible['distance_km']:.1f}км. Краснодарская станция исходной модели: {ref['distance_km']:.1f}км. Ноль полных14-часовых ночей означает непригодность для прежней строгой почасовой оценки, а не отсутствие наблюдений или заморозков. Координаты каталогов приближённые; metadata-end не подтверждает работу станции сейчас.", '',
              'В паспорте Росгидромета2017 отдельно указана станция Крымск37002(44.90,38.00). В используемом NOAA ISD-каталоге этот источник не найден. Его архив и нынешнюю доступность надо проверять отдельно; нельзя подставить эти координаты как готовую цель обучения. [Паспорт Росгидромета](https://www.meteorf.gov.ru/upload/iblock/58b/%D0%9F%D0%B0%D1%81%D0%BF%D0%BE%D1%80%D1%82%20%D0%93%D0%9C%D0%91%20%D0%9A%D1%80%D0%B0%D1%81%D0%BD%D0%BE%D0%B4%D0%B0%D1%80%D1%81%D0%BA%D0%BE%D0%B3%D0%BE%20%D0%BA%D1%80%D0%B0%D1%8F.pdf).', '',
              '## Сетка прогноза и перенос модели', '',
              f"Из реальных замороженных GRIB температуры2м и HGTsurface одного выпуска GFS2022-03-01 06UTC проверена ближайшая ячейка: **{grid['latitude']}, {grid['longitude']}**, {grid['distance_km']:.2f}км от заданной точки, сетка0.25°. Это историческая геометрическая проверка, не прогноз текущей погоды. Высота поверхности GFS сохранена вJSON отдельно и не записана как высота сада.", '',
              'Исходная точечная модель и детектор Краснодара на этот участок не переносятся автоматически. Результаты98.6% холодного покрытия на других станциях не относятся к этой зоне. Новый диапазон не проверен по местным датчикам. Весенняя исследовательская модель не объявлена пригодной для октября. Для нового рабочего прогноза используется исходный GFS по этим координатам, с временем выпуска, сеткой и отдельной проверкой результата.', '',
              '## Следующий этап для этой зоны', '',
              '1. Уточнить контур и рельеф, наличие и расположение сада, сорт и текущую фазу; название партнёра и ответственного оставить неизвестными до подтверждения.',
              '2. Найти независимые наблюдения Крымска/окрестностей; Витязево оставить отдельным внешним сравнением. Архивные данные станции не выдавать за факт на участке.',
              '3. Поставить проверенный датчик воздуха около2м на участке; после обследования рельефа определить, нужен ли второй в потенциально более холодном месте. Датчик кроны хранить отдельной целью.',
              '4. Собирать прогнозы до ночи, наблюдения после их поступления, отдельно отмечать применение защиты. Новую локальную коррекцию и диапазон проверять отдельным заранее зафиксированным протоколом; прежние2025–2026 не использовать для подбора.', '',
              '## Воспроизведение', '', '```powershell', '.\\.venv\\Scripts\\python.exe -m agro_checker.working_zone --register --audit',
              '.\\.venv\\Scripts\\python.exe -m agro_checker field-serve --port 8770', '```', '',
              'Исправление точки выполняется новой ревизией паспорта; повторная регистрация той же точки идемпотентна. При других сохранённых координатах команда останавливается, чтобы не перезаписать новое решение пользователя. Сырые геоданные и audit: data/working-zone-01; воспроизводимый источник координат: config/working_zone.json.', '']
    (ROOT/'docs/WORKING_ZONE.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--register', action='store_true'); parser.add_argument('--audit', action='store_true'); args = parser.parse_args()
    if args.register: print(json.dumps(register(), ensure_ascii=False))
    if args.audit:
        a = source_audit(); report(a)
        print(json.dumps({'point': a['working_point'], 'grid': a['historical_grid_example']['grid'], 'model_admission': a['model_admission']}, ensure_ascii=False))
