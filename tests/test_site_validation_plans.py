"""Technical planning fixtures in disposable stores; no invented production agreement."""
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
from agro_checker import site_validation_plans as p
from agro_checker.site_validation_report import report
from test_field_pilot import profile,ISSUE


class SiteValidationPlansTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=FieldStore(Path(self.tmp.name));self.profile=profile();self.pr=self.store.save_profile(self.profile,now=ISSUE)
        self.raw={'profile_id':self.profile['id'],'zone_id':'z1','profile_revision':self.pr['id'],'expected_latest':None,'source_version':None,
                  'request_id':uuid4().hex,'action':'draft','start':'2027-03-01','end':'2027-05-31','reference_sensor_id':'t1','season':'spring','orchard_confirmed':True,
                  'actor':'TEST ONLY — не реальное согласование','agreement_note':'Техническая проверка в удаляемом журнале','confirmed_agreement':False,'criteria':p.flattened(p.template())}

    def fixed(self):
        self.raw['action']='fix';self.raw['confirmed_agreement']=True
        self.raw['criteria']['human_preparation_support.required_lead_hours']=2
        self.raw['criteria']['human_preparation_support.false_alarm_budget_per_30_nights']=2
        return p.save(self.store,self.raw,ISSUE)

    def test_empty_context_and_read_report_never_create_saved_versions(self):
        before=self.store.records();ctx=p.context(self.store,self.profile['id'],'z1')
        self.assertIsNone(ctx['selected_version']);self.assertEqual(ctx['versions'],[])
        self.assertEqual(len(ctx['schema']),27);self.assertEqual(self.store.records(),before)

    def test_draft_can_leave_sensor_dates_and_business_limits_unknown(self):
        self.raw.update(start=None,end=None,reference_sensor_id=None,orchard_confirmed=False)
        r=p.save(self.store,self.raw,ISSUE)
        self.assertEqual(r['payload']['action'],'draft');self.assertIsNone(r['payload']['plan']['approved_at'])
        self.assertIsNone(r['payload']['plan']['scope']['reference_sensor_id'])
        self.assertFalse(r['payload']['automatic_admission']);self.assertEqual(self.store.records('measurement'),[])

    def test_fixed_plan_pins_exact_context_and_changes_no_models_or_profile(self):
        original=digest(self.store.records('profile'));baseline=p.template()['models'];r=self.fixed()
        plan=r['payload']['plan'];self.assertEqual(plan['scope']['site_profile_revision'],self.pr['id'])
        self.assertEqual(plan['models'],baseline);self.assertEqual(digest(self.store.records('profile')),original)
        self.assertEqual(plan['status'],'criteria_fixed');self.assertEqual(plan['approved_at'],ISSUE.isoformat())
        d=report(self.store,self.profile['id'],'z1',now=ISSUE)
        self.assertEqual(d['plan']['version_id'],r['id']);self.assertEqual(d['overall']['status'],'not_admitted')
        self.assertEqual(next(g for g in d['gates'] if g['id']=='agreement')['status'],'met')

    def test_new_draft_does_not_replace_fixed_default_or_rewrite_old_version(self):
        first=self.fixed();saved=digest(first)
        self.raw.update(action='draft',expected_latest=first['id'],source_version=first['id'],request_id=uuid4().hex,confirmed_agreement=False,start='2028-03-01',end='2028-05-31')
        self.raw['criteria']['human_preparation_support.minimum_mae_c_max']=.8
        second=p.save(self.store,self.raw,ISSUE+timedelta(minutes=1))
        self.assertEqual(p.selected(self.store,self.profile['id'],'z1')[1]['id'],first['id'])
        d=report(self.store,self.profile['id'],'z1',now=ISSUE,version=second['id'])
        self.assertEqual(d['plan']['version_id'],second['id']);self.assertIsNone(d['plan']['approved_at'])
        self.assertEqual(digest(p.records(self.store,self.profile['id'],'z1')[0]),saved)

    def test_retry_is_idempotent_and_changed_retry_is_conflict(self):
        a=p.save(self.store,self.raw,ISSUE);b=p.save(self.store,self.raw,ISSUE+timedelta(days=2))
        self.assertEqual(a['id'],b['id']);self.assertEqual(len(p.records(self.store,self.profile['id'],'z1')),1)
        self.raw['actor']='another TEST'
        with self.assertRaises(p.Conflict):p.save(self.store,self.raw,ISSUE)

    def test_stale_version_and_profile_revision_do_not_overwrite(self):
        r=p.save(self.store,self.raw,ISSUE);self.raw['request_id']=uuid4().hex
        with self.assertRaises(p.Conflict):p.save(self.store,self.raw,ISSUE)
        self.raw['expected_latest']=r['id'];self.profile['name']='TEST changed'
        self.store.save_profile(self.profile,self.pr['id'],ISSUE+timedelta(minutes=1))
        with self.assertRaises(p.Conflict):p.save(self.store,self.raw,ISSUE)

    def test_fix_rejects_past_period_overlap_and_absent_attestation(self):
        self.raw.update(action='fix',confirmed_agreement=False)
        with self.assertRaises(ValueError):p.save(self.store,self.raw,ISSUE)
        first=self.fixed();self.raw.update(expected_latest=first['id'],source_version=first['id'],request_id=uuid4().hex)
        with self.assertRaisesRegex(ValueError,'пересекается'):p.save(self.store,self.raw,ISSUE)
        self.raw.update(start='2026-03-01',end='2026-05-31')
        with self.assertRaisesRegex(ValueError,'будущего'):p.save(self.store,self.raw,ISSUE)

    def test_unknown_sensor_foreign_version_and_unsupported_targets_rejected(self):
        for change in [{'reference_sensor_id':'invented'},{'zone_id':'other'},{'source_version':'a'*32},{'criteria':{}},{'season':'all_year'}, {'model':'new_model'}]:
            raw=deepcopy(self.raw);raw.update(change)
            with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):p.save(self.store,raw,ISSUE)
        self.assertEqual(p.records(self.store,self.profile['id'],'z1'),[])

    def test_invalid_numeric_limits_and_nullable_fixed_limits_rejected(self):
        key='human_preparation_support.recall_min'
        for value in [True,float('nan'),-1,1.1,'90']:
            raw=deepcopy(self.raw);raw['criteria'][key]=value
            with self.assertRaises(ValueError):p.save(self.store,raw,ISSUE)
        self.raw['action']='fix';self.raw['confirmed_agreement']=True
        with self.assertRaises(ValueError):p.save(self.store,self.raw,ISSUE)

    def test_sensor_must_be_checked_reference_air_2m_and_site_confirmed_for_fix(self):
        for change in ['height','unverified','reference','geometry']:
            profile2=deepcopy(self.profile)
            if change=='height':profile2['sensors'][0]['height_m']=.5
            if change=='unverified':profile2['sensors'][0]['calibration_status']='unknown'
            if change=='reference':profile2['zones'][0]['reference_sensor_id']=''
            if change=='geometry':profile2['zones'][0]['geometry']=None
            prev=self.store.profile_record(self.profile['id'])
            current=self.store.save_profile(profile2,prev['id'],ISSUE)
            self.raw['profile_revision']=current['id']
            with self.subTest(change=change),self.assertRaises(ValueError):self.fixed()
        self.assertEqual(p.records(self.store,self.profile['id'],'z1'),[])

    def test_http_requires_token_and_can_read_exact_saved_version(self):
        with make_server(self.store.directory,0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                body=json.dumps(self.raw).encode()
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/site-validation/plans',data=body))
                self.assertEqual(e.exception.code,403)
                token=json.load(urlopen(base+'/api/land/status'))['token']
                headers={'X-Field-Token':token,'Content-Type':'application/json'}
                r=json.load(urlopen(Request(base+'/api/site-validation/plans',data=body,headers=headers)))
                d=json.load(urlopen(base+'/api/site-validation?profile_id='+self.profile['id']+'&zone_id=z1&version='+r['id']))
                self.assertEqual(d['plan']['version_id'],r['id']);self.assertEqual(d['overall']['status'],'not_admitted')
                self.raw['request_id']=uuid4().hex
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/site-validation/plans',data=json.dumps(self.raw).encode(),headers=headers))
                self.assertEqual(e.exception.code,409)
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
