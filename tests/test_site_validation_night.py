"""Disposable technical observations only; these tests never touch the field store."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from uuid import uuid4

from agro_checker.field_store import FieldStore,digest
from agro_checker.field_server import make_server
from agro_checker import site_validation_plans as plans
from agro_checker.site_validation_report import report
from agro_checker.site_validation_night import night_detail
from test_field_pilot import profile,forecast,measurements,ISSUE,END,START


class SiteValidationNightTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FieldStore(Path(self.tmp.name));self.p=profile();self.pr=self.store.save_profile(self.p,now=ISSUE)
        criteria=plans.flattened(plans.template());criteria['human_preparation_support.required_lead_hours']=2;criteria['human_preparation_support.false_alarm_budget_per_30_nights']=2
        self.v=plans.save(self.store,{'profile_id':self.p['id'],'zone_id':'z1','profile_revision':self.pr['id'],'expected_latest':None,'source_version':None,
            'request_id':uuid4().hex,'action':'fix','start':'2026-10-02','end':'2026-10-03','reference_sensor_id':'t1','season':'technical',
            'orchard_confirmed':True,'actor':'TEST ONLY','agreement_note':'Технический тест, не реальное согласование','confirmed_agreement':True,'criteria':criteria},ISSUE)

    def get(self,night='2026-10-02',now=END):return night_detail(self.store,self.p['id'],'z1',self.v['id'],night,now)
    def seed(self):
        f=self.store.issue(self.p['id'],'z1',forecast(self.pr['id']),ISSUE)
        self.store.import_measurements(self.p['id'],measurements(),'TEST.csv',END)
        self.store.journal(self.p['id'],{'forecast_id':f['id'],'actor':'TEST','action':'observe','protection_status':'not_used','decision_at_utc':ISSUE.isoformat()},ISSUE)
        return f

    def test_empty_night_remains_empty_no_forecast_or_perfect_coverage_invented(self):
        before=digest(self.store.records());d=self.get()
        self.assertEqual(d['samples'],[]);self.assertIsNone(d['forecast']);self.assertEqual(d['counts']['hourly'],{'missing':14})
        self.assertEqual(d['counts']['slots'],{'missing':168});self.assertFalse(d['automatic_admission']);self.assertEqual(digest(self.store.records()),before)
        r=report(self.store,self.p['id'],'z1',now=END,version=self.v['id']);self.assertEqual(len(r['night_choices']),2)
        self.assertEqual(r['night_choices'][1]['measurement_rows'],0)

    def test_raw_samples_and_same_persisted_issue_with_zero_changes_to_metrics(self):
        f=self.seed();before=digest(self.store.records());r=report(self.store,self.p['id'],'z1',now=END,version=self.v['id']);d=self.get()
        self.assertEqual(d['forecast']['id'],f['id']);self.assertEqual(d['forecast']['hours'],[{'valid_at_utc':h['valid_at_utc'],'temperature_c':h['temperature_c']} for h in f['payload']['hours']])
        self.assertEqual(d['counts']['usable'],14);self.assertEqual(d['counts']['hourly'],{'present':14});self.assertEqual(d['counts']['slots'],{'present':14,'missing':154})
        self.assertTrue(d['evidence']['included']);self.assertEqual(r['overall']['status'],'not_admitted');self.assertEqual(digest(self.store.records()),before)

    def test_missing_hour_and_rejected_point_are_separate_not_filled(self):
        self.seed();r=self.store.measurements(self.p['id'])[3];self.store.review_measurement(r['id'],'rejected','TEST sensor issue',END)
        d=self.get();self.assertEqual(d['counts']['usable'],13);self.assertEqual(d['counts']['excluded'],1)
        point=next(p for p in d['samples'] if p['id']==r['id']);self.assertFalse(point['usable']);self.assertIn('Измерение отклонено при QC',point['reasons'])
        self.assertEqual(d['hourly'][3]['state'],'missing');self.assertIsNone(d['hourly'][3]['temperature_c']);self.assertFalse(d['evidence']['included'])

    def test_conflicting_accepted_temperatures_excluded_even_after_qc_review(self):
        self.seed();self.store.import_measurements(self.p['id'],measurements([3.]+[2.]*7+[-1.]*6),'TEST-conflict.csv',END)
        suspect=next(r for r in self.store.measurements(self.p['id']) if r['effective_status']=='suspect')
        self.store.review_measurement(suspect['id'],'accepted','TEST review retains conflicting source',END)
        d=self.get();conflicts=[r for r in d['samples'] if r['observed_at_utc']==START.isoformat()]
        self.assertEqual(len(conflicts),2);self.assertTrue(all(not r['usable'] for r in conflicts));self.assertEqual(d['hourly'][0]['state'],'missing')

    def test_metadata_mismatch_excludes_points_without_erasing_values(self):
        self.p['sensors'][0]['height_m']=.5;self.store.save_profile(self.p,self.pr['id'],ISSUE+timedelta(minutes=1))
        self.store.import_measurements(self.p['id'],measurements(),'TEST-new-position.csv',END)
        d=self.get();self.assertEqual(d['counts']['rows'],14);self.assertEqual(d['counts']['usable'],0)
        self.assertTrue(all(any('Метаданные' in reason for reason in r['reasons']) for r in d['samples']))

    def test_latest_gfs_excluded_from_scoring_never_replaced_by_older_issue(self):
        old=self.seed();changed=deepcopy(self.p);changed['zones'][0]['reference_sensor_id']=''
        pr=self.store.save_profile(changed,self.pr['id'],ISSUE+timedelta(minutes=1))
        new=self.store.issue(self.p['id'],'z1',forecast(pr['id'],issued=ISSUE+timedelta(minutes=2)),ISSUE+timedelta(minutes=2))
        d=self.get();self.assertEqual(d['forecast']['id'],new['id']);self.assertNotEqual(d['forecast']['id'],old['id']);self.assertFalse(d['evidence']['included'])

    def test_wrong_dates_other_scope_and_missing_version_are_rejected(self):
        for value in ['2026-10-01','2026-10-04','02/10/2026','2026-02-30']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.get(value)
        with self.assertRaises(ValueError):night_detail(self.store,self.p['id'],'z1',None,'2026-10-02',END)
        with self.assertRaises(KeyError):night_detail(self.store,self.p['id'],'other',self.v['id'],'2026-10-02',END)

    def test_future_targets_pending_not_missing_and_night_without_gfs_kept(self):
        d=self.get(now=ISSUE);self.assertEqual(d['counts']['hourly'],{'pending':14});self.assertEqual(d['counts']['slots'],{'pending':168})
        d=self.get('2026-10-03');self.assertEqual(d['samples'],[]);self.assertIsNone(d['forecast'])
        d=self.get(now=START+timedelta(minutes=2));self.assertEqual(d['counts']['hourly'],{'missing':1,'pending':13})

    def test_unplaced_and_non_temperature_rows_have_no_fabricated_temperature(self):
        self.store.import_measurements(self.p['id'],'sensor_id,observed_at,temperature_c,rh_pct\nt1,bad,1,80\nt1,2026-10-02T16:00Z,,80\n','TEST-invalid.csv',END)
        d=self.get();self.assertEqual(d['unplaced_count'],1);self.assertEqual(d['counts']['excluded'],1)
        self.assertIsNone(d['samples'][0]['temperature_c']);self.assertEqual(d['hourly'][0]['state'],'missing')

    def test_window_ends_at_nine_and_other_sensor_is_not_substituted(self):
        self.seed();self.store.append('measurement',self.p['id'],{'sensor_id':'foreign','zone_id':'z1','observed_at_utc':START.isoformat(),'temperature_c':-55,'rh_pct':80,'status':'accepted','mode':'field'},END)
        self.store.import_measurements(self.p['id'],'sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-03T06:00Z,9,80\n','TEST-end.csv',END)
        d=self.get();self.assertEqual(len(d['samples']),14);self.assertNotIn(-55,[r['temperature_c'] for r in d['samples']])

    def test_http_night_read_only_query_checks_and_static_script(self):
        with make_server(Path(self.tmp.name),0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                query=f'?profile_id={self.p["id"]}&zone_id=z1&version={self.v["id"]}&night=2026-10-02'
                before=digest(self.store.records());d=json.load(urlopen(base+'/api/site-validation/night'+query));self.assertTrue(d['read_only'])
                self.assertIn(b'ValidationNightChart',urlopen(base+'/validation-night.js').read());self.assertEqual(digest(self.store.records()),before)
                for q in ['',query+'&night=2026-10-03',query+'&x=1',query.replace('night=2026-10-02','night=2026-10-04')]:
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/api/site-validation/night'+q)
                    self.assertEqual(e.exception.code,422)
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/site-validation/night'+query,headers={'Origin':'https://other.invalid'}))
                self.assertEqual(e.exception.code,403)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
