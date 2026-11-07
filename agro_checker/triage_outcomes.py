"""Additional 2024 evidence for frozen triage lists, never new inspection labels.

Only target evidence is expanded. Input pool, causal OOF scores, thresholds,
rankings and target window stay fixed. Unknowns are never interpolated.
"""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, timezone
from functools import lru_cache
import hashlib
import json
import numpy as np
from urllib.parse import urlencode
from . import triage_retention as prior
from .farm_context_data import ROOT, read, dump, digest, get
from .sentinel_updates import checked_url, head

D=ROOT/'data/farm-research/triage-outcomes-2024'
base=prior.base
COLLECTION='sentinel-2-c1-l2a'
HYBRID='protect_0.2_margin_0'


def protocol():
    if (D/'protocol.json').exists():return read(D/'protocol.json')
    meta=read(base.D/'meta-oof.json');base.only2024(meta['rows'])
    archive=read(base.INDEX/'protocol.json');dates=[base.day(r['issue']) for r in meta['rows']]
    files=[base.D/'meta-oof.json',base.D/'pool-2024.json',base.D/'frozen-selection.json',
        base.INDEX/'protocol.json',base.INDEX/'observations.json',base.INDEX/'satellite.json',
        prior.D/'frozen-selection.json',prior.D/'candidate-comparison-2024.json',prior.D/'protocol.json',
        ROOT/'data/farm-research/reconstruction/approximate-areas.geojson',
        ROOT/'agro_checker/triage_retention.py',ROOT/'agro_checker/spectral_triage.py',
        ROOT/'agro_checker/triage_outcomes.py',ROOT/'agro_checker/open_land_data.py',ROOT/'agro_checker/farm_context_analysis.py']
    p=dict(created_at_utc=base.stamp(),inputs={q.relative_to(ROOT).as_posix():digest(q) for q in files},
        bounds_utm=archive['bounds_utm'],bbox_wgs84=archive['bbox_wgs84'],collection=COLLECTION,
        start=(min(dates)+timedelta(days=6)).isoformat()+'T00:00:00Z',
        end=(max(dates)+timedelta(days=8)).isoformat()+'T23:59:59Z',
        hybrid=HYBRID,budget_k=3,fixed_parameters=dict(seed=20241021),
        frozen_choices='Only existing699-row baseline, Extra and hybrid choices from candidate-comparison-2024.json. No scores/threshold/rank refit. No2025/2026 outcome analysis.',
        restored_month_boundary='Known targets purged for monthly tuning can be used for evaluation AFTER the hybrid is frozen; never return them to training/selection.',
        additional_scenes='Fresh complete same-collection catalogue2024. Read previously full-grid-rejected adjacent tiles per fragment. Same original grid; outside footprint remains nodata. No partial-field median or mosaic across acquisitions.',
        target='For originally unavailable cases ONLY: same PB, nearest7days in6-8day window, ties earlier day; same-day highest core coverage, then latest acquisition, then ID. All original known and boundary-purged targets immutable.',
        quality='Unchanged core>=10 cells/.8clear, same20m grid/reflectance offsets, SCL4/5 eroded3x3, positive finite bands, sun_elevation>=20. No interpolation, gap widening, cloud relaxation, Landsat or radar pseudo-label.',
        check='Same133slots. Compare known counts, preserve ALL augmented baselineTP, paired missing-outcome bounds vs BOTH references, not only net observed FP. No model selection using recovered labels.',
        limitations='Previously developed2024, correlated dates/fragments; extra evidence is not a new season or human inspection. Reprocessed bytes at historical issue time not proven.',
        sources=['https://earth-search.aws.element84.com/v1/collections/sentinel-2-c1-l2a',
                 'https://sentiwiki.copernicus.eu/web/s2-processing'],
        model_changed=False,human_visits_created=0,production_promoted=False)
    dump(D/'protocol.json',p)
    protected={}
    for folder in ['models','data/field','web/scouting','data/farm-research']:
        for q in (ROOT/folder).rglob('*'):
            if not q.is_file() or D in q.parents:continue
            if folder=='data/farm-research' and q.suffix not in ['.json','.geojson','.joblib','.cbm','.csv']:continue
            protected[q.relative_to(ROOT).as_posix()]=digest(q)
    dump(D/'preservation-before.json',protected)
    return p


