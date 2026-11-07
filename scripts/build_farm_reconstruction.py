"""Research-only screenshot tracing. Never reads/writes the pilot FieldStore/model.

Run with optional GIS dependencies in tmp/georef-deps (see report).
Frozen registration is fitted to training spatial blocks; reserved blocks are validation.
"""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tmp/georef-deps'))
import cv2
import numpy as np
from PIL import Image
from pyproj import Transformer
from shapely.geometry import Polygon, mapping
from shapely import transform as transform_geometry
from shapely.ops import unary_union
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as Patch
import matplotlib.patheffects as pe

D = ROOT / 'data/farm-research/reconstruction'
W = ROOT / 'web/farm-reconstruction'
W.mkdir(parents=True, exist_ok=True)
source = ROOT / 'docs/assets/farm-reference/farm-map-user-provided.png'
reg = json.loads((D / 'registration.json').read_text())
ref = json.loads((D / 'satellite-reference.json').read_text())
M = np.array(reg['matrix_source_to_reference'])
Mi = cv2.invertAffineTransform(M)
ex = ref['extent']
sx = (ex['xmax']-ex['xmin'])/ref['width']
sy = (ex['ymax']-ex['ymin'])/ref['height']
to_utm = Transformer.from_crs(3857,32637,always_xy=True)
from_utm = Transformer.from_crs(32637,3857,always_xy=True)
to_geo = Transformer.from_crs(32637,4326,always_xy=True)
from_geo = Transformer.from_crs(4326,32637,always_xy=True)

def transform(fn,geom):
    return transform_geometry(geom,fn,interleaved=False)

def pixel_utm(points):
    uv = np.c_[np.asarray(points),np.ones(len(points))] @ M.T
    return np.array(to_utm.transform(ex['xmin']+uv[:,0]*sx,ex['ymax']-uv[:,1]*sy)).T

def utm_pixel(points):
    xy = np.array(from_utm.transform(*np.asarray(points).T)).T
    uv = np.c_[(xy[:,0]-ex['xmin'])/sx,(ex['ymax']-xy[:,1])/sy,np.ones(len(xy))]
    return uv @ Mi.T

