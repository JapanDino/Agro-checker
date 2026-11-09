"""Pre-outcome shadow releases. Satellite targets only, never field visits or retuning."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import joblib
import numpy as np
from .pipeline import ROOT
from . import sector_refresh as satellite
from scripts import experiment_consensus_ndmi_guard as repair

base=repair.base
DIRECTORY=ROOT/'data/prospective-scouting'
UTC=timezone.utc


def now():return datetime.now(UTC)
def instant(value):return satellite.instant(value)
def read(path):return satellite.read(path)
def sha(path):return satellite.sha(path)
def encoded(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()


def immutable(path,payload):
    """One complete envelope, exclusive creation. A failed write is not a valid release."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    envelope=dict(payload=payload,sha256=digest(payload))
    temporary=path.with_name(path.name+'.part-'+uuid4().hex)
    try:
        with temporary.open('xb') as file:
            file.write(encoded(envelope)+b'\n');file.flush();os.fsync(file.fileno())
        os.link(temporary,path)  # Atomic exclusive publication; never overwrite an existing record.
    finally:
        temporary.unlink(missing_ok=True)
    return envelope


def checked(path):
    envelope=read(path)
    if set(envelope)!= {'payload','sha256'} or digest(envelope['payload'])!=envelope['sha256']:
        raise ValueError(f'Changed prospective record: {Path(path).name}')
    return envelope


def code_paths():
    return ['agro_checker/prospective_scouting.py','agro_checker/spectral_triage.py',
            'agro_checker/field_decline_experiment.py','agro_checker/field_index_experiment.py',
            'agro_checker/farm_context_analysis.py','agro_checker/ndmi_turn_guard.py',
            'agro_checker/open_land_data.py','agro_checker/farm_context_data.py',
            'agro_checker/sector_refresh.py','scripts/prospective_scouting.py',
            'scripts/experiment_consensus_ndmi_guard.py']


def register(directory=DIRECTORY):
    """Fixed final fit on2024 only. No search, no2025/2026 data or existing-model writes."""
    directory=Path(directory);frozen=directory/'frozen'
    if frozen.exists():return configuration(directory)
    raw=read(base.D/'pool-2024.json')
    available=[r for r in raw if r['fid'] not in base.HOLDOUT]
    base.only2024(available)
    training=[r for r in available if r['y'] is not None]
    base.only2024(training,require_known=True)
    settings=read(base.D/'protocol.json')
    rule=read(repair.D/'protocol.json')
    chosen=read(repair.D/'report.json')['selected_repair']
    if chosen['id']!='protect_weak_divergent_round':raise ValueError('Unexpected frozen research candidate')
    stage=directory/('.registration-'+uuid4().hex);stage.mkdir(parents=True)
    models={}
    for method in ('logit','extra'):
        model=base.fit_model(training,method,settings)
        if model['method']!=method:raise ValueError('Insufficient2024 support; do not pretend a fitted classifier')
        path=stage/(method+'.joblib');joblib.dump(model,path)
        models[method]=dict(file=path.name,sha256=sha(path))
    grid=read(base.INDEX/'protocol.json')
    protocol=dict(schema_version=1,registered_at_utc=now().isoformat(),selection_years=[2024],
        geometry_sha256=grid['contours_sha256'],bounds_utm=grid['bounds_utm'],
        prohibited_tuning_years=[2025,2026],target='Same-baseline core NDVI OR NDMI decline<=-0.10 at6-8 UTC calendar days from anchor acquisition date, NOT from job run or receipt date.',
        threshold=-.10,horizon_days=[6,8],preferred_day=7,target_grace_hours=72,
        budget_k=3,maximum_input_age_hours=72,minimum_sun_elevation_deg=20,
        core_minimum_cells=10,core_minimum_coverage=.8,previous_lag_days=[2,21],
        issue_policy='Latest acquisition UTC day with input-only eligible candidates within72h; one release per anchor day. Entire target window must still be future at actual issue. Include all available fragments, spatial reserve reported separately. No target availability filter.',
        finalisation='Wait through end of8th target UTC day plus72h. Require complete fetched catalogue covering target window. Targets received after deadline remain unknown in primary result. No final label early, no negative for missing/cloudy target, immutable evaluation.',
        pending_policy='No comparison metrics for pending releases. Same candidate pool and same min(3,N) slots for both lists. Unknowns stay in denominators.',
        temporal_limitations='Acquisition+6-8day target retained from training; issuance/receipt/acquisition timestamps differ. Local hashes are NOT a trusted external timestamp or tamperproof archive.',
        models=models,model_parameters=settings['fixed_parameters'],features=base.FEATURES,
        training=dict(source= (base.D/'pool-2024.json').relative_to(ROOT).as_posix(),sha256=sha(base.D/'pool-2024.json'),
                      pairs=len(training),dates=len({r['issue'] for r in training}),max_target=max(r['target'] for r in training),
                      reserved_fragment_ids=sorted(base.HOLDOUT)),
        ranking_parameters=rule,baseline=rule['variants'][0],candidate=chosen,
        code_sha256={name:sha(ROOT/name) for name in code_paths()},
        environment=dict(python=sys.version.split()[0],numpy=np.__version__,sklearn=importlib.metadata.version('scikit-learn'),joblib=joblib.__version__),
        production_promoted=False,field_visits_created=0,automatic_diagnoses=False)
    immutable(stage/'protocol.json',protocol)
    frozen.parent.mkdir(parents=True,exist_ok=True);stage.rename(frozen)
    return configuration(directory)


