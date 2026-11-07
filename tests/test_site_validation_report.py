"""Disposable technical fixtures; never create or approve a production field observation."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen,Request

from agro_checker.field_store import FieldStore,digest
from agro_checker.field_server import make_server
from agro_checker.site_validation_report import report,PLAN,MeasurementIndex,match
from test_field_pilot import profile,forecast,measurements,ISSUE,END,START


class SiteValidationReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FieldStore(Path(self.tmp.name));self.p=profile();self.pr=self.store.save_profile(self.p,now=ISSUE)
        self.plan=json.loads(PLAN.read_text(encoding='utf-8'))

    def bound(self):
        self.plan['scope'].update(orchard_confirmed=True,start='2026-10-02',end='2026-10-02',site_profile_revision=self.pr['id'],reference_sensor_id='t1',validation_season='technical')

    def seed(self,curve=None,protection='not_used'):
        f=self.store.issue(self.p['id'],'z1',forecast(self.pr['id'],curve),ISSUE)
        self.store.import_measurements(self.p['id'],measurements(),'TEST.csv',END)
        self.store.journal(self.p['id'],{'forecast_id':f['id'],'actor':'TEST ONLY','action':'observe','protection_status':protection,'decision_at_utc':(ISSUE+timedelta(minutes=20)).isoformat()},ISSUE+timedelta(minutes=20))
        return f

    def get(self,now=END):return report(self.store,self.p['id'],'z1',self.plan,now)

    def test_empty_report_has_unknown_denominators_no_perfect_scores_and_no_writes(self):
        before=self.store.records();d=self.get()
        self.assertIsNone(d['coverage']['scheduled_completed_nights']);self.assertEqual(d['coverage']['measurement_rows'],0)
        for k in ['minimum_mae_c','recall','false_alarm_share_alerts','false_alarm_rate_warm','severe_misses']:
            self.assertIsNone(d['gfs'][k],k)
        self.assertIsNone(d['candidate']);self.assertIsNone(d['difference'])
        self.assertFalse(any(g['status']=='met' for g in d['gates']))
        self.assertEqual(d['overall']['status'],'not_admitted');self.assertEqual(self.store.records(),before)

    def test_old_measurements_and_forecasts_are_not_claimed_as_registered_control(self):
        self.seed();d=self.get()
        self.assertEqual(d['coverage']['captured_nights'],1);self.assertEqual(d['coverage']['eligible_nights'],0)
        self.assertIsNone(d['gfs']['minimum_mae_c']);self.assertTrue(d['evidence'][0]['reasons'])

    def test_same_hourly_truth_gfs_metrics_are_computed_without_candidate_copy(self):
        self.bound();f=self.seed();before=digest(self.store.records());d=self.get()
        self.assertEqual(d['gfs']['nights'],1);self.assertEqual(d['gfs']['minimum_mae_c'],0)
        self.assertEqual(d['gfs']['tp'],1);self.assertEqual(d['gfs']['recall'],1)
        self.assertIsNone(d['gfs']['false_alarm_rate_warm']);self.assertEqual(d['gfs']['false_alarm_share_alerts'],0)
        self.assertEqual(d['coverage']['complete_truth_fraction'],1)
        self.assertEqual(d['coverage']['frequent_slots_found'],14);self.assertEqual(d['coverage']['frequent_slots_expected'],168)
        self.assertIsNone(d['candidate']);self.assertIsNone(d['difference']);self.assertEqual(digest(self.store.records()),before)
        self.assertEqual(d['overall']['status'],'not_admitted') # Even a perfect technical fixture cannot grant admission.

    def test_no_cold_truth_is_not_reported_as_100_percent_recall(self):
        self.bound();f=self.store.issue(self.p['id'],'z1',forecast(self.pr['id'],[2.]*14),ISSUE)
        self.store.import_measurements(self.p['id'],measurements([2.]*14),'TEST-warm.csv',END)
        self.store.journal(self.p['id'],{'forecast_id':f['id'],'actor':'TEST','action':'observe','protection_status':'not_used','decision_at_utc':ISSUE.isoformat()},ISSUE)
        d=self.get();self.assertEqual(d['gfs']['tn'],1);self.assertIsNone(d['gfs']['recall']);self.assertIsNone(d['gfs']['severe_misses'])

    def test_miss_and_duration_not_hidden_by_good_hourly_average(self):
        self.bound();self.seed([2.]*14);d=self.get()
        self.assertEqual(d['gfs']['fn'],1);self.assertEqual(d['gfs']['severe_misses'],1)
        self.assertEqual(d['gfs']['recall'],0);self.assertEqual(d['gfs']['cold_duration_mae_h'],6)
        self.assertEqual(next(g for g in d['gates'] if g['id']=='severe')['status'],'not_met')

    def test_protection_used_or_unknown_never_becomes_clean_night(self):
        for protection in ['used','unknown']:
            with self.subTest(protection=protection):
                self.bound();self.seed(protection=protection);d=self.get()
                self.assertEqual(d['coverage']['eligible_nights'],0)
                self.assertIsNone(d['gfs']['minimum_mae_c'])

    def test_scheduled_missing_night_reduces_completeness_and_no_partial_30_day_budget(self):
        self.bound();self.seed();self.plan['scope']['end']='2026-11-01'
        d=self.get(END+timedelta(days=32))
        self.assertEqual(d['coverage']['scheduled_completed_nights'],31)
        self.assertEqual(d['coverage']['missing_forecast_nights'],30)
        self.assertAlmostEqual(d['coverage']['complete_truth_fraction'],1/31)
        self.assertIsNone(d['gfs']['max_false_alarms_30_nights'])

    def test_newer_unverified_issue_never_falls_back_to_older_eligible_issue(self):
        self.bound();self.seed();changed=deepcopy(self.p);changed['zones'][0]['reference_sensor_id']=''
        pr=self.store.save_profile(changed,self.pr['id'],ISSUE+timedelta(minutes=1))
        f=self.store.issue(self.p['id'],'z1',forecast(pr['id'],issued=ISSUE+timedelta(minutes=2)),ISSUE+timedelta(minutes=2))
        d=self.get();self.assertEqual(d['coverage']['eligible_nights'],0)
        self.assertEqual(d['evidence'][0]['forecast_id'],f['id'])

    def test_sensor_change_at_import_rejects_false_match_but_preserves_source_rows(self):
        self.bound();self.seed();changed=deepcopy(self.p);changed['sensors'][0]['height_m']=.5
        self.store.save_profile(changed,self.pr['id'],ISSUE+timedelta(minutes=1))
        d=self.get();self.assertEqual(d['coverage']['accepted_temperature_rows'],14)
        self.assertEqual(d['coverage']['unverified_metadata_rows'],14);self.assertEqual(d['coverage']['eligible_nights'],0)

    def test_demo_and_other_zone_are_not_used(self):
        self.bound();self.seed();demo=deepcopy(self.p);demo.update(id='demo-only',mode='demo')
        self.store.save_profile(demo,now=ISSUE)
        d=self.get();self.assertNotIn('demo-only',[p['id'] for p in d['choices']])
        with self.assertRaises(KeyError):report(self.store,'demo-only',plan=self.plan,now=END)
        with self.assertRaises(KeyError):report(self.store,self.p['id'],'unknown',self.plan,END)

    def test_future_period_stays_pending_and_spring_does_not_include_autumn(self):
        self.bound();self.seed();self.plan['scope']['validation_season']='spring'
        self.assertEqual(self.get()['gfs']['nights'],0)
        self.plan['scope'].update(start='2027-03-01',end='2027-05-31')
        d=self.get();self.assertEqual(d['coverage']['scheduled_completed_nights'],0)
        self.assertIsNone(d['coverage']['timely_fresh_issue_fraction'])

    def test_unsupported_plan_rules_are_not_silently_scored_by_old_constants(self):
        self.plan['truth']['registration_minutes']=10
        with self.assertRaisesRegex(ValueError,'Правила плана'):self.get()

    def test_slot_index_does_not_fill_gaps_or_count_dense_duplicates_as_coverage(self):
        self.seed();rows=self.store.measurements(self.p['id']);index=MeasurementIndex(rows)
        self.assertIsNotNone(match(index,START,60));self.assertIsNone(match(index,START+timedelta(minutes=5),60))
        row=deepcopy(rows[0]);row['payload']['temperature_c']=55
        self.assertIsNone(match(MeasurementIndex([*rows,row]),START,60))

    def test_conflicting_accepted_values_do_not_enter_quality_even_after_manual_acceptance(self):
        self.bound();self.seed()
        self.store.import_measurements(self.p['id'],measurements([3.]+[2.]*7+[-1.]*6),'TEST-conflict.csv',END)
        suspect=next(r for r in self.store.measurements(self.p['id']) if r['effective_status']=='suspect')
        self.store.review_measurement(suspect['id'],'accepted','TEST explicit review, old conflicting value still present',END)
        d=self.get();self.assertEqual(d['coverage']['eligible_nights'],0)
        self.assertIn('Нет полной непротиворечивой серии опорной температуры',d['evidence'][0]['reasons'])

    def test_http_read_only_query_validation_same_origin_and_static_page(self):
        with make_server(self.store.directory,0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                d=json.load(urlopen(base+'/api/site-validation'));self.assertTrue(d['read_only']);self.assertIsNone(d['gfs']['recall'])
                self.assertIn(b'validation.js',urlopen(base+'/validation').read())
                for q in ['?extra=1','?profile_id=x&profile_id=y','?profile_id=../x']:
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/api/site-validation'+q)
                    self.assertEqual(e.exception.code,422)
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/site-validation',headers={'Origin':'https://other.invalid'}))
                self.assertEqual(e.exception.code,403)
                self.assertEqual(len(self.store.records()),1)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
