"""Whitelist a reproducible, empty-journal product bundle. No network or training."""
import ast
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker import farm_scouting as scout, farm_workspace as workspace

OUT=ROOT/'outputs/agropulse-review-2026-11-06'
WHEELS=ROOT/'tmp/product-sprint-2026-10-25/wheels'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def modules(names):
    done=set();pending=list(names)
    while pending:
        name=pending.pop()
        if name in done:continue
        p=ROOT/'agro_checker'/(name+'.py')
        if not p.is_file():continue
        done.add(name)
        for node in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
            if isinstance(node,ast.ImportFrom) and node.level==1:
                if node.module:pending.append(node.module.split('.')[0])
                else:pending.extend(n.name for n in node.names)
    return done


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',default='2026-11-06')
    parser.add_argument('--screens',type=Path,default=ROOT/'outputs/agropulse-design-2026-11-06')
    args=parser.parse_args()
    from datetime import date
    date.fromisoformat(args.date)
    global OUT
    OUT=ROOT/'outputs'/('agropulse-review-'+args.date)
    OUT.mkdir(parents=True,exist_ok=True)
    # An existing output is never recursively deleted. Refuse unknown leftovers.
    copied=set()
    def copy(source,name=None):
        source=ROOT/source if isinstance(source,str) else source
        rel=name or source.relative_to(ROOT).as_posix();target=OUT/rel
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);copied.add(rel)
    for f in ['launch.py','START.cmd','README.txt','preview.html']:copy('commission/'+f,f)
    for p in args.screens.glob('*.jpg'):copy(p,'preview/'+p.name)
    if (ROOT/'commission/CHECKS.json').is_file():copy('commission/CHECKS.json','CHECKS.json')
    for m in modules(['scouting_server','farm_workspace','farm_scouting']):copy('agro_checker/'+m+'.py')
    copy('agro_checker/__init__.py')
    for p in (ROOT/'web/scouting').rglob('*'):
        if p.is_file() and p.suffix in {'.html','.js','.css','.jpg','.png','.txt','.json'}:copy(p)
    for name in ['protocol.json','summary.json','satellite.json']:copy('data/farm-research/area-context/'+name)
    copy('data/farm-research/reconstruction/approximate-areas.geojson')
    for p in (ROOT/'data/farm-research/scouting-workspace-2026-10-22').glob('era5-*.json'):copy(p)
    scenes=scout.archive()[3]
    for sid,s in scenes.items():
        if s['status']=='ready':
            p=ROOT/'data/farm-research/area-context/sentinel'/sid/'indices.npz'
            if sha(p)!=s['crop_sha256']['indices.npz']:raise ValueError('Source integrity failure: '+sid)
            copy(p)
    for f in ['R11-evidence.json','R11-boundary-scenarios.csv']:
        copy(ROOT/'outputs/defense-materials-2026-10-22/main'/f,'evidence/'+f)
    copy('tests/test_farm_workspace.py')
    dependencies={}
    if not list(WHEELS.glob('*.whl')):raise ValueError('Download Windows/Python3.13 wheels into '+str(WHEELS))
    for p in WHEELS.glob('*.whl'):
        copy(p,'wheels/'+p.name)
        with zipfile.ZipFile(p) as z:
            metadata=z.read(next(f for f in z.namelist() if f.endswith('.dist-info/METADATA'))).decode()
            name=next(s[6:] for s in metadata.splitlines() if s.startswith('Name: '))
            version=next(s[9:] for s in metadata.splitlines() if s.startswith('Version: '));dependencies[name]=version
    (OUT/'requirements.txt').write_text(''.join(f'{k}=={v}\n' for k,v in sorted(dependencies.items())),encoding='utf-8');copied.add('requirements.txt')
    evidence={p['id']:workspace.comparison(p['id']) for p in scout.archive()[0]['periods']}
    (OUT/'evidence/common-pairs.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');copied.add('evidence/common-pairs.json')
    actual={p.relative_to(OUT).as_posix() for p in OUT.rglob('*') if p.is_file()}
    unknown=actual-copied-{'manifest.json'}
    if unknown:raise ValueError('Unexpected files in delivery folder: '+str(sorted(unknown)[:10]))
    files={f:{'sha256':sha(OUT/f),'bytes':(OUT/f).stat().st_size} for f in sorted(copied)}
    manifest={'schema':1,'prepared_date':args.date,'product':'AgroPulse by Onlyfarms','platform':'Windows x64 / Python 3.13 x64',
              'python_runtime_included':False,'external_requests_during_archive_use':False,'dependencies':dependencies,
              'models_changed':False,'working_journal_included':False,'seeded_tasks':0,'seeded_visits':0,
              'archive_scenes':sum(s['status']=='ready' for s in scenes.values()),'files':files,
              'total_uncompressed_bytes':sum(v['bytes'] for v in files.values())}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with zipfile.ZipFile(OUT.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.rglob('*')):
            if p.is_file():z.write(p,arcname=OUT.name+'/'+p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(OUT.with_suffix('.zip')) as z:assert z.testzip() is None
    print(json.dumps({'files':len(files)+1,'archive_scenes':manifest['archive_scenes'],'zip_bytes':OUT.with_suffix('.zip').stat().st_size,'path':str(OUT.with_suffix('.zip'))},ensure_ascii=False))


if __name__=='__main__':main()
