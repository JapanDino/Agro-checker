"""Reproduce frozen predictions and audit existing sector data without training."""
import json
import hashlib
from pathlib import Path
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from agro_checker import field_index_experiment as ix
from agro_checker import field_decline_experiment as decline
from agro_checker import crop_season_experiment as season
from agro_checker.sector_model_audit import (point_quality, observation_opportunities,
    confusion, merged_event_windows, block_error_intervals)

OUT = ROOT/'outputs/01a12f84-40ae-7493-8aee-01bfa7e3ff6e/sector-audit-2026-10-20'
B = ROOT/'data/farm-research'
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p, obj): p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
def key(r): return r['fid'], r['issue'], r['target']
def equal(a,b, name):
    if not np.allclose(a,b,atol=1e-12,rtol=0, equal_nan=True):
        raise ValueError('Frozen replay mismatch: '+name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    protected = {str(p.relative_to(ROOT)):sha(p) for d in ('models','data/farm-research','data/field')
                 for p in (ROOT/d).rglob('*') if p.is_file()}
    dump(OUT/'preservation-before.json',protected)
    obs = read(B/'field-index-model/observations.json')
    frozen = read(B/'field-index-model/frozen-model.json')
    if sha(B/'field-index-model/training-protocol.json') != frozen['protocol_sha256']:
        raise ValueError('Changed point protocol')
    if sha(B/'field-index-model/spatial-split.json') != frozen['split_sha256']:
        raise ValueError('Changed spatial split')
    pairs = ix.make_pairs(obs['rows'])
    splits = ix.partition(pairs,obs['holdout_ids'])
    rr = splits['temporal2026']+splits['spatial2026']
    saved = [r for r in read(B/'field-index-model/predictions.json') if r['split'] in ('temporal2026','spatial2026')]
    point_map = {key(r):r for r in saved}
    if len(point_map) != len(saved) or set(point_map) != set(map(key,rr)):
        raise ValueError('Point cohort mismatch or duplicate')
    pred = ix.predict(frozen['selected_model'],rr)
    equal(pred, [point_map[key(r)]['prediction'] for r in rr], 'point')
    equal([r['y'] for r in rr],[point_map[key(r)]['actual'] for r in rr], 'point targets')
    equal([r['x'][:2] for r in rr],[point_map[key(r)]['persistence'] for r in rr], 'baseline')
    # Every saved point split is replayed. No fit_model, choose, run or evaluate call.
    for split, records in splits.items():
        cache = {key(r):r for r in read(B/'field-index-model/predictions.json') if r['split']==split}
        if set(cache) != set(map(key,records)) or len(cache)!=len(records):
            raise ValueError('Changed split '+split)
        equal(ix.predict(frozen['selected_model'], records),[cache[key(r)]['prediction'] for r in records],split)
    event_rows = read(B/'field-decline-model/data-2026.json')
    event_frozen = read(B/'field-decline-model/frozen-selection.json')
    protocol_path = ROOT/event_frozen['protocol_path']
    if sha(protocol_path)!=event_frozen['protocol_sha256']:
        raise ValueError('Changed event protocol')
    alarms,_,_ = decline.predict_events(event_rows,read(protocol_path),event_frozen)
    event_cache = [r for r in read(B/'field-decline-model/test-predictions.json') if r['split']=='all2026']
    em = {key(r):r for r in event_cache}
    if len(em)!=len(event_cache) or set(em)!=set(map(key,event_rows)) or set(em)!=set(point_map):
        raise ValueError('Event cohort mismatch')
    equal(alarms,[em[key(r)]['alarms'] for r in event_rows],'event detector')
    seasonal_rows = read(B/'crop-season-model/data-2026.json')
    sf = read(B/'crop-season-model/frozen-selection.json')
    sp = read(B/'crop-season-model/protocol.json')
    if sha(B/'crop-season-model/protocol.json')!=sf['protocol_sha256'] or sha(B/'crop-season-model/crop-evidence.json')!=sf['evidence_sha256']:
        raise ValueError('Changed seasonal source/protocol')
    sc = [r for r in read(B/'crop-season-model/test-predictions.json') if r['split']=='all2026']
    sm = {key(r):r for r in sc}
    if len(sm)!=len(sc) or set(sm)!=set(em) or set(sm)!=set(map(key,seasonal_rows)):
        raise ValueError('Seasonal cohort mismatch')
    seasonal_pred = ix.predict(frozen['selected_model'], seasonal_rows)-np.array([r['x'][:2] for r in seasonal_rows]) <= -.10
    from catboost import CatBoostClassifier
    for j,choice in enumerate(sf['choices']):
        if choice['enabled']:
            entry=sf['models'][j]; path=ROOT/entry['path']
            if sha(path)!=entry['sha256']: raise ValueError('Changed seasonal weights')
            model=CatBoostClassifier();model.load_model(str(path))
            candidate=next(c for c in sp['candidates'] if c['id']==choice['candidate'])
            seasonal_pred[:,j]=(season.score(model,seasonal_rows,candidate)>=choice['threshold']) & decline.gate(seasonal_rows,j,sp['gate'])
    equal(seasonal_pred,[sm[key(r)]['seasonal'] for r in seasonal_rows],'seasonal detector')
    context=read(B/'area-context/summary.json')
    ids=sorted(obs['core_cells'])
    per_area={}
    for fid in ids:
        mask=np.array([r['fid']==fid for r in rr])
        records=[r for r in rr if r['fid']==fid]
        per_area[fid]=dict(core_cells=obs['core_cells'][fid], confirmed_boundary=False, crop=None,
            quality=point_quality(records,pred[mask],frozen['interval']['radius']),
            availability=observation_opportunities([r for r in obs['rows'] if r['fid']==fid], '2026-07-21','2026-10-19'),
            base_context_availability=context['features'][fid]['availability']['recent2026'])
        last = per_area[fid]['availability']['last_clear']
        per_area[fid]['last_clear_age_calendar_days_on_2026_10_20'] = (ix.day('2026-10-20')-ix.day(last)).days if last else None
        per_area[fid]['product_scope'] = 'dated_spectral_monitoring_only' if obs['core_cells'][fid]>=10 else 'base_imagery_only_insufficient_model_core'
    event_detail={}
    for j,name in enumerate(('ndvi','ndmi')):
        y=[r['y'][j]-r['x'][j]<=-.10 for r in event_rows]
        original=[point_map[key(r)]['prediction'][j]-r['x'][j]<=-.10 for r in event_rows]
        # Do not count repeated copies from spatial/temporal summary cohorts twice.
        variants=dict(original=original, direct=[em[key(r)]['alarms'][j] for r in event_rows],
                      seasonal=[sm[key(r)]['seasonal'][j] for r in event_rows])
        robust_true=robust_false=ambiguous=unsupported=0
        for r in event_rows:
            a=r['audit']; delta=a.get('delta_scenarios',[])
            if not a.get('all_11_boundaries') or len(delta)!=11:
                unsupported+=1
            elif all(v[j]<=-.10 for v in delta): robust_true+=1
            elif all(v[j]>-.10 for v in delta): robust_false+=1
            else: ambiguous+=1
        event_detail[name]=dict(methods={n:confusion(y,v) for n,v in variants.items()},
            overlapping_event_window_components=merged_event_windows(event_rows,j),
            event_pairs_already_falling=sum(bool(v) and r['x'][2+j]<0 for r,v in zip(event_rows,y)),
            boundary_labels=dict(all_scenarios_decline=robust_true, all_scenarios_no_decline=robust_false,
                                 threshold_ambiguous=ambiguous, unsupported=unsupported),
            common_pixel_events=sum(a['audit']['common_target_coverage']>=.8 and a['audit']['common_delta'][j]<=-.10 for a in event_rows))
        eligible_true = [r for r,v in zip(event_rows,y) if v]
        event_detail[name]['core_event_boundary_support'] = dict(
            observed_core_event_pairs=len(eligible_true),
            all_scenarios_same_decline=sum(r['audit'].get('all_11_boundaries') and len(r['audit'].get('delta_scenarios',[]))==11
                and all(v[j]<=-.10 for v in r['audit']['delta_scenarios']) for r in eligible_true),
            common_support_still_decline=sum(r['audit']['common_target_coverage']>=.8
                and r['audit']['common_delta'] is not None and r['audit']['common_delta'][j]<=-.10 for r in eligible_true))
    model=frozen['selected_model']; x=np.array([r['x'][:len(model['mean'])] for r in rr])
    z=(x-np.array(model['mean']))/np.array(model['scale'])
    inputs={n:dict(test_min=float(x[:,j].min()),test_max=float(x[:,j].max()),
                  fit_mean=model['mean'][j],fit_scale=model['scale'][j],
                  pairs_abs_standardized_gt3=int((abs(z[:,j])>3).sum())) for j,n in enumerate(model['features'])}
    report=dict(prepared_on='2026-10-20',created_at_utc=datetime.now(timezone.utc).isoformat(),
        status='audit_only_no_model_promotion', tuning_performed=False, promoted=False,
        real_inspection_labels=0, confirmed_boundaries=0, fragment_count=len(ids),
        clear_core_fragments=sum(v>=10 for v in obs['core_cells'].values()),
        replay=dict(point_all_splits_exact=True,event_2026_exact=True,seasonal_2026_exact=True,
                    temporal_pairs=len(rr),unique_issue_dates=len({r['issue'] for r in rr}),
                    first_issue=min(r['issue'] for r in rr),last_target=max(r['target'] for r in rr)),
        point=point_quality(rr,pred,frozen['interval']['radius']), event_analysis=event_detail,
        block_sensitivity=[block_error_intervals(rr,pred,d) for d in (7,14)],
        per_area=per_area, feature_diagnostics=inputs,
        source_hashes={str(p.relative_to(ROOT)):sha(p) for p in (
            B/'field-index-model/frozen-model.json',B/'field-index-model/observations.json',
            B/'field-decline-model/frozen-selection.json',B/'crop-season-model/frozen-selection.json',
            B/'reconstruction/approximate-areas.geojson')},
        limits=['Only observed clear-sky 6–8-day targets scored; current inputs without a future clear target counted separately.',
                'Acquisition-date retrospective archive, not proven historical real-time availability.',
                'No disease, crop, BBCH, soil-moisture, damage or inspection-utility ground truth.',
                '2026 previously examined, not a pristine blind test. No threshold or model chosen by this audit.',
                'Individual fragments and nearby observations correlated; per-area wins are diagnostic, not a deployment selection.'])
    changed=[p for p,h in protected.items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
    report['preservation']=dict(protected_files=len(protected), changed=changed,
                                field_store_files=sum(p.is_file() for p in (ROOT/'data/field').rglob('*')))
    if changed: raise ValueError('Protected bytes changed')
    dump(OUT/'audit.json',report)
    print(json.dumps({k:report[k] for k in ('replay','point','event_analysis','block_sensitivity','preservation')},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
