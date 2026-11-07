from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agro_checker.field_store import FieldStore, digest, seed_demo
from agro_checker.field_server import make_server
from agro_checker.prototype_feedback import feedback_export, save_feedback
from agro_checker.pipeline import ROOT
from test_field_pilot import END, ISSUE, profile, forecast


class PrototypeFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=FieldStore(Path(self.temp.name)/'state');self.pid=seed_demo(self.store,END)
        self.f=self.store.records('forecast',self.pid)[0]
        self.body={'forecast_id':self.f['id'],'participant_code':'P01','source':'user_test','role':'agronomist',
                   'completion':'with_help','data_meaning':'unsure','duration_seconds':None,
                   'notes':'Fixture: unsure about the source; facilitator helped.', 'iteration':'first'}

    def save(self,**extra):return save_feedback(self.store,self.pid,{**self.body,**extra},END)

    def test_feedback_never_changes_forecast_journal_or_field_metrics(self):
        metrics=self.store.state(END)['field_metrics'];fore=digest(self.f);journal=deepcopy(self.store.records('journal'))
        r=self.save();self.assertEqual(r['kind'],'prototype_feedback')
        self.assertEqual(self.store.state(END)['field_metrics'],metrics)
        self.assertEqual(self.store.records('journal'),journal)
        self.assertEqual(digest(self.store.records('forecast',self.pid)[0]),fore)
        self.assertEqual(self.store.state(END)['prototype_feedback'][0]['id'],r['id'])

    def test_failures_and_unknown_duration_are_retained_without_imputation(self):
        r=self.save(completion='not_completed',data_meaning='my_orchard')
        self.assertEqual(r['payload']['completion'],'not_completed')
        self.assertEqual(r['payload']['data_meaning'],'my_orchard')
        self.assertIsNone(r['payload']['duration_seconds'])
        self.assertEqual(r['payload']['duration_source'],'unknown')

    def test_repeat_links_same_participant_issue_and_source(self):
        r=self.save();second=self.save(iteration='repeat',previous_feedback_id=r['id'],completion='without_help',duration_seconds=90)
        self.assertEqual(second['payload']['previous_feedback_id'],r['id'])
        summary=feedback_export(self.store)['summary'];self.assertEqual(summary['user_test_records'],2)
        self.assertEqual(summary['reported_participant_codes'],1);self.assertEqual(summary['repeat_records'],1)
        for extra in [{'participant_code':'P02'},{'forecast_id':self.store.records('forecast',self.pid)[1]['id']},{'source':'engineering_check'}]:
            with self.assertRaises(ValueError):self.save(iteration='repeat',previous_feedback_id=r['id'],**extra)

    def test_missing_and_contradictory_repeat_reference_rejected(self):
        r=self.save()
        with self.assertRaises(ValueError):self.save(iteration='repeat')
        with self.assertRaises(ValueError):self.save(previous_feedback_id=r['id'])
        with self.assertRaises(ValueError):save_feedback(self.store,self.pid,{**self.body,'iteration':'repeat','previous_feedback_id':r['id']},END-timedelta(seconds=1))

    def test_engineering_checks_are_not_counted_as_users(self):
        self.save(source='engineering_check')
        s=feedback_export(self.store)['summary'];self.assertEqual(s['user_test_records'],0)
        self.assertEqual(s['reported_participant_codes'],0);self.assertEqual(s['engineering_records'],1)

    def test_invalid_inputs_and_empty_observations_rejected(self):
        for extra in [{'source':'review'},{'role':''},{'participant_code':'Full Name'}, {'completion':'yes'},
                      {'data_meaning':'other'},{'notes':'   '},{'notes':'x'*3001}, {'duration_seconds':0},
                      {'duration_seconds':1.5},{'duration_seconds':True},{'duration_seconds':float('nan')},
                      {'duration_seconds':86401}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.save(**extra)

    def test_feedback_refuses_real_or_other_profile_issue(self):
        p=profile();pr=self.store.save_profile(p,now=ISSUE)
        f=self.store.issue(p['id'],'z1',forecast(pr['id']),ISSUE)
        with self.assertRaises(ValueError):save_feedback(self.store,p['id'],{**self.body,'forecast_id':f['id'],'mode':'demo'},END)
        with self.assertRaises(ValueError):save_feedback(self.store,p['id'],self.body,END)
        with self.assertRaises(ValueError):self.save(forecast_id='missing')

    def test_version_and_case_hashes_are_server_derived(self):
        r=self.save(server_ui_sha256={'app.js':'forged'},forecast_sha256='forged',recorded_at_utc='2000-01-01T00:00:00Z')
        self.assertEqual(r['payload']['forecast_sha256'],digest(self.f['payload']))
        self.assertEqual(r['payload']['server_ui_sha256']['app.js'],hashlib.sha256((ROOT/'web/field/app.js').read_bytes()).hexdigest())
        self.assertEqual(r['recorded_at_utc'],END.isoformat())

    def test_http_feedback_requires_token_and_exports_separate_evidence(self):
        with make_server(self.store.directory,0) as server:
            t=Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}';state=json.loads(urlopen(base+'/api/state').read())
                self.assertEqual(json.loads(urlopen(base+'/api/prototype-feedback/export').read())['records'],[])
                body=json.dumps({**self.body,'profile_id':self.pid}).encode()
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/prototype-feedback',data=body,headers={'Content-Type':'application/json'}))
                self.assertEqual(e.exception.code,403)
                headers={'Content-Type':'application/json','X-Field-Token':state['token']}
                with self.assertRaises(HTTPError):urlopen(Request(base+'/api/prototype-feedback',data=body,headers={**headers,'Origin':'https://outside.test'}))
                r=json.loads(urlopen(Request(base+'/api/prototype-feedback',data=body,headers=headers)).read())
                out=json.loads(urlopen(base+'/api/prototype-feedback/export').read());self.assertEqual(out['records'][0]['id'],r['id'])
                self.assertEqual(out['summary']['user_test_records'],1)
            finally:server.shutdown();t.join()


if __name__=='__main__':unittest.main()
