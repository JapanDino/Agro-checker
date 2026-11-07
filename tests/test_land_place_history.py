"""Synthetic visits/rasters in disposable stores; no invented production observations."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
import json

from pyproj import Transformer

from agro_checker import land_place_history as h
from agro_checker import land_inspections as j
from agro_checker.field_store import FieldStore
from agro_checker.field_server import make_server


class PlaceHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FieldStore(Path(self.tmp.name)).init()
        self.ref={'latitude':45.024106,'longitude':37.911914}
        x,y=Transformer.from_crs(4326,32637,always_xy=True).transform(self.ref['longitude'],self.ref['latitude'])
        self.w={'id':'w1-2','west_m':round(x/60)*60-x,'north_m':round(y/60)*60-y,'centre':self.ref,'paired_cells':9}
        self.pin={'period':'archive','observation_version':'test','manifest_sha256':'test','comparison_source_sha256':'test','before':'a','after':'b'}
        self.scenes=[{'id':str(day),'acquired_at_utc':f'2024-03-{day:02}T08:00:00Z','processing_baseline':'05.00','point_window_usable':False} for day in [1,3,5,9]]
        self.maps={s['id']:self.raster(i*.1) for i,s in enumerate(self.scenes)}

    def raster(self,value):
        return {'cell_m':20,'width':3,'height':3,'west_m':self.w['west_m'],'north_m':self.w['north_m'],
                'ndvi':[[round(value+.01*r+.02*c,5) for c in range(3)] for r in range(3)],'ndmi':[[value/2]*3 for r in range(3)]}

    def visit(self,day,window=None,note='TEST ONLY',hour=12):
        return self.store.append(j.KIND,j.STREAM,{'reference':self.ref,'selected_window':window or self.w,
            'observed_at_utc':f'2024-03-{day:02}T{hour:02}:00:00+00:00','notes':note,'photos':[],'actor':'TEST'},datetime(2026,10,18,tzinfo=timezone.utc))

    def build(self):return h.build(self.store,self.ref,self.w,self.scenes,self.maps,self.pin)

    def test_ground_identity_survives_crop_ids_but_neighbours_stay_separate(self):
        a=self.visit(5);w=deepcopy(self.w);w['id']='w99-1';self.visit(3,w)
        neighbour=deepcopy(self.w);neighbour['west_m']+=60;self.visit(9,neighbour)
        groups=h.groups(self.store);self.assertEqual(sorted(len(g['record_ids']) for g in groups),[1,2])
        d=self.build();self.assertEqual([r['payload']['observed_at_utc'][8:10] for r in d['visits']],['03','05'])
        self.assertEqual(len(d['intervals']),1)
        with self.assertRaises(ValueError):h.place(self.ref,dict(w,west_m=w['west_m']+1))

    def test_empty_journal_exposes_real_raster_values_not_field_visits(self):
        before=self.store.records();d=self.build()
        self.assertEqual(d['visits'],[]);self.assertEqual(d['intervals'],[]);self.assertEqual(self.store.records(),before)
        self.assertEqual(d['scenes'][0]['ndvi']['median'],.03)
        self.assertTrue(d['scenes'][0]['usable']) # Point flag is for another location.

    def test_dates_sorted_by_observation_not_entry_time_and_only_prior_images_used(self):
        self.visit(5,note='second');self.visit(3,note='first')
        p=self.build()['intervals'][0]
        self.assertEqual(p['status'],'paired');self.assertEqual(p['before']['id'],'3');self.assertEqual(p['after']['id'],'5')
        self.assertEqual(p['paired_cells'],9);self.assertEqual(p['ndvi']['median'],.1);self.assertEqual(p['ndmi']['median'],.05)
        self.assertFalse(self.build()['policy']['interpolation'])

    def test_missing_center_sparse_cloud_and_technical_gaps_remain_gaps(self):
        self.maps['3']['ndvi'][1][1]=None
        self.maps['5']['ndmi']=[[None]*3 for r in range(3)]
        del self.maps['9']
        rows=self.build()['scenes']
        self.assertEqual([s['usable'] for s in rows],[True,False,False,False])
        self.assertEqual(rows[1]['valid_cells'],8);self.assertIsNone(rows[1]['ndvi'])
        self.assertIn('технический',rows[-1]['reason'])

    def test_same_image_is_not_reported_as_zero_change(self):
        self.visit(3);self.visit(4)
        p=self.build()['intervals'][0]
        self.assertEqual(p['status'],'same_scene');self.assertIsNone(p['ndvi'])

    def test_old_or_missing_pre_visit_image_never_backfilled_from_future(self):
        self.visit(1,hour=6);self.visit(3)
        p=self.build()['intervals'][0];self.assertIsNone(p['before']);self.assertEqual(p['status'],'unavailable')
        self.visit(20);p=self.build()['intervals'][-1]
        self.assertFalse(p['after']['within_lag_limit']);self.assertEqual(p['status'],'unavailable')

    def test_different_processing_and_same_time_records_are_not_interpreted_as_change(self):
        self.visit(3);self.visit(5);self.scenes[2]['processing_baseline']='06.00'
        self.assertEqual(self.build()['intervals'][0]['status'],'unavailable')
        self.visit(5);self.assertIn('Одинаковое',self.build()['intervals'][-1]['reason'])

    def test_changes_use_common_pixels_not_difference_of_medians(self):
        self.visit(3);self.visit(5)
        self.maps['3']['ndvi']=[[0,0,None],[0,.3,None],[.4,None,None]]
        self.maps['5']['ndvi']=[[None,None,.1],[None,.5,.2],[.8,.9,None]]
        p=self.build()['intervals'][0]
        self.assertEqual(p['paired_cells'],2);self.assertIsNone(p['ndvi']);self.assertEqual(p['status'],'unavailable')
        self.maps['5']['ndvi']=[[.5,.8,None],[.9,.5,None],[1,None,None]]
        p=self.build()['intervals'][0]
        self.assertEqual(p['paired_cells'],5);self.assertEqual(p['ndvi']['median'],.6)
        self.assertNotEqual(p['ndvi']['median'],.8-0)

    def test_shifted_crops_align_exact_ground_cells_without_resampling(self):
        base=self.raster(.2);m=deepcopy(base)
        m['width']=6;m['west_m']-=60
        for k in ['ndvi','ndmi']:m[k]=[[None]*3+row for row in base[k]]
        a,_=h._sample(self.scenes[0],base,self.w);b,_=h._sample(self.scenes[0],m,self.w)
        self.assertEqual(a,b)
        m['west_m']+=1;c,_=h._sample(self.scenes[0],m,self.w)
        self.assertFalse(c['usable']);self.assertIn('совмещена',c['reason'])

    def test_history_uses_pinned_snapshot_not_current_pointer(self):
        pin=dict(self.pin,period='recent',observation_version='a'*32,manifest_sha256='b'*64)
        d={'reference':self.ref,'blocks':[self.w]}
        with patch.object(j,'resolve',return_value=d),patch('agro_checker.sentinel_updates._load',return_value=({'scenes':self.scenes},self.maps)) as load,patch('agro_checker.sentinel_updates.load_latest',side_effect=AssertionError('Do not follow latest')):
            result=h.history(self.store,pin,self.w['id'])
        self.assertEqual(result['inspection_reference'],pin);self.assertEqual(load.call_args.args[1:],('a'*32,'b'*64))

    def test_history_http_queries_strict_and_read_only(self):
        result=self.build();before=self.store.records()
        with make_server(self.store.directory,0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                with patch.object(h,'history',return_value=result):
                    url=base+'/api/land/inspections/history?'+urlencode({'reference':json.dumps(self.pin),'window':self.w['id']})
                    self.assertEqual(json.load(urlopen(url))['visits'],[])
                    self.assertEqual(json.load(urlopen(base+'/api/land/inspections/'+'a'*32+'/history'))['place'],result['place'])
                for q in ['','?reference={}&window=x&window=y','?reference={}&window=x&extra=1','?reference=null','?reference=bad&window=x']:
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/api/land/inspections/history'+q)
                    self.assertEqual(e.exception.code,422)
                self.assertEqual(self.store.records(),before)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
