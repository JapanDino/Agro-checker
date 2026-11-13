"""Disposable technical records only; no real farm tasks or observations."""
from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import json
from uuid import uuid4
from unittest.mock import patch

from agro_checker import farm_scouting as scout, farm_workspace as w
from agro_checker.field_store import FieldStore
from agro_checker.scouting_server import make_server


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FieldStore(Path(self.tmp.name))

    def task(self,fid='R11',**updates):
        c=w.comparison('recent2026');p=c['pair']
        raw={'request_id':uuid4().hex,'fragment':fid,'period':'recent2026',
             'before':p['before']['id'],'after':p['after']['id'],'version':c['version'],
             'relative':None,'actor':'TEST fixture, not a real agronomist','planned_date':'',
             'objective':'TEST only in temporary store','metric':'ndmi'}
        raw.update(updates);return scout.save_task(self.store,raw)

    def test_shared_dates_reproduce_source_metrics_for_every_supported_area(self):
        c=w.comparison('recent2026');self.assertEqual(c['total_areas'],27)
        self.assertEqual(c['available_areas'],27)
        for r in c['rows']:
            d=scout.detail(r['id'],'recent2026',c['pair']['before']['id'],c['pair']['after']['id'])
            self.assertEqual(d['pair']['metrics']['ndmi'],r['ndmi'])
            self.assertEqual(d['pair']['metrics']['ndvi'],r['ndvi'])
            self.assertEqual(d['pair']['coverage'],r['coverage'])

    def test_spring_missing_areas_never_fallback_to_individual_pairs(self):
        c=w.comparison('spring2024');self.assertGreater(c['available_areas'],0);self.assertLess(c['available_areas'],27)
        for r in c['rows']:
            if r['status']=='missing':
                d=w.area_detail(r['id'],'spring2024',c['pair']['before']['id'],c['pair']['after']['id'])
                self.assertIsNone(d['pair']);self.assertIsNone(d['default_place_comparison'])
                self.assertIsNone(r['ndmi']);self.assertIsNone(r['ndvi']);self.assertTrue(r['reason'])

    def test_wrong_order_and_other_period_rejected(self):
        p=w.catalog('recent2026')['default']
        for period,a,b in [('recent2026',p['after'],p['before']),('spring2024',p['before'],p['after']),('recent2026',p['after'],p['after'])]:
            with self.subTest(period=period,a=a),self.assertRaises(ValueError):w.comparison(period,a,b)

    def test_no_network_or_journal_write_needed_for_archive_comparison(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('unexpected external request')):
            c=w.comparison('recent2026')
            self.assertEqual(c['kind'],'observed_surface_change_not_disease_priority')
        self.assertEqual(self.store.records(),[])

    def test_plan_pins_real_task_evidence_is_idempotent_and_is_not_a_visit(self):
        tasks=[self.task(fid) for fid in ['R11','R08']]
        raw={'request_id':uuid4().hex,'title':'TEST plan','task_ids':[t['id'] for t in tasks],'purpose':'sensor_survey'}
        a=w.save_plan(self.store,raw);b=w.save_plan(self.store,deepcopy(raw));self.assertEqual(a,b)
        exported=w.plan_export(self.store,a['id']);self.assertEqual(exported['tasks'],tasks)
        self.assertEqual(exported['visits'],[]);self.assertFalse(exported['model_training_enabled'])
        self.assertEqual(a['payload']['site_checks'],w.SITE_CHECKS)
        self.assertFalse(a['payload']['route_optimized']);self.assertFalse(a['payload']['model_training_eligible'])
        self.assertEqual(len(w.plans(self.store)),1)
        with self.assertRaises(ValueError):w.save_plan(self.store,{**raw,'title':'conflicting retry'})

    def test_manual_task_can_be_saved_retried_and_exported_without_a_satellite_pair(self):
        raw={'request_id':uuid4().hex,'source':'manual','fragment':'R11','coordinates':None,
             'actor':'TEST fixture','planned_date':'2026-11-13','objective':'TEST manual inspection'}
        with patch.object(scout,'detail',side_effect=AssertionError('Unexpected satellite comparison')):
            task=scout.save_task(self.store,raw)
            self.assertEqual(task,scout.save_task(self.store,raw))
            place=scout.reference_location('R11')
            self.assertEqual(task['payload']['place'],place)
            self.assertIsNone(task['payload']['evidence'])
            longitude,latitude=scout.TO_GEO.transform(place['utm_x']+1,place['utm_y'])
            custom=scout.save_task(self.store,{**raw,'request_id':uuid4().hex,
                                              'coordinates':[longitude,latitude]})
            self.assertEqual(custom['payload']['place']['id'],place['id'])
            self.assertEqual(custom['payload']['place']['longitude'],longitude)
            self.assertEqual(custom['payload']['place']['latitude'],latitude)
            self.assertIsNone(custom['payload']['evidence'])
            with self.assertRaises(ValueError):
                scout.save_task(self.store,{**raw,'request_id':uuid4().hex,'coordinates':[0,0]})
        plan=w.save_plan(self.store,{'request_id':uuid4().hex,'title':'TEST manual plan',
                                    'task_ids':[custom['id']],'purpose':'inspection'})
        self.assertEqual(w.plan_export(self.store,plan['id'])['tasks'],[custom])
        self.assertEqual(scout.listing(self.store)['visits'],[])

    def test_invalid_unknown_duplicate_overfull_and_repeated_place_plans_do_not_write(self):
        t=self.task();repeat=self.task();other=[self.task(fid) for fid in ['R08','R09','R10']]
        raw={'request_id':uuid4().hex,'title':'TEST','task_ids':[t['id']],'purpose':'inspection'}
        before=self.store.records()
        for changes in [{'title':''},{'task_ids':[]},{'task_ids':['missing']},{'task_ids':[t['id'],t['id']]},
                        {'task_ids':[t['id'],repeat['id']]},{'task_ids':[t['id']]+[x['id'] for x in other]}, {'purpose':'disease'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):w.save_plan(self.store,{**raw,**changes})
        self.assertEqual(before,self.store.records())

    def test_standalone_server_is_empty_bounded_and_exports_saved_plan(self):
        server=make_server(Path(self.tmp.name)/'server',0);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path):return json.load(urlopen(base+path))
        state=get('/api/scouting/state');self.assertEqual(state['tasks'],[]);self.assertEqual(state['plans'],[])
        self.assertEqual(server.scouting_store.records(),[])
        for path in ['/','/scouting/about','/scouting/plans.js','/scouting/visit-drafts.js','/scouting/assets/leaflet.js']:
            with urlopen(base+path) as r:self.assertEqual(r.status,200)
        with urlopen(base+'/scouting/offline.js') as r:
            self.assertEqual(r.headers['Service-Worker-Allowed'],'/')
        with self.assertRaises(HTTPError):urlopen(base+'/scouting/../../.env')
        c=get('/api/scouting/workspace?period=recent2026');self.assertEqual(c['available_areas'],27)
        raw={'request_id':uuid4().hex,'fragment':'R11','period':'recent2026','before':c['pair']['before']['id'],
             'after':c['pair']['after']['id'],'version':c['version'],'relative':None,'actor':'TEST fixture',
             'planned_date':'','objective':'TEST technical task','metric':'ndmi'}
        def post(path,body,token=state['token']):
            return json.load(urlopen(Request(base+path,data=json.dumps(body).encode(),headers={'X-Field-Token':token,'Content-Type':'application/json'})))
        with self.assertRaises(HTTPError):post('/api/scouting/tasks',raw,'wrong')
        t=post('/api/scouting/tasks',raw)
        p=post('/api/scouting/plans',{'request_id':uuid4().hex,'title':'TEST only','task_ids':[t['id']],'purpose':'inspection'})
        self.assertEqual(get('/api/scouting/plans/export?id='+p['id'])['tasks'][0],t)
        self.assertEqual(get('/api/scouting/state')['visits'],[])


if __name__=='__main__':unittest.main()
