"""Evidence-led data discovery for the working zone. No model fitting or routing.

HTML archives, daily summaries, reanalyses, satellite catalogues and original
forecasts retain separate identities, timestamps and target semantics.
"""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import calendar
import csv
import json
import math
import re
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from .working_zone import load_config, distance_km

DIRECTORY = ROOT/'data/working-zone-01/research'
RAW = DIRECTORY/'raw'
YEARS = [2022, 2023, 2024]


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self.depth = 0; self.row = None; self.cell = None
        self.flags = []; self.row_flags = None; self.cell_flag = False; self.row_flag = False; self.italic_depth = 0

    @staticmethod
    def marked(attrs):
        a = dict(attrs); style = (a.get('style') or '').lower(); classes = (a.get('class') or '').lower()
        return bool(re.search(r'font-style\s*:\s*(?:italic|oblique)', style) or re.search(r'(?:model|forecast|italic|estimated)', classes))

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth == 1: self.tables.append([]); self.flags.append([])
        if self.depth:
            if tag == 'tr': self.row = []; self.row_flags = []; self.row_flag = self.marked(attrs) or self.italic_depth > 0
            elif tag in ['td', 'th']: self.cell = []; self.cell_flag = self.row_flag or self.marked(attrs) or self.italic_depth > 0
            elif tag == 'br' and self.cell is not None: self.cell.append(' ')
        if tag in ['i', 'em']:
            self.italic_depth += 1
            if self.cell is not None: self.cell_flag = True

    def handle_data(self, value):
        if self.cell is not None: self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in ['td', 'th'] and self.cell is not None:
            if self.row is not None:
                self.row.append(' '.join(''.join(self.cell).split())); self.row_flags.append(self.cell_flag)
            self.cell = None
        if tag == 'tr' and self.row is not None:
            if self.depth: self.tables[-1].append(self.row); self.flags[-1].append(self.row_flags)
            self.row = None
        if tag == 'table': self.depth -= 1
        if tag in ['i', 'em']: self.italic_depth = max(0, self.italic_depth-1)


def optional_number(text, low, high):
    if text in ['', 'н/о', 'н/д', '---', '-', '—']: return None
    try: value = float(text.replace('−', '-').replace(',', '.'))
    except ValueError: raise ValueError('Unexpected numerical cell: '+text)
    if not math.isfinite(value) or not low <= value <= high: raise ValueError('Invalid numerical range')
    return value


def parse_krymsk(content, year, month, first_day=1, last_day=None):
    if year not in range(2011, 2025) or month not in [3, 4, 5, 6]: raise ValueError('Research pre-control seasons only')
    h = content.decode('utf-8'); parser = Tables(); parser.feed(h)
    title = re.search(r'<title>(.*?)</title>', h, re.S | re.I)
    if not title or f'{year} года' not in title.group(1): raise ValueError('Requested year ignored')
    if not re.search(r'name=["\']?id["\']?\s+value=["\']?37002\b', h): raise ValueError('Wrong station')
    if len(parser.tables) != 2: raise ValueError('Expected separate timestamp and weather tables')
    times, values = parser.tables
    if not times or 'UTC' not in ' '.join(times[0]) or len(times) != len(values): raise ValueError('Timestamp/weather alignment')
    if 'Т (С)' not in values[0] or 'Тmin (С)' not in values[0]: raise ValueError('Unknown table fields')
    last_day = last_day or calendar.monthrange(year, month)[1]
    result = []; seen = set()
    for t, v, flags in zip(times[1:], values[1:], parser.flags[1][1:]):
        if len(t) != 2 or len(v) != 18: raise ValueError('Unexpected archive columns')
        hour = int(t[0]); day, actual_month = map(int, t[1].split('.'))
        if actual_month != month or not first_day <= day <= last_day or hour not in range(0, 24, 3):
            raise ValueError('Requested period ignored or unexpected observation time')
        stamp = datetime(year, month, day, hour, tzinfo=UTC).isoformat()
        if stamp in seen: raise ValueError('Duplicate archive timestamp')
        seen.add(stamp)
        result.append({'station_id': '37002', 'observed_at_utc': stamp,
                       'reported_temperature_c': optional_number(v[5], -80, 60),
                       'temperature_c': None if flags[5] else optional_number(v[5], -80, 60),
                       'provider_model_filled_temperature': flags[5],
                       'dewpoint_c': None if flags[6] else optional_number(v[6], -100, 60),
                       'rh_pct_provider': None if flags[7] else optional_number(v[7], 0, 100),
                       'wind_ms': optional_number(v[1], 0, 100) if re.fullmatch(r'[+−\-]?\d+(?:[.,]\d+)?', v[1]) or v[1] in ['', 'н/о', 'н/д', '---', '-', '—'] else None,
                       'wind_text': v[1], 'wind_numeric_unparsed': bool(v[1] and not re.fullmatch(r'[+−\-]?\d+(?:[.,]\d+)?', v[1]) and v[1] not in ['н/о', 'н/д', '---', '-', '—']),
                       'cloud_text': v[4], 'published_minimum_c': optional_number(v[13], -80, 60),
                       'published_minimum_window': 'unverified; do not equate to14-hour pilot target',
                       'original_qc_flags_available': False, 'source': 'pogodaiklimat_archive',
                       'is_orchard_measurement': False})
    if not result: raise ValueError('Empty archive table')
    return result


