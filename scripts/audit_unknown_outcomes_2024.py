"""Explain every conservative unknown2024 outcome; new evidence is evaluation-only."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from agro_checker import triage_outcomes as evidence,sector_refresh as transport
from scripts import experiment_consensus_ndmi_guard as study

base=study.base
D=ROOT/'data/farm-research/unknown-outcome-audit-2024'


def key(item):
    props=item['properties']
    return dict(id=item['id'],baseline=props.get('s2:processing_baseline'),sun=props.get('view:sun_elevation'),
        offset_applied=props.get('earthsearch:boa_offset_applied'),generated=props.get('s2:generation_time'),
        assets={name:{k:item['assets'][name].get(k) for k in ('href','proj:shape','proj:transform','raster:bands','file:checksum')} for name in ('scl','red','nir','swir16')})


def protocol():
    if (D/'protocol.json').exists():return study.read(D/'protocol.json')
    p=study.read(evidence.D/'protocol.json')
    inputs=[base.D/'meta-oof.json',base.D/'pool-2024.json',evidence.D/'catalogue.json',evidence.D/'satellite.json',
            evidence.D/'recovery-ledger.json',study.D/'protocol.json',study.D/'report.json']
    result=dict(created_at_utc=base.stamp(),selection_years=[2024],bounds_utm=p['bounds_utm'],bbox_wgs84=p['bbox_wgs84'],
        collection=p['collection'],periods=[dict(id='audit2024',start=p['start'],end=p['end'])],
        inputs={path.relative_to(ROOT).as_posix():study.digest(path) for path in inputs},
        policy='368 known targets unchanged.331 conservative unknowns individually explained. No threshold/mask/window/model/ranking change. Fresh catalogue2024 only; only new/changed relevant same-baseline sources may be read. No2025/2026 outcome access.',
        recovery='Evaluation only. New original T37TDK targets require unchanged core>=10/.8, PB, SCL/solar/reflectance QC. New adjacent-tile targets additionally require common valid core>=.8 and>=10 with contemporaneous original-tile source and median absolute NDVI/NDMI differences<=.05 (old geometric tolerance). Conflicting old3 remain unknown unless a new original-tile usable target independently resolves them.',
        guards='No interpolation, no5-10day widening, no cross-baseline mixing, no SCL relaxation, no proxy human labels. Recovered labels never enter currently frozen training/selection or prospective study.',
        model_refit_performed=False,production_promoted=False,human_visits_created=0)
    study.dump(D/'protocol.json',result)
    return result


def catalogue():
    p=protocol()
    if (D/'catalogue.json').exists():raise ValueError('Catalogue snapshot already saved')
    return transport.catalogue(p,D)


def relevant(row,item):
    return 6<=(base.day(item['properties']['datetime'])-base.day(row['issue'])).days<=8 and item['properties'].get('s2:processing_baseline')==row['baseline']


def candidates(rows):
    old={f['id']:f for f in study.read(evidence.D/'catalogue.json')['items']}
    fresh=study.read(D/'catalogue.json')['items']
    unknown=[r for r in rows if r['y'] is None]
    novel=[v['item'] for v in fresh if (v['item']['id'] not in old or key(v['item'])!=key(old[v['item']['id']]))
           and any(relevant(r,v['item']) for r in unknown)]
    return novel


def collect():
    p=protocol();rows,_=study.guard.dataset();novel=candidates(rows)
    results=[];old_D=evidence.D
    # Existing collector is reused in a separate process/directory, never changed on disk.
    evidence.D=D;evidence.grids.cache_clear()
    original_head=evidence.head;evidence.head=transport.object_head
    try:
        for item in novel:
            result=evidence.scene(item,{})
            results.append(result);print(result['id'],result['status'],flush=True)
    finally:
        evidence.D=old_D;evidence.head=original_head;evidence.grids.cache_clear()
    study.dump(D/'new-satellite.json',dict(novel_relevant_items=len(novel),scenes=results,counts=dict(Counter(v['status'] for v in results))))
    return results


def failure(q,eroded_fraction=None):
    total=q['total_cells'];counts={int(k):v for k,v in q['scl_counts'].items()}
    if total<10:return 'insufficient_geometric_core'
    raw=(counts.get(4,0)+counts.get(5,0))/total
    if raw<.8:
        blocked={'cloud_or_shadow_scl':sum(counts.get(k,0) for k in (3,8,9,10)),
                 'nodata_or_tile_edge':counts.get(0,0),
                 'water_or_snow_scl':sum(counts.get(k,0) for k in (6,11)),
                 'other_rejected_scl':sum(counts.get(k,0) for k in (1,2,7))}
        return max(blocked,key=blocked.get)
    if eroded_fraction is None:return 'guard_or_spectral_loss_not_separated'
    return 'cloud_edge_guard_loss' if eroded_fraction<.8 else 'spectral_or_band_nodata_loss'


def run():
    p=protocol()
    if (D/'report.json').exists():raise ValueError('Frozen audit: no evidence-driven reruns')
    for name,value in p['inputs'].items():
        if study.digest(ROOT/name)!=value:raise ValueError('Changed frozen audit source '+name)
    rows,records=study.guard.dataset();base.only2024(rows)
    old_sat=study.read(evidence.D/'satellite.json')['scenes'];old_cat=study.read(evidence.D/'catalogue.json')['items']
    previous_ledger={(r['fid'],r['issue']):r for r in study.read(evidence.D/'recovery-ledger.json')}
    grids=evidence.grids()[0];eroded_cache={}
    for scene in old_sat:
        hashes=scene.get('hashes',{})
        path=next((ROOT/k for k in hashes if Path(k).name=='indices.npz'),None)
        if path is not None and path.exists():
            if study.digest(path)!=hashes[path.relative_to(ROOT).as_posix()]:raise ValueError('Changed source indices')
            with np.load(path,allow_pickle=False) as a:scl=a['scl']
        else:
            path=next((ROOT/k for k in hashes if Path(k).name=='scl.tif'),None)
            if path is None or not path.exists():continue
            if study.digest(path)!=hashes[path.relative_to(ROOT).as_posix()]:raise ValueError('Changed source SCL')
            import rasterio
            with rasterio.open(path) as raster:scl=raster.read(1)
        land=evidence.land_support(scl,scl!=0).ravel()
        eroded_cache[scene['id']]={fid:float(land[g['core']].mean()) if len(g['core']) else 0 for fid,g in grids.items()}
    ledger=[]
    for row in rows:
        if row['y'] is not None:continue
        previous=previous_ledger[base.key(row)]
        scene_rows=[]
        for scene in old_sat:
            if not (6<=(base.day(scene['acquired_at_utc'])-base.day(row['issue'])).days<=8 and scene['baseline']==row['baseline']):continue
            q=scene.get('fields',{}).get(row['fid'])
            if q:
                total=q['total_cells'];classes=q['scl_counts']
                scene_rows.append(dict(id=scene['id'],at=scene['acquired_at_utc'],status=scene['status'],
                    valid_fraction=q['valid_fraction'],valid_cells=q['valid_cells'],total_cells=total,
                    raw_land_fraction=(classes.get('4',0)+classes.get('5',0))/total if total else 0,
                    cloud_shadow_fraction=sum(classes.get(str(k),0) for k in (3,8,9,10))/total if total else 0,
                    scl_counts=classes,eroded_land_fraction=eroded_cache.get(scene['id'],{}).get(row['fid']),
                    failure=failure(q,eroded_cache.get(scene['id'],{}).get(row['fid']))))
        why=previous['reason']
        if why=='additional_scene_restored':
            why='conflicting_adjacent_scl' if row['fid'] in ('R12','R13') else 'adjacent_target_not_independently_confirmed'
        elif why=='insufficient_clear_core':
            best=max(scene_rows,key=lambda v:(v['valid_fraction'],v['at'],v['id']),default=None)
            why=best['failure'] if best else 'missing_target_extraction'
        fresh=[v['item'] for v in study.read(D/'catalogue.json')['items'] if relevant(row,v['item'])]
        ledger.append(dict(fid=row['fid'],issue=row['issue'],baseline=row['baseline'],original_reason=previous['reason'],
            reason=why,nearest_target_window=[(base.day(row['issue'])+__import__('datetime').timedelta(days=d)).isoformat() for d in (6,8)],
            best_valid_fraction=max((v['valid_fraction'] for v in scene_rows),default=None),
            scene_evidence=scene_rows,current_same_baseline_items=[v['id'] for v in fresh],
            original_same_baseline_items=[v['id'] for v in old_cat if relevant(row,v)]))
    additions=study.read(D/'new-satellite.json')['scenes']
    # Admit only newly observed ORIGINAL-tile targets. Adjacent-only sources remain
    # proposals until independent original coverage passes; never accept a conflicting mask.
    admitted=[v for v in additions if v['status']=='ready' and '_T37TDK_' in v['id']]
    updated=deepcopy(rows);recoveries=[]
    for row in updated:
        if row['y'] is not None:continue
        target=evidence.choose_target(row,admitted)
        if target:
            row.update(y=target['y'],target=target['target'],target_scene=target['target_scene'])
            recoveries.append(dict(fid=row['fid'],issue=row['issue'],**target))
    selection=study.read(study.D/'report.json')['selected_repair'];params=study.read(study.D/'protocol.json')
    choices={name:study.choose(rows,records,v,params) for name,v in [('baseline',params['variants'][0]),('candidate',selection)]}
    report=dict(created_at_utc=base.stamp(),protocol_sha256=study.digest(D/'protocol.json'),
        catalogue_sha256=study.digest(D/'catalogue.json'),input_count=len(rows),known_before=sum(r['y'] is not None for r in rows),
        unknown_before=len(ledger),known_after=sum(r['y'] is not None for r in updated),unknown_after=sum(r['y'] is None for r in updated),
        reasons=dict(Counter(r['reason'] for r in ledger)),
        old_catalogue_items=len(old_cat),new_catalogue_items=len(study.read(D/'catalogue.json')['items']),
        novel_relevant_items=len(candidates(rows)),new_source_statuses=study.read(D/'new-satellite.json')['counts'],
        recovered=recoveries,adjacent_proposals_not_admitted=[v['id'] for v in additions if '_T37TDL_' in v['id']],
        queue_metrics={stage:{name:base.rank_metrics(rr,mask,params) for name,mask in choices.items()} for stage,rr in [('before',rows),('after',updated)]},
        model_fit_performed=False,rankings_changed=False,selection_years=[2024],test_years_evaluated=[],human_visits_created=0,
        prospective_study_changed=False,notes='Diagnostic grouping uses best possible target coverage, never observed index size. It is not a diagnosis or cloud-mask ground truth.')
    study.dump(D/'unknown-ledger.json',ledger);study.dump(D/'evaluation-only-rows.json',updated);study.dump(D/'report.json',report)
    print({k:report[k] for k in ('unknown_before','unknown_after','reasons','novel_relevant_items','new_source_statuses')})
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('protocol','catalogue','collect','run'))
    dict(protocol=protocol,catalogue=catalogue,collect=collect,run=run)[parser.parse_args().action]()
