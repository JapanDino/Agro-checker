import json
from pathlib import Path
import unittest
from agro_checker.product_domain import threshold_order_valid
from agro_checker.pipeline import ROOT


class DomainTests(unittest.TestCase):
    def test_colder_threshold_cannot_have_greater_probability(self):
        self.assertTrue(threshold_order_valid({'0':{'probability':.5},'-2':{'probability':.1},'-1':{'probability':.3}}))
        self.assertFalse(threshold_order_valid({'0':{'probability':.5},'-2':{'probability':.4},'-1':{'probability':.3}}))

    def test_invalid_probability_and_empty_registry_are_refused(self):
        for p in [None,True,float('nan'),float('inf'),-1,2]:self.assertFalse(threshold_order_valid({'0':{'probability':p}}))
        self.assertFalse(threshold_order_valid({}))

    def test_raw_failed_control_predictions_remain_in_snapshot(self):
        file=ROOT/'data/processed/product-risk/night-forecasts.json'
        if not file.exists():self.skipTest('Run frozen experiment')
        frames=json.loads(file.read_text(encoding='utf-8'));failed=[f for f in frames if not threshold_order_valid(f['threshold_forecasts'])]
        self.assertEqual(len(frames),184);self.assertEqual(len(failed),3)
        self.assertTrue(all(f['legacy_unchanged'] for f in failed))


if __name__=='__main__':unittest.main()