def check_inputs(p):
    for name,sha in p['inputs'].items():
        if digest(ROOT/name)!=sha:raise ValueError('Frozen source changed: '+name)


def catalogue():
    p=protocol();check_inputs(p)
    if (D/'catalogue.json').exists():return read(D/'catalogue.json')
    url='https://earth-search.aws.element84.com/v1/search?'+urlencode(dict(collections=COLLECTION,
        bbox=','.join(map(str,p['bbox_wgs84'])),datetime=p['start']+'/'+p['end'],limit=100))
    items={};pages=[];seen=set()
    for n in range(20):
        checked_url(url,'catalogue')
        if url in seen:raise ValueError('Catalogue cycle')
        seen.add(url);raw,meta=get(url,D/'catalogue'/f'page-{n+1}.json');obj=json.loads(raw);pages.append(meta)
        for f in obj['features']:
            if f['collection']!=COLLECTION or not p['start']<=f['properties']['datetime']<=p['end']:raise ValueError('Wrong source period')
            if f['id'] in items and items[f['id']]!=f:raise ValueError('Conflicting duplicate item')
            items[f['id']]=f
        nxt=[v for v in obj.get('links',[]) if v['rel']=='next']
        if not nxt or not obj['features']:break
        if len(nxt)!=1 or nxt[0].get('method','GET')!='GET':raise ValueError('Unsupported pagination')
        url=nxt[0]['href']
    else:raise ValueError('Incomplete catalogue')
    out=dict(items=sorted(items.values(),key=lambda f:(f['properties']['datetime'],f['id'])),pages=pages,pagination_completed=True)
    dump(D/'catalogue.json',out);print('Catalogue',len(items),flush=True);return out


@lru_cache(maxsize=1)
def grids():
    from rasterio.transform import from_origin
    from pyproj import Transformer
    from shapely import transform as st
    from shapely.geometry import shape
    from .farm_context_analysis import masks,scenarios
    p=read(D/'protocol.json');x0,y0,x1,y1=p['bounds_utm'];tr=from_origin(x0,y1,20,20)
    shape_=(int((y1-y0)/20),int((x1-x0)/20));project=Transformer.from_crs(4326,32637,always_xy=True);out={}
    for f in read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')['features']:
        poly=st(shape(f['geometry']),project.transform,interleaved=False)
        out[f['properties']['id']]=masks(scenarios(poly,f['properties']['working_band_m']),tr,shape_)
    return out,shape_,tr


def summary(ndvi,ndmi,valid,scl):
    from .farm_context_analysis import spectral_stats
    fields={}
    for fid,variants in grids()[0].items():
        values={}
        for name,ix in variants.items():
            ss=[spectral_stats(a,valid,ix) for a in [ndvi,ndmi]]
            if all(v['usable'] for v in ss):values[name]=dict(value=[v['value'] for v in ss],coverage=ss[0]['valid_fraction'])
        core=variants['core'];classes=Counter(map(int,scl.ravel()[core]));count=int(valid.ravel()[core].sum())
        fields[fid]=dict(values=values,total_cells=len(core),valid_cells=count,
            valid_fraction=count/len(core) if len(core) else 0.,scl_counts={str(k):v for k,v in classes.items()})
    return fields


def land_support(scl,raw_valid):
    land=np.isin(scl,[4,5]);eroded=np.lib.stride_tricks.sliding_window_view(np.pad(land,1,constant_values=False),(3,3)).all(axis=(-2,-1))
    return eroded&raw_valid


def item_accepted(f):
    props=f['properties'];pb=props.get('s2:processing_baseline')
    return pb is not None and float(pb)>=5 and pb!='05.09' and props.get('earthsearch:boa_offset_applied') is not True and props.get('view:sun_elevation',-1)>=20


