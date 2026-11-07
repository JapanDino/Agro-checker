"""Separate expanded acquisition-date archive for the field-index experiment.

Previously frozen context bytes are referenced read-only, never overwritten.
"""
from pathlib import Path
from datetime import datetime,timezone
import sys,json,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agro_checker import farm_context_data as source

D=ROOT/'data/farm-research/field-index-model'
OLD=ROOT/'data/farm-research/area-context'
D.mkdir(parents=True,exist_ok=True)
protocol_path=D/'protocol.json'
if not protocol_path.exists():
    original=source.read(OLD/'protocol.json')
    p={**original,'created_at_utc':datetime.now(timezone.utc).isoformat(),'periods':[
        dict(id='archive2023',label='Сезон 2023',start='2023-03-01T00:00:00Z',end='2023-10-31T23:59:59Z'),
        dict(id='archive2024',label='Сезон 2024',start='2024-03-01T00:00:00Z',end='2024-10-31T23:59:59Z')],
        'experiment':dict(task='expected core NDVI/NDMI 7 days after an observation; retrospective proxy for inspection support, NOT disease labels',
            horizon_days=7,target_window_days=[6,8],minimum_core_cells=10,coverage_min=.8,previous_lag_days=[2,21],
            daily_selection='maximum clear fraction in fixed core; tie latest acquisition; issue at end of acquisition UTC day',
            feature_policy='only current/past clear core indices, observed slopes/lag, day-of-year; optional DSM elevation/slope, no field id or future cloud/acquisition lead features',
            spatial_holdout='five southernmost fragment representative-point latitudes, frozen before training; never fit/tune on their labels',
            initial_fit='2023, remaining supported fragments',selection='2024 issue and target before 2024-07-01, remaining supported fragments',
            final_fit='2023 plus selection2024, remaining supported fragments',calibration='2024 issue >=2024-07-01, remaining supported fragments',
            temporal_test='2026 existing snapshot, no fitting/selection',test_not_blind='2026 index history was already visually/descriptively examined; evaluation is held out from fitting/selection, not an untouched external study',
            candidates=['persistence','linear_trend','temporal_ridge_1','temporal_ridge_10','temporal_ridge_100','terrain_ridge_1','terrain_ridge_10','terrain_ridge_100'],
            selection_metric='equal weight of NDVI/NDMI, equal total weight per issue calendar day; tie prefer simpler in listed order',
            interval='per-output 90% nominal split bound from maximum absolute error across fit-fragments on each calibration issue day; no local/exchangeability guarantee',
            decline_proxy=-.10,production_policy='research only regardless of holdout result; original weather detector and field store unchanged',
            historical_live_backtest=False,historical_availability='C1 reprocessing availability at original 2023/2024 issue date is not proven; this is an acquisition-date retrospective experiment'),
       'used_by_model':False,'policy':'Expanded immutable research snapshot for a separately frozen experiment; not operational forecasts or confirmed field/crop labels.'}
    source.dump(protocol_path,p)
else:
    p=source.read(protocol_path)
    if p['experiment']['candidates']!=['persistence','linear_trend','temporal_ridge_1','temporal_ridge_10','temporal_ridge_100','terrain_ridge_1','terrain_ridge_10','terrain_ridge_100']:raise ValueError('Protocol changed')

before=ROOT/'tmp/field-index-model-before.json'
if not before.exists():
    paths=[q for folder in ['data/field','models','data/working-zone-01/open-land','data/working-zone-01/sentinel-updates','data/farm-research/area-context','data/farm-research/reconstruction'] for q in (ROOT/folder).rglob('*') if q.is_file()]
    source.dump(before,{str(q.relative_to(ROOT)):source.digest(q) for q in paths})

source.D=D
original_scene=source.scene
def scene(entry):
    sid=entry['item']['id'];folder=OLD/'sentinel'/sid;manifest=folder/'manifest.json'
    item_sha=hashlib.sha256(json.dumps(entry['item'],sort_keys=True).encode()).hexdigest()
    if manifest.exists():
        cached=source.read(manifest)
        if cached['item_sha256']==item_sha:
            for name,h in cached['crop_sha256'].items():
                if source.digest(folder/name)!=h:raise ValueError('Frozen source crop changed')
            print('Reused frozen scene',sid,flush=True)
            return {**cached,'period':entry['period'],'local_folder':str(folder.relative_to(ROOT)),'read_only_source_reused':True}
    result=original_scene(entry)
    return {**result,'local_folder':str((D/'sentinel'/sid).relative_to(ROOT)),'read_only_source_reused':False}
source.scene=scene
result=source.satellite()
source.dump(D/'archive-collection.json',dict(status='complete',counts=result['counts'],protocol_sha256=source.digest(protocol_path),collector_sha256=source.digest(__file__)))
print('Expanded training archive complete',result['counts'],flush=True)
