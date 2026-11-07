"""Boundary sensitivity, actual source integrity, missingness and safe delivery."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tmp/georef-deps'))
from shapely.geometry import box
from shapely import transform
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from agro_checker.farm_context_analysis import scenarios,metric,spectral_stats,soil_weights,weighted
from agro_checker.farm_context_data import D,R,SOIL_CRS,read,digest


class FarmContextTest(unittest.TestCase):
    def test_radial_shifts_and_core_are_metric(self):
        p=box(0,0,500,500);v=dict(scenarios(p,100))
        self.assertEqual(len(v),11)
        for key in ['N','NE','E','SE','S','SW','W','NW']:self.assertAlmostEqual(v[key].centroid.distance(p.centroid),100)
        self.assertAlmostEqual(v['core'].area,300*300)
        self.assertTrue(v['outer'].contains(p))

    def test_no_values_for_clouds_or_inadequate_support(self):
        a=np.full((4,5),.4);ix=np.arange(20)
        self.assertIsNone(spectral_stats(a,np.zeros_like(a,dtype=bool),ix)['value'])
        self.assertIsNone(spectral_stats(a,np.ones_like(a,dtype=bool),np.arange(9))['value'])
        valid=np.ones_like(a,dtype=bool);valid.ravel()[:5]=False
        self.assertFalse(spectral_stats(a,valid,ix)['usable'])
        valid.ravel()[4]=True
        self.assertTrue(spectral_stats(a,valid,ix)['usable'])
        self.assertIsNone(spectral_stats(a,valid,np.array([],dtype=int))['value'])

    def test_stability_requires_all_shifts_and_supported_core(self):
        values={k:.4 for k in ['base','N','NE','E','SE','S','SW','W','NW','core','outer']}
        self.assertTrue(metric(values,.05)['robust'])
        self.assertFalse(metric(values,.05,core_ok=False)['robust'])
        values['W']=None
        self.assertFalse(metric(values,.05)['robust'])
        self.assertFalse(metric(values,.05)['all_shifts_available'])
        values['W']=.4;values['outer']=.6
        self.assertTrue(metric(values,.05)['shift_stable'])
        self.assertFalse(metric(values,.05)['robust'])

    def test_soil_uses_fractional_native_cells(self):
        to_utm=Transformer.from_crs(SOIL_CRS,32637,always_xy=True)
        native=box(3976375,5001000,3976625,5001250)
        polygon=transform(native,to_utm.transform,interleaved=False)
        with MemoryFile() as file:
            with file.open(driver='GTiff',width=2,height=1,count=1,dtype='float32',crs=SOIL_CRS,transform=from_origin(3976250,5001250,250,250)) as r:
                ix,w=soil_weights(polygon,r)
        self.assertEqual(len(ix),2)
        self.assertAlmostEqual(w[0]/w.sum(),.5,places=5)
        self.assertAlmostEqual(weighted(np.array([[20.,40.]]),ix,w),30,places=4)
        self.assertIsNone(weighted(np.array([[np.nan,40.]]),ix,w))

    def test_frozen_provenance_and_all_source_crops(self):
        summary=read(D/'summary.json')
        self.assertEqual(summary['provenance']['contours_sha256'],digest(R/'approximate-areas.geojson'))
        self.assertEqual(summary['provenance']['analysis_sha256'],digest(ROOT/'agro_checker/farm_context_analysis.py'))
        for m in read(D/'soil/manifest.json')['layers']:
            self.assertEqual(digest(ROOT/m['path']),m['source']['sha256'])
        for m in read(D/'dem/manifest.json')['sources']:
            self.assertEqual(digest(ROOT/m['path']),m['source']['sha256'])
        for page in read(D/'catalogue.json')['pages']:
            self.assertTrue(page['url'].startswith('https://earth-search.aws.element84.com/v1/search?'))
        for scene in read(D/'satellite.json')['scenes']:
            if scene['status']!='ready':continue
            for name,h in scene['crop_sha256'].items():self.assertEqual(digest(D/'sentinel'/scene['id']/name),h)
        self.assertFalse(summary['summary']['used_by_model'])
        self.assertFalse(summary['protocol']['confirmed_boundary'])

    def test_actual_records_retain_missing_dates_and_separate_baselines(self):
        out=read(D/'summary.json');self.assertEqual(len(out['features']),27)
        for feature in out['features'].values():
            self.assertEqual(len(feature['history']),184)
            self.assertEqual(feature['availability']['spring2024']['full_extent_calendar_days'],36)
            self.assertEqual(feature['availability']['recent2026']['full_extent_calendar_days'],45)
            for av in feature['availability'].values():
                self.assertLessEqual(av['base_usable_calendar_days'],av['base_usable'])
                self.assertLessEqual(av['robust_both_calendar_days'],av['robust_both_indices'])
            for row in feature['history']:
                if row['source_status']=='outside_full_grid':self.assertIsNone(row['ndvi']);continue
                if row.get('coverage',0)<.8:self.assertIsNone(row['ndvi']['base'])
            pair=next(c for c in feature['changes'] if c['period']=='recent2026')
            self.assertFalse(pair['same_processing_baseline'])
            self.assertEqual(pair['ndvi']['direction'],'uncertain')
            window=pair['same_baseline_comparison'];self.assertTrue(window['same_processing_baseline'])
            self.assertEqual(window['before_baseline'],window['after_baseline'])
        self.assertIn('R18',out['summary']['no_satellite_core'])
        self.assertEqual(out['features']['R18']['availability']['recent2026']['robust_both_indices'],0)
        self.assertFalse(out['features']['R25']['dsm']['height']['robust'])

    def test_actual_pair_uses_identical_clear_pixels(self):
        from shapely.geometry import shape
        from rasterio.features import geometry_mask
        out=read(D/'summary.json');p=out['protocol'];pair=out['features']['R25']['changes'][-1]['same_baseline_comparison']
        f=next(f for f in read(R/'approximate-areas.geojson')['features'] if f['properties']['id']=='R25')
        poly=transform(shape(f['geometry']),Transformer.from_crs(4326,32637,always_xy=True).transform,interleaved=False).buffer(-np.sqrt(200))
        x0,y0,x1,y1=p['bounds_utm'];sh=(int((y1-y0)/20),int((x1-x0)/20))
        mask=geometry_mask([poly],out_shape=sh,transform=from_origin(x0,y1,20,20),invert=True)
        with np.load(D/'sentinel'/pair['before_id']/'indices.npz') as a,np.load(D/'sentinel'/pair['after_id']/'indices.npz') as b:
            common=mask&a['valid']&b['valid']
            self.assertAlmostEqual(common.sum()/mask.sum(),pair['common_coverage'])
            self.assertAlmostEqual(float(np.median((b['ndvi']-a['ndvi'])[common])),pair['ndvi']['base'])


if __name__=='__main__':unittest.main()
