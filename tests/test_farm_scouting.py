"""Technical fixtures live only in disposable stores, never the farm journal."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

from PIL import Image
import numpy as np

from agro_checker import farm_scouting as s
from agro_checker.field_store import FieldStore
from agro_checker.field_server import make_server

NOW = datetime(2026,10,20,15,tzinfo=timezone.utc)


class ScoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detail = s.detail('R11','recent2026')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = FieldStore(Path(self.tmp.name))
        p = self.detail['pair']
        self.raw = {'request_id':uuid4().hex,'fragment':'R11','period':'recent2026',
                    'before':p['before']['id'],'after':p['after']['id'],'version':p['version'],
                    'relative':None,'actor':'TEST fixture, not a real agronomist','planned_date':'',
                    'objective':'TEST task in disposable store only','metric':'ndmi'}

    def task(self):
        return s.save_task(self.store,self.raw,NOW)

    def visit(self,task,**changes):
        raw={'request_id':uuid4().hex,'task_id':task['id'],'actor':'TEST fixture',
             'observed_at':'2026-10-20T16:00:00+03:00','notes':'TEST fixture, not a field observation',
             'outcome':'unclear','follow_up':'','photos':[],'real_visit_confirmed':True}
        raw.update(changes)
        return raw

    def test_read_does_not_seed_tasks_or_observations(self):
        initial=self.store.records()
        self.assertEqual(s.listing(self.store)['tasks'],[])
        self.assertEqual(s.listing(self.store)['visits'],[])
        self.assertEqual(len(s.overview()['items']),27)
        self.assertEqual(initial,self.store.records())
        self.assertFalse(s.overview()['confirmed_boundaries'])

    def test_default_pair_reproduces_original_paired_pixel_analysis(self):
        original=s.archive()[1]['features']['R11']['changes'][1]['same_baseline_comparison']
        for key in ['ndvi','ndmi']:
            self.assertAlmostEqual(self.detail['pair']['metrics'][key]['base'],original[key]['base'],places=7)
        self.assertTrue(self.detail['pair']['same_baseline'])

    def test_cloudy_dates_preserved_but_not_eligible_for_comparison(self):
        cloudy=next(v for v in self.detail['dates'] if not v['usable'])
        self.assertIsNone(cloudy['ndmi'])
        with self.assertRaises(ValueError):
            s.detail('R11','recent2026',cloudy['id'],self.detail['pair']['after']['id'])

    def test_same_date_reversed_dates_unknown_fragment_and_period_rejected(self):
        p=self.detail['pair']
        for args in [('R99','recent2026'),('R11','future'),('R11','recent2026',p['after']['id'],p['before']['id']),('R11','recent2026',p['before']['id'],p['before']['id'])]:
            with self.subTest(args=args),self.assertRaises(ValueError):s.detail(*args)

    def test_actual_rasters_render_maps_with_shared_crop(self):
        p=self.detail['pair'];sizes=[]
        for side in ['before','after','delta']:
            raw=s.map_png('R11','recent2026',p['before']['id'],p['after']['id'],'ndmi',side)
            with Image.open(io.BytesIO(raw)) as image:
                sizes.append(image.size);self.assertGreater(len(image.getcolors(1_000_000)),10)
        self.assertEqual(sizes,[sizes[0]]*3)

    def test_task_is_not_a_visit_and_pins_original_evidence(self):
        record=self.task();p=record['payload']
        self.assertEqual(p['evidence'],self.detail['pair'])
        self.assertEqual(p['purpose'],'human_task_from_archive_not_current_alert')
        self.assertFalse(p['model_training_eligible']);self.assertIsNone(p['automatic_diagnosis'])
        self.assertEqual(s.listing(self.store)['visits'],[])
        self.assertEqual(self.store.records('land_inspection'),[])
        self.assertEqual(self.store.records('forecast'),[])
        self.assertEqual(p['place_evidence'],self.detail['default_place_comparison'])

    def test_stale_version_invalid_metric_and_outside_map_do_not_write(self):
        for updates in [{'version':'0'*64},{'metric':'disease'},{'relative':[0,0]},{'relative':[False,1]},{'actor':''},{'planned_date':'2026-02-30'}]:
            with self.subTest(updates=updates),self.assertRaises(ValueError):s.save_task(self.store,{**self.raw,**updates},NOW)
        self.assertEqual(self.store.records(),[])

    def test_retry_is_idempotent_and_conflicting_retry_cannot_overwrite(self):
        first=self.task();second=self.task();self.assertEqual(first,second)
        self.assertEqual(len(s.listing(self.store)['tasks']),1)
        with self.assertRaises(ValueError):s.save_task(self.store,{**self.raw,'objective':'changed'},NOW)

    def test_same_place_survives_repeat_task_and_other_scene_pair(self):
        first=self.task();self.raw['request_id']=uuid4().hex
        self.raw['before']=next(v['id'] for v in self.detail['dates'] if v['usable'])
        self.raw['objective']='TEST repeat task'
        second=self.task()
        self.assertEqual(first['payload']['place']['id'],second['payload']['place']['id'])
        self.assertNotEqual(first['id'],second['id'])
        self.assertNotEqual(first['payload']['evidence']['before'],second['payload']['evidence']['before'])

    def test_place_series_matches_actual_native_pixels_and_preserves_gaps(self):
        place=self.detail['default_location'];series=s.place_series(place['id'])
        self.assertEqual(series['total_cells'],9)
        self.assertTrue(any(r['ndmi'] is None for r in series['rows']))
        row=next(r for r in series['rows'] if r['ndmi'] is not None)
        a=s.raster(row['scene']);x0,y0,x1,y1=s.archive()[0]['bounds_utm']
        _,cx,cy=place['id'].split('-');cx,cy=int(cx),int(cy)
        cells=[(int((y1-yy-20)/20),int((xx-x0)/20)) for yy in range(cy*60,cy*60+60,20) for xx in range(cx*60,cx*60+60,20)]
        actual=[a['ndmi'][r,c] for r,c in cells if a['valid'][r,c]]
        self.assertAlmostEqual(row['ndmi'],float(np.median(actual)),places=7)

    def test_visit_requires_confirmation_known_task_and_valid_past_time(self):
        task=self.task()
        for changes in [{'real_visit_confirmed':False},{'task_id':'0'*32},{'observed_at':'2027-01-01T00:00:00Z'},{'observed_at':'2026-10-20T12:00:00'},{'notes':''},{'outcome':'automatic_diagnosis'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):s.save_visit(self.store,self.visit(task,**changes),NOW)
        self.assertEqual(s.listing(self.store)['visits'],[])

    def test_two_visits_append_without_overwriting_original_and_retry_is_safe(self):
        task=self.task();raw=self.visit(task);first=s.save_visit(self.store,raw,NOW)
        retry=s.save_visit(self.store,raw,NOW);self.assertEqual(first,retry)
        second=s.save_visit(self.store,self.visit(task,observed_at='2026-10-20T17:00:00+03:00',notes='TEST second fixture'),NOW)
        visits=s.listing(self.store)['visits'];self.assertEqual(len(visits),2)
        self.assertEqual(first['payload']['place'],second['payload']['place'])
        self.assertEqual(visits[0]['payload']['notes'],raw['notes'])
        self.assertFalse(second['payload']['model_training_eligible'])

    def test_photo_is_validated_and_can_only_be_read_from_its_own_visit(self):
        out=io.BytesIO();Image.new('RGB',(8,8),'white').save(out,format='PNG');binary=out.getvalue()
        photo={'filename':'TEST-pattern.png','data_base64':base64.b64encode(binary).decode()}
        visit=s.save_visit(self.store,self.visit(self.task(),photos=[photo]),NOW)
        p=visit['payload']['photos'][0]
        self.assertEqual(s.saved_photo(self.store,visit['id'],p['id']),(binary,'image/png'))
        with self.assertRaises(KeyError):s.saved_photo(self.store,'0'*32,p['id'])
        (self.store.directory/'farm-scouting-media'/(p['id']+'.bin')).write_bytes(b'corrupted TEST')
        with self.assertRaises(ValueError):s.saved_photo(self.store,visit['id'],p['id'])

    def test_api_routes_token_and_no_read_side_effects(self):
        server=make_server(Path(self.tmp.name),0);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urlopen(base+path) as r:return r.status,r.headers.get_content_type(),r.read()
        status,mime,body=get('/api/scouting/state');state=json.loads(body)
        self.assertEqual(status,200);self.assertEqual(state['visits'],[])
        self.assertEqual(get('/scouting')[0],200)
        p=self.detail['pair'];crop=p['crop'];loc=self.detail['default_location']
        from urllib.parse import urlencode
        relative=[(loc['utm_x']-crop['west'])/(crop['width']*20),(crop['north']-loc['utm_y'])/(crop['height']*20)]
        query={'fid':'R11','period':'recent2026','before':p['before']['id'],'after':p['after']['id'],'relative':json.dumps(relative)}
        location=json.loads(get('/api/scouting/location?'+urlencode(query))[2])
        self.assertEqual(location['location']['id'],loc['id'])
        self.assertEqual(location['comparison'],self.detail['default_place_comparison'])
        query['relative']='[0,0]'
        with self.assertRaises(HTTPError):get('/api/scouting/location?'+urlencode(query))
        with self.assertRaises(HTTPError):urlopen(Request(base+'/api/scouting/tasks',data=json.dumps(self.raw).encode(),headers={'Content-Type':'application/json'}))
        with urlopen(Request(base+'/api/scouting/tasks',data=json.dumps(self.raw).encode(),headers={'Content-Type':'application/json','X-Field-Token':state['token']})) as r:
            self.assertIn('id',json.load(r))
        status,mime,body=get('/api/scouting/export');self.assertEqual(len(json.loads(body)['tasks']),1);self.assertEqual(json.loads(body)['visits'],[])
        with self.assertRaises(HTTPError):get('/api/scouting/detail?fid=R11&period=recent2026&unexpected=1')
        self.assertEqual(len(server.field_store.records(s.TASK,s.STREAM)),1)


if __name__=='__main__':unittest.main()
