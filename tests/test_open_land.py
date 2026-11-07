import unittest
import numpy as np
from agro_checker import open_land_data as data

XML = b'''<root xmlns:g="http://www.opengis.net/gml/3.2"><g:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/152160"/><g:origin><g:Point><g:pos>-19949625 8360875</g:pos></g:Point></g:origin><g:offsetVector>250 0</g:offsetVector><g:offsetVector>0 -250</g:offsetVector></root>'''


class OpenLandTests(unittest.TestCase):
    def test_reference_is_not_invented_orchard(self):
        p=data.protocol(); self.assertFalse(p['orchard_boundary_known'])
        self.assertIn('NOT orchard',p['reference']['role'])

    def test_soil_window_aligns_native_grid_and_contains_point(self):
        w=data.soil_window(XML,37.911914,45.024106,9); left,bottom,right,top=w['bbox']
        self.assertAlmostEqual((left+19949750)%250,0)
        self.assertAlmostEqual((8361000-top)%250,0)
        self.assertEqual(right-left,2250); self.assertEqual(top-bottom,2250)
        self.assertTrue(left+4*250<=w['x']<left+5*250)
        self.assertTrue(top-5*250<w['y']<=top-4*250)

    def test_unknown_grid_not_silently_reprojected_or_resampled(self):
        with self.assertRaises(ValueError): data.soil_window(XML.replace(b'250 0',b'100 0'),37.9,45.)
        with self.assertRaises(ValueError): data.soil_window(XML,37.9,45.,8)
        with self.assertRaises(ValueError): data.soil_window(XML.replace(b'152160',b'4326'),37.9,45.)

    def test_units_missing_sentinels_and_ph_bounds(self):
        self.assertEqual(data.soil_value('clay',335),33.5)
        self.assertEqual(data.soil_value('soc',210),21.)
        self.assertEqual(data.soil_value('phh2o',72),7.2)
        for prop,v in [('clay',-32768),('clay',1001),('phh2o',9999),('soc',float('nan'))]:
            with self.assertRaises(ValueError): data.soil_value(prop,v)

    def test_explicit_scale_offset_applied_once(self):
        asset={'raster:bands':[{'scale':.0001,'offset':-.1}]}
        self.assertAlmostEqual(data.reflectance(np.array([2224]),asset,.0001,-.1)[0],.1224)
        with self.assertRaises(ValueError): data.reflectance(np.array([2224]),asset,1.,0.)

    def test_nodata_child_invalidates_20m_average(self):
        a=np.array([[10,20,30,40],[10,20,30,40],[0,20,30,40],[10,20,30,40]])
        result,valid=data.to_twenty(a,2,0)
        self.assertTrue(np.array_equal(result,np.array([[15,35],[12.5,35]])))
        self.assertFalse(valid[1,0]); self.assertTrue(valid[0,0])
        with self.assertRaises(ValueError): data.to_twenty(np.ones((3,3)),2,0)

    def test_cloud_neighbour_rejects_centre_and_does_not_fill(self):
        red=np.full((7,7),.2); nir=np.full((7,7),.6); swir=np.full((7,7),.3); scl=np.full((7,7),4)
        scl[3,4]=9
        ndvi,ndmi,valid=data.indices(red,nir,swir,scl,np.ones((7,7),dtype=bool))
        self.assertFalse(valid[3,3]); self.assertTrue(np.isnan(ndvi[3,3])); self.assertFalse(valid[0,0])
        self.assertAlmostEqual(ndvi[1,1],.5); self.assertAlmostEqual(ndmi[1,1],1/3)

    def test_water_snow_and_nonpositive_reflectance_not_land_stress(self):
        a=np.ones((7,7)); scl=np.full((7,7),6)
        self.assertFalse(data.indices(a,a,a,scl,a.astype(bool))[2].any())
        scl[:]=11
        self.assertFalse(data.indices(a,a,a,scl,a.astype(bool))[2].any())
        scl[:]=4; red=a.copy(); red[3,3]=0
        self.assertFalse(data.indices(red,a,a,scl,a.astype(bool))[2][3,3])


if __name__=='__main__': unittest.main()