def scene(f,reused):
    import rasterio,certifi
    from rasterio.windows import Window
    from .open_land_data import to_twenty,reflectance,indices
    sid=f['id'];folder=D/'sentinel'/sid;manifest=folder/'manifest.json';folder.mkdir(parents=True,exist_ok=True)
    if manifest.exists():
        old=read(manifest)
        if old['status']!='read_error':
            for path,sha in old.get('hashes',{}).items():
                if digest(ROOT/path)!=sha:raise ValueError('Changed target crop')
            return old
    itempath=folder/'item.json';dump(itempath,f)
    out=dict(id=sid,acquired_at_utc=f['properties']['datetime'],baseline=f['properties'].get('s2:processing_baseline'),
        item_path=itempath.relative_to(ROOT).as_posix(),retrieved_at_utc=base.stamp(),hashes={itempath.relative_to(ROOT).as_posix():digest(itempath)})
    if not item_accepted(f):
        out.update(status='metadata_rejected',fields={});dump(manifest,out);return out
    try:
        if sid in reused:
            record=reused[sid];oldfolder=ROOT/record['local_folder'] if record.get('local_folder') else base.INDEX/'sentinel'/sid
            original=read(oldfolder/'item.json')
            if original['properties']['s2:processing_baseline']!=out['baseline']:raise ValueError('Changed baseline in reused source')
            if any(original['assets'][k]['href']!=f['assets'][k]['href'] for k in ['scl','red','nir','swir16']):raise ValueError('Changed asset URL')
            for name,sha in record['crop_sha256'].items():
                path=oldfolder/name
                if digest(path)!=sha:raise ValueError('Old crop changed')
                out['hashes'][path.relative_to(ROOT).as_posix()]=sha
            with np.load(oldfolder/'indices.npz') as raw:
                ndvi=raw['ndvi'];ndmi=raw['ndmi'];valid=raw['valid'];scl=raw['scl']
            out.update(status='ready',reused=True,fields=summary(ndvi,ndmi,valid,scl));dump(manifest,out);return out
        x0,y0,x1,y1=read(D/'protocol.json')['bounds_utm'];shape_=grids()[1];arrays={};valids=[];versions={}
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_CAINFO=certifi.where(),GDAL_HTTP_TIMEOUT='30',GDAL_HTTP_MAX_RETRY='1'):
            for key in ['scl','red','nir','swir16']:
                asset=f['assets'][key];url=checked_url(asset['href'],'asset');before=head(url)
                with rasterio.open(url) as src:
                    if src.crs.to_epsg()!=32637 or list(src.shape)!=asset['proj:shape'] or not np.allclose(list(src.transform)[:6],asset['proj:transform'][:6],rtol=0,atol=1e-6):raise ValueError('Asset grid mismatch')
                    if src.transform.b or src.transform.d or src.transform.a not in [10,20] or src.transform.e!=-src.transform.a:raise ValueError('Unsupported grid')
                    factor=int(20/src.transform.a);col=(x0-src.transform.c)/src.transform.a;row=(src.transform.f-y1)/src.transform.a
                    if abs(col-round(col))>1e-6 or abs(row-round(row))>1e-6:raise ValueError('Unaligned source')
                    win=Window(round(col),round(row),shape_[1]*factor,shape_[0]*factor)
                    # No reprojection, interpolation or spatial clipping of field masks.
                    raw=src.read(1,window=win,boundless=True,fill_value=src.nodata)
                    if raw.shape!=(shape_[0]*factor,shape_[1]*factor):raise ValueError('Wrong crop shape')
                    profile=src.profile.copy();profile.update(width=raw.shape[1],height=raw.shape[0],transform=src.window_transform(win),driver='GTiff',compress='deflate')
                    path=folder/(key+'.tif')
                    with rasterio.open(path,'w',**profile) as dst:dst.write(raw,1);dst.scales=src.scales;dst.offsets=src.offsets
                    out['hashes'][path.relative_to(ROOT).as_posix()]=digest(path)
                    a,valid=to_twenty(raw,factor,src.nodata)
                    arrays[key]=a if key=='scl' else reflectance(a,asset,src.scales[0],src.offsets[0]);valids.append(valid)
                if head(url)!=before:raise ValueError('Remote object changed during read')
                versions[key]=before
                if key=='scl':
                    potential=land_support(a,valid)
                    possible=any(len(v['core'])>=10 and potential.ravel()[v['core']].sum()>=10 and potential.ravel()[v['core']].mean()>=.8 for v in grids()[0].values())
                    if not possible:
                        blank=np.full(shape_,np.nan);out.update(status='scl_only_no_eligible_core',reused=False,source_versions=versions,
                            fields=summary(blank,blank,potential,a))
                        dump(manifest,out);return out
        ndvi,ndmi,valid=indices(arrays['red'],arrays['nir'],arrays['swir16'],arrays['scl'],np.logical_and.reduce(valids))
        path=folder/'indices.npz';np.savez_compressed(path,ndvi=ndvi.astype('float32'),ndmi=ndmi.astype('float32'),valid=valid,scl=arrays['scl'].astype('uint8'))
        out['hashes'][path.relative_to(ROOT).as_posix()]=digest(path)
        # Summarise the actual stored float32 bytes, as in the frozen experiment.
        with np.load(path) as raw:fields=summary(raw['ndvi'],raw['ndmi'],raw['valid'],raw['scl'])
        out.update(status='ready',reused=False,source_versions=versions,fields=fields);dump(manifest,out);return out
    except Exception as exc:
        out.update(status='read_error',error=f'{type(exc).__name__}: {exc}',fields={});dump(manifest,out);return out