def checked_raw(name):
    path = RAW/name; meta = json.loads(path.with_name(path.name+'.meta.json').read_text(encoding='utf-8'))
    if sha(path) != meta['sha256']: raise ValueError('Source bytes changed')
    return path.read_bytes(), meta


def krymsk_audit():
    records = []; provenance = {}
    for y in YEARS:
        for m in [3, 4, 5, 6]:
            name = 'krymsk-march2024-bot2.html' if (y, m) == (2024, 3) else f'krymsk-{y}-06-01.html' if m == 6 else f'krymsk-{y}-{m:02}.html'
            raw, meta = checked_raw(name)
            records += parse_krymsk(raw, y, m, last_day=1 if m == 6 else None); provenance[name] = meta
    if len({r['observed_at_utc'] for r in records}) != len(records): raise ValueError('Duplicate monthly joins')
    by_time = {r['observed_at_utc']: r for r in records}; years = {}; nights = []
    for y in YEARS:
        rr = [r for r in records if r['observed_at_utc'].startswith(str(y)) and r['observed_at_utc'][5:7] in ['03', '04', '05']]
        nn = []
        for i in range(92):
            d = date(y, 3, 1)+timedelta(days=i); start = datetime(d.year, d.month, d.day, 16, tzinfo=UTC)
            expected = [start+timedelta(hours=h) for h in range(14) if (start+timedelta(hours=h)).hour % 3 == 0]
            obs = [by_time.get(t.isoformat(), {}).get('temperature_c') for t in expected]
            complete = all(t is not None for t in obs)
            n = {'year': y, 'date': d.isoformat(), 'expected_observation_hours_utc': [t.isoformat() for t in expected],
                 'observed_temperature_c': obs, 'complete_4_slots': complete,
                 'sampled_minimum_c': min(obs) if complete else None,
                 'target_kind': 'minimum of4 three-hour observations; not14-hour or continuous minimum',
                 'is_orchard_truth': False}
            nn.append(n)
        nights.extend(nn)
        years[str(y)] = {'expected_reports': 92*8, 'reports': len(rr),
                        'temperature_present': sum(r['temperature_c'] is not None for r in rr),
                        'provider_model_temperature_excluded': sum(r['provider_model_filled_temperature'] for r in rr),
                        'dewpoint_present': sum(r['dewpoint_c'] is not None for r in rr),
                        'humidity_present': sum(r['rh_pct_provider'] is not None for r in rr),
                        'published_minimum_present': sum(r['published_minimum_c'] is not None for r in rr),
                        'complete_4_slot_nights': sum(n['complete_4_slots'] for n in nn),
                        'cold_4_slot_nights': sum(n['sampled_minimum_c'] is not None and n['sampled_minimum_c'] < 0 for n in nn)}
    write_json(DIRECTORY/'krymsk-three-hour-observations.json', records)
    write_json(DIRECTORY/'krymsk-four-slot-nights.json', nights)
    point = load_config()
    return {'station_id': '37002', 'latitude': 44.9109, 'longitude': 38.0053, 'provider_elevation_m': 35,
            'distance_km': distance_km(point['latitude'], point['longitude'], 44.9109, 38.0053),
            'metadata_source': 'https://www.pogodaiklimat.ru/station/37002.htm', 'years': years,
            'provenance': provenance, 'observations_sha256': sha(DIRECTORY/'krymsk-three-hour-observations.json'),
            'night_sample_sha256': sha(DIRECTORY/'krymsk-four-slot-nights.json'),
            'operational_training_admission': 'not for commercial product without written permission; pending original QC and extrema windows',
            'existing_models_modified': False}


