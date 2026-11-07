"""Sensor-site ranking: transparent factors, points inside outlines, foci from the journal."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.request import urlopen

from shapely.geometry import Point

from agro_checker import sensor_siting as siting
from agro_checker.field_store import FieldStore
from agro_checker.scouting_server import make_server

FIDS = [f'R{i:02d}' for i in range(1, 28)]


class SitingTest(unittest.TestCase):
    def test_r11_shape(self):
        r = siting.siting(None, 'R11')
        self.assertEqual(r['fid'], 'R11')
        self.assertEqual([f['id'] for f in r['factors']], ['relief', 'tall_objects', 'soil', 'foci'])
        for f in r['factors']:
            self.assertIn(f['status'], {'used', 'unavailable', 'no_data'})
            self.assertTrue(f['summary'])
        self.assertTrue(r['sites'])
        self.assertEqual([s['rank'] for s in r['sites']], list(range(1, len(r['sites'])+1)))
        for s in r['sites']:
            self.assertEqual(s['status'], 'candidate_for_site_visit_not_sensor_installation')
            self.assertIn(s['kind'], {'low', 'high', 'shelter', 'focus'})
            self.assertTrue(s['reasons'])
        self.assertTrue(r['recommendation'] and r['method'] and r['limitations'])

    def test_all_outlines_sites_inside_and_apart(self):
        for fid in FIDS:
            with self.subTest(fid=fid):
                r = siting.siting(None, fid)
                geo = siting.outline(fid)[1]
                self.assertLessEqual(len(r['sites']), 4)
                for s in r['sites']:
                    self.assertTrue(geo.contains(Point(s['longitude'], s['latitude'])))
                pts = [siting.TO_UTM.transform(s['longitude'], s['latitude']) for s in r['sites']]
                for i, a in enumerate(pts):
                    for b in pts[i+1:]:
                        self.assertGreaterEqual(((a[0]-b[0])**2+(a[1]-b[1])**2)**.5, siting.MIN_GAP_M-1)

    def test_slope_outlines_do_not_claim_tall_objects(self):
        r = siting.siting(None, 'R25')
        self.assertEqual(r['factors'][1]['status'], 'unavailable')
        self.assertNotIn('shelter', [s['kind'] for s in r['sites']])

    def test_recorded_focus_ranks_first_and_lifts_nearby_site(self):
        with TemporaryDirectory() as d:
            store = FieldStore(Path(d))
            base = siting.siting(store, 'R11')
            self.assertEqual(base['factors'][3]['status'], 'no_data')
            high = next(s for s in base['sites'] if s['kind'] == 'high')
            store.append(siting.IMPORT_KIND, siting.STREAM, {'rows': [
                {'type': 'focus', 'fid': 'R11', 'date': '2025-05-10', 'disease': 'септориоз',
                 'latitude': high['latitude']+0.0004, 'longitude': high['longitude']},
                {'type': 'field', 'fid': 'R11'}]})
            r = siting.siting(store, 'R11')
            self.assertEqual(r['factors'][3]['status'], 'used')
            self.assertEqual(r['sites'][0]['kind'], 'focus')
            self.assertEqual(r['sites'][0]['rank'], 1)
            self.assertGreater(next(s['rank'] for s in base['sites'] if s['kind'] == 'high'), 1)
            # the focus lies ~45 m from the former high site: merged, keeping both reasons
            self.assertTrue(any('DSM' in x for x in r['sites'][0]['reasons']))
            self.assertNotIn('high', [s['kind'] for s in r['sites']])

    def test_focus_mark_without_coordinates_is_outline_priority(self):
        with TemporaryDirectory() as d:
            store = FieldStore(Path(d))
            store.append(siting.MARK_KIND, siting.STREAM, {'fid': 'R07', 'observed_on': '2026-05-01', 'result': 'focus'})
            store.append(siting.MARK_KIND, siting.STREAM, {'fid': 'R07', 'observed_on': '2026-05-02', 'result': 'clean'})
            r = siting.siting(store, 'R07')
            self.assertEqual(r['factors'][3]['status'], 'used')
            self.assertEqual(r['factors'][3]['details']['count'], 1)
            self.assertNotIn('focus', [s['kind'] for s in r['sites']])

    def test_invalid_fid(self):
        for fid in ('R00', 'R28', '', None, '../x'):
            with self.subTest(fid=fid):
                with self.assertRaises(ValueError):
                    siting.siting(None, fid)


class SitingRouteTest(unittest.TestCase):
    def test_http_route(self):
        with TemporaryDirectory() as d:
            server = make_server(Path(d), 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/scouting/siting?fid=R11') as r:
                    self.assertEqual(r.status, 200)
                    body = json.loads(r.read())
                self.assertEqual(body['fid'], 'R11')
                self.assertEqual(server.scouting_store.records(), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
