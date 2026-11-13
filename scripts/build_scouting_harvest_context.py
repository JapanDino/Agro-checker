"""Collect local Sentinel-2 evidence around reported harvests or selected operations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
from shutil import copy2
import sys
from tempfile import TemporaryDirectory

import numpy as np
from rasterio.transform import from_origin
from shapely import transform
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agro_checker import farm_context_data as source, farm_scouting as scout
from agro_checker.farm_context_analysis import masks, spectral_stats
from agro_checker.sector_refresh import BASE, catalogue, dump, object_head, read


WINDOW_DAYS = 14
PERIOD = 'harvest2025'


def window(event_date, days=WINDOW_DAYS):
    day = date.fromisoformat(event_date)
    return day - timedelta(days=days), day + timedelta(days=days)


def collect(ranges, protocol, directory, workers, period_id, caches=()):
    entries = {}
    for start, end in sorted(set(ranges)):
        # Separate catalogue requests so days between the harvest windows are not downloaded.
        period = {'id': period_id, 'start': f'{start}T00:00:00Z', 'end': f'{end}T23:59:59Z'}
        result = catalogue({**protocol, 'periods': [period]}, directory/f'window-{start}-{end}')
        for entry in result['items']:
            entries[entry['item']['id']] = entry
    print(f'Catalogue: {len(entries)} scenes', flush=True)

    source.D, source.head = directory, object_head

    def extract(entry):
        item = entry['item']
        scene_id = item['id']
        asset = item['assets'].get('scl', {})
        grid = asset.get('proj:transform', [])
        shape = asset.get('proj:shape', [])
        if (item['properties'].get('proj:epsg') == 32637 and len(grid) == 6 and len(shape) == 2
                and grid[:2] == [20, 0] and grid[3:5] == [0, -20]):
            west, north = grid[2], grid[5]
            x0, y0, x1, y1 = protocol['bounds_utm']
            if west > x0 or north < y1 or west+20*shape[1] < x1 or north-20*shape[0] > y0:
                return {'id': scene_id, 'status': 'outside_full_grid',
                        'acquired_at_utc': item['properties']['datetime'], 'reason': 'SCL tile does not cover the farm grid'}
        try:
            target = directory/'sentinel'/scene_id
            if not (target/'manifest.json').exists():
                item_hash = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
                for cache in caches:
                    folder = cache/'sentinel'/scene_id
                    manifest = folder/'manifest.json'
                    if not manifest.is_file():
                        continue
                    metadata = read(manifest)
                    if metadata['item_sha256'] != item_hash:
                        continue
                    target.mkdir(parents=True, exist_ok=True)
                    for name in [*metadata['crop_sha256'], 'manifest.json']:
                        if (target/name).exists():
                            continue
                        try:
                            os.link(folder/name, target/name)
                        except OSError:
                            copy2(folder/name, target/name)
                    break
            return {**source.scene(entry), 'period': period_id}
        except (OSError, ValueError, RuntimeError) as error:
            return {'id': item['id'], 'status': 'extraction_error',
                    'acquired_at_utc': item['properties']['datetime'], 'reason': str(error)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        scenes = list(pool.map(extract, entries.values()))
        failed = [entry for entry, scene in zip(entries.values(), scenes) if scene['status'] == 'extraction_error']
        if failed:
            retries = {scene['id']: scene for scene in pool.map(extract, failed)}
            scenes = [retries.get(scene['id'], scene) for scene in scenes]
    return sorted(scenes, key=lambda item: (item['acquired_at_utc'], item['id']))


def field_observations(protocol, scenes, rasters, footprint, start, end):
    x0, y0, x1, y1 = protocol['bounds_utm']
    grid = from_origin(x0, y1, 20, 20)
    grid_shape = ((y1-y0)//20, (x1-x0)//20)
    pixels = masks([('base', footprint)], grid, grid_shape)['base']
    minimum = protocol['minimum_valid_cells']
    coverage_min = protocol['coverage_min']
    rows = []
    for scene in scenes:
        day = date.fromisoformat(scene['acquired_at_utc'][:10])
        if not start <= day <= end or scene['status'] == 'outside_full_grid':
            continue
        row = {'id': scene['id'], 'at': scene['acquired_at_utc'], 'baseline': scene.get('processing_baseline'),
               'status': scene['status'], 'coverage': None, 'ndvi': None, 'ndmi': None}
        if scene['status'] == 'ready':
            raster = rasters[scene['id']]
            valid = raster['valid']
            row['raster_sha256'] = scene['crop_sha256']['indices.npz']
            row['coverage'] = float(valid.ravel()[pixels].mean()) if len(pixels) else 0
            for key in ('ndvi', 'ndmi'):
                row[key] = spectral_stats(raster[key], valid, pixels, minimum, coverage_min)['value']
        rows.append(row)

    return rows, pixels


def comparison_pair(event_date, rows, rasters, pixels, protocol, days=WINDOW_DAYS):
    start, end = window(event_date, days)
    minimum = protocol['minimum_valid_cells']
    coverage_min = protocol['coverage_min']
    # Compare dates bracketing the operation, with matching processing and shared pixels.
    # Acquisition dates and coverage determine selection, regardless of index change.
    usable = [r for r in rows if r['baseline'] and r['ndvi'] is not None and r['ndmi'] is not None
              and start.isoformat() <= r['at'][:10] <= end.isoformat()]
    candidates = []
    for before in usable:
        for after in usable:
            if not before['at'][:10] < event_date < after['at'][:10] or before['baseline'] != after['baseline']:
                continue
            a, b = rasters[before['id']], rasters[after['id']]
            common = a['valid'].ravel()[pixels] & b['valid'].ravel()[pixels]
            if common.sum() < minimum or common.mean() < coverage_min:
                continue
            days = (scout.stamp(after['at'])-scout.stamp(before['at'])).total_seconds()/86400
            candidates.append((days, -float(common.mean()), before['id'], after['id'], before, after, common))
    pair = None
    if candidates:
        days, coverage, _, _, before, after, common = min(candidates, key=lambda item: item[:4])
        a, b = rasters[before['id']], rasters[after['id']]
        common_pixels = pixels[common]
        metrics = {}
        for key in ('ndvi', 'ndmi'):
            early, late = a[key].ravel()[common_pixels], b[key].ravel()[common_pixels]
            metrics[key] = {'before': float(np.median(early)), 'after': float(np.median(late)),
                            'delta': float(np.median(late-early))}
        pair = {'before': before, 'after': after, 'days': days, 'coverage': -coverage,
                'paired_cells': int(common.sum()), 'metrics': metrics}
    return pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'data/agromon-private/gmail-2026-11-12')
    parser.add_argument('--workers', type=int, default=4)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--operation', action='append', metavar='EVENT_KEY', help='Operation to collect, repeatable.')
    mode.add_argument('--season', action='store_true', help='Collect the full work season across the five fields.')
    args = parser.parse_args()
    context = args.directory.resolve()
    package = context/'scouting-import.json'
    review_geometry = context/'field-20-review.geojson'
    package_bytes, geometry_bytes = package.read_bytes(), review_geometry.read_bytes()
    fields = json.loads(package_bytes)['fields']
    geometry = json.loads(geometry_bytes)['features']
    targets = {field['field_key']: (field, field['reported_harvest_date_2025']) for field in fields}
    if args.operation:
        operations = {op['event_key']: (field, op['date']) for field in fields for op in field['operations']}
        unknown = set(args.operation) - operations.keys()
        if unknown:
            parser.error(f'Unknown operation: {", ".join(sorted(unknown))}')
        targets = {key: operations[key] for key in args.operation}
    period_id = 'season2025' if args.season else 'operation' if args.operation else PERIOD
    archive_name = 'season-sentinel' if args.season else 'operation-sentinel' if args.operation else 'harvest-sentinel'
    directory = context/archive_name
    protocol = read(BASE/'protocol.json')
    dump(directory/'protocol.json', protocol)
    caches = []
    if args.season:
        dates = [date.fromisoformat(op['date']) for field in fields for op in field['operations']]
        season_bounds = (min(dates)-timedelta(days=30), max(dates)+timedelta(days=30))
        ranges = [season_bounds]
        for name in ('operation-sentinel', 'harvest-sentinel'):
            cache = context/name
            if (cache/'protocol.json').is_file():
                old = read(cache/'protocol.json')
                if all(old.get(key) == protocol[key] for key in ('collection', 'bounds_utm', 'contours_sha256')):
                    caches.append(cache)
    else:
        ranges = [window(day) for _, day in targets.values()]
    scenes = collect(ranges, protocol, directory, args.workers, period_id, caches)
    dump(directory/'satellite.json', {'scenes': scenes})
    rasters = {}
    for scene in scenes:
        if scene['status'] == 'ready':
            path = directory/'sentinel'/scene['id']/'indices.npz'
            with np.load(path, allow_pickle=False) as raster:
                rasters[scene['id']] = {key: raster[key] for key in ('ndvi', 'ndmi', 'valid')}
    result = {}
    for key, (field, event_date) in targets.items():
        match = field['map_match']
        if match['use_scouting_fid_as_field_context']:
            footprint = scout.fragment(match['fid'])[3]
        else:
            feature, = [f for f in geometry if f['properties']['field_key'] == field['field_key']]
            footprint = transform(shape(feature['geometry']), scout.TO_UTM.transform, interleaved=False)
        start, end = season_bounds if args.season else window(event_date)
        rows, pixels = field_observations(protocol, scenes, rasters, footprint, start, end)
        result[key] = {'start': f'{start}T00:00:00Z', 'end': f'{end}T23:59:59Z', 'rows': rows}
        if args.season:
            pairs_by_date = {
                day: {str(days): comparison_pair(day, rows, rasters, pixels, protocol, days) for days in (14, 30)}
                for day in sorted({op['date'] for op in field['operations']})
            }
            result[key]['pairs'] = {op['event_key']: pairs_by_date[op['date']] for op in field['operations']}
            print(key, 'scenes:', len(rows), 'operation dates:', len(pairs_by_date), flush=True)
        else:
            result[key]['pair'] = comparison_pair(event_date, rows, rasters, pixels, protocol)
            print(key, 'scenes:', len(rows), 'paired:', bool(result[key]['pair']), flush=True)
    with TemporaryDirectory(dir=context) as staging:
        report = Path(staging)/('season-sentinel.json' if args.season else
                                'operation-sentinel.json' if args.operation else 'harvest-2025.json')
        metadata = {'schema_version': 1, 'package_sha256': hashlib.sha256(package_bytes).hexdigest(),
                    'review_geometry_sha256': hashlib.sha256(geometry_bytes).hexdigest(),
                    'contours_sha256': protocol['contours_sha256'],
                    'source': 'Earth Search Sentinel-2 Collection 1 Level-2A'}
        previous = context/report.name
        if args.operation and previous.is_file():
            published = read(previous)
            if all(published.get(key) == value for key, value in metadata.items()):
                result = {**published['windows'], **result}
        dump(report, {**metadata, 'windows': result})
        report.replace(context/report.name)


if __name__ == '__main__':
    main()
