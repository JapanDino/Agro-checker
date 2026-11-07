"""Validate the frozen research output and isolate it from the operational pilot."""
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError

from agro_checker.field_server import make_server
from agro_checker.pipeline import ROOT

D = ROOT/'data/farm-research/reconstruction'


class FarmReconstructionTest(unittest.TestCase):
    def test_frozen_source_and_reserved_spatial_blocks(self):
        summary=json.loads((D/'summary.json').read_text(encoding='utf-8'))
        for name,digest in summary['source_hashes'].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)
        reg=json.loads((D/'registration.json').read_text())
        blocks={True:set(),False:set()}
        for point in reg['pairs']:
            x,y=point['source'];block=(int(x//200),int(y//200))
            self.assertEqual(point['reserved'],(block[0]+2*block[1])%4==0)
            blocks[point['reserved']].add(block)
        self.assertFalse(blocks[True]&blocks[False])
        self.assertEqual(sum(p['reserved'] for p in reg['pairs']),163)
        self.assertGreater(summary['relative_alignment_p95_m'],15)
        self.assertLess(summary['relative_alignment_p95_m'],17)
        self.assertEqual(summary['absolute_accuracy'],'unknown')

    def test_geometry_geography_and_unconfirmed_scope(self):
        areas=json.loads((D/'approximate-areas.geojson').read_text())
        bands=json.loads((D/'uncertainty-bands.geojson').read_text())
        self.assertEqual(len(areas['features']),27)
        self.assertEqual(len(bands['features']),27)
        expected={f'R{i:02}' for i in range(1,28)}
        self.assertEqual({f['properties']['id'] for f in areas['features']},expected)
        for feature in areas['features']:
            p=feature['properties'];self.assertFalse(p['confirmed_boundary'])
            self.assertIsNone(p['farm_field_id']);self.assertIsNone(p['crop'])
            self.assertEqual(p['working_band_m'],100 if p['icon_occluded'] else 50)
            ring=feature['geometry']['coordinates'][0]
            self.assertEqual(ring[0],ring[-1])
            for lon,lat in ring:
                self.assertTrue(37.89<lon<38.03 and 44.96<lat<45.13)

    def test_read_only_http_assets_and_no_operational_records(self):
        with tempfile.TemporaryDirectory() as folder:
            server=make_server(folder,port=0)
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            before={str(p.relative_to(folder)):p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                for route in ['','/app.js','/styles.css','/satellite-aligned.jpg','/screenshot.jpg','/data.json','/areas.geojson','/uncertainty.geojson','/context.js','/context.css','/context-data.json','/context-terrain.png','/context-clay.png','/context-ph.png','/context-terrain-scale.png','/context-clay-scale.png','/context-ph-scale.png']:
                    with urlopen(base+'/farm-reconstruction'+route) as r:
                        raw=r.read();self.assertEqual(r.status,200)
                        self.assertIn("default-src 'self'",r.headers['Content-Security-Policy'])
                        if route.endswith(('.json','.geojson')):json.loads(raw)
                with self.assertRaises(HTTPError) as error:urlopen(base+'/farm-reconstruction/registration.json')
                self.assertEqual(error.exception.code,404)
                after={str(p.relative_to(folder)):p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}
                self.assertEqual(before,after)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
