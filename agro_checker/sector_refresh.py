"""Versioned, additive sector observations. Never fit models or write field journals."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
from urllib.parse import urlencode, urlparse

from .pipeline import ROOT

BASE = ROOT/'data/farm-research/area-context'
DIRECTORY = ROOT/'data/farm-observations'
PERIOD = 'autumn2026'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def instant(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Explicit timezone required')
    return t.astimezone(timezone.utc)


def curl_bytes(url, head=False):
    command = ['curl.exe' if os.name == 'nt' else 'curl', '--http1.1', '--retry', '1', '--retry-all-errors',
               '--connect-timeout', '10', '--max-time', '45',
               '--fail', '--silent', '--show-error']
    if head:
        command.append('--head')
    else:
        command += ['--max-filesize', '20000000']
    result = subprocess.run(command+[url], capture_output=True, timeout=100)
    if result.returncode:
        raise OSError(result.stderr.decode('utf-8', errors='replace').strip())
    if len(result.stdout) > 20_000_000:
        raise ValueError('Oversized response')
    return result.stdout


def object_head(url):
    from .sentinel_updates import checked_url
    checked_url(url, 'asset')
    raw = curl_bytes(url, head=True).decode('iso-8859-1')
    block = [v for v in raw.replace('\r\n', '\n').split('\n\n') if v.startswith('HTTP/')][-1]
    lines = block.splitlines()
    if lines[0].split()[1] != '200':
        raise ValueError('Unexpected object HEAD status')
    headers = dict((a.strip().lower(), b.strip()) for line in lines[1:] if ':' in line for a,b in [line.split(':',1)])
    result = {'url':url, 'etag':headers.get('etag'), 'last_modified':headers.get('last-modified'),
              'bytes':int(headers.get('content-length','0'))}
    if not result['etag'] or not result['bytes']:
        raise ValueError('Missing remote object version')
    return result


def catalogue(protocol, directory):
    """No tile-cloud filter. Save every page; exhaust and validate pagination."""
    period = protocol['periods'][0]
    start, end = instant(period['start']), instant(period['end'])
    url = 'https://earth-search.aws.element84.com/v1/search?'+urlencode(dict(
        collections=protocol['collection'], bbox=','.join(map(str,protocol['bbox_wgs84'])),
        datetime=period['start']+'/'+period['end'], limit=100))
    items, pages, seen = {}, [], set()
    for number in range(20):
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc, parsed.path) != ('https','earth-search.aws.element84.com','/v1/search') or url in seen:
            raise ValueError('Unexpected or cyclic catalogue link')
        seen.add(url)
        raw = curl_bytes(url)
        payload = json.loads(raw)
        if not isinstance(payload.get('features'),list):
            raise ValueError('Not a STAC item collection')
        page = directory/'catalogue'/f'page-{number+1:02d}.json'
        page.parent.mkdir(parents=True,exist_ok=True)
        page.write_bytes(raw)
        pages.append(dict(path=page.relative_to(directory).as_posix(),url=url,sha256=sha(page),
                          received_at_utc=datetime.now(timezone.utc).isoformat()))
        for item in payload['features']:
            if item['collection'] != protocol['collection'] or not start <= instant(item['properties']['datetime']) <= end:
                raise ValueError('Wrong collection/acquisition')
            if not re.fullmatch(r'S2[ABC]_T\d{2}[A-Z]{3}_\d{8}T\d{6}_L2A',item['id']):
                raise ValueError('Unexpected scene identity')
            if item['id'] in items and items[item['id']] != item:
                raise ValueError('Conflicting duplicate scene')
            items[item['id']] = item
        nexts = [v for v in payload.get('links',[]) if v.get('rel')=='next']
        if not nexts:
            break
        if len(nexts)!=1 or nexts[0].get('method','GET')!='GET':
            raise ValueError('Unsupported pagination')
        url = nexts[0]['href']
    else:
        raise ValueError('Incomplete catalogue')
    result = dict(items=[dict(period=period['id'],item=v) for v in sorted(items.values(),key=lambda f:(f['properties']['datetime'],f['id']))],
                  pages=pages,pagination_completed=True)
    dump(directory/'catalogue.json',result)
    return result


def initialise(through, directory):
    if (directory/'summary.json').exists():
        raise ValueError('Analysed version is frozen; create a new version for recollection')
    end = instant(through)
    if end > datetime.now(timezone.utc):
        raise ValueError('Cannot collect future observations')
    base = read(BASE/'protocol.json')
    p = {**base,'created_at_utc':datetime.now(timezone.utc).isoformat(),
         'periods':[dict(id=PERIOD,label='Октябрь–декабрь 2026' if end.month==12 else 'Октябрь–ноябрь 2026',start='2026-10-20T00:00:00Z',end=end.isoformat())],
         'base_protocol_sha256':sha(BASE/'protocol.json'), 'training_selection_years':[2024],
         'policy':'Observations only. Frozen base unchanged. No tuning on2025/2026, no operational writes.'}
    if end < instant(p['periods'][0]['start']):
        raise ValueError('End precedes sector extension start')
    path = directory/'protocol.json'
    if path.exists():
        old = read(path)
        for key in ('periods','base_protocol_sha256','contours_sha256','bounds_utm'):
            if old[key]!=p[key]:
                raise ValueError('Different collection protocol; use a new version directory')
        return old
    dump(path,p)
    return p


def collect(through, directory, workers=2, prepared_catalogue=None, cache_from=None):
    """Reuse the frozen spectral algorithm in an isolated collector process."""
    from . import farm_context_data as source
    directory = Path(directory).resolve()
    p = initialise(through,directory)
    cat = prepared_catalogue if prepared_catalogue is not None else catalogue(p,directory)
    cached_outside={}
    if cache_from is not None:
        cache_from=Path(cache_from)
        old_p=read(cache_from/'protocol.json')
        if any(old_p[key]!=p[key] for key in ('bounds_utm','contours_sha256','collection')):
            raise ValueError('Cached archive uses another grid/geometry/collection')
        old_items={r['item']['id']:r['item'] for r in read(cache_from/'catalogue.json')['items']}
        old_scenes={r['id']:r for r in read(cache_from/'satellite.json')['scenes']}
        for entry in cat['items']:
            sid=entry['item']['id'];scene=old_scenes.get(sid)
            if not scene or old_items.get(sid)!=entry['item']:continue
            if scene['status']=='outside_full_grid':
                cached_outside[sid]=scene
            elif scene['status']=='ready':
                folder=cache_from/'sentinel'/sid
                if not all((folder/name).is_file() and sha(folder/name)==value for name,value in scene['crop_sha256'].items()):
                    continue  # Missing optional native cache after clone: download instead.
                target=directory/'sentinel'/sid;target.mkdir(parents=True,exist_ok=True)
                for name in [*scene['crop_sha256'],'manifest.json']:
                    if (target/name).exists():raise ValueError('Cache target is not a new version')
                    try:os.link(folder/name,target/name)
                    except OSError:shutil.copy2(folder/name,target/name)
    previous_directory, previous_head = source.D, source.head
    source.D, source.head = directory, object_head
    try:
        def one(entry):
            try:
                result = cached_outside.get(entry['item']['id']) or source.scene(entry)
            except Exception as error:
                result = dict(id=entry['item']['id'],period=PERIOD,status='extraction_error',
                              acquired_at_utc=entry['item']['properties']['datetime'],reason=f'{type(error).__name__}: {error}')
            print(result['id'],result['status'],result.get('reason',''),flush=True)
            return result
        with ThreadPoolExecutor(max_workers=workers) as pool:
            scenes = list(pool.map(one,cat['items']))
    finally:
        source.D, source.head = previous_directory, previous_head
    result = dict(scenes=scenes,pagination_completed=True,
                  counts={k:sum(v['status']==k for v in scenes) for k in ('ready','outside_full_grid','extraction_error')})
    dump(directory/'satellite.json',result)
    return result


def analyse(directory):
    from . import farm_context_data as source, farm_context_analysis as analysis
    from pyproj import Transformer
    from shapely import transform
    from shapely.geometry import shape
    directory = Path(directory).resolve()
    pointer = directory.parent.parent/'current.json'
    if pointer.exists() and read(pointer).get('directory')==directory.relative_to(directory.parent.parent).as_posix():
        raise ValueError('Published observation version is immutable')
    satellite = read(directory/'satellite.json')
    if satellite['counts']['extraction_error'] or not satellite['counts']['ready']:
        raise ValueError('Do not publish a technically incomplete/empty extraction')
    p = read(directory/'protocol.json')
    geo = read(source.R/'approximate-areas.geojson')
    if sha(source.R/'approximate-areas.geojson') != p['contours_sha256']:
        raise ValueError('Geometry changed')
    projection = Transformer.from_crs(4326,32637,always_xy=True)
    features = [(f['properties']['id'],transform(shape(f['geometry']),projection.transform,interleaved=False),
                 f['properties']['working_band_m']) for f in geo['features']]
    old_source, old_analysis = source.D, analysis.D
    source.D = analysis.D = directory
    try:
        history, changes = analysis.satellite_context(features)
    finally:
        source.D, analysis.D = old_source, old_analysis
    quality = {}
    for fid, rows in history.items():
        full = [r for r in rows if r['source_status']=='ready']
        usable = [r for r in full if r.get('ndvi') and r['ndvi']['base'] is not None and r['ndmi']['base'] is not None]
        robust = [r for r in usable if r['ndvi']['robust'] and r['ndmi']['robust']]
        quality[fid] = dict(total_full_extent=len(full),base_usable=len(usable),robust_both_indices=len(robust),
            full_extent_calendar_days=len({r['acquired_at_utc'][:10] for r in full}),
            base_usable_calendar_days=len({r['acquired_at_utc'][:10] for r in usable}),
            robust_both_calendar_days=len({r['acquired_at_utc'][:10] for r in robust}),
            latest_usable_acquisition_utc=max((r['acquired_at_utc'] for r in usable),default=None))
    out = dict(protocol=p, features={fid:dict(history=history[fid],changes=changes[fid],availability={PERIOD:quality[fid]}) for fid in history},
               summary=dict(fragment_count=len(history),satellite_counts=satellite['counts'],confirmed_boundary=False,used_by_model=False),
               provenance=dict(protocol_sha256=sha(directory/'protocol.json'),satellite_manifest_sha256=sha(directory/'satellite.json'),
                   base_summary_sha256=sha(BASE/'summary.json'),contours_sha256=p['contours_sha256'],
                   collection_code_sha256=sha(Path(source.__file__)),analysis_code_sha256=sha(Path(analysis.__file__)),
                   spectral_algorithm_sha256=sha(ROOT/'agro_checker/open_land_data.py')))
    dump(directory/'collector-code.json',dict(
        sector_refresh_sha256=sha(Path(__file__)),
        snapshot=dict(sector_refresh=Path(__file__).read_text(encoding='utf-8'),
                      collector=Path(source.__file__).read_text(encoding='utf-8'),
                      analysis=Path(analysis.__file__).read_text(encoding='utf-8'))))
    out['provenance']['code_snapshot_sha256']=sha(directory/'collector-code.json')
    dump(directory/'summary.json',out)
    dump(directory/'quality-report.json',dict(period=PERIOD,counts=satellite['counts'],areas=quality,
        latest_acquisition_utc=max(v['acquired_at_utc'] for v in satellite['scenes']),
        notice='Pixel QC at unconfirmed fragments, not cloud-mask validation or model accuracy.'))
    return out


def publish(directory, root=DIRECTORY):
    """Validate files before atomically switching the sole pointer; retain old versions."""
    directory, root = Path(directory).resolve(), Path(root).resolve()
    if directory.parent != root/'versions':
        raise ValueError('Publish only an immediate version directory')
    summary = read(directory/'summary.json')
    satellite = read(directory/'satellite.json')
    if satellite['counts']['extraction_error']:
        raise ValueError('Cannot publish extraction errors')
    if sha(directory/'protocol.json')!=summary['provenance']['protocol_sha256'] or sha(directory/'satellite.json')!=summary['provenance']['satellite_manifest_sha256']:
        raise ValueError('Summary input mismatch')
    for scene in satellite['scenes']:
        if scene['status']=='ready' and sha(directory/'sentinel'/scene['id']/'indices.npz')!=scene['crop_sha256']['indices.npz']:
            raise ValueError('Index checksum mismatch')
    pointer = dict(schema_version=1,directory=directory.relative_to(root).as_posix(),summary_sha256=sha(directory/'summary.json'),
                   published_at_utc=datetime.now(timezone.utc).isoformat())
    temporary = root/'current.json.tmp'
    dump(temporary,pointer)
    temporary.replace(root/'current.json')
    return pointer


def extend_archive(protocol, summary, scenes, root=DIRECTORY):
    """Read only a fully published extension. Base files and historical periods stay intact."""
    root = Path(root)
    if not (root/'current.json').exists():
        return protocol,summary,scenes
    pointer = read(root/'current.json')
    if not re.fullmatch(r'versions/[a-zA-Z0-9_-]+',pointer['directory']):
        raise ValueError('Unexpected observation version path')
    directory = root/pointer['directory']
    if sha(directory/'summary.json')!=pointer['summary_sha256']:
        raise ValueError('Observation summary integrity failed')
    extra = read(directory/'summary.json')
    p = read(directory/'protocol.json')
    v = extra['provenance']
    if sha(directory/'protocol.json')!=v['protocol_sha256'] or sha(directory/'satellite.json')!=v['satellite_manifest_sha256']:
        raise ValueError('Observation inputs integrity failed')
    if sha(directory/'collector-code.json')!=v['code_snapshot_sha256']:
        raise ValueError('Observation code snapshot integrity failed')
    if v['base_summary_sha256']!=sha(BASE/'summary.json') or p['base_protocol_sha256']!=sha(BASE/'protocol.json') or p['bounds_utm']!=protocol['bounds_utm'] or p['contours_sha256']!=protocol['contours_sha256']:
        raise ValueError('Observation extension does not match the frozen base')
    if set(extra['features'])!=set(summary['features']):
        raise ValueError('Observation fragment set differs')
    old_ids={v['id'] for v in protocol['periods']}
    if any(v['id'] in old_ids for v in p['periods']):
        raise ValueError('Observation periods must be additive')
    merged_p = {**protocol,'periods':protocol['periods']+p['periods'],'created_at_utc':p['created_at_utc']}
    merged_s = deepcopy(summary)
    for fid, feature in extra['features'].items():
        merged_s['features'][fid]['history'] += feature['history']
        merged_s['features'][fid]['changes'] += feature['changes']
        merged_s['features'][fid]['availability'].update(feature['availability'])
    combined = dict(scenes)
    for scene in read(directory/'satellite.json')['scenes']:
        if scene['id'] in combined:
            raise ValueError('Duplicate scene in additive archive')
        combined[scene['id']]={**scene,'observation_directory':str(directory)}
    merged_s['_scouting_version']=hashlib.sha256((summary['_scouting_version']+pointer['summary_sha256']).encode()).hexdigest()
    merged_s['_observation_version']=pointer['summary_sha256']
    return merged_p,merged_s,combined
