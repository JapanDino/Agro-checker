from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import csv
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from agro_checker.hourly_study import (
    RH_CANDIDATES, T_CANDIDATES, WEATHER_NAMES, compare_timing_cohorts, fit_hourly, forecast_features, frost_score, frost_timing,
    hourly_block_bootstrap, hourly_features, hourly_score, load_hourly_model, load_hourly_protocol, parse_humidity_observations,
    predict_hourly, replay_hourly, save_hourly_model, timed_frost, validate_hourly,
)
from agro_checker.humidity_archive import RH_FIELDS, load_humidity, seal_humidity
from agro_checker.gfs_covariates import decode_field, field_range
from agro_checker.model_study import load_protocol
from agro_checker.pipeline import ROOT, write_json

try:
    import eccodes as ec
except ImportError:
    ec=None
SCIENCE=all(importlib.util.find_spec(p) for p in ["numpy","sklearn","catboost"])


def synthetic_rows(year,day=1):
    d=date(year,3,day); curve=[7+day*.3-i*.4 for i in range(14)]
    f=forecast_features(d,curve); temperature=hourly_features({"curve":curve,"features":f})
    result=[]
    for i,t in enumerate(curve):
        humidity=75+i*.5
        weather=[t,humidity,t-2,2,3,50,-.4,min(curve),sum(curve)/14,max(curve)-min(curve),78,7,3,50,f[10],f[11]]+[float(i==j) for j in range(14)]
        stamp=(datetime(year,3,day,16,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat()
        result.append({"date":d.isoformat(),"year":year,"hour_index":i,"lead_hours":i+10,"valid_at_utc":stamp,
                       "base":{"temperature":t,"humidity":humidity},"target":{"temperature":t-.5+day*.04+i*.01,"humidity":humidity+2+day*.1+i*.03},
                       "features_temperature":temperature[i],"features_weather":weather})
    return result


def observation_bytes(rows):
    output=io.StringIO(); writer=csv.DictWriter(output,fieldnames=["year","month","day","hour","rhum","rhum_source"])
    writer.writeheader(); writer.writerows(rows)
    return gzip.compress(output.getvalue().encode())


class HourlyTests(unittest.TestCase):
    def setUp(self):
        self.protocol=load_hourly_protocol(ROOT/"config/hourly_study.json")
        self.base=load_protocol(ROOT/"config/model_study.json"); self.base["catboost"]["iterations"]=4

    def test_humidity_targets_filter_source_range_and_duplicate(self):
        rows=[{"year":2025,"month":3,"day":1,"hour":h,"rhum":v,"rhum_source":source} for h,v,source in
              [(0,0,"metar"),(1,95,"isd_lite"),(2,99,"dwd_mosmix"),(3,101,"metar"),(4,"","metar")]]
        parsed=parse_humidity_observations(observation_bytes(rows),["isd_lite","metar"])
        values=[r["value"] for r in parsed.values()]
        self.assertEqual(values,[0.,95.,None,None,None])
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            parse_humidity_observations(observation_bytes(rows+[rows[0]]),["metar"])

    def test_zero_is_not_frost_and_multiple_episodes_do_not_bridge_warm_hours(self):
        curve=[1.,-1.,-2.,0.,2.,-1.]+[1.]*8
        f=frost_timing(curve)
        self.assertEqual(f["cold_bins_hours"],3)
        self.assertEqual(f["longest_cold_bins_hours"],2)
        self.assertEqual(len(f["episodes"]),2)
        self.assertEqual(f["onset_bracket_indices"],[0,1])
        self.assertFalse(frost_timing([0.]*14)["event"])

    def test_window_edges_are_censored_not_claimed_exact_start_end(self):
        f=frost_timing([-1.]*14)
        self.assertTrue(f["left_censored"])
        self.assertTrue(f["right_censored"])
        self.assertIsNone(f["onset_bracket_indices"])
        stamps=[r["valid_at_utc"] for r in synthetic_rows(2025)]
        timed=timed_frost([-1.]*14,stamps)
        self.assertEqual(timed["episodes"][0]["end_bin_boundary_at_utc"],"2025-03-02T06:00:00+00:00")

    def test_missing_targets_do_not_create_zero_duration_or_onset(self):
        self.assertIsNone(frost_timing([None]+[-1.]*13))
        rows=synthetic_rows(2025); rows[0]["target"]["temperature"]=None
        scored=frost_score(rows,[-1.]*14)
        self.assertEqual(scored["complete_nights"],0)
        self.assertEqual(scored["excluded_temperature_nights"],["2025-03-01"])
        self.assertIsNone(scored["cold_bin_duration_mae_all_h"])

    def test_misses_penalize_duration_and_are_not_zero_onset_error(self):
        rows=synthetic_rows(2025)
        for i,r in enumerate(rows): r["target"]["temperature"]=-1. if i>=10 else 1.
        score=frost_score(rows,[1.]*14)
        self.assertEqual(score["misses"],1)
        self.assertEqual(score["cold_bin_duration_mae_cold_h"],4)
        self.assertEqual(score["first_sample_pairs"],0)
        self.assertIsNone(score["onset_mae_interior_hits_h"])

    def test_false_alarm_is_separate_from_first_cold_hour_score(self):
        rows=synthetic_rows(2025)
        for r in rows: r["target"]["temperature"]=1.
        score=frost_score(rows,[1.]*10+[-1.]*4)
        self.assertEqual(score["false_alarms"],1)
        self.assertEqual(score["cold_bin_duration_mae_all_h"],4)
        self.assertIsNone(score["cold_bin_duration_mae_cold_h"])

    def test_onset_interior_metric_has_its_own_denominator(self):
        rows=synthetic_rows(2025)
        for i,r in enumerate(rows): r["target"]["temperature"]=-1. if i>=10 else 1.
        m=frost_score(rows,[1.]*8+[-1.]*6)
        self.assertEqual(m["onset_mae_interior_hits_h"],2)
        self.assertEqual(m["interior_onset_pairs"],1)
        m=frost_score(rows,[-1.]*14)
        self.assertEqual(m["first_cold_sample_mae_hits_h"],10)
        self.assertEqual(m["interior_onset_pairs"],0)
        self.assertIsNone(m["onset_mae_interior_hits_h"])

    def test_paired_timing_excludes_different_hits_and_censored_model_starts(self):
        rows=synthetic_rows(2025)
        for i,r in enumerate(rows): r["target"]["temperature"]=-1. if i>=10 else 1.
        base=frost_score(rows,[1.]*9+[-1.]*5)
        candidate=frost_score(rows,[-1.]*14)
        compared=compare_timing_cohorts(base,candidate)
        self.assertEqual(compared["first_sample_common_hits"]["paired_nights"],1)
        self.assertEqual(compared["first_sample_common_hits"]["baseline_mae_h"],1)
        self.assertEqual(compared["first_sample_common_hits"]["corrected_mae_h"],10)
        self.assertEqual(compared["onset_common_interior_hits"]["paired_nights"],0)
        missed=frost_score(rows,[1.]*14)
        self.assertEqual(compare_timing_cohorts(base,missed)["first_sample_common_hits"]["paired_nights"],0)

    def test_humidity_error_is_percentage_points_and_bounds_are_fixed(self):
        rows=synthetic_rows(2025)
        for r in rows: r["target"]["humidity"]=90.
        self.assertEqual(hourly_score("humidity",rows,[80.]*14)["mae"],10)
        model={"meta":{"name":"lead_hour_median","target":"humidity"},"offsets":[100.]*14}
        self.assertEqual(predict_hourly(model,rows),[100.]*14)

    def test_2025_targets_cannot_enter_fit(self):
        with self.assertRaisesRegex(ValueError,"2025"):
            fit_hourly("identity","temperature",synthetic_rows(2025),self.base)

    def test_humidity_cache_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder); seal_humidity(data,{},[],[],[])
            self.assertEqual(load_humidity(data,{})[0],[])
            (data/"raw/humidity-points.json").write_text("{}",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"hash"):
                load_humidity(data,{})

    @unittest.skipUnless(SCIENCE,"Install requirements-model.txt")
    def test_hourly_bootstrap_preserves_calendar_and_target_pairs(self):
        rows=synthetic_rows(2025,1)+synthetic_rows(2025,8)
        base=[r["target"]["temperature"]+2 for r in rows]
        corrected=[r["target"]["temperature"]+1 for r in rows]
        rows[0]["target"]["temperature"]=None
        result=hourly_block_bootstrap("temperature",rows,base,corrected,{"block_days":[7],"replicates":100,"seed":3})["7"]
        self.assertEqual(result["blocks"],2)
        self.assertAlmostEqual(result["improvement"],1)
        for v in result["interval_95"]: self.assertAlmostEqual(v,1)

    def test_replay_checks_integrity_and_separates_later_observations(self):
        rows=synthetic_rows(2025)
        forecast=[{"valid_at_utc":r["valid_at_utc"],"gfs_temperature_c":2,"corrected_temperature_c":1,"gfs_rh_pct":80,"corrected_rh_pct":85,
                   "observed_temperature_c":-100,"observed_rh_pct":10,"temperature_source":"metar","humidity_source":"metar"} for r in rows]
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder); path=data/"processed/hourly-study/forecast-snapshot.json"
            write_json(path,{"selected_models":{"temperature":"ridge_temperature","humidity":"catboost_weather"},"nights":[{
                "date":"2025-03-01","year":2025,"forecast_run_at_utc":"2025-03-01T06:00:00+00:00","issued_at_utc":"2025-03-01T15:00:00+00:00",
                "gfs_frost":frost_timing([2.]*14),"selected_frost":frost_timing([1.]*14),"hindsight_frost":frost_timing([-100.]*14),"hours":forecast}]})
            import hashlib
            write_json(path.with_name(path.name+".meta.json"),{"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
            replay=replay_hourly(data,"2025-03-01")
            self.assertFalse(replay["selected_frost"]["event"])
            self.assertTrue(all("observed_temperature_c" not in r for r in replay["forecast"]))
            self.assertEqual(replay["hindsight_observations"][0]["observed_temperature_c"],-100)
            path.write_text("{}",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"hash"): replay_hourly(data,"2025-03-01")

    @unittest.skipUnless(SCIENCE,"Install requirements-model.txt")
    def test_poisoned_2025_targets_cannot_change_validation_or_forecasts(self):
        rows=[r for y in range(2022,2026) for d in range(1,5) for r in synthetic_rows(y,d)]
        other=deepcopy(rows)
        for r in other:
            if r["year"]==2025: r["target"]={"temperature":-1000.,"humidity":-1000.}
        self.assertEqual(validate_hourly(rows,self.protocol,self.base),validate_hourly(other,self.protocol,self.base))
        train=[r for r in rows if r["year"]<2024]
        check=[r for r in rows if r["year"]==2025]; changed=[r for r in other if r["year"]==2025]
        for target,names in [("temperature",T_CANDIDATES),("humidity",RH_CANDIDATES)]:
            for n in names:
                model=fit_hourly(n,target,train,self.base)
                self.assertEqual(predict_hourly(model,check),predict_hourly(model,changed))

    @unittest.skipUnless(SCIENCE,"Install requirements-model.txt")
    def test_saved_models_preserve_all_predictions(self):
        train=[r for d in range(1,5) for r in synthetic_rows(2022,d)]; check=synthetic_rows(2025)
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            for target,names in [("temperature",T_CANDIDATES),("humidity",RH_CANDIDATES)]:
                for n in names:
                    model=fit_hourly(n,target,train,self.base); save_hourly_model(model,directory)
                    replay=load_hourly_model(directory,target,n)
                    for a,b in zip(predict_hourly(model,check),predict_hourly(replay,check)): self.assertAlmostEqual(a,b,places=10)


@unittest.skipIf(ec is None,"Install requirements-archive.txt")
class NativeHumidityTests(unittest.TestCase):
    def test_direct_rh_layer_units_and_step(self):
        h=ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
        try:
            attrs={"centre":"kwbc","Ni":2,"Nj":2,"latitudeOfFirstGridPointInDegrees":45.,"longitudeOfFirstGridPointInDegrees":39.,
                   "latitudeOfLastGridPointInDegrees":44.75,"longitudeOfLastGridPointInDegrees":39.25,"iDirectionIncrementInDegrees":.25,
                   "jDirectionIncrementInDegrees":.25,"paramId":260242,"typeOfLevel":"heightAboveGround","level":2,
                   "dataDate":20220301,"dataTime":600,"stepUnits":1,"forecastTime":10}
            for k,v in attrs.items(): ec.codes_set(h,k,v)
            ec.codes_set_values(h,[80,90,95,100]); content=ec.codes_get_message(h)
        finally: ec.codes_release(h)
        run=datetime(2022,3,1,6,tzinfo=timezone.utc)
        result=decode_field(content,{"latitude":44.99,"longitude":39.15},run,run+timedelta(hours=10),"rh",RH_FIELDS)
        self.assertEqual(result["value"],90)
        self.assertEqual(result["units"],"%")
        with self.assertRaises(ValueError): decode_field(content,{"latitude":44.99,"longitude":39.15},run,run+timedelta(hours=11),"rh",RH_FIELDS)
        index=b"1:0:d=2022030106:RH:2 mb:10 hour fcst:\n2:100:d=2022030106:RH:2 m above ground:10 hour fcst:\n3:200:d=2022030106:TMP:surface:10 hour fcst:\n"
        self.assertEqual(field_range(index,run,10,"rh",RH_FIELDS),(100,199))


if __name__=="__main__": unittest.main()