def collect():
    if (D/'frozen-evaluation.json').exists():raise ValueError('Frozen evidence cannot be recollected')
    p=protocol();check_inputs(p);cat=catalogue();rows=read(base.D/'meta-oof.json')['rows']
    missing=[r for r in rows if r['target'] is None]
    needed={f['id'] for f in cat['items'] if any(6<=(base.day(f['properties']['datetime'])-base.day(r['issue'])).days<=8 and f['properties'].get('s2:processing_baseline')==r['baseline'] for r in missing)}
    old={s['id']:s for s in read(base.INDEX/'satellite.json')['scenes'] if s['status']=='ready' and s['acquired_at_utc'][:4]=='2024'}
    items=[f for f in cat['items'] if f['id'] in needed];results=[];grids()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i,out in enumerate(pool.map(lambda f:scene(f,old),items),1):
            results.append(out);print(i,'/',len(items),out['id'],out['status'],flush=True)
    result=dict(created_at_utc=base.stamp(),catalogue_items=len(cat['items']),needed_items=len(items),
        counts=dict(Counter(s['status'] for s in results)),scenes=results,pagination_completed=cat['pagination_completed'])
    dump(D/'satellite.json',result);return result


def choose_target(row,scenes):
    """Quality, version and date policy only. Never select by observed index value."""
    daily={}
    for s in scenes:
        lag=(base.day(s['acquired_at_utc'])-base.day(row['issue'])).days
        if not 6<=lag<=8 or s['baseline']!=row['baseline'] or s['status']!='ready':continue
        q=s.get('fields',{}).get(row['fid'],{}).get('values',{}).get('core')
        if not q:continue
        day=s['acquired_at_utc'][:10];rank=(q['coverage'],s['acquired_at_utc'],s['id'])
        if day not in daily or rank>daily[day][0]:daily[day]=(rank,s,q)
    if not daily:return None
    day=min(daily,key=lambda d:(abs((base.day(d)-base.day(row['issue'])).days-7),d))
    _,s,q=daily[day]
    return dict(target=day,target_scene=s['id'],y=q['value'],coverage=q['coverage'],baseline=s['baseline'])


def extend(rows,pool,scenes,items):
    lookup={base.key(r):r for r in pool};augmented=[];ledger=[]
    for r in rows:
        original=lookup[base.key(r)];out=dict(r);reason='original_known';evidence=None
        if r['y'] is None:
            if original['y'] is not None:
                out.update(y=original['y'],target=original['target'],target_scene=original['target_scene']);reason='month_boundary_restored'
            else:
                evidence=choose_target(r,scenes)
                if evidence:
                    out.update(y=evidence['y'],target=evidence['target'],target_scene=evidence['target_scene']);reason='additional_scene_restored'
                else:
                    relevant=[f for f in items if 6<=(base.day(f['properties']['datetime'])-base.day(r['issue'])).days<=8]
                    same=[f for f in relevant if f['properties'].get('s2:processing_baseline')==r['baseline']]
                    ss=[s for s in scenes if s['id'] in {f['id'] for f in same}]
                    if not relevant:reason='no_catalogue_acquisition_in_window'
                    elif not same:reason='no_same_baseline_in_window'
                    elif any(s['status']=='read_error' for s in ss):reason='unresolved_source_read_error'
                    elif not any(item_accepted(f) for f in same):reason='metadata_quality_rejected'
                    else:reason='insufficient_clear_core'
        if out.get('y') is not None:
            if not 6<=(base.day(out['target'])-base.day(out['issue'])).days<=8:raise ValueError('Changed target window')
        augmented.append(out)
        ledger.append(dict(fid=r['fid'],issue=r['issue'],reason=reason,original_unknown=r['y'] is None,
            target=out.get('target'),target_scene=out.get('target_scene'),additional_evidence=evidence,
            possible_source_ids=[s['id'] for s in scenes if s['baseline']==r['baseline'] and 6<=(base.day(s['acquired_at_utc'])-base.day(r['issue'])).days<=8]))
    return augmented,ledger


