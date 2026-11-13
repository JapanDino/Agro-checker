"""Restore ignored RTC crops from public sources, accepting only exact recorded hashes."""
import argparse
from pathlib import Path
import shutil
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro_checker import radar_data as d


def restore(scene=None,verify_only=False):
    original=d.D;manifest=d.read(original/'satellite.json')
    rows=[v for v in manifest['scenes'] if v['status']=='ready' and (scene is None or v['id']==scene)]
    if scene is not None and len(rows)!=1:raise ValueError('Unknown scene')
    missing=[]
    for row in rows:
        p=original/'scenes'/row['id']/'radar.npz'
        if p.exists():
            if d.sha(p)!=row['crop_sha256']:raise ValueError('Changed existing crop: '+row['id'])
        else:missing.append(row)
    if verify_only and missing:raise ValueError(f'{len(missing)} raw crops missing; run restore without --verify-only')
    if not missing:return dict(verified=len(rows),restored=0)
    stage=d.ROOT/'tmp'/('radar-restore-'+uuid4().hex);stage.mkdir(parents=True)
    shutil.copyfile(original/'protocol.json',stage/'protocol.json')
    items=[d.read(original/'scenes'/r['id']/'item.json') for r in missing];token=d.signed_assets(items)
    try:
        d.D=stage
        for row,item in zip(missing,items):
            value=d.collect_scene(item,token);cache=stage/'scenes'/row['id']/'radar.npz'
            if value['status']!='ready' or d.sha(cache)!=row['crop_sha256']:
                raise ValueError('Remote data/runtime no longer reproduce recorded crop; nothing admitted: '+row['id'])
            target=original/'scenes'/row['id']/'radar.npz'
            if target.exists():raise ValueError('Concurrent cache restoration; stopped')
            shutil.copyfile(cache,target);print('Restored verified crop',row['id'],flush=True)
    finally:d.D=original
    return dict(verified=len(rows),restored=len(missing))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scene');parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args();print(restore(args.scene,args.verify_only))
