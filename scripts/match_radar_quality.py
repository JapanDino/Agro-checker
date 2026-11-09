"""Read official RFI PDFs with pypdf; post-fit evidence only, no model imports."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re

from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/farm-research/sentinel1-2024'
Q=D/'quality'
RX=re.compile(r'S1A_([A-Z0-9_*]{5,18})_(\d{8}T\d{6})_(\d{8}T\d{6})_(\d{6})_([0-9A-F]{6})(?:_[0-9A-F]{4})?')


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')
def parsed(name):
    m=RX.search(name)
    if not m:return None
    prefix,a,b,orbit,take=m.groups()
    return dict(mode=prefix[:2],start=datetime.strptime(a,'%Y%m%dT%H%M%S'),end=datetime.strptime(b,'%Y%m%dT%H%M%S'),orbit=orbit,take=take)


def relation(scene,name):
    a,b=parsed(scene),parsed(name)
    if a is None or b is None:return None
    if any(a[k]!=b[k] for k in ('mode','orbit','take')):return None
    if name.startswith(scene.removesuffix('_rtc')+'_'):return 'same_grd_acquisition_template'
    overlap=(min(a['end'],b['end'])-max(a['start'],b['start'])).total_seconds()
    if overlap>=min(10.,.5*min((a['end']-a['start']).total_seconds(),(b['end']-b['start']).total_seconds())):
        return 'same_datatake_overlapping_acquisition'
    return None


def main():
    output=Q/'scene-risk.json'
    if output.exists():raise ValueError('Frozen post-fit source audit; no rerun')
    manifest=read(Q/'rfi-reports.json');files=[ROOT/p['path'] for r in manifest['reports'] for p in r['pdfs']]
    previous=Q/'matching-protocol.json'
    if previous.exists():
        if previous.resolve().parent!=Q.resolve():raise ValueError('Unexpected quality protocol location')
        attempt=1
        while (Q/f'matching-protocol-parser-attempt-{attempt}.json').exists():attempt+=1
        previous.rename(Q/f'matching-protocol-parser-attempt-{attempt}.json')
    save(Q/'matching-protocol.json',dict(code_sha256=sha(Path(__file__)),input_pdf_sha256={p.relative_to(ROOT).as_posix():sha(p) for p in files},
        parser_note='Initial parsers stopped on May/January wildcard product templates and title variants; retained protocols, accepted those PDF syntax variants before matching. No model refit or outcome selection.',
        already_frozen_model_report_sha256=sha(ROOT/'data/farm-research/radar-priority-2024/report.json'),
        rule='Parse product lists only. Match original GRD acquisition template or same platform/mode/absolute orbit/datatake and>=min(10s,50%shorter) time overlap. Flag potential source-product risk, not proven effect on farm pixels. Nonlisted=UNKNOWN, never clean. Post-fit audit; no scores/labels/masks/pool changes.'))
    products=[];documents=[]
    for p in files:
        reader=PdfReader(p);started=False;count=0
        for page,content in enumerate(reader.pages,1):
            text=content.extract_text()
            if re.search(r'List of (?:impacted )?products',text):started=True
            if not started:continue
            for name in sorted(set(m.group(0) for m in RX.finditer(text))):
                products.append(dict(name=name,pdf=p.relative_to(ROOT).as_posix(),page=page));count+=1
        if not count:raise ValueError('Official product list not extracted: '+p.name)
        documents.append(dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p),pages=len(reader.pages),product_patterns=count))
    scenes=[]
    for source in read(D/'satellite.json')['scenes']:
        matches=[]
        for v in products:
            why=relation(source['id'],v['name'])
            if why:matches.append(dict(**v,relation=why))
        scenes.append(dict(id=source['id'],at=source['at'],status='potential_source_rfi' if matches else 'not_listed_clearance_unknown',
            sector_pixels_proven_affected=False,matches=matches))
    affected={s['id'] for s in scenes if s['matches']}
    audit=read(ROOT/'data/farm-research/radar-priority-2024/feature-audit.json')['validation']
    uses=[dict(fid=v['fid'],issue=v['issue'],current=v['current'],previous=v['previous']) for v in audit
          if v['current'] in affected or v['previous'] in affected]
    result=dict(documents=documents,scenes=scenes,scene_count=len(scenes),potential_source_rfi_count=len(affected),
        validation_pairs_using_potential_rfi_source=len(uses),validation_uses=uses,
        source_model_report_changed=False,source_labels_changed=False,
        interpretation='Official quick-look product lists are non-exhaustive and degradation can affect only part of a product. Matching acquisition does not establish degradation inside our sector. Nonlisted scenes are not certified clean.')
    save(output,result);print('RFI source risk:',len(affected),'of',len(scenes),'scenes;',len(uses),'of',len(audit),'validation feature pairs')


if __name__=='__main__':main()