def parse_daily(content, expected_id):
    records = []
    for l in content.decode('ascii').splitlines():
        if len(l) != 269 or l[:11] != expected_id: raise ValueError('Wrong GHCN fixed-width schema or station')
        year, month, element = int(l[11:15]), int(l[15:17]), l[17:21]
        if year not in YEARS or month not in [3, 4, 5] or element != 'TMIN': continue
        for d in range(1, calendar.monthrange(year, month)[1]+1):
            v = l[21+(d-1)*8:21+d*8]; value = int(v[:5]); mf, qf, sf = v[5:8]
            if value == -9999: continue
            if not -800 <= value <= 600: raise ValueError('Invalid TMIN scale/range')
            records.append({'station_id': expected_id, 'date': date(year, month, d).isoformat(),
                            'temperature_minimum_c': value/10, 'measurement_flag': mf, 'quality_flag': qf,
                            'source_flag': sf, 'admitted_descriptive': not qf.strip(),
                            'is_pilot_night_minimum': False, 'is_orchard_truth': False})
    return records


def daily_audit():
    result = {}; all_records = []
    for name, sid in [('SLAVJANSK', 'RSM00034924'), ('ANAPA', 'RSM00037001'), ('TEMRYUK', 'RSM00034915')]:
        raw, meta = checked_raw(f'ghcn-{name}.dly'); records = parse_daily(raw, sid); all_records += records
        result[sid] = {'name': name, 'provenance': meta, 'years': {}}
        for y in YEARS:
            rr = [r for r in records if r['date'].startswith(str(y))]; accepted = [r for r in rr if r['admitted_descriptive']]
            result[sid]['years'][str(y)] = {'expected_days': 92, 'reported': len(rr), 'qc_accepted': len(accepted),
                'qc_rejected': len(rr)-len(accepted), 'cold_days': sum(r['temperature_minimum_c'] < 0 for r in accepted),
                'measurement_flags': dict(Counter(r['measurement_flag'] for r in rr)),
                'source_flags': dict(Counter(r['source_flag'] for r in rr))}
    write_json(DIRECTORY/'daily-minimum-samples.json', all_records)
    return result


def terrain_satellite_reanalysis():
    p = load_config(); raw, emeta = checked_raw('elevation-grid.json'); elevations = json.loads(raw)['elevation']
    locations = [(p['latitude']+dy*.005, p['longitude']+dx*.007) for dy in range(-2, 3) for dx in range(-2, 3)]
    if len(elevations) != 25 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in elevations): raise ValueError('Invalid DEM response')
    terrain = {'provenance': emeta, 'dem': 'Copernicus DEM2021 GLO90 via Open-Meteo', 'underlying_resolution_m': 90,
               'sample_spacing_approx_m': 550, 'scope': '25 contextual points in roughly2.2x2.2km box, not farm boundary',
               'point_elevation_estimate_m': elevations[12], 'sample_minimum_m': min(elevations), 'sample_maximum_m': max(elevations),
               'points': [{'latitude': a, 'longitude': b, 'elevation_m': v} for (a, b), v in zip(locations, elevations)],
               'is_survey': False, 'cold_pool_validated': False}
    raw, smeta = checked_raw('sentinel2-spring2024.json'); catalogue = json.loads(raw)
    if any(v.get('rel') == 'next' for v in catalogue['links']): raise ValueError('Incomplete satellite catalogue pagination')
    scenes = []
    for f in catalogue['features']:
        q = f['properties']; t = q['datetime']
        if not '2024-03-01' <= t[:10] <= '2024-05-31': raise ValueError('Wrong satellite period')
        scenes.append({'id': f['id'], 'datetime': t, 'created': q.get('created'), 'updated': q.get('updated'),
                       'tile': q.get('grid:code'), 'cloud_cover_tile_percent': q['eo:cloud_cover'],
                       'catalogue_href': next(v['href'] for v in f['links'] if v['rel'] == 'self')})
    write_json(DIRECTORY/'sentinel2-scenes.json', scenes)
    satellite = {'provenance': smeta, 'items': len(scenes), 'unique_dates': len({s['datetime'][:10] for s in scenes}),
                 'items_cloud_under20_percent': sum(s['cloud_cover_tile_percent'] < 20 for s in scenes),
                 'tiles': sorted({s['tile'] for s in scenes}), 'scope': 'point neighbourhood catalogue; no orchard polygon',
                 'pixel_cloud_mask_checked': False, 'ndvi_computed': False, 'raw_pixels_downloaded': False,
                 'reprocessing_future_information': 'Updated2026 versions exist: not assumed available at original2024 issue time'}
    reanalysis = {}
    for model in ['era5land', 'era5']:
        raw, meta = checked_raw(model+'-sample.json'); data = json.loads(raw)
        times = data['hourly']['time']
        if len(times) != 72 or times[0] != '2024-04-01T00:00' or times[-1] != '2024-04-03T23:00': raise ValueError('Wrong reanalysis period')
        reanalysis[model] = {'provenance': meta, 'latitude': data['latitude'], 'longitude': data['longitude'],
                            'grid_elevation_m': data['elevation'], 'hours': len(times), 'units': data['hourly_units'],
                            'nonmissing': {k: sum(v is not None for v in values) for k, values in data['hourly'].items() if k != 'time'},
                            'is_observation': False, 'known_at_historical_issue': False}
    return terrain, satellite, reanalysis


