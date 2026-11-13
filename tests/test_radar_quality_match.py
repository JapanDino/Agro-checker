"""Run with bundled document Python if pypdf is absent from the frozen model environment."""
import unittest
try:
    from scripts import match_radar_quality as q
except ImportError:
    raise unittest.SkipTest('Use bundled document Python for optional PDF matcher checks')

SCENE='S1A_IW_GRDH_1SDV_20240914T152818_20240914T152843_055663_06CBF7_rtc'


class MatchingTests(unittest.TestCase):
    def test_exact_grd_acquisition_matches_without_asserting_crc_identity(self):
        self.assertEqual(q.relation(SCENE,SCENE.removesuffix('_rtc')+'_498F'),'same_grd_acquisition_template')

    def test_official_wildcards_and_slc_slice_time_difference_are_supported(self):
        self.assertIsNotNone(q.relation(SCENE,'S1A_IW_*SDV_20240914T152817_20240914T152844_055663_06CBF7_*'))
        self.assertIsNotNone(q.relation(SCENE,'S1A_IW_*_*DV_20240914T152817_20240914T152844_055663_06CBF7_*'))

    def test_different_datatake_or_nonoverlapping_slice_cannot_match(self):
        self.assertIsNone(q.relation(SCENE,'S1A_IW_SLC__1SDV_20240914T152817_20240914T152844_055663_06CBF8_ABCD'))
        self.assertIsNone(q.relation(SCENE,'S1A_IW_SLC__1SDV_20240914T152900_20240914T152927_055663_06CBF7_ABCD'))

    def test_actual_known_september_match_is_traceable_to_pdf_page(self):
        risk=q.read(q.Q/'scene-risk.json');row=next(v for v in risk['scenes'] if v['id']==SCENE)
        self.assertTrue(any(v['page']==19 and v['relation']=='same_grd_acquisition_template' for v in row['matches']))
        self.assertEqual(q.sha(q.Path(q.__file__)),q.read(q.Q/'matching-protocol.json')['code_sha256'])


if __name__=='__main__':unittest.main()
