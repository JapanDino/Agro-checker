"""Evidence grouping must not relax unknowns into inferred negative labels."""
from collections import Counter
import unittest

from scripts import audit_unknown_outcomes_2024 as a


class AuditTest(unittest.TestCase):
    def test_complete331_case_ledger_exactly_matches_conservative_unknowns(self):
        rows,_=a.study.guard.dataset();ledger=a.study.read(a.D/'unknown-ledger.json')
        self.assertEqual({a.base.key(r) for r in rows if r['y'] is None},{a.base.key(r) for r in ledger})
        self.assertEqual(len(ledger),331)
        report=a.study.read(a.D/'report.json')
        self.assertEqual(Counter(v['reason'] for v in ledger),report['reasons'])
        self.assertEqual(sum(report['reasons'].values()),331)

    def test_no_new_source_no_recovery_and_no_training_or_future_tuning(self):
        report=a.study.read(a.D/'report.json');rows,_=a.study.guard.dataset()
        updated=a.study.read(a.D/'evaluation-only-rows.json')
        self.assertEqual(rows,updated)
        self.assertEqual(report['unknown_after'],331);self.assertEqual(report['recovered'],[])
        self.assertEqual(report['old_catalogue_items'],118);self.assertEqual(report['new_catalogue_items'],118)
        self.assertEqual(report['novel_relevant_items'],0)
        self.assertFalse(report['model_fit_performed']);self.assertFalse(report['rankings_changed'])
        self.assertFalse(report['prospective_study_changed']);self.assertEqual(report['test_years_evaluated'],[])

    def test_failures_distinguish_clouds_edges_and_spectral_loss(self):
        q=dict(total_cells=100,scl_counts={'5':70,'10':30})
        self.assertEqual(a.failure(q,.65),'cloud_or_shadow_scl')
        q['scl_counts']={'5':100}
        self.assertEqual(a.failure(q,.79),'cloud_edge_guard_loss')
        self.assertEqual(a.failure(q,1.),'spectral_or_band_nodata_loss')
        q['scl_counts']={'0':100}
        self.assertEqual(a.failure(q,0.),'nodata_or_tile_edge')

    def test_catalogue_and_input_hashes_remain_valid(self):
        p=a.study.read(a.D/'protocol.json')
        for name,value in p['inputs'].items():self.assertEqual(a.study.digest(a.ROOT/name),value)
        cat=a.study.read(a.D/'catalogue.json')
        self.assertTrue(cat['pagination_completed'])
        self.assertTrue(all(v['item']['properties']['datetime'][:4]=='2024' for v in cat['items']))

    def test_export_manifest_and_csv_cover_every_unknown_without_new_labels(self):
        import csv
        manifest=a.study.read(a.D/'manifest.json')
        for name,value in manifest['files'].items():self.assertEqual(a.study.digest(a.D/name),value)
        for name,value in manifest['source_code'].items():self.assertEqual(a.study.digest(a.ROOT/name),value)
        with (a.D/'unknown-outcomes.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),331)
        self.assertEqual(len({(v['fragment'],v['issue_utc_date']) for v in rows}),331)
        self.assertEqual(sum(v['selected_baseline']=='True' for v in rows),65)
        self.assertEqual(sum(v['selected_candidate']=='True' for v in rows),65)