def extract_gfs():
    """Forecast-only archive for the farm point; no proxy station targets attached."""
    from .regional_data import decode_day
    p = load_config(); site = {'id': p['id'], 'latitude': p['latitude'], 'longitude': p['longitude']}
    jobs = [(date(y, 3, 1)+timedelta(days=i), [site]) for y in YEARS for i in range(92)]
    rows = []; manifest = {}; distance = 0.
    with ProcessPoolExecutor(max_workers=4) as pool:
        for done, (day, curves, dm, ds) in enumerate(pool.map(decode_day, jobs), 1):
            values = curves[p['id']]; manifest.update(dm); distance = max(distance, ds[p['id']])
            rows.append({'id': p['id'], 'date': day.isoformat(), 'year': day.year,
                         'latitude': p['latitude'], 'longitude': p['longitude'],
                         'run_at_utc': datetime(day.year, day.month, day.day, 6, tzinfo=UTC).isoformat(),
                         'scenario_issued_at_utc': datetime(day.year, day.month, day.day, 15, tzinfo=UTC).isoformat(),
                         'lead_hours': list(range(10, 24)), 'gfs': values, 'is_observation': False,
                         'historical_first_publication_proven': False, 'target_temperature_c': None})
            if done % 30 == 0: print('Farm-point original GFS decoded', done, '/276', flush=True)
    write_json(DIRECTORY/'farm-gfs-prior-nights.json', rows); write_json(DIRECTORY/'farm-gfs-manifest.json', manifest)
    metadata = {'years': YEARS, 'nights': len(rows), 'hours': len(rows)*14, 'fields': sorted(rows[0]['gfs']),
                'point': {k: p[k] for k in ['latitude', 'longitude']}, 'max_grid_distance_km': distance,
                'dataset_sha256': sha(DIRECTORY/'farm-gfs-prior-nights.json'), 'manifest_sha256': sha(DIRECTORY/'farm-gfs-manifest.json'),
                'decoder_source_sha256': sha(ROOT/'agro_checker/regional_data.py'), 'working_config_sha256': sha(ROOT/'config/working_zone.json'),
                'scope': 'forecast inputs only; no farm observations or fitted local correction',
                'availability': 'archive Last-Modified+10min<=18MSK proxy, not proof of first historical publication',
                'generated_at_utc': datetime.now(UTC).isoformat()}
    write_json(DIRECTORY/'farm-gfs.meta.json', metadata); return metadata


