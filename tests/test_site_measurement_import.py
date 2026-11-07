"""All observations here are synthetic fixtures in disposable stores, never field data."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4
from unittest.mock import patch

from agro_checker.field_store import FieldStore,digest
from agro_checker.field_server import make_server
from agro_checker.site_validation_report import report
from agro_checker import site_validation_plans as plans
from agro_checker import site_measurement_import as m
from test_field_pilot import profile,measurements,forecast,ISSUE,END,START


class SiteMeasurementImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FieldStore(Path(self.tmp.name));self.p=profile();self.pr=self.store.save_profile(self.p,now=ISSUE)
        criteria=plans.flattened(plans.template());criteria['human_preparation_support.required_lead_hours']=2;criteria['human_preparation_support.false_alarm_budget_per_30_nights']=2
        self.version=plans.save(self.store,{'profile_id':self.p['id'],'zone_id':'z1','profile_revision':self.pr['id'],'expected_latest':None,'source_version':None,
            'request_id':uuid4().hex,'action':'fix','start':'2026-10-02','end':'2026-10-02','reference_sensor_id':'t1','season':'technical',
            'orchard_confirmed':True,'actor':'TEST ONLY','agreement_note':'Технический тест, не реальное согласование','confirmed_agreement':True,'criteria':criteria},ISSUE)
        self.raw={'profile_id':self.p['id'],'zone_id':'z1','version':self.version['id'],'csv':measurements(),'filename':'TEST-fixture.csv','unit':'C','time_basis':'offset','delimiter':','}

    def preview(self):return m.preview(self.store,self.raw,END)
    def payload(self,checked=None):return {**self.raw,'preview_sha256':(checked or self.preview())['preview_sha256'],'request_id':uuid4().hex,'actor':'TEST ONLY','confirmed_source':True}
    def csv(self,rows,header='observed_at,temperature_c,rh_pct'):self.raw['csv']=header+'\n'+'\n'.join(rows)+'\n'

    def test_preview_no_writes_commit_report_and_model_unchanged(self):
        f=self.store.issue(self.p['id'],'z1',forecast(self.pr['id']),ISSUE)
        self.store.journal(self.p['id'],{'forecast_id':f['id'],'actor':'TEST','action':'observe','protection_status':'not_used','decision_at_utc':ISSUE.isoformat()},ISSUE)
        before=digest(self.store.records());checked=self.preview();self.assertEqual(digest(self.store.records()),before)
        self.assertTrue(checked['can_commit']);self.assertEqual(checked['nights'][0]['hourly_after'],14)
        self.assertEqual(checked['nights'][0]['slots_missing'],154)
        saved=m.commit(self.store,self.payload(checked),END);self.assertEqual(saved['imported_rows'],14)
        d=report(self.store,self.p['id'],'z1',now=END,version=self.version['id'])
        self.assertEqual(d['coverage']['eligible_nights'],1);self.assertEqual(d['gfs']['minimum_mae_c'],0)
        self.assertEqual(d['overall']['status'],'not_admitted');self.assertEqual(self.store.records('forecast')[0],f)
        self.assertEqual(self.store.profile_record(self.p['id']),self.pr)
        self.assertEqual(self.store.records('import_batch')[0]['payload']['sensor_snapshot']['id'],'t1')

    def test_units_and_timezone_are_explicit_and_normalized(self):
        self.raw.update(unit='F',time_basis='msk',delimiter=';')
        self.csv(['2026-10-02 19:00;32;80'],'observed_at;temperature_f;rh_pct')
        c=self.preview();self.assertEqual(c['rows'][0]['temperature_c'],0);self.assertEqual(c['rows'][0]['observed_at_utc'],START.isoformat())
        self.raw.update(unit='K',time_basis='utc',delimiter=',');self.csv(['2026-10-02 16:00,273.15,'],'observed_at,temperature_k,rh_pct')
        self.assertEqual(self.preview()['rows'][0]['temperature_c'],0)
        self.raw.update(unit='C',time_basis='msk',delimiter=';');self.csv(['2026-10-02 19:00;-1,5;'],'observed_at;temperature_c;rh_pct')
        self.assertEqual(self.preview()['rows'][0]['temperature_c'],-1.5)
        self.raw['unit']='F'
        with self.assertRaises(ValueError):self.preview()

    def test_invalid_dates_units_missing_nan_wrong_sensor_and_future_block_commit(self):
        for row in ['2026-10-02 19:00,1,80','02/10/2026 19:00,1,80','2026-02-30T19:00:00Z,1,80',
                    '2026-10-02T16:00:00Z,,80','2026-10-02T16:00:00Z,NaN,80','2026-10-02T16:00:00Z,99,80',
                    '2026-10-02T16:00:00Z,1,101','2027-10-02T16:00:00Z,1,80','2026-10-02T16:00:00Z,1,80,extra']:
            with self.subTest(row=row):
                self.csv([row]);c=self.preview();self.assertEqual(c['counts']['invalid'],1);self.assertFalse(c['can_commit'])
                with self.assertRaises(ValueError):m.commit(self.store,self.payload(c),END)
        self.csv(['wrong,2026-10-02T16:00:00Z,1,80'],'sensor_id,observed_at,temperature_c,rh_pct');self.assertFalse(self.preview()['can_commit'])
        self.assertEqual(self.store.records('measurement'),[])

    def test_mixed_offset_naive_mode_and_duplicate_headers_rejected(self):
        self.raw['time_basis']='msk';self.csv(['2026-10-02T19:00:00+03:00,1,80']);self.assertFalse(self.preview()['can_commit'])
        self.raw['time_basis']='offset';self.csv(['2026-10-02T19:00:00+03:00,1,80']);self.assertTrue(self.preview()['can_commit'])
        self.raw['csv']='observed_at,temperature_c,temperature_c\n2026-10-02T16:00Z,1,2\n'
        with self.assertRaises(ValueError):self.preview()
        self.raw['csv']='observed_at,temperature_c\n"unclosed,1'
        with self.assertRaises(ValueError):self.preview()

    def test_equal_duplicate_normalized_times_skipped_conflicts_block_whole_file(self):
        self.csv(['2026-10-02T16:00Z,1,80','2026-10-02T19:00+03:00,1,80'])
        c=self.preview();self.assertEqual(c['counts'],{'ready':1,'duplicate':1})
        m.commit(self.store,self.payload(c),END);self.assertEqual(len(self.store.records('measurement')),1)
        self.assertEqual(self.preview()['counts'],{'duplicate':2});self.assertFalse(self.preview()['can_commit'])
        self.csv(['2026-10-02T16:00Z,2,80','2026-10-02T17:00Z,1,80'])
        c=self.preview();self.assertEqual(c['counts']['conflict'],1);self.assertFalse(c['can_commit'])
        with self.assertRaises(ValueError):m.commit(self.store,self.payload(c),END)
        self.assertEqual(len(self.store.records('measurement')),1)

    def test_conflicts_in_file_all_marked_and_spikes_in_both_directions(self):
        self.csv(['2026-10-02T16:00Z,1,80','2026-10-02T19:00+03:00,2,80'])
        self.assertEqual(self.preview()['counts'],{'conflict':2})
        self.csv(['2026-10-02T16:01Z,6,80','2026-10-02T16:00Z,1,80'])
        c=self.preview();self.assertTrue(c['unsorted']);self.assertEqual(c['counts'],{'suspect':2})
        self.csv(['2026-10-02T16:05Z,6,80']);self.store.import_measurements(self.p['id'],'sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-02T16:06Z,0,80\n',now=END)
        self.assertFalse(self.preview()['can_commit'])

    def test_outside_night_and_period_excluded_gaps_never_filled(self):
        self.csv(['2026-10-02T15:59Z,1,','2026-10-03T06:00Z,1,','2026-10-01T16:00Z,1,','2026-10-02T16:00Z,1,'])
        c=self.preview();self.assertEqual(c['counts'],{'outside':3,'ready':1});self.assertTrue(c['can_commit'])
        self.assertEqual(c['nights'][0]['hourly_missing'],13);self.assertEqual(c['nights'][0]['slots_missing'],167)
        m.commit(self.store,self.payload(c),END);self.assertEqual(len(self.store.records('measurement')),1)

    def test_retry_exact_idempotency_and_stale_journal_conflict(self):
        raw=self.payload();a=m.commit(self.store,raw,END);b=m.commit(self.store,raw,END+timedelta(seconds=1))
        self.assertEqual(a['batch_id'],b['batch_id']);self.assertTrue(b['replayed']);self.assertEqual(len(self.store.records('measurement')),14)
        raw['actor']='changed'
        with self.assertRaises(plans.Conflict):m.commit(self.store,raw,END)
        self.csv(['2026-10-02T16:30Z,2,80']);raw=self.payload()
        self.store.import_measurements(self.p['id'],'sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-02T16:31Z,2,80\n',now=END)
        with self.assertRaises(plans.Conflict):m.commit(self.store,raw,END)

    def test_changed_profile_wrong_version_scope_and_attestation(self):
        raw=self.payload();raw['confirmed_source']=False
        with self.assertRaises(ValueError):m.commit(self.store,raw,END)
        other=deepcopy(self.raw);other['zone_id']='other'
        with self.assertRaises(KeyError):m.preview(self.store,other,END)
        other=deepcopy(self.raw);other['version']=None
        with self.assertRaises(ValueError):m.preview(self.store,other,END)
        self.p['sensors'][0]['height_m']=3;self.store.save_profile(self.p,self.pr['id'],END)
        with self.assertRaises(plans.Conflict):self.preview()

    def test_public_preview_limits_detail_but_prioritizes_all_errors(self):
        rows=[{'line':i,'status':'ready'} for i in range(300)]+[{'line':302,'status':'invalid'}]
        result=m.public_preview({'rows':rows,'rows_total':301,'preview_sha256':'unchanged'})
        self.assertEqual(len(result['rows']),200);self.assertEqual(result['rows'][0]['status'],'invalid');self.assertEqual(result['preview_sha256'],'unchanged')

    def test_failed_write_rolls_back_batch_and_rows_together(self):
        raw=self.payload();before=digest(self.store.records());append=self.store.append
        def fail(kind,*args,**kwargs):
            if kind=='measurement':raise OSError('TEST ONLY simulated disk failure')
            return append(kind,*args,**kwargs)
        with patch.object(self.store,'append',side_effect=fail):
            with self.assertRaises(OSError):m.commit(self.store,raw,END)
        self.assertEqual(digest(self.store.records()),before)

    def test_report_measurement_counters_only_cover_selected_night_windows(self):
        m.commit(self.store,self.payload(),END)
        self.store.import_measurements(self.p['id'],'sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-02T12:00Z,2,80\nt1,2026-10-01T16:00Z,2,80\n',now=END)
        d=report(self.store,self.p['id'],'z1',now=END,version=self.version['id'])
        self.assertEqual(d['coverage']['measurement_rows'],14);self.assertEqual(d['coverage']['outside_period_rows'],2)

    def test_http_preview_token_commit_and_template_without_fake_rows(self):
        server=make_server(Path(self.tmp.name),0);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}';token=json.load(urlopen(base+'/api/land/status'))['token']
        def post(path,body,auth=True):return json.load(urlopen(Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'X-Field-Token':token} if auth else {})})))
        with self.assertRaises(HTTPError) as error:post('/api/site-validation/measurements/preview',self.raw,False)
        self.assertEqual(error.exception.code,403)
        checked=post('/api/site-validation/measurements/preview',self.raw);self.assertTrue(checked['can_commit'])
        saved=post('/api/site-validation/measurements/commit',self.payload(checked));self.assertEqual(saved['imported_rows'],14)
        self.assertEqual(len(urlopen(base+'/validation-template.csv').read().decode().splitlines()),1)
        self.assertIn('ValidationMeasurementImport',urlopen(base+'/validation-import.js').read().decode())


if __name__=='__main__':unittest.main()