def configuration(directory=DIRECTORY):
    directory=Path(directory);envelope=checked(directory/'frozen/protocol.json');p=envelope['payload']
    for name,value in p['code_sha256'].items():
        if sha(ROOT/name)!=value:raise ValueError(f'Frozen scoring/evaluation code changed: {name}; create a separately registered study')
    for m in p['models'].values():
        if m['file'] not in ('logit.joblib','extra.joblib') or sha(directory/'frozen'/m['file'])!=m['sha256']:
            raise ValueError('Frozen model integrity failed')
    if (importlib.metadata.version('scikit-learn')!=p['environment']['sklearn'] or
        np.__version__!=p['environment']['numpy'] or joblib.__version__!=p['environment']['joblib']):
        raise ValueError('Different model runtime; do not silently change frozen inference')
    return p,envelope['sha256']


def snapshot(cutoff):
    """Read a published archive; retain actual receipt times and pixel hashes."""
    pointer_path=satellite.DIRECTORY/'current.json'
    pointer_bytes=pointer_path.read_bytes() if pointer_path.exists() else None
    p=read(satellite.BASE/'protocol.json');s=read(satellite.BASE/'summary.json')
    if (sha(satellite.BASE/'protocol.json')!=s['provenance']['protocol_sha256'] or
        sha(satellite.BASE/'satellite.json')!=s['provenance']['satellite_manifest_sha256']):
        raise ValueError('Frozen source manifest/protocol changed')
    s['_scouting_version']=sha(satellite.BASE/'summary.json')
    sources={v['id']:v for v in read(satellite.BASE/'satellite.json')['scenes']}
    p,s,sources=satellite.extend_archive(p,s,sources)
    result=[]
    for sid,scene in sorted(sources.items()):
        if scene['status']!='ready':continue
        if instant(scene['acquired_at_utc'])>cutoff:continue
        received=scene.get('retrieved_at_utc')
        if not received or instant(received)>cutoff:continue
        folder=Path(scene['observation_directory'])/'sentinel'/sid if scene.get('observation_directory') else satellite.BASE/'sentinel'/sid
        if sha(folder/'indices.npz')!=scene['crop_sha256']['indices.npz'] or sha(folder/'item.json')!=scene['crop_sha256']['item.json']:
            raise ValueError('Changed source pixels or metadata')
        result.append(dict(scene=scene,folder=folder,item=read(folder/'item.json')))
    extension=json.loads(pointer_bytes) if pointer_bytes is not None else None
    catalogue_folder=satellite.DIRECTORY/extension['directory'] if extension else satellite.BASE
    catalogue=read(catalogue_folder/'catalogue.json')
    if not catalogue.get('pagination_completed'):raise ValueError('Incomplete observation catalogue')
    if extension:
        for page in catalogue['pages']:
            part=Path(page['path'])
            if part.is_absolute() or '..' in part.parts or part.parts[0]!='catalogue' or sha(catalogue_folder/part)!=page['sha256']:
                raise ValueError('Catalogue page integrity failed')
        if not catalogue['pages']:raise ValueError('No saved catalogue pages')
        last=read(catalogue_folder/catalogue['pages'][-1]['path'])
        if any(v.get('rel')=='next' for v in last.get('links',[])):
            raise ValueError('Catalogue pagination was not exhausted')
        source_ids={r['id'] for r in read(catalogue_folder/'satellite.json')['scenes']}
        if source_ids!={r['item']['id'] for r in catalogue['items']}:raise ValueError('Catalogue/source set mismatch')
    period=read(catalogue_folder/'protocol.json')['periods'][-1]
    coverage=dict(from_utc=period['start'],through_utc=period['end'],catalogue_complete=True,
                  archive_version=s['_scouting_version'],extension=extension,
                  frozen_base_sha256=sha(satellite.BASE/'summary.json'))
    if (pointer_path.read_bytes() if pointer_path.exists() else None)!=pointer_bytes:
        raise ValueError('Observation publication changed during snapshot; rerun')
    return p,s,result,coverage