def build_audit():
    terrain, satellite, reanalysis = terrain_satellite_reanalysis()
    gfs_path = DIRECTORY/'farm-gfs.meta.json'; gfs = json.loads(gfs_path.read_text(encoding='utf-8')) if gfs_path.exists() else None
    if gfs:
        if sha(DIRECTORY/'farm-gfs-prior-nights.json') != gfs['dataset_sha256'] or sha(DIRECTORY/'farm-gfs-manifest.json') != gfs['manifest_sha256']:
            raise ValueError('Farm GFS bytes changed')
    result = {'point': load_config(), 'krymsk': krymsk_audit(), 'daily_sources': daily_audit(),
              'terrain': terrain, 'satellite': satellite, 'reanalysis_samples': reanalysis, 'gfs': gfs,
              'invalid_or_unavailable_probes': {
                  'meteostat37002': '404; no admitted2024 hourly CSV',
                  'noaa370020': 'NCEI HTTP200 HTML directory frontend instead of CSV; direct NOAA S3 object404; no admitted observations',
                  'ogimet37002': 'empty response for requested2024 interval; not admitted',
                  'first_pogoda_query': 'bot=2 absent: returned current October instead of requested March; rejected by parser',
                  'meteo_ru': 'direct dataset-page fetch failed; official source access still needs verification',
                  'soilgrids_rest': 'provider documentation says service paused; no local soil values retrieved',
                  'initial_single_run_ecmwf': 'initial query malformed; corrected query stored separately as hindcast, never an operational2024 benchmark'},
              'metadata_identity_conflicts': [
                  {'id': '37001', 'noaa_isd_name': 'VITYAZEVO', 'noaa_isd_coordinates': [45.002, 37.347],
                   'ghcn_name': 'ANAPA', 'ghcn_coordinates': [44.9, 37.3], 'iem_URKA_coordinates': [44.8833, 37.2833],
                   'policy': 'Do not join targets by WMO/ICAO alone; confirm station history and instrument location'},
                  {'id': '34927', 'ghcn_coordinates': [45.03, 39.15],
                   'official2017_station_coordinates': [45.05, 39.0],
                   'policy': 'Metadata discrepancy; no automatic station relocation or label merge'}],
              'model_fitted': False, 'old_detector_changed': False, 'old_control_years_used_for_tuning': False,
              'license_access': {'noaa': 'free public climate archive; credit NOAA and underlying providers',
                  'pogodaiklimat': 'terms3.1 personal noncommercial use;3.5 commercial use requires prior written permission;3.7 public data requires active attribution; no product permission obtained',
                  'copernicus_dem': 'Copernicus attribution and Open-Meteo attribution required; hosted API commercial terms separate',
                  'sentinel2': 'public catalogue; downloading CDSE assets may require account/access credentials',
                  'soilgrids': 'CC BY4.0 data; REST is complementary paused/beta service, use stable raster distribution if needed'},
              'generated_at_utc': datetime.now(UTC).isoformat()}
    early = DIRECTORY/'early-archive-probe.json'
    if early.exists(): result['early_archive_probe'] = json.loads(early.read_text(encoding='utf-8'))
    result['license_access']['pogodaiklimat_terms_url'] = 'https://www.pogodaiklimat.ru/agreement.php'
    raw, meta = checked_raw('ecmwf-corrected-sample.json'); alternate = json.loads(raw)
    if alternate['hourly']['time'] != [f'2024-04-01T{h:02}:00' for h in range(24)]: raise ValueError('Wrong alternate run sample')
    result['alternate_run_sample'] = {'provenance': meta, 'hours': 24,
        'nonmissing': {k: sum(v is not None for v in vals) for k, vals in alternate['hourly'].items() if k != 'time'},
        'latitude': alternate['latitude'], 'longitude': alternate['longitude'], 'elevation': alternate['elevation'],
        'interpretation': 'early ECMWF IFS49R1 hindcast according to provider, not a proven issued2024 forecast',
        'admitted_operational_benchmark': False}
    write_json(DIRECTORY/'data-audit.json', result)
    write_json(ROOT/'docs/FARM_DATA_RESEARCH_SUMMARY.json', result)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--extract-gfs', action='store_true'); a = p.parse_args()
    if a.extract_gfs: print(json.dumps(extract_gfs(), ensure_ascii=False))
    else:
        r = build_audit(); print(json.dumps({'krymsk': r['krymsk']['years'], 'satellite': r['satellite'],
                                            'terrain': {k: r['terrain'][k] for k in ['point_elevation_estimate_m', 'sample_minimum_m', 'sample_maximum_m']}}, ensure_ascii=False))
