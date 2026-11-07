"""Synthetic entries and image patterns only in disposable test stores."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import time
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image
from agro_checker import land_inspections as j
from agro_checker.field_store import FieldStore
from agro_checker.field_server import make_server
from agro_checker.land_view import overview
from agro_checker.land_change import comparison

NOW=datetime(2026,10,18,18,tzinfo=timezone.utc)


def pattern(fmt='PNG'):
    b=io.BytesIO();Image.new('RGB',(8,8),'white').save(b,format=fmt)
    return {'filename':'TEST-pattern.'+fmt.lower(),'data_base64':base64.b64encode(b.getvalue()).decode()},b.getvalue()


class LandInspectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        good=[s for s in overview()['satellite']['scenes'] if s['point_window_usable']]
        cls.comparison=comparison('archive',good[-2]['id'],good[-1]['id'])
        cls.reference=j.reference_for(cls.comparison,'archive')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        expected=Path(self.tmp.name).resolve()
        def cleanup():
            # Windows scanners can briefly retain freshly verified media files.
            # Retry bounded cleanup, without ignoring a persistent failure.
            assert Path(self.tmp.name).resolve()==expected
            for attempt in range(5):
                try:self.tmp.cleanup();return
                except OSError:
                    if attempt==4:raise
                    time.sleep(.05*(attempt+1))
        self.addCleanup(cleanup)
        self.store=FieldStore(Path(self.tmp.name))
        block=next(b for b in self.comparison['blocks'] if b['paired_cells']==9)
        self.raw={'request_id':uuid4().hex,'reference':deepcopy(self.reference),'window_id':block['id'],
                  'metric':'ndvi','threshold':.1,'actor':'TEST — не реальный осмотр','observed_at':'2026-10-18T12:00:00+03:00',
                  'notes':'Техническая фикстура в отдельном временном журнале.','photos':[]}

    def test_initial_journal_empty_read_has_no_side_effects(self):
        self.assertEqual(j.listing(self.store),[])
        self.assertFalse((self.store.directory/'land-inspection-contexts').exists())
        self.assertEqual(self.store.records(),[])

    def test_observation_is_pinned_and_not_a_forecast_score_or_diagnosis(self):
        record=j.save(self.store,self.raw,NOW);p=record['payload']
        self.assertEqual(p['selected_window'],next(w for w in self.comparison['blocks'] if w['id']==self.raw['window_id']))
        self.assertEqual(p['observed_at_utc'],'2026-10-18T09:00:00+00:00')
        self.assertIsNone(p['automatic_diagnosis']);self.assertFalse(p['forecast_scoring_eligible']);self.assertFalse(p['model_training_eligible'])
        self.assertEqual(self.store.records('inspection'),[])
        self.assertEqual(self.store.records('forecast'),[])
        with patch.object(j,'resolve',side_effect=ValueError('new catalogue unavailable')):
            old=j.saved_comparison(self.store,record['id'])
        self.assertEqual(old['before'],self.comparison['before']);self.assertEqual(old['after'],self.comparison['after'])

    def test_photo_bytes_and_mime_preserved_and_bound_to_record(self):
        for fmt in ['PNG','JPEG','WEBP']:
            photo,raw=pattern(fmt);self.raw['request_id']=uuid4().hex;self.raw['photos']=[photo]
            record=j.save(self.store,self.raw,NOW);saved=record['payload']['photos'][0]
            data,mime=j.saved_photo(self.store,record['id'],saved['id'])
            self.assertEqual(data,raw);self.assertEqual(mime,j.MIME[fmt])
            self.assertFalse(saved['photograph_time_or_gps_verified'])
            with self.assertRaises(KeyError):j.saved_photo(self.store,record['id'],'0'*64)

    def test_retry_is_idempotent_and_does_not_overwrite_previous_record(self):
        self.raw['photos']=[pattern()[0]]
        first=j.save(self.store,self.raw,NOW);second=j.save(self.store,self.raw,NOW)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(j.listing(self.store)),1)
        self.assertEqual(len(list((self.store.directory/'land-inspection-media').glob('*.bin'))),1)
        changed=deepcopy(self.raw);changed['notes']='changed'
        with self.assertRaises(ValueError):j.save(self.store,changed,NOW)
        self.assertEqual(j.listing(self.store)[0]['payload']['notes'],self.raw['notes'])

    def test_invalid_or_future_entries_never_create_observations(self):
        variants=[{'actor':''},{'notes':''},{'observed_at':'2027-01-01T00:00:00Z'},
                  {'observed_at':'2026-10-18T12:00:00'},{'window_id':'w999-999'},
                  {'metric':'diagnosis'},{'threshold':True}]
        for updates in variants:
            with self.subTest(updates=updates),self.assertRaises((ValueError,KeyError)):
                j.save(self.store,{**self.raw,**updates},NOW)
        self.assertEqual(j.listing(self.store),[])

    def test_client_cannot_replace_coordinates_or_satellite_version(self):
        wrong=deepcopy(self.raw);wrong['reference']['manifest_sha256']='0'*64
        with self.assertRaises(ValueError):j.save(self.store,wrong,NOW)
        wrong=deepcopy(self.raw);wrong['reference']['comparison_source_sha256']='0'*64
        with self.assertRaises(ValueError):j.save(self.store,wrong,NOW)
        with self.assertRaises(ValueError):j.save(self.store,{**self.raw,'latitude':0},NOW)
        self.assertEqual(self.store.records(),[])

    def test_bad_media_rejected_before_any_record_or_context_write(self):
        bad={'filename':'TEST.svg','data_base64':base64.b64encode(b'<svg onload="alert(1)"/>').decode()}
        variants=[[bad],[{'filename':'x','data_base64':'!invalid'}],[pattern()[0]]*4,[pattern()[0]]*2]
        for photos in variants:
            with self.subTest(count=len(photos)),self.assertRaises(ValueError):j.save(self.store,{**self.raw,'photos':photos},NOW)
        self.assertEqual(j.listing(self.store),[]);self.assertFalse((self.store.directory/'land-inspection-contexts').exists())

    def test_photo_limits_checked_before_decode_or_save(self):
        with patch.object(j,'MAX_PHOTO_BYTES',10),self.assertRaises(ValueError):j.photo_input(pattern()[0])
        b=io.BytesIO();Image.new('1',(8000,5000)).save(b,format='PNG')
        with self.assertRaisesRegex(ValueError,'32 мегапикселей'):
            j.photo_input({'filename':'TEST-large.png','data_base64':base64.b64encode(b.getvalue()).decode()})

    def test_saved_files_tamper_is_detected_without_changing_record(self):
        self.raw['photos']=[pattern()[0]];r=j.save(self.store,self.raw,NOW)
        photo=r['payload']['photos'][0];p=self.store.directory/'land-inspection-media'/(photo['id']+'.bin');p.write_bytes(b'changed')
        with self.assertRaises(ValueError):j.saved_photo(self.store,r['id'],photo['id'])
        p=self.store.directory/'land-inspection-contexts'/(r['payload']['comparison_artifact_sha256']+'.json');p.write_text('{}')
        with self.assertRaises(ValueError):j.saved_comparison(self.store,r['id'])
        self.assertEqual(self.store.records(j.KIND)[0]['sha256'],r['sha256'])

    def test_recent_reference_loads_requested_version_not_latest_pointer(self):
        d=deepcopy(self.comparison);d['observation_version']='a'*32
        a,b=d['before'],d['after'];a['map_key']='a'*64;b['map_key']='b'*64
        snapshot={'id':'a'*32,'reference':d['reference'],'scenes':[dict(a,point_window_usable=True),dict(b,point_window_usable=True)]}
        reference={**self.reference,'period':'recent','before':'a'*64,'after':'b'*64,'observation_version':'a'*32,'manifest_sha256':'b'*64}
        with patch('agro_checker.sentinel_updates._load',return_value=(snapshot,{'a'*64:d['maps']['before'],'b'*64:d['maps']['after']})) as load,patch('agro_checker.sentinel_updates.load_latest',side_effect=AssertionError('Must not follow latest')):
            resolved=j.resolve(reference)
        self.assertEqual(resolved['observation_version'],'a'*32);self.assertEqual(load.call_args.args[1:],(reference['observation_version'],reference['manifest_sha256']))

    def test_http_token_photo_roundtrip_and_persistent_journal(self):
        with make_server(self.store.directory,0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                self.raw['photos']=[pattern()[0]];body=json.dumps(self.raw).encode()
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/land/inspections',data=body))
                self.assertEqual(e.exception.code,403)
                status=json.load(urlopen(base+'/api/land/status'))
                headers={'Content-Type':'application/json','X-Field-Token':status['token']}
                r=json.load(urlopen(Request(base+'/api/land/inspections',data=body,headers=headers)))
                items=json.load(urlopen(base+'/api/land/inspections'))['items'];self.assertEqual(items[0]['id'],r['id'])
                url=base+'/api/land/inspections/'+r['id']+'/photos/'+r['payload']['photos'][0]['id']
                with urlopen(url) as image:
                    self.assertEqual(image.headers['Content-Type'],'image/png');self.assertEqual(image.headers['Cross-Origin-Resource-Policy'],'same-origin')
                    self.assertEqual(image.read(),pattern()[1])
                with self.assertRaises(HTTPError) as e:urlopen(Request(url,headers={'Origin':'https://external.invalid'}))
                self.assertEqual(e.exception.code,403)
                self.assertEqual(json.load(urlopen(base+'/api/land/inspections/export'))['items'][0]['id'],r['id'])
                saved=json.load(urlopen(base+'/api/land/inspections/'+r['id']+'/comparison'));self.assertEqual(saved['after'],self.comparison['after'])
                history=json.load(urlopen(base+'/api/land/inspections/'+r['id']+'/history'))
                self.assertEqual([v['id'] for v in history['visits']],[r['id']]);self.assertEqual(len(history['scenes']),36)
                self.assertEqual(len(json.load(urlopen(base+'/api/land/inspections'))['places']),1)
                self.assertEqual(len(j.listing(FieldStore(self.store.directory))),1)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