def extract_observations(p,summary,sources,settings,deduplicate=True):
    """Same core statistics, grid, solar QC and UTC-day dedup as2024 features."""
    from pyproj import Transformer
    from rasterio.transform import from_origin
    from shapely import transform
    from shapely.geometry import shape
    from .farm_context_analysis import scenarios,masks,spectral_stats
    geo=read(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')
    if sha(ROOT/'data/farm-research/reconstruction/approximate-areas.geojson')!=p['contours_sha256']:
        raise ValueError('Changed geometry')
    x0,y0,x1,y1=p['bounds_utm'];tr=from_origin(x0,y1,20,20)
    dimensions=(int((y1-y0)/20),int((x1-x0)/20));projection=Transformer.from_crs(4326,32637,always_xy=True)
    grids={f['properties']['id']:masks(scenarios(transform(shape(f['geometry']),projection.transform,interleaved=False),
             f['properties']['working_band_m']),tr,dimensions) for f in geo['features']}
    observations=[];metadata={};locations={}
    for source in sources:
        scene=source['scene'];sid=scene['id'];item=source['item'];folder=source['folder']
        metadata[sid]=dict(id=sid,acquired_at_utc=scene['acquired_at_utc'],received_at_utc=scene['retrieved_at_utc'],
            baseline=scene['processing_baseline'],indices_sha256=scene['crop_sha256']['indices.npz'],
            item_sha256=scene['crop_sha256']['item.json'],directory=folder.relative_to(ROOT).as_posix())
        sun=item['properties'].get('view:sun_elevation')
        if sun is None or sun<settings['minimum_sun_elevation_deg']:continue
        locations[sid]=folder
        with np.load(folder/'indices.npz',allow_pickle=False) as archive:
            arrays={k:archive[k] for k in ('ndvi','ndmi','valid')}
        if any(a.shape!=dimensions for a in arrays.values()):raise ValueError('Wrong source grid')
        for fid,variants in grids.items():
            values={}
            for name,ix in variants.items():
                stats=[spectral_stats(arrays[key],arrays['valid'],ix,settings['core_minimum_cells'],settings['core_minimum_coverage']) for key in ('ndvi','ndmi')]
                if all(stat['usable'] for stat in stats):
                    values[name]=dict(value=[stat['value'] for stat in stats],coverage=stats[0]['valid_fraction'])
            if 'core' not in values:continue
            static=summary['features'][fid]['dsm'];terrain=[static['height']['base'],static['slope']['base']]
            if any(v is None for v in terrain):continue
            observations.append(dict(fid=fid,scene_id=sid,date=scene['acquired_at_utc'][:10],acquired_at_utc=scene['acquired_at_utc'],
                received_at_utc=scene['retrieved_at_utc'],baseline=scene['processing_baseline'],values=values,terrain=terrain))
    daily={}
    for row in observations:
        key=(row['fid'],row['date']);order=(row['values']['core']['coverage'],row['acquired_at_utc'],row['scene_id'])
        old=daily.get(key)
        if old is None or order>(old['values']['core']['coverage'],old['acquired_at_utc'],old['scene_id']):daily[key]=row
    @lru_cache(maxsize=6)
    def pixels(sid,fid):
        with np.load(locations[sid]/'indices.npz',allow_pickle=False) as archive:
            a={k:archive[k].ravel() for k in ('ndvi','ndmi','valid')}
        return {name:(np.column_stack([a['ndvi'][ix],a['ndmi'][ix]]),a['valid'][ix]) for name,ix in grids[fid].items()}
    return sorted(daily.values() if deduplicate else observations,key=lambda r:(r['date'],r['fid'])),metadata,pixels


def issue(directory=DIRECTORY):
    directory=Path(directory);settings,protocol_sha=configuration(directory)
    cutoff=now();p,summary,sources,coverage=snapshot(cutoff)
    if cutoff<instant(settings['registered_at_utc']):raise ValueError('Clock precedes model registration')
    if p['contours_sha256']!=settings['geometry_sha256'] or p['bounds_utm']!=settings['bounds_utm']:
        raise ValueError('Different study geometry/grid; register a separate study')
    observations,source_metadata,pixels=extract_observations(p,summary,sources,settings)
    # Inference pool must not depend on any future target or target availability.
    possible=base.issue_pool(observations)
    fresh=[r for r in possible if 0<=(cutoff-instant(source_metadata[r['issue_scene']]['acquired_at_utc'])).total_seconds()/3600<=settings['maximum_input_age_hours']]
    if not fresh:return dict(status='insufficient_input_data',reason='No recent clear core with same-baseline2-21day prior; no fabricated release')
    anchor=max(r['issue'] for r in fresh);path=directory/'releases'/(anchor+'.json')
    if path.exists():
        envelope=checked(path)
        if envelope['payload']['protocol_sha256']!=protocol_sha:raise ValueError('Existing release uses another study')
        return dict(status='already_issued',release=envelope)
    start=datetime.combine(date_value(anchor)+timedelta(days=settings['horizon_days'][0]),datetime.min.time(),UTC)
    end=datetime.combine(date_value(anchor)+timedelta(days=settings['horizon_days'][1]+1),datetime.min.time(),UTC)
    if start<=cutoff:raise ValueError('Target window is not wholly future; no backdated forecast')
    pool=[r for r in fresh if r['issue']==anchor];history=defaultdict(list)
    lookup={(r['fid'],r['date']):r for r in observations}
    for row in observations:history[row['fid']].append(row)
    for row in pool:
        current=pixels(row['issue_scene'],row['fid']);previous=pixels(row['previous_scene'],row['fid'])
        row['triage_features'],row['quality']=base.input_vector(row,history[row['fid']],current,previous,lookup[(row['fid'],row['issue'])]['values']['core']['coverage'])
        row.update(y=None,target=None,target_scene=None,spatial_reserve=row['fid'] in base.HOLDOUT)
    scores=dict(recent_drop=base.rule_score(pool,'recent_drop'))
    for method,m in settings['models'].items():
        model=joblib.load(directory/'frozen'/m['file'])
        scores[method]=base.score(model,pool)
    baseline=repair.choose(pool,scores,settings['baseline'],settings['ranking_parameters'])
    candidate,trace=repair.choose(pool,scores,settings['candidate'],settings['ranking_parameters'],True)
    if baseline.sum()!=candidate.sum() or baseline.sum()!=min(settings['budget_k'],len(pool)):
        raise ValueError('Unequal comparison budgets')
    issued=now()
    if issued>=start:raise ValueError('Target window began while calculating; release rejected')
    # Save the actual history used by historical_vector, not only the last pair.
    used_scenes={r['scene_id'] for r in observations if any(r['fid']==v['fid'] and r['baseline']==v['baseline'] and
        0<=(date_value(anchor)-date_value(r['date'])).days<=30 for v in pool)}
    used_scenes.update(v['previous_scene'] for v in pool)
    payload=dict(schema_version=1,kind='prospective_satellite_comparison',anchor_acquisition_date=anchor,
        issued_at_utc=issued.isoformat(),feature_cutoff_utc=cutoff.isoformat(),protocol_sha256=protocol_sha,
        target_window=dict(start_utc=start.isoformat(),end_exclusive_utc=end.isoformat(),
            finalisation_not_before_utc=(end+timedelta(hours=settings['target_grace_hours'])).isoformat(),anchor='acquisition UTC date'),
        archive=coverage,source_scenes=[source_metadata[sid] for sid in sorted(used_scenes)],
        candidates=pool,scores={n:s.tolist() for n,s in scores.items()},
        selected=dict(baseline=[r['fid'] for r,c in zip(pool,baseline) if c],candidate=[r['fid'] for r,c in zip(pool,candidate) if c]),
        slots_each=int(baseline.sum()),excluded_fragments=[fid for fid in sorted(summary['features']) if fid not in {r['fid'] for r in pool}],
        selection_trace=trace,meaning='Future satellite index event, NOT useful visit/disease probability',
        outcomes_known_at_issue=False,field_visits_created=0)
    return dict(status='issued',release=immutable(path,payload))


def date_value(value):return datetime.fromisoformat(value[:10]).date()


def outcome_rows(release,observations,settings):
    """Labels attached only after issue. Missing scenes/quality are never negative."""
    deadline=instant(release['target_window']['finalisation_not_before_utc']);anchor=date_value(release['anchor_acquisition_date'])
    rows=[];ledger=[]
    for row in release['candidates']:
        possible=[r for r in observations if r['fid']==row['fid'] and r['baseline']==row['baseline'] and
                  settings['horizon_days'][0]<=(date_value(r['date'])-anchor).days<=settings['horizon_days'][1] and
                  instant(r['acquired_at_utc'])>instant(release['issued_at_utc'])]
        available=[r for r in possible if instant(r['received_at_utc'])<=deadline]
        # Select the best same-day scene ONLY among on-time receipts.
        # A late clearer pass must not hide an earlier usable on-time observation.
        daily={}
        for r in available:
            old=daily.get(r['date'])
            rank=(r['values']['core']['coverage'],r['acquired_at_utc'],r['scene_id'])
            if old is None or rank>(old['values']['core']['coverage'],old['acquired_at_utc'],old['scene_id']):daily[r['date']]=r
        available=list(daily.values())
        target=min(available,key=lambda r:(abs((date_value(r['date'])-anchor).days-settings['preferred_day']),r['date'])) if available else None
        rows.append(dict(row,y=target['values']['core']['value'] if target else None,
                         target=target['date'] if target else None,target_scene=target['scene_id'] if target else None))
        ledger.append(dict(fid=row['fid'],outcome=None if target is None else any(a-b<=settings['threshold'] for a,b in zip(target['values']['core']['value'],row['x'][:2])),
            target=target['date'] if target else None,target_scene=target['scene_id'] if target else None,
            reason='observed_satellite_target' if target else 'late_receipt_only' if possible else 'no_usable_same_baseline_target',
            received_at_utc=target['received_at_utc'] if target else None))
    return rows,ledger


def evaluate(directory=DIRECTORY):
    directory=Path(directory);settings,protocol_sha=configuration(directory);when=now()
    pending=[];results=[];material=None
    for path in sorted((directory/'releases').glob('*.json')):
        envelope=checked(path);release=envelope['payload'];anchor=release['anchor_acquisition_date']
        if release['protocol_sha256']!=protocol_sha:raise ValueError('Release uses another frozen protocol')
        target=directory/'evaluations'/(anchor+'.json')
        if target.exists():
            evaluation=checked(target)
            if evaluation['payload']['release_sha256']!=envelope['sha256']:raise ValueError('Changed evaluated release')
            results.append(evaluation);continue
        if when<instant(release['target_window']['finalisation_not_before_utc']):
            pending.append(dict(anchor=anchor,status='pending_target_window_and_receipt_grace'));continue
        if material is None:
            p,s,sources,coverage=snapshot(when)
            observations,metadata,_=extract_observations(p,s,sources,settings,deduplicate=False)
            material=(observations,metadata,coverage)
        observations,metadata,coverage=material
        if (not coverage['catalogue_complete'] or
            instant(coverage.get('from_utc',release['target_window']['start_utc']))>instant(release['target_window']['start_utc']) or
            instant(coverage['through_utc'])<instant(release['target_window']['end_exclusive_utc'])):
            pending.append(dict(anchor=anchor,status='pending_complete_target_catalogue'));continue
        rows,ledger=outcome_rows(release,observations,settings)
        masks={name:np.array([r['fid'] in ids for r in rows]) for name,ids in release['selected'].items()}
        if sum(masks['baseline'])!=sum(masks['candidate']):raise ValueError('Unequal frozen budgets')
        payload=dict(schema_version=1,evaluated_at_utc=when.isoformat(),release_sha256=envelope['sha256'],
            protocol_sha256=protocol_sha,anchor_acquisition_date=anchor,archive=coverage,
            source_scenes=[metadata[r['target_scene']] for r in rows if r['target_scene']],outcomes=ledger,
            rows=rows,selected=release['selected'],
            metrics={n:base.rank_metrics(rows,mask,settings) for n,mask in masks.items()},
            paired=base.paired_metrics(rows,masks['baseline'],masks['candidate']),
            meaning='Missing-outcome scenario bounds, NOT statistical confidence or agronomist usefulness',
            automatic_promotion=False,field_visits_created=0)
        results.append(immutable(target,payload))
    return dict(finalised=results,pending=pending)


def report(directory=DIRECTORY):
    directory=Path(directory);p,protocol_sha=configuration(directory)
    releases=[checked(path) for path in sorted((directory/'releases').glob('*.json'))]
    evaluations=[checked(path) for path in sorted((directory/'evaluations').glob('*.json'))]
    by_anchor={r['payload']['anchor_acquisition_date']:r for r in releases}
    if any(r['payload']['protocol_sha256']!=protocol_sha for r in releases):raise ValueError('Mixed study releases')
    all_rows=[];masks={n:[] for n in ('baseline','candidate')}
    for evaluation in evaluations:
        v=evaluation['payload'];original=by_anchor.get(v['anchor_acquisition_date'])
        if not original or v['release_sha256']!=original['sha256'] or v['protocol_sha256']!=protocol_sha:
            raise ValueError('Evaluation is not linked to its frozen release')
        all_rows.extend(v['rows'])
        for name in masks:masks[name].extend(r['fid'] in v['selected'][name] for r in v['rows'])
    metrics={n:base.rank_metrics(all_rows,np.array(mask,bool),p) for n,mask in masks.items()}
    paired=base.paired_metrics(all_rows,np.array(masks['baseline'],bool),np.array(masks['candidate'],bool)) if all_rows else None
    cohorts={}
    for reserved,label in [(False,'training_region'),(True,'spatial_reserve')]:
        yy=base.labels(all_rows)
        cohorts[label]={name:dict(slots=sum(c and bool(r.get('spatial_reserve'))==reserved for r,c in zip(all_rows,mask)),
            tp=sum(c and bool(r.get('spatial_reserve'))==reserved and y is True for r,c,y in zip(all_rows,mask,yy)),
            fp=sum(c and bool(r.get('spatial_reserve'))==reserved and y is False for r,c,y in zip(all_rows,mask,yy)),
            unknown=sum(c and bool(r.get('spatial_reserve'))==reserved and y is None for r,c,y in zip(all_rows,mask,yy)))
            for name,mask in masks.items()}
    return dict(generated_at_utc=now().isoformat(),protocol_sha256=protocol_sha,
        releases=len(releases),finalised=len(evaluations),pending=len(releases)-len(evaluations),
        slots_each_issued=sum(v['payload']['slots_each'] for v in releases),
        metrics=metrics,paired=paired,cohorts_descriptive_only=cohorts,
        available_quality_evidence=any(m['tp']+m['fp']>0 for m in metrics.values()),
        status='awaiting_future_outcomes' if not evaluations else 'prospective_satellite_results_not_agronomic_validation',
        automatically_promoted=False,selection_years=[2024],test_data_used_for_tuning=False,field_visits_created=0)
