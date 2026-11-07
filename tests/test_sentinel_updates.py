import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agro_checker import sentinel_updates as u
from agro_checker.field_server import make_server


def feature(day='20261016', hour='08:37:49Z'):
    return {'id':f'S2A_T37TDK_{day}T082843_L2A','collection':'sentinel-2-c1-l2a',
            'properties':{'datetime':f'{day[:4]}-{day[4:6]}-{day[6:]}T{hour}','eo:cloud_cover':100,'s2:processing_baseline':'05.13'}}


class SentinelUpdatesTest(unittest.TestCase):
    def test_pagination_retains_cloudy_and_duplicate_scenes_once(self):
        cfg=u.read(u.CONFIG);end=u.instant('2026-10-18T00:00:00Z');start=u.instant('2026-10-01T00:00:00Z')
        calls=[]
        def fetch(url):
            calls.append(url)
            return json.dumps({'features':[feature()] if len(calls)==1 else [],'links':[{'rel':'next','href':cfg['catalogue']+'?page=2','method':'GET'}] if len(calls)==1 else []}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            cat=u.catalogue(cfg,start,end,Path(tmp),fetch)
            self.assertEqual(len(cat['items']),1);self.assertEqual(len(cat['pages']),2)
            self.assertEqual(cat['items'][0]['properties']['eo:cloud_cover'],100)
            self.assertEqual(len(list(Path(tmp).glob('catalogue-page-*.json'))),2)
            self.assertTrue(cat['pagination_completed'])

    def test_future_dates_naive_dates_cycles_and_external_next_are_rejected(self):
        cfg=u.read(u.CONFIG);end=u.instant('2026-10-18T00:00:00Z');start=u.instant('2026-10-01T00:00:00Z')
        with self.assertRaises(ValueError):u.instant('2026-10-16T08:00:00')
        bad=[{'features':[feature('20261019')],'links':[]},
             {'features':[feature()],'links':[{'rel':'next','href':'https://external.invalid/v1/search'}]},
             {'features':[feature()],'links':[{'rel':'next','href':cfg['catalogue']}]}]
        for obj in bad:
            with self.subTest(obj=obj),tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):
                u.catalogue(cfg,start,end,Path(tmp),lambda _:json.dumps(obj).encode())

    def test_source_urls_are_fixed_not_arbitrary_user_urls(self):
        for value in ['http://earth-search.aws.element84.com/v1/search','https://localhost/v1/search','https://earth-search.aws.element84.com.evil/v1/search']:
            with self.assertRaises(ValueError):u.checked_url(value,'catalogue')
        with self.assertRaises(ValueError):u.checked_url('https://earth-search.aws.element84.com/v1/search','asset')

    def test_cloud_and_technical_errors_are_different_and_empty_stats_safe(self):
        bad=u.failed_scene(feature());cloud={**bad,'extraction_status':'ready','point_scl':9,'valid_land_cells_in_point_60m_window':0}
        stats=u.statistics([cloud,bad]);self.assertEqual(stats['extraction_error_dates'],1)
        self.assertEqual(stats['rejected_point_dates'],1);self.assertIsNone(stats['latest_usable_at_utc'])
        self.assertIsNone(stats['longest_gap_between_usable_acquisitions_days'])
        self.assertIsNone(u.statistics([])['latest_acquisition_at_utc'])
        self.assertIsNone(bad['point_60m_ndmi_median']);self.assertIsNone(bad['valid_land_cells_in_point_60m_window'])

    def test_catalogue_failure_never_replaces_published_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);u.dump(directory/'latest.json',{'run':'previous'})
            before=(directory/'latest.json').read_bytes()
            with patch.object(u,'catalogue',side_effect=OSError('offline')),self.assertRaises(OSError):u.refresh(directory)
            self.assertEqual((directory/'latest.json').read_bytes(),before)

    def test_partial_publication_keeps_failed_acquisition_and_atomic_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);f=feature()
            with patch.object(u,'catalogue',return_value={'items':[f],'pages':[],'pagination_completed':True}),patch.object(u,'extract',side_effect=OSError('offline')):
                snapshot=u.refresh(directory,now=u.instant('2026-10-18T00:00:00Z'))
            loaded,maps=u.load_latest(directory)
            self.assertEqual(loaded,snapshot);self.assertEqual(maps,{})
            self.assertEqual(loaded['scenes'][0]['id'],f['id']);self.assertEqual(loaded['statistics']['extraction_error_dates'],1)
            run=directory/'runs'/loaded['id'];(run/'snapshot.json').write_text('{}')
            u._load.cache_clear()
            with self.assertRaises(ValueError):u.load_latest(directory)

    def test_native_grid_mismatch_never_publishes_a_valid_object(self):
        import rasterio
        from agro_checker import open_land_data
        f=open_land_data.read(open_land_data.DIRECTORY/'catalogue.json')['items'][0]
        actual_open=rasterio.open
        def local_open(path,*args,**kwargs):
            if str(path).startswith('https:'):
                key=next(k for k,a in f['assets'].items() if a['href']==path)
                return actual_open(open_land_data.DIRECTORY/'sentinel'/f['id']/(key+'.tif'))
            return actual_open(path,*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp,patch.object(u,'head',return_value={'etag':'version'}),patch('rasterio.open',side_effect=local_open):
            with self.assertRaisesRegex(ValueError,'CRS/shape/transform'):u.extract(f,u.read(u.CONFIG),Path(tmp),'test')
            self.assertFalse((Path(tmp)/'objects').exists())

    def test_changed_remote_version_during_crop_is_not_published(self):
        from copy import deepcopy
        import rasterio
        from agro_checker import open_land_data
        f=deepcopy(open_land_data.read(open_land_data.DIRECTORY/'catalogue.json')['items'][0])
        actual_open=rasterio.open
        paths={key:open_land_data.DIRECTORY/'sentinel'/f['id']/(key+'.tif') for key in ['scl','red','nir','swir16']}
        for key,path in paths.items():
            with actual_open(path) as r:
                f['assets'][key]['proj:shape']=list(r.shape)
                f['assets'][key]['proj:transform']=list(r.transform)[:6]
        def local_open(path,*args,**kwargs):
            if str(path).startswith('https:'):
                key=next(k for k in paths if f['assets'][k]['href']==path)
                return actual_open(paths[key])
            return actual_open(path,*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp,patch.object(u,'head',side_effect=[{'etag':'old'},{'etag':'new'}]),patch('rasterio.open',side_effect=local_open):
            with self.assertRaisesRegex(ValueError,'Remote asset changed'):u.extract(f,u.read(u.CONFIG),Path(tmp),'test')
            self.assertFalse((Path(tmp)/'objects').exists())

    def test_refresh_api_requires_token_deduplicates_running_job_and_leaves_field_unchanged(self):
        started=Event();release=Event();calls=[]
        def work(progress):
            calls.append(1);started.set();release.wait(4);return {'id':'test','statistics':{'extraction_error_dates':0}}
        with tempfile.TemporaryDirectory() as tmp,make_server(Path(tmp),0) as server,patch.object(u,'refresh',side_effect=work):
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                before=server.field_store.records()
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/land/refresh',data=b'{}'))
                self.assertEqual(e.exception.code,403)
                status=json.load(urlopen(base+'/api/land/status'));headers={'X-Field-Token':status['token'],'Content-Type':'application/json'}
                request=lambda:json.load(urlopen(Request(base+'/api/land/refresh',data=b'{}',headers=headers)))
                first=request();self.assertTrue(started.wait(2));second=request()
                self.assertEqual(first['job']['id'],second['job']['id']);self.assertEqual(len(calls),1)
                release.set()
                for _ in range(30):
                    if json.load(urlopen(base+'/api/land/status'))['job']['status']=='completed':break
                    time.sleep(.02)
                self.assertEqual(server.field_store.records(),before)
            finally:release.set();server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
