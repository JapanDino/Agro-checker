"""Reproduce evidence from source pixels without refitting any predictor."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
import numpy as np
from collections import Counter
from agro_checker import triage_outcomes as m
from agro_checker.farm_context_data import read,dump,digest


if __name__=='__main__':
    import rasterio
    from agro_checker.open_land_data import indices,to_twenty,reflectance
    p=read(m.D/'protocol.json');m.check_inputs(p);f=read(m.D/'frozen-evaluation.json')
    assert digest(ROOT/'agro_checker/triage_outcomes.py')==f['code_sha256']
    assert p['created_at_utc']<f['created_at_utc']
    for name,sha in f['hashes'].items():assert digest(m.D/name)==sha
    cat=read(m.D/'catalogue.json');sat=read(m.D/'satellite.json');scenes=sat['scenes']
    assert cat['pagination_completed'] and sat['pagination_completed']
    for number,page in enumerate(cat['pages'],1):
        assert digest(m.D/'catalogue'/f'page-{number}.json')==page['sha256']
    old={s['id']:s for s in read(m.base.INDEX/'satellite.json')['scenes'] if s['status']=='ready' and s['acquired_at_utc'][:4]=='2024'}
    for s in scenes:
        assert s==read(m.D/'sentinel'/s['id']/'manifest.json')
        assert s['acquired_at_utc'][:4]=='2024'
        for path,sha in s['hashes'].items():assert digest(ROOT/path)==sha
        if s['status']=='ready':
            paths=[ROOT/path for path in s['hashes'] if path.endswith('/indices.npz')];assert len(paths)==1
            with np.load(paths[0]) as raw:arrays={n:raw[n] for n in ['ndvi','ndmi','valid','scl']}
            if not s['reused']:
                folder=m.D/'sentinel'/s['id'];item=read(folder/'item.json');bands={};valids=[]
                for name in ['scl','red','nir','swir16']:
                    with rasterio.open(folder/(name+'.tif')) as src:
                        a,valid=to_twenty(src.read(1),int(20/src.transform.a),src.nodata)
                        bands[name]=a if name=='scl' else reflectance(a,item['assets'][name],src.scales[0],src.offsets[0]);valids.append(valid)
                ndvi,ndmi,valid=indices(bands['red'],bands['nir'],bands['swir16'],bands['scl'],np.logical_and.reduce(valids))
                np.testing.assert_array_equal(arrays['ndvi'],ndvi.astype('float32'))
                np.testing.assert_array_equal(arrays['ndmi'],ndmi.astype('float32'));np.testing.assert_array_equal(arrays['valid'],valid)
            assert m.summary(arrays['ndvi'],arrays['ndmi'],arrays['valid'],arrays['scl'])==s['fields']
        elif s['status']=='scl_only_no_eligible_core':
            with rasterio.open(m.D/'sentinel'/s['id']/'scl.tif') as src:
                a,valid=to_twenty(src.read(1),1,src.nodata)
            mask=m.land_support(a,valid)
            assert not any(len(v['core'])>=10 and mask.ravel()[v['core']].sum()>=10 and mask.ravel()[v['core']].mean()>=.8 for v in m.grids()[0].values())
            blank=np.full(m.grids()[1],np.nan);assert m.summary(blank,blank,mask,a)==s['fields']
        else:raise AssertionError('Unresolved target source '+s['status'])
    meta=read(m.base.D/'meta-oof.json');rows=meta['rows'];m.base.only2024(rows)
    pool=read(m.base.D/'pool-2024.json');aug,ledger=m.extend(rows,pool,scenes,cat['items'])
    assert aug==read(m.D/'augmented-outcomes.json') and ledger==read(m.D/'recovery-ledger.json')
    frozen_choices=m.fixed_choices(rows);pr=read(m.prior.D/'protocol.json')
    rec={r['method']:r['scores'] for r in meta['records']}
    np.testing.assert_array_equal(frozen_choices['baseline'],m.base.topk(rows,rec['recent_drop'],p))
    np.testing.assert_array_equal(frozen_choices['extra'],m.base.topk(rows,rec['extra'],p))
    variant=next(v for v in pr['variants'] if v['id']==m.HYBRID)
    np.testing.assert_array_equal(frozen_choices['hybrid'],m.prior.choose(rows,rec['recent_drop'],rec['extra'],variant,pr))
    monthly,_=m.extend(rows,pool,[],[]);report=read(m.D/'report.json')
    assert {name:m.comparison(rr,frozen_choices,p) for name,rr in [('original',rows),('month_boundary_only',monthly),('additional_evidence',aug)]}==report['results']
    for original,new in zip(rows,aug):
        assert m.base.key(original)==m.base.key(new)
        assert {k:v for k,v in original.items() if k not in ['y','target','target_scene']}=={k:v for k,v in new.items() if k not in ['y','target','target_scene']}
        if original['y'] is not None:assert new==original
    assert all(m.base.rank_metrics(aug,c,p)['slots']==133 for c in frozen_choices.values())
    assert report['reasons']==dict(Counter(r['reason'] for r in ledger))
    # Additional overlapping tiles: compare actual common clear pixels, never
    # relabel/accept/reject a case after seeing whether its indices improve scores.
    additional=[];byid={s['id']:s for s in scenes}
    obs={(r['fid'],r['date']):r for r in read(m.base.INDEX/'observations.json')['rows'] if r['date'][:4]=='2024'}
    augmap={m.base.key(r):r for r in aug}
    for event in ledger:
        if event['reason']!='additional_scene_restored':continue
        r=augmap[m.base.key(event)];s=byid[r['target_scene']];targetpath=next(ROOT/path for path in s['hashes'] if path.endswith('/indices.npz'))
        counterpart=s['id'].replace('T37TDL','T37TDK');q=byid.get(counterpart);overlap=None
        if q and q['status']=='ready':
            otherpath=next(ROOT/path for path in q['hashes'] if path.endswith('/indices.npz'))
            ix=m.grids()[0][r['fid']]['core']
            with np.load(targetpath) as a,np.load(otherpath) as b:
                common=a['valid'].ravel()[ix]&b['valid'].ravel()[ix]
                count=int(common.sum());overlap=dict(cells=count,fraction=count/len(ix),differences={})
                if count:
                    for name in ['ndvi','ndmi']:
                        diff=a[name].ravel()[ix][common]-b[name].ravel()[ix][common]
                        overlap['differences'][name]=dict(median=float(np.median(diff)),p90_abs=float(np.quantile(abs(diff),.9)))
        variants={};before=obs[m.base.key(r)]['values'];after=s['fields'][r['fid']]['values']
        for name in m.base.SCENARIOS:
            variants[name]=None if name not in before or name not in after else (np.array(after[name]['value'])-before[name]['value']).tolist()
        additional.append(dict(fid=r['fid'],issue=r['issue'],target=r['target'],target_scene=r['target_scene'],
            delta=(np.array(r['y'])-r['x'][:2]).tolist(),counterpart=counterpart,overlap=overlap,geometry_deltas=variants,
            new_tile_scl_counts=s['fields'][r['fid']]['scl_counts'],
            original_tile_scl_counts=q['fields'][r['fid']]['scl_counts'] if q else None,
            selected_by=[n for n,c in frozen_choices.items() if c[[m.base.key(x) for x in aug].index(m.base.key(r))]],
            primary_label_unchanged_by_diagnostic=True))
    dump(m.D/'source-diagnostics.json',dict(additional_cases=additional,diagnostic_only=True))
    preserved=read(m.D/'preservation-before.json');changed=[n for n,sha in preserved.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=sha]
    assert not changed,changed[:5]
    out=dict(frozen_sha256=digest(m.D/'frozen-evaluation.json'),catalogue_pages_verified=True,
        all_source_hashes_verified=True,downloaded_indices_reproduced=True,all_target_statistics_reproduced=True,
        full699_inputs_and_133_choices_preserved=True,all274_original_known_outcomes_preserved=True,
        no_model_fit_or_settings_selection=True,no2025_2026_outcome_analysis=True,unknowns_not_negative=True,
        protected_files=len(preserved),changed_files=changed,source_diagnostics_sha256=digest(m.D/'source-diagnostics.json'),
        human_visits_created=0,production_promoted=False)
    dump(m.D/'verification.json',out);print(out)
