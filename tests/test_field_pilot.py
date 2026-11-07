from copy import deepcopy
from datetime import datetime, timedelta, timezone
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agro_checker.field_live import candidate_runs, capture_forecast, next_night
from agro_checker.field_server import make_server
from agro_checker.field_store import FieldStore, readiness, seed_demo, stamp, validate_profile
from agro_checker.pipeline import ROOT, UTC

START = datetime(2026, 10, 2, 16, tzinfo=UTC)
ISSUE = START-timedelta(hours=4)
END = START+timedelta(hours=20)


def profile():
    p=json.loads((ROOT/"config/field_profile.json").read_text(encoding="utf-8"))
    p.update(name="Test orchard",agronomist="Test operator",equipment_status="none")
    p["zones"][0].update(latitude=45.03,longitude=39.15,area_ha=2,cultivar="Test",phase="Test",phase_recorded_at_utc=ISSUE.isoformat(),reference_sensor_id="t1",
                         geometry={"type":"Polygon","coordinates":[[[39.15,45.03],[39.16,45.03],[39.16,45.04],[39.15,45.03]]]})
    p["sensors"]=[{"id":"t1","zone_id":"z1","measurement_type":"air_2m","height_m":2.,"interval_minutes":10.,"calibration_status":"checked"}]
    p["decision"].update(required_lead_hours=2,false_alarm_budget_per_month=2,review_status="reviewed",reviewed_by="Test operator",reviewed_at_utc=ISSUE.isoformat(),agreed_protocol="Observe only")
    return p


def forecast(revision, curve=None, issued=ISSUE):
    curve=curve or [2.]*8+[-1.]*6
    return {"origin":"live_gfs","night_date_local":"2026-10-02","run_at_utc":(ISSUE-timedelta(hours=6)).isoformat(),
            "issued_at_utc":issued.isoformat(),"retrieved_by_utc":(issued-timedelta(minutes=1)).isoformat(),"capture_profile_revision":revision,
            "availability_evidence":"observed_retrieval","manifest":[{"sha256":"fixture-only"}],
            "hours":[{"valid_at_utc":(START+timedelta(hours=i)).isoformat(),"temperature_c":v,"rh_pct":80.} for i,v in enumerate(curve)]}


def measurements(curve=None, offset_seconds=0):
    curve=curve or [2.]*8+[-1.]*6
    text=io.StringIO(); w=csv.writer(text);w.writerow(["sensor_id","observed_at","temperature_c","rh_pct"])
    for i,v in enumerate(curve):w.writerow(["t1",(START+timedelta(hours=i,seconds=offset_seconds)).isoformat(),v,80])
    return text.getvalue()


class FieldPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=FieldStore(Path(self.temp.name)/"state")
        self.p=profile();self.pr=self.store.save_profile(self.p,now=ISSUE)

    def issue(self,curve=None,issued=ISSUE):return self.store.issue(self.p["id"],"z1",forecast(self.pr["id"],curve,issued),issued)

    def journal(self,f,action="observe",protection_status="not_used",cost=None):
        return self.store.journal(self.p["id"],{"forecast_id":f["id"],"actor":"Test operator","action":action,"protection_status":protection_status,
                                  "seen_at_utc":(ISSUE+timedelta(minutes=10)).isoformat(),"decision_at_utc":(ISSUE+timedelta(minutes=20)).isoformat(),"cost_rub":cost,"helpful":True},END)

    def test_readiness_draft_unknown_and_real_profile_explicit(self):
        draft=json.loads((ROOT/"config/field_profile.json").read_text(encoding="utf-8"))
        self.assertFalse(readiness(validate_profile(draft))["ready_for_field_decision_review"])
        self.assertTrue(readiness(self.p)["ready_for_field_decision_review"])
        p=deepcopy(self.p);p["mode"]="demo"
        self.assertFalse(readiness(p)["ready_for_field_decision_review"])
        p=deepcopy(self.p);p["decision"]["reviewed_by"]=""
        with self.assertRaises(ValueError):validate_profile(p)

    def test_profile_revision_and_forecast_snapshot_not_overwritten(self):
        f=self.issue();p=deepcopy(self.p);p["zones"][0]["reference_sensor_id"]=""
        self.store.save_profile(p,self.pr["id"],END)
        frozen=self.store.records("forecast")[0]
        self.assertEqual(frozen["payload"]["profile_snapshot"]["zones"][0]["reference_sensor_id"],"t1")
        self.assertEqual(frozen["sha256"],f["sha256"])
        with self.assertRaisesRegex(ValueError,"Паспорт изменился"):
            self.store.save_profile(p,self.pr["id"],END)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as c:c.execute("UPDATE events SET payload='{}'")

    def test_issue_cannot_be_after_night_or_without_actual_retrieval(self):
        f=forecast(self.pr["id"]);f["issued_at_utc"]=START.isoformat()
        with self.assertRaises(ValueError):self.store.issue(self.p["id"],"z1",f,START)
        f=forecast(self.pr["id"]);f["retrieved_by_utc"]=(ISSUE+timedelta(minutes=1)).isoformat()
        with self.assertRaises(ValueError):self.store.issue(self.p["id"],"z1",f,ISSUE)
        f=forecast(self.pr["id"]);f["origin"]="demo"
        with self.assertRaisesRegex(ValueError,"Синтетический"):
            self.store.issue(self.p["id"],"z1",f,ISSUE)

    def test_changed_profile_during_acquisition_rejected(self):
        p=deepcopy(self.p);p["name"]="Changed"
        self.store.save_profile(p,self.pr["id"],ISSUE)
        with self.assertRaisesRegex(ValueError,"во время получения"):
            self.issue()

    def test_csv_rejects_naive_future_ranges_unknown_sensor_and_keeps_raw(self):
        text="sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-02T19:00:00,0,80\nt1,2030-01-01T00:00:00Z,0,80\nt1,2026-10-02T16:00:00Z,90,80\nx,2026-10-02T16:00:00Z,0,80\n"
        result=self.store.import_measurements(self.p["id"],text,now=END)
        self.assertEqual(result["counts"],{"rejected":4})
        self.assertEqual(self.store.records("import_batch")[0]["payload"]["raw_csv"],text)
        with self.assertRaises(ValueError):stamp("2026-10-02T19:00:00")

    def test_duplicate_conflict_and_cross_batch_spike_screening(self):
        a="sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-02T16:00:00Z,0,80\n"
        self.store.import_measurements(self.p["id"],a,now=END)
        duplicate=self.store.import_measurements(self.p["id"],a,now=END)
        self.assertEqual(duplicate["counts"],{"duplicate":1})
        conflict=self.store.import_measurements(self.p["id"],a.replace(",0,80",",2,80"),now=END)
        self.assertEqual(conflict["counts"],{"suspect":1})
        spike=self.store.import_measurements(self.p["id"],a.replace("16:00:00","16:01:00").replace(",0,80",",10,80"),now=END)
        self.assertEqual(spike["counts"],{"suspect":1})

    def test_qc_review_is_audited_not_mutation_and_cannot_override_hard_error(self):
        self.store.import_measurements(self.p["id"],"sensor_id,observed_at,temperature_c,rh_pct\nt1,2026-10-02T16:00:00Z,0,80\nt1,2026-10-02T16:01:00Z,10,80\nt1,2026-10-02T17:00:00Z,100,80\n",now=END)
        records=self.store.records("measurement");suspect=records[1];bad=records[2]
        self.store.review_measurement(suspect["id"],"accepted","Sensor checked",END)
        self.assertEqual(self.store.records("measurement")[1]["payload"]["status"],"suspect")
        self.assertEqual(self.store.measurements(self.p["id"])[1]["effective_status"],"accepted")
        with self.assertRaises(ValueError):self.store.review_measurement(bad["id"],"accepted","Check",END)

    def test_exact_zero_no_frost_missing_point_not_imputed(self):
        f=self.issue([0.]*14);self.journal(f);self.store.import_measurements(self.p["id"],measurements([0.]*14),now=END)
        a=self.store.assess(f,END)
        self.assertEqual(a["outcome"],"correct_negative")
        f2=self.issue();lines=measurements().splitlines();del lines[6]
        # A separate store avoids filling the missing row from the first test import.
        s=FieldStore(Path(self.temp.name)/"missing");pr=s.save_profile(self.p,now=ISSUE);f2=s.issue(self.p["id"],"z1",forecast(pr["id"]),ISSUE)
        s.import_measurements(self.p["id"],"\n".join(lines),now=END)
        s.journal(self.p["id"],{"forecast_id":f2["id"],"actor":"Test","action":"observe","protection_status":"not_used","decision_at_utc":ISSUE.isoformat(),"helpful":None},END)
        a=s.assess(f2,END);self.assertEqual(a["matched_hours"],13);self.assertIsNone(a["outcome"])

    def test_matching_nearest_within_5min_without_interpolation(self):
        f=self.issue();self.journal(f)
        self.store.import_measurements(self.p["id"],measurements(offset_seconds=301),now=END)
        self.assertEqual(self.store.assess(f,END)["matched_hours"],0)
        self.store.import_measurements(self.p["id"],measurements(offset_seconds=-120),now=END)
        self.assertEqual(self.store.assess(f,END)["matched_hours"],14)

    def test_protection_or_unknown_not_false_alarm_across_all_issues(self):
        f=self.issue();f2=self.issue(issued=ISSUE+timedelta(minutes=1))
        self.store.import_measurements(self.p["id"],measurements([3.]*14),now=END)
        self.assertEqual(self.store.assess(f2,END)["status"],"intervention_unknown")
        self.journal(f,action="protection",protection_status="used")
        self.assertEqual(self.store.assess(f2,END)["status"],"intervention_confounded")
        self.assertIsNone(self.store.assess(f2,END)["outcome"])

    def test_canopy_soil_and_unchecked_sensor_not_air_2m_target(self):
        for kind in ["canopy","soil"]:
            s=FieldStore(Path(self.temp.name)/kind);p=deepcopy(self.p);p["sensors"][0]["measurement_type"]=kind
            pr=s.save_profile(p,now=ISSUE);f=s.issue(p["id"],"z1",forecast(pr["id"]),ISSUE);s.import_measurements(p["id"],measurements(),now=END)
            self.assertEqual(s.assess(f,END)["matched_hours"],0)

    def test_latest_prenight_issue_one_score_and_cost_unknown_not_zero(self):
        f=self.issue();f2=self.issue(issued=ISSUE+timedelta(minutes=1));self.journal(f2)
        self.store.import_measurements(self.p["id"],measurements(),now=END)
        state=self.store.state(END)
        self.assertEqual(state["field_metrics"]["unique_site_nights"],1)
        self.assertEqual(state["field_metrics"]["outcomes"],{"hit":1})
        self.assertEqual(state["field_metrics"]["hourly_temperature_mae_c"],0.)
        self.assertIsNone(state["field_metrics"]["reported_cost_rub"])
        self.assertIsNone(state["field_metrics"]["prevented_loss_rub"])

    def test_demo_never_enters_field_metrics_or_becomes_field(self):
        seed_demo(self.store,END);state=self.store.state(END)
        self.assertEqual(state["field_metrics"]["unique_site_nights"],0)
        self.assertEqual({a["outcome"] for a in state["assessments"]},{"hit","miss","false_alarm","correct_negative"})
        r=self.store.profile_record("demo-station");p=deepcopy(r["payload"]);p["mode"]="field"
        with self.assertRaises(ValueError):self.store.save_profile(p,r["id"],END)

    def test_next_night_and_run_search_calendar(self):
        self.assertEqual(next_night(datetime(2026,10,12,18,tzinfo=UTC)).isoformat(),"2026-10-13")
        self.assertEqual(candidate_runs(datetime(2026,10,12,18,tzinfo=UTC))[0].hour,12)
        with self.assertRaisesRegex(ValueError,"координаты"):
            s=FieldStore(Path(self.temp.name)/"draft").init();capture_forecast(s,"orchard-draft","z1",now=ISSUE)

    def test_forecast_unchanged_by_later_measurements(self):
        f=self.issue();self.store.import_measurements(self.p["id"],measurements([-2.]*14),now=END)
        self.assertEqual(self.store.records("forecast")[0]["sha256"],f["sha256"])
        self.assertEqual(self.store.records("forecast")[0]["payload"],f["payload"])

    def test_loopback_api_csrf_and_bounded_paths(self):
        server=make_server(Path(self.temp.name)/"http",0);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f"http://127.0.0.1:{server.server_port}"
        with urlopen(base+"/api/state") as r:state=json.load(r)
        with self.assertRaises(HTTPError):urlopen(Request(base+"/api/demo",data=b"{}",headers={"Content-Type":"application/json"}))
        headers={"Content-Type":"application/json","X-Field-Token":state["token"],"Origin":base}
        with urlopen(Request(base+"/api/demo",data=b"{}",headers=headers)) as r:self.assertEqual(json.load(r)["profile_id"],"demo-station")
        headers["Origin"]="https://external.invalid"
        with self.assertRaises(HTTPError):urlopen(Request(base+"/api/demo",data=b"{}",headers=headers))
        with self.assertRaises(HTTPError):urlopen(base+"/../../config/field_profile.json")


if __name__ == "__main__":unittest.main()