def fixed_choices(rows):
    comparison=read(prior.D/'candidate-comparison-2024.json')
    if comparison['variant']['id']!=HYBRID:raise ValueError('Changed hybrid')
    candidates=comparison['candidates']
    if [base.key(r) for r in rows]!=[base.key(r) for r in candidates]:raise ValueError('Changed input pool')
    return {name:np.array([r[field] for r in candidates],bool) for name,field in
        [('baseline','baseline_selected'),('extra','extra_selected'),('hybrid','candidate_selected')]}


def comparison(rows,choices,p):
    base.only2024(rows);metrics={name:base.rank_metrics(rows,chosen,p) for name,chosen in choices.items()}
    paired={name:base.paired_metrics(rows,choices[name],choices['hybrid']) for name in ['baseline','extra']}
    checks=dict(all_augmented_baseline_tp_retained=paired['baseline']['lost_known_baseline_tp']==0,
        events_not_below_extra=metrics['hybrid']['tp']>=metrics['extra']['tp'],
        known_negatives_not_above_either=metrics['hybrid']['fp']<=min(metrics[n]['fp'] for n in ['baseline','extra']),
        possible_negative_growth_vs_baseline=paired['baseline']['negative_growth_upper']<=0,
        possible_negative_growth_vs_extra=paired['extra']['negative_growth_upper']<=0,
        strict_paired_gain_vs_baseline=paired['baseline']['difference_lower']>1e-12)
    return dict(metrics=metrics,paired=paired,checks=checks,passed=all(checks.values()))


def evaluate():
    if (D/'frozen-evaluation.json').exists():raise ValueError('Already frozen; no evidence-driven retries')
    p=protocol();check_inputs(p);meta=read(base.D/'meta-oof.json');rows=meta['rows'];base.only2024(rows)
    sat=read(D/'satellite.json');cat=read(D/'catalogue.json');pool=read(base.D/'pool-2024.json')
    augmented,ledger=extend(rows,pool,sat['scenes'],cat['items']);choices=fixed_choices(rows)
    monthly,_=extend(rows,pool,[],[])
    results={label:comparison(rr,choices,p) for label,rr in [('original',rows),('month_boundary_only',monthly),('additional_evidence',augmented)]}
    dump(D/'augmented-outcomes.json',augmented);dump(D/'recovery-ledger.json',ledger)
    report=dict(created_at_utc=base.stamp(),results=results,reasons=dict(Counter(r['reason'] for r in ledger)),
        candidate_count=len(rows),known_before=sum(r['y'] is not None for r in rows),known_after=sum(r['y'] is not None for r in augmented),
        chosen_reasons={n:dict(Counter(r['reason'] for r,c in zip(ledger,choices[n]) if c)) for n in choices},
        parameters_changed=False,model_fit_performed=False,test_years_evaluated=[],human_visits_created=0,production_promoted=False)
    dump(D/'report.json',report)
    frozen=dict(created_at_utc=base.stamp(),hashes={n:digest(D/n) for n in ['protocol.json','catalogue.json','satellite.json','augmented-outcomes.json','recovery-ledger.json','report.json']},code_sha256=digest(__file__))
    dump(D/'frozen-evaluation.json',frozen)
    print('Recovery',report['reasons'],flush=True)
    for label,rr in results.items():print(label,{n:{k:v[k] for k in ['tp','fp','unknown']} for n,v in rr['metrics'].items()},rr['checks'],flush=True)
    return report
