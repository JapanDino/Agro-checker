from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

import numpy as np
from pyproj import Transformer
from agro_checker.land_change import compare, inspection, comparison
from agro_checker.field_server import make_server


REF={'latitude':45.024106,'longitude':37.911914}
X,Y=Transformer.from_crs(4326,32637,always_xy=True).transform(REF['longitude'],REF['latitude'])
WEST=np.floor((X-180)/60)*60-X
NORTH=np.ceil((Y+180)/60)*60-Y


def sample(day):
    return {'id':'S2C_'+str(day),'acquired_at_utc':f'2026-10-{day:02d}T08:00:00Z',
            'point_window_usable':True,'processing_baseline':'05.13','extraction_status':'ready'}


def grid(value=.2):
    return {'width':21,'height':21,'west_m':float(WEST),'north_m':float(NORTH),'cell_m':20,
            'ndvi':np.full((21,21),value).tolist(),'ndmi':np.full((21,21),value).tolist()}


class LandChangeTest(unittest.TestCase):
    def compare(self,a=None,b=None,sa=None,sb=None):
        return compare(sa or sample(11),sb or sample(14),a or grid(),b or grid(.4),REF)

    def test_identity_has_zero_change_and_no_inspection_windows(self):
        a=grid();d=self.compare(a,a)
        self.assertTrue(all(v==0 for row in d['delta']['ndvi'] for v in row))
        self.assertEqual(d['statistics']['default_inspection_counts'],{'ndvi':0,'ndmi':0})

    def test_common_mask_keeps_missing_pixels_null_not_zero(self):
        a,b=grid(),grid(.4);a['ndvi'][8][8]=None;b['ndmi'][9][9]=None
        d=self.compare(a,b)
        self.assertIsNone(d['delta']['ndvi'][8][8]);self.assertIsNone(d['delta']['ndvi'][9][9])
        self.assertEqual(d['coverage'][8][8],2);self.assertEqual(d['coverage'][9][9],1)
        self.assertEqual(d['statistics']['paired_pixels'],439)
        for block in d['blocks']:
            if block['paired_cells']<9:self.assertFalse(inspection(block,'ndvi'))
        json.dumps(d,allow_nan=False)

    def test_integer_offset_aligns_by_location_not_array_position(self):
        # Same geographic surface sampled in shifted crop windows must yield zero.
        a=grid();b=grid();a['ndvi']=[[.01*c for c in range(21)] for r in range(21)]
        b['ndvi']=[[.01*(c+1) for c in range(21)] for r in range(21)];b['west_m']+=20
        d=self.compare(a,b)
        self.assertEqual(d['grid']['width'],20)
        self.assertTrue(all(v==0 for row in d['delta']['ndvi'] for v in row))

    def test_fractional_grid_shift_and_invalid_dates_rejected(self):
        b=grid();b['west_m']+=1
        with self.assertRaisesRegex(ValueError,'Сетки'):self.compare(b=b)
        with self.assertRaises(ValueError):self.compare(sa=sample(14),sb=sample(11))
        with self.assertRaises(ValueError):self.compare(sa=sample(14),sb=sample(14))
        bad=sample(11);bad['point_window_usable']=False
        with self.assertRaises(ValueError):self.compare(sa=bad)

    def test_large_uniform_change_survives_all_shifts_in_interior(self):
        d=self.compare();block=next(b for b in d['blocks'] if b['ndvi']['shift_scenarios']==9)
        self.assertTrue(inspection(block,'ndvi'));self.assertEqual(block['ndvi']['shift_range'],[.2,.2])
        self.assertEqual(block['ndvi']['positive_cells'],9)
        self.assertFalse(inspection(block,'ndvi',threshold=.25))

    def test_sharp_boundary_change_is_suppressed_when_shift_reverses_it(self):
        a=grid(.2);b=grid(.2)
        # A local gradient displaced one column has nine positive paired changes,
        # but the -20m scenario brings its median back to zero.
        for ar,br in zip(a['ndvi'],b['ndvi']):
            for c,v in [(6,.2),(7,.4),(8,.6)]:ar[c]=v;br[c]=v+.2
        d=self.compare(a,b)
        ambiguous=[v for v in d['blocks'] if v['ndvi']['median']>=.1 and v['ndvi']['shift_scenarios']==9]
        self.assertTrue(ambiguous)
        self.assertTrue(all(not inspection(v,'ndvi') for v in ambiguous))

    def test_different_baselines_disable_inspection_but_preserve_mathematical_delta(self):
        b=sample(14);b['processing_baseline']='05.12';d=self.compare(sb=b)
        self.assertFalse(d['processing_comparable']);self.assertEqual(d['delta']['ndvi'][0][0],.2)
        self.assertEqual(d['statistics']['default_inspection_counts']['ndvi'],0)

    def test_landmark_delta_uses_common_pixels_not_two_unpaired_medians(self):
        a,b=grid(.2),grid(.4)
        c=int(np.floor(-WEST/20));r=int(np.floor(NORTH/20))
        a['ndvi'][r-1][c-1]=None;b['ndvi'][r+1][c+1]=None
        a['ndvi'][r+1][c+1]=.9;b['ndvi'][r-1][c-1]=.9
        d=self.compare(a,b)
        self.assertEqual(d['point']['paired_cells'],7);self.assertAlmostEqual(d['point']['ndvi'],.2)

    def test_real_archive_http_and_readonly_input_allowlist(self):
        from agro_checker.land_view import overview
        good=[s for s in overview()['satellite']['scenes'] if s['point_window_usable']]
        args={'period':'archive','before':good[0]['id'],'after':good[1]['id']}
        with tempfile.TemporaryDirectory() as tmp,make_server(Path(tmp),0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                before=server.field_store.records()
                d=json.load(urlopen(base+'/api/land/compare?'+urlencode(args)))
                self.assertEqual(d['before']['id'],good[0]['id']);self.assertEqual(d['after']['id'],good[1]['id'])
                self.assertFalse(d['orchard_boundary_known']);self.assertFalse(d['policy']['diagnoses_enabled'])
                for query,code in [('period=recent',422),('period=recent&before=x&after=y&extra=1',422),
                                   (urlencode({**args,'before':'../../config/working_zone.json'}),404),
                                   (urlencode({**args,'before':args['after']}),422)]:
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/api/land/compare?'+query)
                    self.assertEqual(e.exception.code,code)
                self.assertEqual(server.field_store.records(),before)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
