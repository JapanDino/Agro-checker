"""Read-only agronomic context from the local pilot package."""
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
from math import ceil, floor, isfinite
import re

import numpy as np
from shapely import transform
from shapely.geometry import shape

from . import farm_scouting as scout
from .pipeline import ROOT
from .sector_refresh import read, sha


DEFAULT_DIRECTORY = ROOT/'data/agromon-private/gmail-2026-11-12'
MAX_OPERATION_WINDOW_DAYS = 30


def rainfall_window(directory, review):
    """Return one value per calendar day. Missing precipitation stays null."""
    path = directory/'weather-daily.json'
    if not review or not path.is_file():
        return None
    try:
        weather = read(path)
        if weather.get('scope') != 'farm_context':
            return None
        columns = [' '.join(column.split()) for column in weather['columns']]
        day_column, rain_column = columns.index('День'), columns.index('Осадки, мм')
        start, end = date.fromisoformat(review['start'][:10]), date.fromisoformat(review['end'][:10])
        values = {}
        for row in weather['rows']:
            day = date.fromisoformat(row[day_column])
            if start <= day <= end:
                rain = row[rain_column]
                values[day] = rain if type(rain) in (int, float) and isfinite(rain) and rain >= 0 else None
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    days = [start + timedelta(days=offset) for offset in range((end-start).days+1)]
    return {'scope': 'farm_context', 'rows': [
        {'date': day.isoformat(), 'precipitation_mm': values.get(day)} for day in days
    ]}


def review_windows(path, package_hash, contour_hash, geometry_hash):
    if not path.is_file():
        return {}
    report = read(path)
    if (report.get('schema_version') == 1
            and report.get('package_sha256') == package_hash
            and report.get('contours_sha256') == contour_hash
            and (geometry_hash is None or report.get('review_geometry_sha256') == geometry_hash)):
        return report
    return {}


def field_context(directory, fid):
    scout.fragment(fid)
    package_path = directory/'scouting-import.json'
    if not package_path.is_file():
        return {'status': 'unconfigured', 'field': None, 'harvest_review': None}

    package = read(package_path)
    if package.get('schema_version') != 1 or package.get('kind') != 'scouting_field_context_staging':
        raise ValueError('Формат истории поля не поддерживается')
    contour_path = scout.R/'approximate-areas.geojson'
    contour_hash = sha(contour_path)
    evidence = package['alignment']['repository_evidence']
    source = next((item for item in evidence if item['path'].endswith('/approximate-areas.geojson')), {})
    if source.get('sha256') != contour_hash:
        return {'status': 'outdated_mapping', 'field': None, 'harvest_review': None}

    fields = [field for field in package['fields'] if field['map_match']['fid'] == fid]
    if not fields:
        return {'status': 'unmapped', 'field': None, 'harvest_review': None}
    if len(fields) != 1:
        raise ValueError('Для этой площади найдено несколько карточек поля')
    field = fields[0]
    geometry_path = directory/'field-20-review.geojson'
    package_hash = sha(package_path)
    review = operation_report = season_report = {}
    if field['map_match']['use_scouting_fid_as_field_context'] or geometry_path.is_file():
        geometry_hash = None if field['map_match']['use_scouting_fid_as_field_context'] else sha(geometry_path)
        review = review_windows(directory/'harvest-2025.json', package_hash, contour_hash, geometry_hash)
        operation_report = review_windows(directory/'operation-sentinel.json', package_hash, contour_hash, geometry_hash)
        season_report = review_windows(directory/'season-sentinel.json', package_hash, contour_hash, geometry_hash)
    harvest_review = review.get('windows', {}).get(field['field_key'])
    operation_windows = operation_report.get('windows', {})
    operation_reviews = {item['event_key']: operation_windows[item['event_key']]
                         for item in field['operations'] if item['event_key'] in operation_windows}
    season_satellite = season_report.get('windows', {}).get(field['field_key'])

    operation_dates = [date.fromisoformat(item['date']) for item in field['operations']]
    season_window = None
    operation_weather = None
    if operation_dates:
        start, end = min(operation_dates), max(operation_dates)
        season_window = {'start': start.isoformat(), 'end': end.isoformat()}
        margin = timedelta(days=MAX_OPERATION_WINDOW_DAYS)
        operation_weather = rainfall_window(directory, {'start': (start-margin).isoformat(),
                                                         'end': (end+margin).isoformat()})
    weather = None
    if harvest_review and operation_weather:
        weather = {**operation_weather, 'rows': [
            row for row in operation_weather['rows']
            if harvest_review['start'][:10] <= row['date'] <= harvest_review['end'][:10]
        ]}
    return {'status': 'ready', 'field': field, 'harvest_review': harvest_review,
            'operation_reviews': operation_reviews, 'season_satellite': season_satellite,
            'weather': weather, 'operation_weather': operation_weather, 'season_window': season_window,
            'source': package['sources']['works']['archive_member'],
            'satellite_source': season_report.get('source') or operation_report.get('source') or review.get('source')}


def operation_image(directory, fid, event, scene):
    if not re.fullmatch(r'S2[ABC]_T\d{2}[A-Z]{3}_\d{8}T\d{6}_L2A', scene):
        raise ValueError('Неизвестная съёмка')
    data = field_context(directory, fid)
    field = data.get('field')
    if not field or not any(op['event_key'] == event for op in field['operations']):
        raise ValueError('Для этой работы снимок недоступен')
    season = data.get('season_satellite')
    review = season or data['operation_reviews'].get(event)
    row = next((row for row in review['rows'] if row['id'] == scene and (row['coverage'] or 0) > 0), None) if review else None
    if not row or not row.get('raster_sha256'):
        raise ValueError('Для этой работы снимок недоступен')
    archive_name = 'season-sentinel' if season else 'operation-sentinel'
    path = directory/archive_name/'sentinel'/scene/'indices.npz'
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != row['raster_sha256']:
        raise ValueError('Растр не прошёл проверку целостности')

    protocol, _, _, polygon = scout.fragment(fid)
    if not field['map_match']['use_scouting_fid_as_field_context']:
        feature, = [feature for feature in read(directory/'field-20-review.geojson')['features']
                    if feature['properties']['field_key'] == field['field_key']]
        polygon = transform(shape(feature['geometry']), scout.TO_UTM.transform, interleaved=False)
    x0, y0, x1, y1 = protocol['bounds_utm']
    width, height = (x1-x0)//20, (y1-y0)//20
    west, south, east, north = polygon.buffer(80).bounds
    left, right = max(0, floor((west-x0)/20)), min(width, ceil((east-x0)/20))
    top, bottom = max(0, floor((y1-north)/20)), min(height, ceil((y1-south)/20))
    crop = {'west': x0+left*20, 'north': y1-top*20, 'width': right-left, 'height': bottom-top}
    with np.load(BytesIO(raw), allow_pickle=False) as raster:
        vals, valid = raster['ndvi'], raster['valid']
    if vals.shape != (height, width) or valid.shape != vals.shape or min(crop['width'], crop['height']) <= 0:
        raise ValueError('Не совпадает сетка спутниковых наблюдений')
    area = (slice(top, bottom), slice(left, right))
    return scout.index_png(vals[area], valid[area], 'ndvi', 'before', crop, polygon)
