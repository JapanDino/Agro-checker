from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agro_checker.field_store import FieldStore, digest
from agro_checker.field_server import make_server
from test_field_pilot import profile, forecast, ISSUE, START, END, measurements


class FieldJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=FieldStore(Path(self.temp.name)/'state');self.p=profile()
        self.pr=self.store.save_profile(self.p,now=ISSUE)
        self.f=self.store.issue(self.p['id'],'z1',forecast(self.pr['id']),ISSUE)

    def read(self,when=ISSUE+timedelta(minutes=3)):
        return self.store.acknowledge_forecast(self.p['id'],self.f['id'],when)

    def record(self,read,when=ISSUE+timedelta(minutes=4),**extra):
        return self.store.journal(self.p['id'],{'forecast_id':self.f['id'],'actor':'Tester','action':'observe',
            'record_now':True,'read_id':read['id'],**extra},when)

    def test_read_and_decision_use_server_time_and_do_not_change_issue(self):
        before=digest(self.f);r=self.read();j=self.record(r,decision_at_utc=ISSUE.isoformat())
        self.assertEqual(j['payload']['decision_at_utc'],(ISSUE+timedelta(minutes=4)).isoformat())
        self.assertEqual(j['payload']['seen_at_utc'],r['recorded_at_utc'])
        self.assertEqual(j['payload']['read_id'],r['id'])
        self.assertEqual(j['payload']['decision_time_source'],'server_now')
        self.assertEqual(digest(self.store.records('forecast')[0]),before)

    def test_read_cannot_be_attached_to_another_issue_or_profile(self):
        r=self.read();other=self.store.issue(self.p['id'],'z1',forecast(self.pr['id'],issued=ISSUE+timedelta(minutes=1)),ISSUE+timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError,'другой выдаче'):self.record(r,forecast_id=other['id'])
        p=deepcopy(self.p);p['id']='other';pr=self.store.save_profile(p,now=ISSUE)
        f=self.store.issue('other','z1',forecast(pr['id']),ISSUE)
        with self.assertRaises(ValueError):self.store.journal('other',{'forecast_id':f['id'],'actor':'Tester','action':'observe','record_now':True,'read_id':r['id']},END)
        with self.assertRaises(ValueError):self.store.acknowledge_forecast('other',self.f['id'],END)

    def test_read_before_issue_and_backdated_decision_are_rejected(self):
        with self.assertRaises(ValueError):self.read(ISSUE-timedelta(seconds=1))
        r=self.read()
        with self.assertRaises(ValueError):self.record(r,record_now=False,decision_at_utc=(ISSUE+timedelta(minutes=1)).isoformat())
        with self.assertRaises(ValueError):self.record(r,seen_at_utc=ISSUE.isoformat())
        with self.assertRaises(ValueError):self.record(r,record_now='yes')

    def test_late_read_and_decision_do_not_prove_pre_night_lead(self):
        self.store.import_measurements(self.p['id'],measurements(),'fixture.csv',END)
        r=self.read(END);self.record(r,END,protection_status='not_used')
        a=self.store.assess(self.f,END)
        self.assertIsNone(a['lead_decision_id']);self.assertIsNone(a['readiness_lead_met'])
        self.assertEqual(a['retrospective_decision_entries'],1)

    def test_read_does_not_confirm_absence_of_protection_or_create_decision(self):
        self.store.import_measurements(self.p['id'],measurements(),'fixture.csv',END)
        self.read(END)
        self.assertEqual(self.store.records('journal'),[])
        self.assertIn('protection_unknown',self.store.assess(self.f,END)['exclusion_reasons'])
        self.assertEqual(len(self.store.state(END)['reads']),1)

    def test_reported_past_decision_keeps_current_review_separate_from_original_read(self):
        r=self.read(END)
        j=self.record(r,END,record_now=False,read_id=None,review_read_id=r['id'],decision_at_utc=(ISSUE+timedelta(minutes=1)).isoformat())
        self.assertIsNone(j['payload']['seen_at_utc']);self.assertIsNone(j['payload']['read_id'])
        self.assertEqual(j['payload']['review_read_id'],r['id'])
        self.assertEqual(j['payload']['decision_time_source'],'operator_reported')
        self.assertIsNone(self.store.assess(self.f,END)['lead_decision_id'])
        other=self.store.issue(self.p['id'],'z1',forecast(self.pr['id'],issued=ISSUE+timedelta(minutes=1)),ISSUE+timedelta(minutes=1))
        with self.assertRaises(ValueError):self.record(r,END,record_now=False,read_id=None,review_read_id=r['id'],forecast_id=other['id'],decision_at_utc=(ISSUE+timedelta(minutes=2)).isoformat())

    def test_http_acknowledgement_same_origin_token_and_static_module(self):
        with make_server(self.store.directory,0) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                state=json.loads(urlopen(base+'/api/state').read())
                payload=json.dumps({'profile_id':self.p['id'],'forecast_id':self.f['id']}).encode()
                with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/read',data=payload,headers={'Content-Type':'application/json'}))
                self.assertEqual(e.exception.code,403)
                r=json.loads(urlopen(Request(base+'/api/read',data=payload,headers={'Content-Type':'application/json','X-Field-Token':state['token']})).read())
                self.assertEqual(r['kind'],'forecast_read')
                self.assertEqual(r['payload']['forecast_id'],self.f['id'])
                self.assertIn(b'FieldJourney',urlopen(base+'/journey.js').read())
            finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
