"""Source integrity, causal radar features and unchanged optical comparisons."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

from agro_checker import radar_data as data,radar_experiment as study
from scripts.check_prospective_health import operational_checks


class RadarDataTests(unittest.TestCase):
    def test_catalogue_complete_2024_only_and_not_claimed_complete_global_archive(self):
        cat=data.read(data.D/'catalogues.json');manifest=data.read(data.D/'satellite.json')
        self.assertEqual(len(cat['pc_rtc']['items']),102);self.assertEqual(len(cat['earth_grd']['items']),120)
        for v in cat.values():
            self.assertTrue(v['pagination_complete'])
            self.assertTrue(all(s['properties']['datetime'][:4]=='2024' for s in v['items']))
            for p in v['pages']:self.assertEqual(data.sha(data.ROOT/p['path']),p['sha256'])
        self.assertEqual(manifest['counts'],{'ready':102})
        self.assertEqual(len({v['track'] for v in manifest['scenes']}),4)

    def test_linear_power_units_and_joint_coverage(self):
        grid={'R00':{'core':np.arange(10)}}
        value=data.summarise(np.ones(10),np.ones(10)*.1,grid)['R00']
        self.assertAlmostEqual(value['values']['core']['vv_db'],0)
        self.assertAlmostEqual(value['values']['core']['vh_db'],-10)
        self.assertAlmostEqual(value['values']['core']['ratio_db'],-10)
        a=np.ones(10);a[:3]=np.nan
        value=data.summarise(a,np.ones(10),grid)['R00']
        self.assertEqual(value['support']['core']['cells'],7);self.assertNotIn('core',value['values'])

    def test_quality_risk_is_not_pixel_damage_or_clean_certificate(self):
        q=data.read(data.D/'quality/scene-risk.json')
        self.assertEqual(q['potential_source_rfi_count'],13)
        self.assertEqual(q['validation_pairs_using_potential_rfi_source'],273)
        self.assertFalse(q['source_model_report_changed']);self.assertFalse(q['source_labels_changed'])
        self.assertTrue(all(not v['sector_pixels_proven_affected'] for v in q['scenes']))
        self.assertEqual(sum(v['status']=='not_listed_clearance_unknown' for v in q['scenes']),89)
        for doc in q['documents']:self.assertEqual(data.sha(data.ROOT/doc['path']),doc['sha256'])

    def test_all_saved_evidence_and_frozen_source_hashes(self):
        manifest=data.read(study.D/'artifact-manifest.json')
        for name,value in manifest['files'].items():self.assertEqual(data.sha(data.ROOT/name),value,name)
        for name,value in manifest['code_sha256'].items():self.assertEqual(data.sha(data.ROOT/name),value,name)
        study.checked(data.read(study.D/'protocol.json'))


class RadarExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=data.read(study.D/'protocol.json');cls.report=data.read(study.D/'report.json')
        cls.audit=data.read(study.D/'feature-audit.json')['validation']

    def test_every_feature_pair_is_same_track_and_before_issue_cutoff(self):
        sources=study.sources()
        self.assertEqual(len(self.audit),699)
        for v in self.audit:
            self.assertIsNotNone(v['current'])
            a,b=sources[v['current']],sources[v['previous']]
            self.assertEqual(a['track'],b['track'])
            self.assertLess(data.instant(b['at']),data.instant(a['at']))
            self.assertLess(data.instant(a['at']),data.instant(v['cutoff_exclusive_utc']))
            self.assertLessEqual(v['features'][6],self.p['maximum_radar_age_days'])
            self.assertGreaterEqual(v['common_core_coverage'],.8)

    def test_different_tracks_or_future_source_cannot_make_a_radar_pair(self):
        current=dict(id='a',at='2024-06-01T15:00:00Z',track='s1a:43:ascending',fields={'R11':{'values':{'core':{}}}})
        previous=dict(current,id='b',at='2024-05-20T15:00:00Z',track='s1a:94:descending')
        with patch.object(study,'common_pair',side_effect=AssertionError('Different tracks cannot be compared')):
            values,audit=study.features('R11','2024-06-01',self.p,{'a':current,'b':previous})
        self.assertIsNone(audit['current']);self.assertEqual(values[study.SAR_FEATURES.index('s1_missing')],1)
        values,audit=study.features('R11','2024-05-01',self.p,{'a':current})
        self.assertIsNone(audit['current'])
        with self.assertRaises(ValueError):study.features('R11','2025-05-01',self.p,{'a':current})

    @unittest.skipUnless((data.D/'scenes').exists(),'Raw RTC cache not downloaded')
    def test_deleting_future_radar_cannot_change_actual_issue_features(self):
        sample=next(v for v in self.audit if v['fid']=='R11' and v['issue']=='2024-09-29')
        if not (data.D/'scenes'/sample['current']/'radar.npz').exists():self.skipTest('Re-download raw crops to repeat pixel-level check')
        cutoff=data.instant(sample['cutoff_exclusive_utc']);sources=study.sources()
        truncated={k:v for k,v in sources.items() if data.instant(v['at'])<cutoff}
        a,meta=study.features(sample['fid'],sample['issue'],self.p)
        b,other=study.features(sample['fid'],sample['issue'],self.p,truncated)
        np.testing.assert_allclose(a,b,rtol=0,atol=0);self.assertEqual(meta,other)

    def test_same699_candidates_unknowns_and133_slots(self):
        rows,_=study.old.guard.dataset();saved=data.read(study.D/'oof.json')
        self.assertEqual(rows,saved['rows']);self.assertEqual(self.report['known'],368);self.assertEqual(self.report['unknown'],331)
        self.assertTrue(self.report['optical_controls_reproduced'])
        for entry in self.report['table']:
            self.assertEqual(entry['metrics']['slots'],133)
            self.assertEqual(entry['metrics']['pool'],699)
            self.assertEqual(sum(entry['metrics'][k] for k in ('tp','fp','unknown')),133)

    def test_new_model_cannot_pass_on_equal_counts_when_it_loses_found_events(self):
        table={v['method']:v for v in self.report['table']}
        a,b=table['fixed_candidate']['metrics'],table['radar_consensus']['metrics']
        self.assertEqual([a[k] for k in ('tp','fp','unknown')],[b[k] for k in ('tp','fp','unknown')])
        pair=table['radar_consensus']['paired']['fixed_candidate']
        self.assertEqual(pair['lost_known_baseline_tp'],2);self.assertEqual(pair['negative_growth_upper'],6)
        self.assertFalse(table['radar_consensus']['development_gate_passed'])
        self.assertEqual(self.report['selected_development'],'fixed_candidate')

    def test_training_and_selection_only_completed2024_targets(self):
        for fold in self.report['folds']:self.assertLess(fold['fit_max_target'],fold['start'])
        for fold in self.report['forward']['selection']:
            if fold['max_known_target']:self.assertLess(fold['max_known_target'],fold['start'])
        self.assertEqual(self.report['selection_years'],[2024]);self.assertEqual(self.report['test_years_evaluated'],[])
        self.assertFalse(self.report['production_promoted']);self.assertFalse(self.report['prospective_study_changed'])
        self.assertFalse(self.report['source_models_changed']);self.assertEqual(self.report['human_visits_created'],0)
        for value in self.report['experimental_models'].values():self.assertEqual(data.sha(data.ROOT/value['path']),value['sha256'])


class ProspectiveHealthTests(unittest.TestCase):
    def test_successful_but_stale_task_is_not_healthy(self):
        at=datetime(2026,11,9,17,tzinfo=timezone.utc)
        task=dict(status='checked',state='Ready',last_result=0,last_run_utc='2026-11-01T15:00:00Z')
        checks=operational_checks(at,at-timedelta(hours=1),task,dict(releases=1,finalised=0,available_quality_evidence=False),dict(releases_verified=1))
        self.assertTrue(checks['last_scheduled_run_succeeded']);self.assertFalse(checks['last_scheduled_run_recent'])

    def test_pending_quality_claim_and_future_archive_clock_fail_health(self):
        at=datetime(2026,11,9,17,tzinfo=timezone.utc)
        task=dict(status='checked',state='Ready',last_result=0,last_run_utc=at.isoformat())
        checks=operational_checks(at,at+timedelta(hours=1),task,dict(releases=1,finalised=0,available_quality_evidence=True),dict(releases_verified=1))
        self.assertFalse(checks['pending_not_used_as_quality']);self.assertFalse(checks['catalogue_period_recent'])
