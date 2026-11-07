"""Checks against the actual frozen rasters and HTTP isolation, not synthetic geodata."""
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from agro_checker.field_server import make_server
from agro_checker.land_view import overview, scene
from agro_checker.pipeline import ROOT


class LandViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = overview()

    def test_real_native_geometry_and_soil_intervals(self):
        import rasterio
        from pyproj import Transformer
        g = self.data['soil']['geometry']
        # Corner coordinates must map back to the source, not an invented square grid.
        x0, y0 = Transformer.from_crs(4326, 32637, always_xy=True).transform(37.911914, 45.024106)
        with rasterio.open(ROOT/'data/working-zone-01/open-land/raw/soil-native/clay_0-5cm_mean.tif') as r:
            x, y = Transformer.from_crs(r.crs, 32637, always_xy=True).transform(r.transform.c, r.transform.f)
            self.assertAlmostEqual(g['east'][0][0]+x0, x, places=2)
            self.assertAlmostEqual(g['north'][0][0]+y0, y, places=2)
        self.assertEqual((g['width'],g['height']), (9,9))
        clay = self.data['soil']['layers']['clay']['0-5cm']
        self.assertEqual([clay[k][4][4] for k in ['Q0.05','mean','Q0.95']], [2.6,33.5,86.4])
        self.assertFalse(self.data['orchard_boundary_known'])
        self.assertFalse(self.data['agronomic_accuracy_claimed'])

    def test_cloud_gaps_stay_null_in_both_series_and_maps(self):
        rows = self.data['satellite']['scenes']
        self.assertEqual(len(rows), 36)
        self.assertEqual(sum(s['point_window_usable'] for s in rows),13)
        cloudy = rows[0]
        self.assertEqual(cloudy['point_scl'],9)
        for key in ['ndvi','ndmi']:
            self.assertIsNone(cloudy[f'point_60m_{key}_median'])
            self.assertIsNone(cloudy['spatial_range'][key])
            self.assertTrue(all(v is None for row in scene(cloudy['id'])[key] for v in row))
        for s in rows:
            if not s['point_window_usable']:
                self.assertIsNone(s['point_60m_ndvi_median'])
                self.assertIsNone(s['point_60m_ndmi_median'])

    def test_spatial_ranges_reproduce_accepted_window_not_confidence_intervals(self):
        import numpy as np
        for s in self.data['satellite']['scenes']:
            if not s['point_window_usable']: continue
            m=scene(s['id'])
            for key in ['ndvi','ndmi']:
                a=np.array([[np.nan if v is None else v for v in row] for row in m[key]])[49:52,49:52]
                self.assertAlmostEqual(float(np.nanmedian(a)),s[f'point_60m_{key}_median'],places=4)
                self.assertAlmostEqual(float(np.nanmin(a)),s['spatial_range'][key][0],places=4)
                self.assertAlmostEqual(float(np.nanmax(a)),s['spatial_range'][key][1],places=4)
                self.assertEqual(int(np.isfinite(a).sum()),s['valid_land_cells_in_point_60m_window'])
        json.dumps(self.data,allow_nan=False)

    def test_http_read_only_catalogue_allowlist_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp, make_server(Path(tmp),0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                before=server.field_store.records()
                with urlopen(base+'/land') as r:
                    self.assertIn("default-src 'self'",r.headers['Content-Security-Policy'])
                    self.assertIn('границы сада неизвестны',r.read().decode())
                for path in ['/land.js','/land.css','/api/land','/api/land/scene/'+self.data['satellite']['scenes'][0]['id']]:
                    with urlopen(base+path) as r:self.assertEqual(r.status,200)
                for path in ['/api/land/scene/../../../config/working_zone.json','/api/land/scene/unknown','/land/../../config/working_zone.json']:
                    with self.assertRaises(HTTPError) as e:urlopen(base+path)
                    self.assertEqual(e.exception.code,404)
                with patch('agro_checker.land_view.overview',side_effect=ValueError('Changed artifact')):
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/api/land')
                    self.assertEqual(e.exception.code,503)
                    self.assertIn('не прошёл проверку',e.exception.read().decode())
                self.assertEqual(server.field_store.records(),before)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
