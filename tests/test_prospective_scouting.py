"""Prospective technical fixtures use disposable stores and mocked clocks only."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from agro_checker import prospective_scouting as s

UTC=timezone.utc
CUTOFF=datetime(2030,1,2,12,tzinfo=UTC)


def settings():
    ranking=s.read(s.repair.D/'protocol.json')
    return dict(budget_k=3,maximum_input_age_hours=72,horizon_days=[6,8],preferred_day=7,target_grace_hours=72,threshold=-.10,
        registered_at_utc='2029-01-01T00:00:00Z',geometry_sha256='TEST_GEOMETRY',bounds_utm=[0,0,100,100],
        ranking_parameters=ranking,baseline=ranking['variants'][0],candidate=next(v for v in ranking['variants'] if v['id']=='protect_weak_divergent_round'),
        models={n:dict(file=n+'.joblib') for n in ('logit','extra')})


def observations():
    result=[]
    for fid in ('R01','R02'):
        for date in ('2029-12-28','2030-01-01'):
            result.append(dict(fid=fid,date=date,scene_id=date,acquired_at_utc=date+'T08:00:00Z',
                received_at_utc=date+'T10:00:00Z',baseline='05.13',terrain=[10.,1.],
                values=dict(core=dict(value=[.5,.2] if date.endswith('28') else [.45,.15],coverage=1.))))
    return result


class ProspectiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name);self.settings=settings()
        self.config=patch.object(s,'configuration',return_value=(self.settings,'TEST_PROTOCOL'))
        self.config.start();self.addCleanup(self.config.stop)

    def issue(self,clock=CUTOFF,finish=None):
        obs=observations();meta={r['scene_id']:dict(id=r['scene_id'],acquired_at_utc=r['acquired_at_utc'],
                received_at_utc=r['received_at_utc'],baseline=r['baseline'],indices_sha256='TEST') for r in obs}
        coverage=dict(through_utc=clock.isoformat(),catalogue_complete=True,archive_version='TEST')
        def vector(*args):return [0.]*len(s.base.FEATURES),dict(all_boundaries_supported=False,boundary_delta={})
        with patch.object(s,'now',side_effect=[clock,finish or clock]),patch.object(s,'snapshot',return_value=(dict(contours_sha256='TEST_GEOMETRY',bounds_utm=[0,0,100,100]),dict(features={'R01':{},'R02':{},'R03':{}}),[],coverage)),\
             patch.object(s,'extract_observations',return_value=(obs,meta,lambda *a:{})),\
             patch.object(s.base,'input_vector',side_effect=vector),patch.object(s.joblib,'load',return_value={}),\
             patch.object(s.base,'score',side_effect=lambda model,rows:np.arange(len(rows),dtype=float)):
            return s.issue(self.directory)

    def test_real_issue_time_future_window_equal_budgets_and_no_outcomes(self):
        r=self.issue();self.assertEqual(r['status'],'issued');p=r['release']['payload']
        self.assertEqual(p['issued_at_utc'],CUTOFF.isoformat())
        self.assertGreater(s.instant(p['target_window']['start_utc']),CUTOFF)
        self.assertEqual(p['slots_each'],2)
        self.assertEqual(len(p['selected']['baseline']),len(p['selected']['candidate']))
        self.assertTrue(all(row['y'] is None and row['target'] is None for row in p['candidates']))
        self.assertEqual(p['field_visits_created'],0)
        self.assertFalse(list(self.directory.rglob('*.sqlite3')))

    def test_duplicate_day_cannot_backdate_or_replace_release(self):
        first=self.issue();path=self.directory/'releases/2030-01-01.json';before=path.read_bytes()
        second=self.issue(CUTOFF+timedelta(hours=1))
        self.assertEqual(second['status'],'already_issued')
        self.assertEqual(first['release'],second['release']);self.assertEqual(path.read_bytes(),before)

    def test_old_inputs_create_no_forecast(self):
        result=self.issue(CUTOFF+timedelta(days=10))
        self.assertEqual(result['status'],'insufficient_input_data')
        self.assertFalse((self.directory/'releases').exists())

    def test_clock_jump_into_target_window_cannot_create_release(self):
        with self.assertRaises(ValueError):self.issue(finish=CUTOFF+timedelta(days=8))
        self.assertFalse((self.directory/'releases').exists())

    def test_unequal_budgets_are_not_saved(self):
        with patch.object(s.repair,'choose',side_effect=[np.ones(2,bool),(np.array([True,False]),[])]):
            with self.assertRaises(ValueError):self.issue()
        self.assertFalse((self.directory/'releases').exists())

    def test_changed_bytes_detected_and_exclusive_creation(self):
        path=self.directory/'record.json';s.immutable(path,dict(test=True))
        with self.assertRaises(FileExistsError):s.immutable(path,dict(test=False))
        content=path.read_bytes();path.write_bytes(content.replace(b'true',b'false'))
        with self.assertRaises(ValueError):s.checked(path)

    def test_pending_does_not_read_future_or_produce_quality(self):
        self.issue()
        with patch.object(s,'now',return_value=CUTOFF+timedelta(days=3)),patch.object(s,'snapshot') as snapshot:
            result=s.evaluate(self.directory)
            self.assertEqual(len(result['pending']),1);self.assertEqual(result['finalised'],[])
            snapshot.assert_not_called()
            r=s.report(self.directory)
        self.assertEqual(r['status'],'awaiting_future_outcomes')
        self.assertIsNone(r['metrics']['baseline']['precision_lower']);self.assertIsNone(r['paired'])

    def test_future_targets_missing_or_cloudy_are_unknown_not_negative(self):
        release=self.issue()['release']['payload']
        rows,ledger=s.outcome_rows(release,[],self.settings)
        self.assertTrue(all(r['y'] is None for r in rows))
        self.assertTrue(all(r['outcome'] is None for r in ledger))

    def test_target_choice_preserves_window_baseline_preferred_day_and_receipt_deadline(self):
        release=self.issue()['release']['payload'];targets=[]
        for fid in ('R01','R02'):
            for day in (6,7,8):
                date=f'2030-01-{1+day:02d}'
                targets.append(dict(fid=fid,date=date,acquired_at_utc=date+'T08:00:00Z',received_at_utc=date+'T10:00:00Z',
                    baseline='05.13',scene_id=fid+date,values=dict(core=dict(value=[.1,.1],coverage=.9))))
        rows,ledger=s.outcome_rows(release,targets,self.settings)
        self.assertTrue(all(r['target']=='2030-01-08' for r in rows))
        self.assertTrue(all(r['outcome'] is True for r in ledger))
        for target in targets:target['received_at_utc']='2030-01-20T10:00:00Z'
        rows,ledger=s.outcome_rows(release,targets,self.settings)
        self.assertTrue(all(r['y'] is None for r in rows));self.assertTrue(all(r['reason']=='late_receipt_only' for r in ledger))
        for target in targets:target['baseline']='06.00'
        self.assertTrue(all(r['y'] is None for r in s.outcome_rows(release,targets,self.settings)[0]))

    def test_mature_but_stale_catalogue_does_not_finalize(self):
        self.issue()
        with patch.object(s,'now',return_value=CUTOFF+timedelta(days=20)),\
             patch.object(s,'snapshot',return_value=({}, {}, [],dict(catalogue_complete=True,through_utc=CUTOFF.isoformat()))),\
             patch.object(s,'extract_observations',return_value=([],{},None)):
            result=s.evaluate(self.directory)
        self.assertEqual(result['pending'][0]['status'],'pending_complete_target_catalogue')
        self.assertFalse((self.directory/'evaluations').exists())

    def test_late_clearer_scene_does_not_hide_on_time_target(self):
        release=self.issue()['release']['payload']
        ontime=dict(fid='R01',date='2030-01-08',acquired_at_utc='2030-01-08T08:00:00Z',
            received_at_utc='2030-01-08T10:00:00Z',baseline='05.13',scene_id='ONTIME',
            values=dict(core=dict(value=[.1,.1],coverage=.85)))
        late=deepcopy(ontime);late.update(scene_id='LATE',acquired_at_utc='2030-01-08T09:00:00Z',received_at_utc='2030-01-20T10:00:00Z')
        late['values']['core']['coverage']=1.
        rows,_=s.outcome_rows(release,[ontime,late],self.settings)
        self.assertEqual(rows[0]['target_scene'],'ONTIME')

    def test_final_unknowns_in_denominator_and_finalisation_idempotent(self):
        self.issue();when=CUTOFF+timedelta(days=20)
        with patch.object(s,'now',return_value=when),\
             patch.object(s,'snapshot',return_value=({}, {}, [],dict(catalogue_complete=True,through_utc=when.isoformat()))),\
             patch.object(s,'extract_observations',return_value=([],{},None)):
            result=s.evaluate(self.directory)
            path=self.directory/'evaluations/2030-01-01.json';before=path.read_bytes()
            again=s.evaluate(self.directory);r=s.report(self.directory)
        self.assertEqual(len(result['finalised']),1);self.assertEqual(len(again['finalised']),1)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(r['metrics']['baseline']['unknown'],2)
        self.assertEqual(r['metrics']['baseline']['precision_lower'],0.)
        self.assertEqual(r['metrics']['baseline']['precision_upper'],1.)
        self.assertFalse(r['available_quality_evidence'])

    def test_finalised_known_outcomes_are_scored_only_after_deadline(self):
        self.issue();when=CUTOFF+timedelta(days=20)
        targets=[];metadata={}
        for fid,values in [('R01',[.1,.15]),('R02',[.45,.15])]:
            sid='TEST_TARGET_'+fid
            targets.append(dict(fid=fid,date='2030-01-08',scene_id=sid,acquired_at_utc='2030-01-08T08:00:00Z',
                received_at_utc='2030-01-08T10:00:00Z',baseline='05.13',values=dict(core=dict(value=values,coverage=1.))))
            metadata[sid]=dict(id=sid,test_fixture=True)
        with patch.object(s,'now',return_value=when),\
             patch.object(s,'snapshot',return_value=({}, {}, [],dict(catalogue_complete=True,through_utc=when.isoformat()))),\
             patch.object(s,'extract_observations',return_value=(targets,metadata,None)):
            s.evaluate(self.directory);result=s.report(self.directory)
        self.assertEqual(result['metrics']['baseline']['tp'],1)
        self.assertEqual(result['metrics']['baseline']['fp'],1)
        self.assertEqual(result['metrics']['baseline']['unknown'],0)
        self.assertEqual(result['paired']['difference_lower'],0.)
        self.assertTrue(result['available_quality_evidence'])

    def test_register_only_2024_nonreserved_fit_and_never_retrain_on_repeat(self):
        self.config.stop()
        seen=[]
        def fit(rows,method,p):
            seen.append(method)
            self.assertTrue(all(r['issue'][:4]=='2024' and r['target'][:4]=='2024' for r in rows))
            self.assertTrue(all(r['fid'] not in s.base.HOLDOUT for r in rows))
            return dict(method=method,test_fixture=True)
        with patch.object(s.base,'fit_model',side_effect=fit):
            p,h=s.register(self.directory);p2,h2=s.register(self.directory)
        self.assertEqual(seen,['logit','extra']);self.assertEqual(h,h2)
        self.assertEqual(p['selection_years'],[2024]);self.assertLess(p['training']['max_target'],'2025')

    def test_prediction_code_change_blocks_inference(self):
        self.config.stop()
        with patch.object(s.base,'fit_model',side_effect=lambda r,m,p:dict(method=m)):
            s.register(self.directory)
        original_sha=s.sha
        with patch.object(s,'sha',side_effect=lambda p:'changed' if Path(p).name=='prospective_scouting.py' else original_sha(p)):
            with self.assertRaises(ValueError):s.configuration(self.directory)

    def test_readonly_html_report_uses_pending_not_fake_quality(self):
        from scripts.prospective_scouting import save_report
        self.issue()
        with patch.object(s,'now',return_value=CUTOFF):result=save_report(self.directory)
        self.assertEqual(result['pending'],1)
        document=(self.directory/'reports/latest.html').read_text(encoding='utf-8')
        self.assertIn('не оценено',document)
        self.assertEqual(s.checked(self.directory/'releases/2030-01-01.json')['payload']['field_visits_created'],0)

    def test_real_sealed_release_replays_without_refit_or_outcomes(self):
        if not (s.DIRECTORY/'frozen/protocol.json').exists():self.skipTest('Optional registered future study absent')
        self.config.stop()
        from scripts.verify_prospective_scouting import verify
        with patch.object(s.base,'fit_model',side_effect=AssertionError('No fit during replay')):
            r=verify(s.DIRECTORY)
        self.assertGreaterEqual(r['releases_verified'],1)
        self.assertFalse(r['model_training_performed']);self.assertEqual(r['records_written'],0)