def dump(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def build_trace_config():
    # Candidate contours reviewed against the original image. Cyan pins are not fields.
    candidates=json.loads((D/'segmentation-candidates.json').read_text())
    keep=[1,2,3,4,5,6,7,8,9,12,14,15,16,18,19,20,22]
    items=[dict(candidate_id=c['id'],color=c['color'],pixels=c['polygon'],method='colour segmentation, visually reviewed') for c in candidates if c['id'] in keep]
    overrides={
        1:[(453,648),(450,637),(469,596),(482,596),(520,620),(546,612),(560,611),(565,606),(587,608),(623,639),(635,642),(657,654),(684,652),(713,682),(714,727),(670,727),(669,789),(624,789),(623,740),(544,740),(544,689),(482,689),(482,733),(453,733)],
    }
    for item in items:
        if item['candidate_id'] in overrides:
            item['pixels']=[[x*1244/995,y*1.25] for x,y in overrides[item['candidate_id']]]
            item['method']='manual visible edge trace; hidden edge bridged under icons'
        if item['candidate_id']==4:
            item['pixels']=[(500,1187),(548,1149),(594,1196),(512,1267),(550,1312),(535,1314),(513,1310),(493,1322),(478,1312),(496,1287)]
            item['method']='manual visible edge trace; hidden edge bridged under icon'
        if item['candidate_id']==8:
            item['pixels']=[(348,1596),(354,1562),(378,1529),(430,1499),(451,1500),(496,1555),(493,1556),(493,1566),(489,1572),(460,1679),(458,1684),(400,1639)]
            item['method']='manual visible edge trace; hidden edge bridged under icon'
        if item['candidate_id']==19:
            item['pixels']=[(512,1267),(593,1197),(635,1245),(550,1312)]
            item['method']='manual visible edge trace; hidden edge bridged under icon'
        if item['candidate_id']==22:
            item['pixels']=[(401,1645),(460,1684),(499,1718),(480,1738),(394,1660)]
            item['method']='manual visible red fragments joined under icon; topology uncertain'
    extra=[
      ('pale',[(452,489),(626,585),(625,637),(590,605),(567,605),(549,613),(529,621),(479,591),(508,538),(453,515)]),
      ('blue',[(482,715),(544,715),(544,740),(482,740)]),
      ('blue',[(482,740),(621,740),(621,792),(482,792)]),
      ('blue',[(482,795),(550,795),(550,830),(496,830),(496,858),(482,858)]),
      ('blue',[(551,795),(566,795),(566,858),(551,858)]),
      ('pink',[(568,795),(623,795),(623,830),(568,830)]),
      ('pink',[(624,790),(669,790),(669,851),(625,851)]),
      ('red',[(670,728),(799,728),(799,787),(778,787),(778,790),(670,790)]),
      ('red',[(670,791),(778,791),(778,850),(670,850)]),
    ]
    for color,pts in extra:
        items.append(dict(candidate_id=None,color=color,pixels=[[x*1244/995,y*1.25] for x,y in pts],method='manual trace of visible colour fragment'))
    # Small isolated blue patch on the canal was outside the first segmentation ROIs.
    items.append(dict(candidate_id=None,color='blue',pixels=[(639,1153),(674,1174),(666,1183),(650,1171)],method='manual trace of small visible blue patch'))
    items.sort(key=lambda a:(min(y for x,y in a['pixels']),min(x for x,y in a['pixels'])))
    for i,item in enumerate(items,1):item['id']=f'R{i:02}'
    dump(D/'pixel-traces.json',dict(source_size=[1244,2560],excluded_candidates={'10':'thin canal outline, not an area','11':'thin canal outline, not an area'},items=items))

if '--initialize-traces' in sys.argv:build_trace_config()
traces=json.loads((D/'pixel-traces.json').read_text())['items']
occlusions=[]
for x,y,rx,ry in [(546,607,24,29),(617,628,32,39),(688,680,22,31),(515,754,24,32),(437,1000,26,33),(375,1243,25,32),(338,1342,26,33)]:
    t=np.linspace(0,2*np.pi,49)
    occlusions.append(np.c_[(x+rx*np.cos(t))*1244/995,(y+ry*np.sin(t))*1.25])
occ_utm=[Polygon(pixel_utm(p)) for p in occlusions]
palette={'green':'#32d591','blue':'#9f9bff','pink':'#f58be2','red':'#ff6958','pale':'#e6e9f7'}
features=[];bands=[];viewer=[];polys=[]
for item in traces:
    p=Polygon(pixel_utm(item['pixels']))
    assert p.is_valid and p.area>0,item['id']
    polys.append(p)
    occluded=any(p.intersects(o) for o in occ_utm)
    radius=100 if occluded else 50
    props=dict(id=item['id'],source_color=item['color'],geometry_status='approximate_research',confirmed_boundary=False,farm_field_id=None,crop=None,trace_method=item['method'],icon_occluded=occluded,working_band_m=radius,band_interpretation='heuristic sensitivity scenario; not a confidence interval',traced_area_ha=round(p.area/10000,1))
    features.append(dict(type='Feature',properties=props,geometry=mapping(transform(to_geo.transform,p))))
    band=p.boundary.buffer(radius)
    bands.append(dict(type='Feature',properties=props,geometry=mapping(transform(to_geo.transform,band))))
    bparts=list(band.geoms) if band.geom_type=='MultiPolygon' else [band]
    bands_px=[]
    for b in bparts:bands_px.append([utm_pixel(b.exterior.coords).tolist()]+[utm_pixel(r.coords).tolist() for r in b.interiors])
    center=p.representative_point()
    lon,lat=to_geo.transform(center.x,center.y)
    viewer.append(dict(**props,fill=palette[item['color']],pixels=item['pixels'],bands=bands_px,label=utm_pixel([[center.x,center.y]])[0].tolist(),longitude=lon,latitude=lat))

dump(D/'approximate-areas.geojson',dict(type='FeatureCollection',name='Unconfirmed screenshot colour fragments',features=features))
dump(D/'uncertainty-bands.geojson',dict(type='FeatureCollection',name='Heuristic boundary sensitivity bands, NOT statistical confidence',features=bands))
reserved=[p for p in reg['pairs'] if p['reserved']]
src=np.array([p['source'] for p in reserved]);target=np.array([p['reference'] for p in reserved])
expected=pixel_utm(src)
actual=np.array(to_utm.transform(ex['xmin']+target[:,0]*sx,ex['ymax']-target[:,1]*sy)).T
errors=np.linalg.norm(expected-actual,axis=1)
union=unary_union(polys)
bbox=transform(to_geo.transform,union).bounds
summary=dict(fragment_count=len(features),colours={c:sum(f['properties']['source_color']==c for f in features) for c in palette},bounding_box_wgs84=list(bbox),reserved_count=len(reserved),training_count=reg['metrics']['train'],relative_alignment_median_m=float(np.median(errors)),relative_alignment_p95_m=float(np.quantile(errors,.95)),relative_alignment_max_m=float(max(errors)),ground_m_per_source_pixel=float(np.linalg.norm(pixel_utm([[650,1250],[651,1250]])[1]-pixel_utm([[650,1250],[651,1250]])[0])),working_bands_m=[50,100],absolute_accuracy='unknown',imagery_acquisition_date='unknown (mosaic)',source_capture_date='unknown; 2026 is the UI season, not a verified capture date',union_traced_area_ha=round(union.area/10000,1),area_interpretation='area of the traced colour fragments, NOT verified farm area',confirmed_boundary=False,used_by_model=False,attribution='Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community',request_url=ref['request_url'],source_hashes={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in [source,D/'satellite-reference.jpg',D/'satellite-reference.json',D/'world-imagery-service.json',D/'registration.json',D/'pixel-traces.json']})
dump(D/'summary.json',summary)
dump(W/'data.json',dict(summary=summary,features=viewer,occlusions=[p.tolist() for p in occlusions]))
src_image=np.array(Image.open(source).convert('RGB'))
ref_image=np.array(Image.open(D/'satellite-reference.jpg').convert('RGB'))
aligned=cv2.warpAffine(ref_image,Mi,(1244,2560),flags=cv2.INTER_LINEAR)
Image.fromarray(aligned).save(W/'satellite-aligned.jpg',quality=92)
Image.fromarray(src_image).save(W/'screenshot.jpg',quality=93)

fig,axes=plt.subplots(1,2,figsize=(14,15),layout='constrained')
for ax,im,title in zip(axes,[src_image,aligned],['Исходный скриншот · все цветовые фрагменты','Спутниковая подложка · приближённые контуры']):
    ax.imshow(im)
    for item in viewer:
        ax.add_patch(Patch(item['pixels'],facecolor=item['fill'],edgecolor='white',alpha=.29 if ax is axes[1] else .08,lw=.7))
        ax.plot(*np.array(item['pixels']+[item['pixels'][0]]).T,color=item['fill'],lw=.9)
        if ax is axes[1]:
            for rings in item['bands']:
                ax.plot(*np.array(rings[0]).T,color='#ffe090',alpha=.65,lw=.6,linestyle='--')
        x,y=item['label'];txt=ax.text(x,y,item['id'],fontsize=7,color='white',ha='center',va='center')
        txt.set_path_effects([pe.withStroke(linewidth=2,foreground='#172420')])
    for occ in occlusions:ax.add_patch(Patch(occ,facecolor='#a7b0bc',edgecolor='#eee',alpha=.32,lw=.5,hatch='///'))
    ax.set_xlim(285,1065);ax.set_ylim(1970,575);ax.set_title(title,fontsize=12);ax.set_axis_off()
    # Metric scale computed in UTM at map centre, rather than in Mercator metres.
    a=pixel_utm([[320,1890]])[0];b=utm_pixel([a+[1000,0]])[0]
    ax.plot([320,b[0]],[1890,b[1]],color='white',lw=3);ax.text(320,1881,'1 км',color='white',fontsize=9,path_effects=[pe.withStroke(linewidth=2,foreground='black')])
    ax.text(1020,620,'С ↑',color='white',fontsize=10,path_effects=[pe.withStroke(linewidth=2,foreground='black')])
fig.suptitle(f'{len(features)} цветовых фрагментов · границы не подтверждены',fontsize=18)
fig.supxlabel('Пунктир: рабочая полоса ±50/100 м, не 95% доверительный интервал. Серое: перекрытия значками.\nОтносительное совмещение: P95 ≈ '+str(round(summary['relative_alignment_p95_m']))+' м. Абсолютная точность и дата спутниковой съёмки неизвестны.\nSource: Esri, Vantor, Earthstar Geographics, and the GIS User Community',fontsize=9)
fig.savefig(ROOT/'docs/assets/farm-reference/reconstructed-comparison.png',dpi=150)
plt.close(fig)
print(json.dumps({k:summary[k] for k in ['fragment_count','colours','bounding_box_wgs84','relative_alignment_p95_m','union_traced_area_ha']},ensure_ascii=False))
