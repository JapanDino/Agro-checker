"""Cloudy/unknown observations stay unknown; publication never overwrites the base."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio

from agro_checker import sector_refresh as s
from agro_checker import farm_context_data as source
from agro_checker.open_land_data import reflectance,to_twenty,indices


class RefreshTest(unittest.TestCase):
    def test_explicit_timezone_future_and_analysed_version_rejected(self):
        with self.assertRaises(ValueError):s.instant('2026-11-09T12:00:00')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            with self.assertRaises(ValueError):
                s.initialise((datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),path)
            s.dump(path/'summary.json',{})
            with self.assertRaises(ValueError):s.initialise('2026-11-09T00:00:00Z',path)

    def test_catalogue_exhausts_empty_page_keeps_cloudy_and_rejects_foreign_link(self):
        import json
        p=source.read(source.D/'protocol.json')
        p['periods']=[dict(id=s.PERIOD,start='2026-11-01T00:00:00Z',end='2026-11-09T00:00:00Z')]
        item=dict(collection=p['collection'],id='S2B_T37TDK_20261108T083052_L2A',
                  properties=dict(datetime='2026-11-08T08:37:39Z',**{'eo:cloud_cover':100}))
        next_url='https://earth-search.aws.element84.com/v1/search?next=fixture'
        first=dict(features=[item],links=[dict(rel='next',href=next_url)])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(s,'curl_bytes',side_effect=[json.dumps(first).encode(),b'{"features":[],"links":[]}']):
                result=s.catalogue(p,Path(directory))
            self.assertEqual(len(result['pages']),2)
            self.assertEqual(len(result['items']),1)
            first['links'][0]['href']='https://unregistered.example/search'
            with patch.object(s,'curl_bytes',return_value=json.dumps(first).encode()) as fetch:
                with self.assertRaises(ValueError):s.catalogue(p,Path(directory))
                self.assertEqual(fetch.call_count,1)

    def test_failed_publish_preserves_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);version=root/'versions'/'fixture'
            s.dump(root/'current.json',dict(keep='previous'))
            before=(root/'current.json').read_bytes()
            s.dump(version/'summary.json',{})
            s.dump(version/'satellite.json',dict(counts=dict(extraction_error=1)))
            with self.assertRaises(ValueError):s.publish(version,root)
            self.assertEqual(before,(root/'current.json').read_bytes())

    def test_unpublished_version_does_not_change_reader(self):
        p,summ,scenes={'periods':[]},{'features':{}},{}
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(s.extend_archive(p,summ,scenes,Path(directory)),(p,summ,scenes))

    def test_same_item_and_grid_reuse_immutable_cache_without_remote_reads(self):
        old=s.DIRECTORY/'versions/2026-11-09'
        if not (old/'summary.json').exists():self.skipTest('Optional native cache absent')
        scenes=s.read(old/'satellite.json')['scenes']
        ready=next(v for v in scenes if v['status']=='ready')
        folder=old/'sentinel'/ready['id']
        if not (folder/'scl.tif').exists():self.skipTest('Native TIFF cache not needed in a fresh clone')
        outside=next(v for v in scenes if v['status']=='outside_full_grid')
        cat=s.read(old/'catalogue.json')
        subset={**cat,'items':[v for v in cat['items'] if v['item']['id'] in {ready['id'],outside['id']}]}
        before=s.sha(folder/'indices.npz')
        with tempfile.TemporaryDirectory() as directory,patch.object(s,'object_head',side_effect=AssertionError('No remote read for unchanged cache')):
            result=s.collect(s.read(old/'protocol.json')['periods'][0]['end'],Path(directory),prepared_catalogue=subset,cache_from=old)
            self.assertEqual(result['counts'],dict(ready=1,outside_full_grid=1,extraction_error=0))
            self.assertEqual(s.sha(Path(directory)/'sentinel'/ready['id']/'indices.npz'),before)
        self.assertEqual(s.sha(folder/'indices.npz'),before)

    def test_pointer_cannot_escape_version_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);s.dump(root/'current.json',dict(directory='../outside'))
            with self.assertRaises(ValueError):s.extend_archive({}, {}, {},root)

    def test_actual_new_pixels_reproduce_indices_and_cloud_gaps(self):
        directory=s.DIRECTORY/'versions/2026-11-09'
        if not (directory/'summary.json').exists():self.skipTest('Optional refreshed archive not installed')
        summary=s.read(directory/'summary.json')
        satellite=s.read(directory/'satellite.json')
        self.assertEqual(satellite['counts'],dict(ready=12,outside_full_grid=12,extraction_error=0))
        for scene in satellite['scenes']:
            if scene['status']!='ready':continue
            folder=directory/'sentinel'/scene['id']
            # Native TIFFs are a re-downloadable cache, not required by a fresh clone.
            if not all((folder/(key+'.tif')).exists() for key in ('red','nir','swir16','scl')):continue
            item=s.read(folder/'item.json');arrays={};valids=[]
            for key in ('red','nir','swir16','scl'):
                with rasterio.open(folder/(key+'.tif')) as raster:
                    value,valid=to_twenty(raster.read(1),int(20/raster.transform.a),raster.nodata)
                    arrays[key]=value if key=='scl' else reflectance(value,item['assets'][key],raster.scales[0],raster.offsets[0])
                    valids.append(valid)
            ndvi,ndmi,valid=indices(arrays['red'],arrays['nir'],arrays['swir16'],arrays['scl'],np.logical_and.reduce(valids))
            with np.load(folder/'indices.npz') as saved:
                np.testing.assert_array_equal(saved['valid'],valid)
                np.testing.assert_allclose(saved['ndvi'],ndvi.astype('float32'),equal_nan=True)
                np.testing.assert_allclose(saved['ndmi'],ndmi.astype('float32'),equal_nan=True)
        for feature in summary['features'].values():
            for row in feature['history']:
                if row['source_status']!='ready':
                    self.assertIsNone(row['ndmi']);continue
                if row['coverage']<.8:self.assertIsNone(row['ndmi']['base'])
        r08=summary['features']['R08']['availability'][s.PERIOD]
        self.assertEqual(r08['base_usable'],0)
        self.assertIsNone(r08['latest_usable_acquisition_utc'])

    def test_published_extension_preserves_frozen_periods(self):
        if not (s.DIRECTORY/'current.json').exists():self.skipTest('No published extension')
        from agro_checker import farm_scouting as scout
        p=s.read(source.D/'protocol.json');summ=s.read(source.D/'summary.json')
        summ['_scouting_version']=s.sha(source.D/'summary.json')
        scenes={v['id']:v for v in s.read(source.D/'satellite.json')['scenes']}
        originals=deepcopy((p,summ,scenes))
        extended=s.extend_archive(p,summ,scenes)
        self.assertEqual((p,summ,scenes),originals)
        self.assertEqual(extended[0]['periods'][:2],p['periods'])
        for fid,feature in summ['features'].items():
            self.assertEqual(extended[1]['features'][fid]['history'][:len(feature['history'])],feature['history'])
        overview=scout.overview()
        latest_ready = max(scene['acquired_at_utc'] for scene in extended[2].values() if scene['status']=='ready')
        self.assertEqual(overview['snapshot_at'],latest_ready)
        self.assertFalse(overview['model_training_enabled'])
