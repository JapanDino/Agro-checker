"""Rebuild the workstation's farm map from the scouting context.

Crops web/scouting/assets/basemap.jpg (Esri World Imagery, Web Mercator) to the farm,
projects the 27 approximate field outlines from public-context.json into image pixels and
writes both into web/workstation: farm-basemap.jpg and the MAPGEO constant in workstation.js.

    python web/workstation/tools/build_farm_map.py
"""
import json
import math
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = ROOT / 'web/scouting/assets/public-context.json'
BASEMAP = ROOT / 'web/scouting/assets/basemap.jpg'
OUT = ROOT / 'web/workstation'
CROP = (37.866, 38.026, 44.958, 45.133)  # lon0, lon1, lat0, lat1: the farm plus the ERA5 cell's west and north edges
HEIGHT = 1500


def mercator_y(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def main():
    ctx = json.loads(CONTEXT.read_text(encoding='utf-8'))
    (la0, lo0), (la1, lo1) = ctx['basemap']['bounds']
    image = Image.open(BASEMAP)
    w, h = image.size

    def px(lon, lat):
        return ((lon - lo0) / (lo1 - lo0) * w,
                (mercator_y(la1) - mercator_y(lat)) / (mercator_y(la1) - mercator_y(la0)) * h)

    cl0, cl1, ca0, ca1 = CROP
    x0, y0 = (round(v) for v in px(cl0, ca1))
    x1, y1 = (round(v) for v in px(cl1, ca0))
    crop = image.crop((x0, y0, x1, y1))
    scale = HEIGHT / crop.size[1]
    out = crop.resize((round(crop.size[0] * scale), HEIGHT), Image.LANCZOS)
    out.save(OUT / 'farm-basemap.jpg', quality=78, optimize=True, progressive=True)

    def to_map(lon, lat):
        x, y = px(lon, lat)
        return [round((x - x0) * scale, 1), round((y - y0) * scale, 1)]

    fields = {}
    for feature in ctx['geometry']['features']:
        props, ring = feature['properties'], feature['geometry']['coordinates'][0]
        area = cx = cy = 0.0
        for (xa, ya), (xb, yb) in zip(ring, ring[1:] + ring[:1]):
            cross = xa * yb - xb * ya
            area += cross
            cx += (xa + xb) * cross
            cy += (ya + yb) * cross
        area /= 2
        cx, cy = cx / (6 * area), cy / (6 * area)
        fields[props['id']] = {'pts': [to_map(lo, la) for lo, la in ring], 'c': to_map(cx, cy),
                               'll': [round(cy, 5), round(cx, 5)], 'ha': props['traced_area_ha']}

    grid = ctx['weather']['spring2024']
    glat, glon = grid['grid_latitude'], grid['grid_longitude']
    geo = {'w': out.size[0], 'h': out.size[1], 'fields': fields,
           'sector': {'node': to_map(glon, glat), 'west': to_map(glon - .125, ca1)[0],
                      'north': to_map(cl0, glat + .125)[1], 'grid': [glat, glon]},
           'kmpx': round((cl1 - cl0) * 111.32 * math.cos(math.radians((ca0 + ca1) / 2)) / out.size[0], 5),
           'attribution': ctx['basemap']['attribution']}
    page = OUT / 'workstation.js'
    html = page.read_text(encoding='utf-8')
    html, n = re.subn(r'const MAPGEO=\{.*?\};\n', lambda _: 'const MAPGEO=' + json.dumps(geo, ensure_ascii=False, separators=(',', ':')) + ';\n', html, count=1)
    if n != 1:
        raise SystemExit('MAPGEO not found in workstation.js')
    page.write_text(html, encoding='utf-8')
    print(f'farm-basemap.jpg {out.size[0]}×{out.size[1]}, {len(fields)} fields, MAPGEO updated')


if __name__ == '__main__':
    main()
