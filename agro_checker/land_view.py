"""Read-only presentation adapter for the frozen landmark audit. No downloads or fits."""
from functools import lru_cache
from threading import Lock

from .pipeline import ROOT

_lock = Lock()


def _numbers(a, digits=5):
    import numpy as np
    return [[round(float(v), digits) if np.isfinite(v) else None for v in row] for row in a]


def _geometry(r, x0, y0):
    """Native cell corners projected into one metric display CRS, without resampling."""
    import numpy as np
    from pyproj import Transformer
    cols, rows = np.meshgrid(np.arange(r.width+1), np.arange(r.height+1))
    x = r.transform.c + cols*r.transform.a + rows*r.transform.b
    y = r.transform.f + cols*r.transform.d + rows*r.transform.e
    x, y = Transformer.from_crs(r.crs, 32637, always_xy=True).transform(x, y)
    return {'width': r.width, 'height': r.height,
            'east': _numbers(x-x0, 2), 'north': _numbers(y-y0, 2)}


def _build():
    import numpy as np
    import rasterio
    from pyproj import Transformer
    from . import open_land_data as data
    from .open_land_report import scene_indices, statistics
    # Verify every frozen artifact before exposing a single value. Never collect here.
    soil, dem, satellite, provenance = data.load()
    ref = data.protocol()['reference']
    x0, y0 = Transformer.from_crs(4326, 32637, always_xy=True).transform(ref['longitude'], ref['latitude'])
    with rasterio.open(ROOT/dem['native_crop']) as r:
        terrain = {'geometry': _geometry(r, x0, y0), 'values': _numbers(r.read(1, masked=True).filled(np.nan), 3),
                   'landmark_height_m': dem['height_at_landmark_m'],
                   'range_m': [dem['context_min_m'], dem['context_max_m']],
                   'release': '2021', 'surface_not_bare_ground': True}
    layers = {}
    geometry = None
    for item in soil['values']:
        with rasterio.open(ROOT/item['path']) as r:
            if geometry is None: geometry = _geometry(r, x0, y0)
            raw = r.read(1, masked=True)
            a = np.full(raw.shape, np.nan)
            for row, col in np.ndindex(raw.shape):
                if not np.ma.is_masked(raw[row, col]):
                    a[row, col] = data.soil_value(item['property'], float(raw[row, col]))
            layers.setdefault(item['property'], {}).setdefault(item['depth'], {})[item['statistic']] = _numbers(a, 1)
    soil_view = {'geometry': geometry, 'layers': layers, 'cell_m': 250,
                 'landmark_cell': [4, 4], 'local_coverage_validated': False,
                 'retrieved_at_utc': soil['values'][0]['source']['retrieved_at_utc']}
    catalogue = {f['id']: f for f in data.read(data.DIRECTORY/'catalogue.json')['items']}
    summaries, maps = [], {}
    for s in sorted(satellite['samples'], key=lambda v: v['acquired_at_utc']):
        ndvi, ndmi, valid = scene_indices(s, catalogue[s['id']])
        h, w = valid.shape
        window = (slice(h//2-1, h//2+2), slice(w//2-1, w//2+2))
        if int(valid[window].sum()) != s['valid_land_cells_in_point_60m_window']:
            raise ValueError('Satellite support disagrees with frozen audit')
        accepted = bool(valid[h//2, w//2] and valid[window].sum() >= 5)
        if accepted != s['point_window_usable']: raise ValueError('Satellite acceptance disagrees with audit')
        ranges = {}
        for name, a in [('ndvi', ndvi), ('ndmi', ndmi)]:
            if accepted:
                values = a[window][valid[window]]
                median = float(np.median(values))
                if not np.isclose(median, s[f'point_60m_{name}_median'], atol=1e-10):
                    raise ValueError('Satellite index disagrees with frozen audit')
                ranges[name] = [float(values.min()), float(values.max())]
            else: ranges[name] = None
        keys = ['id', 'acquired_at_utc', 'processing_generated_at_utc', 'catalogue_created_at_utc',
                'retrieved_at_utc', 'processing_baseline', 'point_scl', 'point_window_usable',
                'valid_land_cells_in_point_60m_window', 'context_valid_land_fraction',
                'cloud_cover_tile_percent', 'point_60m_ndvi_median', 'point_60m_ndmi_median']
        summaries.append({**{k: s[k] for k in keys}, 'spatial_range': ranges})
        t = s['common_transform_utm']
        maps[s['id']] = {'id': s['id'], 'width': w, 'height': h,
                         'west_m': t[2]-x0, 'north_m': t[5]-y0, 'cell_m': 20,
                         'ndvi': _numbers(ndvi), 'ndmi': _numbers(ndmi)}
    return {'schema_version': 1, 'reference': ref, 'orchard_boundary_known': False,
            'audit_sha256': data.sha(data.DIRECTORY/'audit.meta.json'),
            'extracted_at_utc': provenance['generated_at_utc'],
            'terrain': terrain, 'soil': soil_view,
            'satellite': {'scenes': summaries, 'statistics': statistics(satellite),
                          'window_m': 60, 'cell_m': 20, 'minimum_valid_cells': 5,
                          'cloud_mask_independently_validated': False},
            'agronomic_accuracy_claimed': False, 'model_fit_performed': False}, maps


@lru_cache(maxsize=1)
def _cached():
    return _build()


def overview():
    with _lock: return _cached()[0]


def scene(scene_id):
    # Exact catalogue IDs only: no user-controlled filesystem paths.
    with _lock:
        maps = _cached()[1]
        if scene_id not in maps: raise KeyError('Unknown archived scene')
        return maps[scene_id]


def recent():
    from .sentinel_updates import load_latest
    snapshot, _ = load_latest()
    return {'snapshot': {k:v for k,v in snapshot.items() if k not in ['scenes','errors']} if snapshot else None,
            'scenes': snapshot['scenes'] if snapshot else [],
            'statistics': snapshot['statistics'] if snapshot else {'catalogue_scenes':0,'usable_point_dates':0,
                'longest_gap_between_usable_acquisitions_days':None},
            'window_m':60,'cell_m':20,'minimum_valid_cells':5,'cloud_mask_independently_validated':False}


def recent_scene(key):
    from .sentinel_updates import load_latest
    _, maps = load_latest()
    if key not in maps: raise KeyError('Unknown recent scene version')
    return maps[key]
