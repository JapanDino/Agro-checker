from copy import deepcopy
import hashlib
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from agro_checker.agronom_brief import brief,causal_measurements,sensor_context,field_brief
from agro_checker.agronom_rehearsal import save_decision,result
from agro_checker.field_store import FieldStore,digest
from tests.test_field_pilot import profile,forecast,START,ISSUE


class BriefTests(unittest.TestCase):
    def call(self,curve=None,**kw):
        f=forecast('fixture',curve)
        return brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],f['issued_at_utc'],**kw)

    def test_missing_phase_lead_protocol_are_tasks_not_invented_damage(self):
        r=self.call();self.assertFalse(r['phase_known_at_issue']);self.assertFalse(r['protocol_reviewed_at_issue'])
        self.assertIsNone(r['prepare_by_utc']);self.assertIsNone(r['crop_damage_probability']);self.assertFalse(r['all_clear_allowed'])

    def test_disagreement_never_becomes_warm_all_clear(self):
        r=self.call([2.]*14,reference_event=True)
        self.assertTrue(r['signals_disagree']);self.assertFalse(r['all_clear_allowed']);self.assertIsNone(r['prepare_by_utc'])

    def test_censored_edges_do_not_invent_deadline(self):
        r=self.call([-1.]*14,lead=2)
        self.assertTrue(r['left_censored']);self.assertIsNone(r['prepare_by_utc']);self.assertTrue(r['right_censored'])

    def test_deadline_uses_gfs_earlier_bracket_edge_only(self):
        r=self.call(lead=2)
        self.assertEqual(r['prepare_by_utc'],(START+timedelta(hours=5)).isoformat())
        self.assertIn('GFS',r['deadline_source'])

    def test_incomplete_and_mixed_run_data_are_refused(self):
        for change in ['missing','run','gap']:
            f=forecast('fixture')
            if change=='missing':f['hours'][4]['temperature_c']=None
            elif change=='run':f['hours'][4]['run_at_utc']=(ISSUE-timedelta(hours=12)).isoformat()
            else:f['hours'][4]['valid_at_utc']=f['hours'][3]['valid_at_utc']
            r=brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],f['issued_at_utc'],lead=2)
            self.assertFalse(r['data_valid']);self.assertIsNone(r['prepare_by_utc']);self.assertIsNone(r['gfs_minimum_c'])

    def test_future_issue_and_future_context_cannot_be_used(self):
        f=forecast('fixture')
        r=brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],(ISSUE-timedelta(hours=1)).isoformat())
        self.assertFalse(r['data_valid'])
        c={'phase':'bloom','phase_recorded_at_utc':(ISSUE+timedelta(hours=1)).isoformat(),'review_status':'reviewed','reviewed_at_utc':(ISSUE+timedelta(hours=1)).isoformat(),'agreed_protocol':'Test'}
        r=self.call(context=c);self.assertFalse(r['phase_known_at_issue']);self.assertFalse(r['protocol_reviewed_at_issue'])

    def test_missing_wind_cloud_do_not_claim_inversion_or_equipment_commands(self):
        r=self.call();self.assertIsNone(r['conditions']['wind_10m_range_ms']);self.assertIsNone(r['conditions']['cloud_range_pct'])
        self.assertFalse(r['conditions']['inversion_verified']);self.assertIsNone(r['protection_start_stop_instruction'])

    def test_completed_window_not_current_warning(self):
        f=forecast('fixture');r=brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],(START+timedelta(hours=15)).isoformat(),scope='field')
        self.assertEqual(r['window_state'],'past');self.assertFalse(r['operational_actionable'])

    def test_measured_cold_does_not_offer_a_future_gfs_prepare_deadline(self):
        f=forecast('fixture');now=START+timedelta(hours=1)
        sensor={'status':'fresh_checked','temperature_c':-1.,'observed_at_utc':now.isoformat()}
        r=brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],now.isoformat(),scope='field',lead=2,sensor=sensor)
        self.assertTrue(r['observed_threshold_disagreement']);self.assertEqual(r['matching_gfs_at_observation_c'],2.)
        self.assertIsNone(r['prepare_by_utc']);self.assertIsNone(r['slack_hours']);self.assertIsNone(r['protection_start_stop_instruction'])


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=FieldStore(self.tmp.name)
        self.p=profile();pr=self.store.save_profile(self.p,now=ISSUE);self.f=self.store.issue(self.p['id'],'z1',forecast(pr['id']),ISSUE)
        self.now=START+timedelta(hours=1)
        csv='sensor_id,observed_at,temperature_c,rh_pct\n'+'\n'.join(f't1,{(START+timedelta(minutes=i*30)).isoformat()},{1-i*.1},80' for i in range(3))
        self.store.import_measurements(self.p['id'],csv,now=self.now)

    def test_future_imports_and_future_qc_do_not_change_historical_context(self):
        before=sensor_context(self.store,self.f,self.now)
        m=self.store.records('measurement')[0];self.store.review_measurement(m['id'],'rejected','Future review',now=self.now+timedelta(hours=1))
        after=sensor_context(self.store,self.f,self.now)
        self.assertEqual(before,after);self.assertEqual(after['temperature_c'],.8);self.assertAlmostEqual(after['temperature_change_c_per_h'],-.2)

    def test_sensor_changed_since_issue_is_not_used_as_compatible(self):
        p=deepcopy(self.p);p['sensors'][0]['height_m']=.5
        self.store.save_profile(p,self.f['payload']['profile_revision'],self.now)
        r=sensor_context(self.store,self.f,self.now);self.assertIsNone(r['temperature_c']);self.assertTrue(r['profile_changed_since_issue'])

    def test_changed_zone_location_blocks_old_forecast_sensor_pair(self):
        p=deepcopy(self.p);p['zones'][0]['latitude']+=.1
        self.store.save_profile(p,self.f['payload']['profile_revision'],self.now)
        r=sensor_context(self.store,self.f,self.now);self.assertIsNone(r['temperature_c'])
        self.assertIsNone(r['local_preview']['hours'])

    def test_recent_temperature_is_not_tissue_temperature_or_damage(self):
        r=field_brief(self.store,self.f['id'],self.now)
        self.assertTrue(r['sensor']['not_tissue_temperature']);self.assertIsNone(r['crop_damage_probability']);self.assertFalse(r['local_transfer_validated'])

    def test_stale_measurements_remain_unknown(self):
        r=sensor_context(self.store,self.f,self.now+timedelta(minutes=31))
        self.assertIsNone(r['temperature_c']);self.assertEqual(r['status'],'stale')

    def test_conflicting_accepted_duplicate_cannot_drive_local_preview(self):
        self.store.journal(self.p['id'],{'forecast_id':self.f['id'],'actor':'Test','action':'observe','record_now':True,'protection_status':'not_used'},self.now)
        m=self.store.records('measurement')[0]['payload'].copy();m['temperature_c']=2.5
        self.store.append('measurement',self.p['id'],m,self.now)
        r=sensor_context(self.store,self.f,self.now)
        self.assertEqual(r['local_preview']['status'],'insufficient_recent_measurements');self.assertIsNone(r['local_preview']['hours'])


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        f=forecast('fixture');self.case={'date':'2026-10-02','gfs_temperature_c':[h['temperature_c'] for h in f['hours']],
                                       'issued_at_utc':f['issued_at_utc']}
        self.b=brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],f['issued_at_utc'])
        self.raw={'forecast_sha256':digest(self.case),'action':'verify_data','notes':'Engineering check: collect a local temperature reading'}

    def test_issue_binding_refuses_foreign_or_changed_forecast(self):
        with self.assertRaises(ValueError):result(self.tmp.name,'unknown',self.case,[1.]*14)
        raw=self.raw|{'forecast_sha256':'wrong'}
        with self.assertRaises(ValueError):save_decision(self.tmp.name,self.case,self.b,raw)
        saved=save_decision(self.tmp.name,self.case,self.b,self.raw);case=self.case|{'date':'2026-10-03'}
        with self.assertRaises(ValueError):result(self.tmp.name,saved['id'],case,[1.]*14)

    def test_archived_actions_never_become_field_metrics_or_interviews(self):
        saved=save_decision(self.tmp.name,self.case,self.b,self.raw)
        self.assertTrue(saved['payload']['not_field_metrics']);self.assertTrue(saved['payload']['not_user_interview'])
        self.assertTrue(saved['payload']['not_prospective_validation'])
        self.assertEqual(digest(saved['payload']['forecast_snapshot']),saved['payload']['forecast_sha256'])
        self.assertFalse(FieldStore(self.tmp.name).records('journal'))

    def test_unknown_fact_does_not_supply_minimum_damage_or_savings(self):
        saved=save_decision(self.tmp.name,self.case,self.b,self.raw);truth=[1.]*14;truth[4]=None
        r=result(self.tmp.name,saved['id'],self.case,truth)
        self.assertFalse(r['fact_complete']);self.assertIsNone(r['actual_minimum_c']);self.assertIsNone(r['actual_exposure']['degree_hours_c_h'])
        self.assertIsNone(r['decision_effect_on_crop']);self.assertIsNone(r['confirmed_savings_rub'])
        self.assertEqual(hashlib.sha256(r['source_forecast_canonical_json'].encode()).hexdigest(),r['source_forecast_sha256'])
        self.assertEqual(hashlib.sha256(r['decision_canonical_payload_json'].encode()).hexdigest(),r['decision_payload_sha256'])

    def test_repeat_decision_records_that_facts_were_already_opened(self):
        a=save_decision(self.tmp.name,self.case,self.b,self.raw);self.assertFalse(a['payload']['fact_visible_when_saved'])
        result(self.tmp.name,a['id'],self.case,[-1.]*14)
        b=save_decision(self.tmp.name,self.case,self.b,self.raw);self.assertTrue(b['payload']['fact_visible_when_saved'])
        self.assertTrue(b['payload']['prior_rehearsal_fact_read_ids'])


if __name__=='__main__':unittest.main()
