import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from agro_checker import continuation_shape as m
from tests.test_continuation_recovery import example,protocol


class ShapeTests(unittest.TestCase):
    def test_no_base_score_or_label_in_direct_model(self):
        a=example();b=copy.deepcopy(a)
        b.update(raw_score=[0,1],y=[-1,-1],audit={'future':100},target='2026-12-31')
        b['continuation'][18:20]=[-100,100]
        for family in m.FAMILIES[1:]:
            np.testing.assert_array_equal(m.matrix([a],family),m.matrix([b],family))

    def test_direct_logistic_supports_constant_flags_without_nan(self):
        rr=[example(i,i%2==1) for i in range(1,9)]
        for i,r in enumerate(rr):
            r['continuation']=[0.]*28;r['continuation'][2]=float(i%2)
            r['continuation'][22]=1.;r['continuation'][24]=.01
        model=m.fit_filter(rr,0,'temporal_logit',protocol())
        expected=np.average(m.matrix(rr,'temporal_logit'),axis=0)
        np.testing.assert_allclose(model['mean'],expected)
        self.assertTrue(np.isfinite(model['scale']).all())
        self.assertTrue(np.isfinite(m.score_filter(model,rr)).all())
        self.assertEqual(model['scale'][20],1.)

    def test_future_years_and_reserve_rejected(self):
        for year in [2025,2026]:
            r=example();r['issue']=f'{year}-06-01';r['target']=f'{year}-06-08'
            with self.assertRaisesRegex(ValueError,'2024'):m.fit_filter([r],0,'pixels_extra',protocol())
        r=example();r['fid']='R23'
        with self.assertRaisesRegex(ValueError,'reserve'):m.fit_filter([r],0,'pixels_extra',protocol())

    def test_full_season_experiment_cannot_refit_after_freeze(self):
        with TemporaryDirectory() as folder:
            path=Path(folder);(path/'frozen-selection.json').write_text('{}')
            with patch.object(m,'D',path):
                with self.assertRaisesRegex(ValueError,'cannot be retuned'):m.fit()


if __name__=='__main__':unittest.main()
