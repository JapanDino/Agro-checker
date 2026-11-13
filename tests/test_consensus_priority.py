"""Technical chooser fixtures are not agronomist observations."""
from copy import deepcopy
import unittest
import numpy as np

from scripts import experiment_consensus_priority as e


class ConsensusTest(unittest.TestCase):
    def fixture(self):
        rows=[dict(fid=f'R{i:02d}',issue='2024-07-01',quality=dict(all_boundaries_supported=True),y=None,target=None)
              for i in range(1,7)]
        records=dict(recent_drop=np.array([.3,.15,.1,.08,.07,.06]),
                     logit=np.array([.8,.3,.1,.9,.4,.2]),extra=np.array([.8,.3,.1,.9,.4,.2]))
        p=dict(candidates=e.candidates(),budget_k=3,fixed_parameters=dict(seed=20241021))
        return rows,records,p

    def test_at_most_one_swap_same_budget_and_strong_anchor(self):
        rows,scores,p=self.fixture()
        baseline=e.choose(rows,scores,p['candidates'][0],p)
        chosen=e.choose(rows,scores,p['candidates'][-2],p)
        self.assertEqual(sum(chosen),3)
        self.assertEqual(sum(chosen!=baseline),2)
        self.assertTrue(chosen[0])

    def test_disagreement_blocks_replacement(self):
        rows,scores,p=self.fixture()
        scores['logit']=scores['recent_drop']
        chosen=e.choose(rows,scores,p['candidates'][-2],p)
        np.testing.assert_array_equal(chosen,e.choose(rows,scores,p['candidates'][0],p))

    def test_changing_future_labels_cannot_change_choices(self):
        rows,scores,p=self.fixture();changed=deepcopy(rows)
        for r in changed:r.update(y=[-.9,-.9],target='2026-12-31',target_cloud=100)
        np.testing.assert_array_equal(e.choose(rows,scores,p['candidates'][-2],p),
                                      e.choose(changed,scores,p['candidates'][-2],p))

    def test_unknown_scores_rejected_not_zero_filled(self):
        rows,scores,p=self.fixture();scores['logit'][0]=np.nan
        with self.assertRaises(ValueError):e.choose(rows,scores,p['candidates'][-2],p)

    def test_development_artifact_has_no_promotion_or_test_years(self):
        if not (e.D/'report.json').exists():self.skipTest('Experiment not installed')
        report=e.read(e.D/'report.json')
        self.assertEqual(report['selection_years'],[2024])
        self.assertEqual(report['test_years_evaluated'],[])
        self.assertEqual(report['selected'],dict(id='baseline'))
        self.assertFalse(report['production_promoted'])
        self.assertEqual(report['human_visits_created'],0)
