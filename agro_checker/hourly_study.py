"""Separate hourly T/RH forecasts and explicitly sampled frost timing, forward by season."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import csv
import gzip
import hashlib
import importlib.metadata
import io
import json
import math
from statistics import median

from .covariate_study import load_inputs
from .gfs_covariates import FIELDS, load_covariates
from .humidity_archive import RH_FIELDS, load_humidity
from .model_study import forecast_features, hourly_features, load_protocol
from .pipeline import ROOT, UTC, cached_download, number, write_csv, write_json
from .single_run import load_single_config

TEMPERATURE_NAMES = ["gfs_temperature", "gfs_min", "gfs_range", "overnight_change", "season_sin", "season_cos"] + [f"lead_{i}" for i in range(10,24)]
WEATHER_NAMES = ["gfs_temperature_c", "gfs_rh_pct", "gfs_dewpoint_c", "gfs_dewpoint_spread_c", "gfs_wind_ms", "gfs_cloud_pct",
                 "gfs_temperature_change_c", "night_gfs_min_c", "night_gfs_mean_c", "night_gfs_range_c", "night_rh_mean_pct",
                 "night_rh_range_pp", "night_wind_mean_ms", "night_cloud_mean_pct", "season_sin", "season_cos"] + [f"lead_{i}" for i in range(10,24)]
T_CANDIDATES = ["identity", "lead_hour_median", "ridge_temperature", "ridge_weather", "catboost_weather"]
RH_CANDIDATES = ["identity", "lead_hour_median", "ridge_weather", "catboost_weather"]


def load_hourly_protocol(path):
    p=json.loads(path.read_text(encoding="utf-8"))
    if p["schema_version"]!=1 or p["temperature_candidates"]!=T_CANDIDATES or p["humidity_candidates"]!=RH_CANDIDATES:
        raise ValueError("Unexpected hourly model registry")
    if p["source_config"]!="single_run.json" or p["base_model_protocol"]!="model_study.json":
        raise ValueError("Unexpected hourly source configs")
    if (p["temperature_selection"]!="pooled_validation_hourly_mae_c" or p["humidity_selection"]!="pooled_validation_hourly_mae_percentage_points"
            or p["humidity_forecast_bounds_pct"]!=[0,100] or p["humidity_input_valid_range_pct"]!=[0,110]
            or p["weather_threshold_c"]!=0 or p["observation_sources"]!=["isd_lite","metar"]):
        raise ValueError("Unexpected hourly metric/bounds/source settings")
    return p


def parse_humidity_observations(content, allowed):
    reader=csv.DictReader(io.StringIO(gzip.decompress(content).decode("utf-8-sig")))
    if not {"year","month","day","hour","rhum","rhum_source"}.issubset(reader.fieldnames or []):
        raise ValueError("Missing humidity columns")
    result={}
    for row in reader:
        stamp=datetime(*(int(row[k]) for k in ["year","month","day","hour"]),tzinfo=UTC).isoformat()
        if stamp in result: raise ValueError("Duplicate humidity observation time")
        raw=number(row["rhum"]); source=row["rhum_source"]
        valid=raw is not None and 0<=raw<=100 and source in allowed
        result[stamp]={"value":raw if valid else None,"raw_value":raw,"source":source,
                       "status":"accepted" if valid else "invalid_range" if raw is not None and not 0<=raw<=100 else "excluded_source_or_missing"}
    return result


def verify_fields(row,hour,expected_fields,config):
    for k in ["night_date_local","valid_at_utc","forecast_run_at_utc","issued_at_utc","lead_hours"]:
        if row[k]!=hour[k]: raise ValueError(f"Hourly field join {k} mismatch")
    if set(row["fields"])!=set(expected_fields): raise ValueError("Missing hourly forecast field")
    for key,field in row["fields"].items():
        modified=datetime.fromisoformat(field["archive_last_modified_at_utc"])
        gate=modified+timedelta(minutes=config["archive_availability_buffer_minutes"])
        issue=datetime.fromisoformat(hour["issued_at_utc"])
        if modified!=datetime.fromisoformat(hour["archive_last_modified_at_utc"]): raise ValueError("Different archive object version")
        if field["availability_gate_at_utc"]!=gate.isoformat() or field["available_by_issue_proxy"]!=(gate<=issue): raise ValueError("Invalid hourly availability gate")
        if not gate<=issue: raise ValueError("Late hourly forecast; cannot evaluate complete forecast registry")
        units="degC" if key=="dewpoint" else expected_fields[key]["units"]
        if field["units"]!=units or field["step_type"]!="instant" or not math.isfinite(field["value"]): raise ValueError("Invalid hourly units/step/value")


def build_hourly_rows(hourly,cov,rh,observations,config):
    def index(rows):
        out={}
        for r in rows:
            if r["valid_at_utc"] in out: raise ValueError("Duplicate forecast valid time")
            out[r["valid_at_utc"]]=r
        return out
    fields, humid=index(cov),index(rh)
    expected={h["valid_at_utc"] for h in hourly}
    if set(fields)!=expected or set(humid)!=expected: raise ValueError("Forecast acquisition incomplete; finish humidity-fetch/covariate-fetch")
    nights=defaultdict(list)
    for h in hourly: nights[h["night_date_local"]].append(h)
    result=[]
    for evening,hours in sorted(nights.items()):
        hours=sorted(hours,key=lambda h:h["lead_hours"])
        if len(hours)!=14 or [h["lead_hours"] for h in hours]!=list(range(10,24)): raise ValueError("Expected 14 forecast hours")
        curve=[h["forecast_temperature_c"] for h in hours]
        if any(v is None for v in curve): raise ValueError("Incomplete temperature forecast")
        for h in hours:
            verify_fields(fields[h["valid_at_utc"]],h,FIELDS,config)
            verify_fields(humid[h["valid_at_utc"]],h,RH_FIELDS,config)
        fs=[fields[h["valid_at_utc"]]["fields"] for h in hours]
        raw_rh=[humid[h["valid_at_utc"]]["fields"]["rh"]["value"] for h in hours]
        if any(not 0<=v<=110 for v in raw_rh): raise ValueError("Invalid GFS RH input range")
        wind=[math.hypot(f["u10"]["value"],f["v10"]["value"]) for f in fs]
        clouds=[f["cloud"]["value"] for f in fs]
        f=forecast_features(date.fromisoformat(evening),curve)
        temp_features=hourly_features({"curve":curve,"features":f})
        for i,h in enumerate(hours):
            dew=fs[i]["dewpoint"]["value"]
            change=curve[i]-curve[i-1] if i else curve[1]-curve[0]
            x=[curve[i],raw_rh[i],dew,curve[i]-dew,wind[i],clouds[i],change,min(curve),sum(curve)/14,max(curve)-min(curve),
               sum(raw_rh)/14,max(raw_rh)-min(raw_rh),sum(wind)/14,sum(clouds)/14,f[10],f[11]]+[float(i==j) for j in range(14)]
            obs=observations.get(h["valid_at_utc"],{"value":None,"source":"","status":"missing"})
            result.append({"date":evening,"year":int(evening[:4]),"valid_at_utc":h["valid_at_utc"],"hour_index":i,"lead_hours":h["lead_hours"],
                           "forecast_run_at_utc":h["forecast_run_at_utc"],"issued_at_utc":h["issued_at_utc"],
                           "base":{"temperature":curve[i],"humidity":max(0.,min(100.,raw_rh[i]))},"raw_gfs_rh_pct":raw_rh[i],
                           "target":{"temperature":h["target_temperature_c"],"humidity":obs["value"]},
                           "source":{"temperature":h["target_source"],"humidity":obs["source"]},"humidity_target_status":obs["status"],
                           "features_temperature":temp_features[i],"features_weather":x})
    return result


def fit_hourly(name,target,rows,model_protocol):
    if any(r["year"]>=model_protocol["historical_check_year"] for r in rows): raise ValueError("2025 targets cannot enter hourly fit")
    usable=[r for r in rows if r["target"][target] is not None]
    if not usable: raise ValueError("No valid training target")
    meta={"name":name,"target":target,"training_hours":len(usable),"training_nights":len({r['date'] for r in usable}),
          "training_years":sorted({r['year'] for r in usable}),"training_last_valid_at_utc":max(r['valid_at_utc'] for r in usable)}
    if name=="identity": return {"meta":meta,"offset":0.}
    if name=="lead_hour_median":
        return {"meta":meta,"offsets":[median(r["target"][target]-r["base"][target] for r in usable if r["hour_index"]==i) for i in range(14)]}
    key="features_temperature" if name=="ridge_temperature" else "features_weather"
    x=[r[key] for r in usable]; y=[r["target"][target]-r["base"][target] for r in usable]
    if name.startswith("ridge"):
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import Ridge
        estimator=make_pipeline(StandardScaler(),Ridge(alpha=model_protocol["ridge_alpha"],solver="svd"))
    elif name=="catboost_weather":
        from catboost import CatBoostRegressor
        estimator=CatBoostRegressor(**model_protocol["catboost"])
    else: raise ValueError("Unknown hourly model")
    estimator.fit(x,y)
    return {"meta":{**meta,"feature_key":key},"estimator":estimator}


def predict_hourly(model,rows):
    meta=model["meta"]; name,target=meta["name"],meta["target"]
    if name=="identity": offsets=[0.]*len(rows)
    elif name=="lead_hour_median": offsets=[model["offsets"][r["hour_index"]] for r in rows]
    else:
        x=[r[meta["feature_key"]] for r in rows]
        if "linear" in model:
            m=model["linear"]
            offsets=[m["intercept"]+sum((v-mean)/scale*c for v,mean,scale,c in zip(row,m["scaler_mean"],m["scaler_scale"],m["coefficients"])) for row in x]
        elif name=="catboost_weather": offsets=model["estimator"].predict(x,thread_count=2)
        else: offsets=model["estimator"].predict(x)
    values=[float(r["base"][target]+offset) for r,offset in zip(rows,offsets)]
    if any(not math.isfinite(v) for v in values): raise ValueError("Nonfinite hourly prediction")
    return [max(0.,min(100.,v)) for v in values] if target=="humidity" else values


def hourly_score(target,rows,values):
    if len(rows)!=len(values): raise ValueError("Hourly prediction length mismatch")
    errors=[v-r["target"][target] for r,v in zip(rows,values) if r["target"][target] is not None]
    if not errors: raise ValueError("No paired scoring targets")
    return {"paired_hours":len(errors),"mae":sum(map(abs,errors))/len(errors),"bias":sum(errors)/len(errors),
            "rmse":math.sqrt(sum(e*e for e in errors)/len(errors)),"units":"degC" if target=="temperature" else "percentage_points"}


def hourly_block_bootstrap(target,rows,baseline,corrected,settings):
    import numpy as np
    grouped=defaultdict(list)
    for r,b,c in zip(rows,baseline,corrected):
        actual=r["target"][target]
        if actual is not None: grouped[r["date"]].append(abs(b-actual)-abs(c-actual))
    output={}
    for width in settings["block_days"]:
        blocks=defaultdict(list)
        for evening,errors in grouped.items():
            d=date.fromisoformat(evening)
            blocks[(d.year,(d-date(d.year,3,1)).days//width)].extend(errors)
        values=list(blocks.values()); sums=np.array([sum(v) for v in values]); counts=np.array([len(v) for v in values])
        rng=np.random.default_rng(settings["seed"])
        picks=rng.integers(0,len(values),size=(settings["replicates"],len(values)))
        means=sums[picks].sum(axis=1)/counts[picks].sum(axis=1); low,high=np.quantile(means,[.025,.975])
        output[str(width)]={"block_days":width,"blocks":len(values),"replicates":settings["replicates"],
                            "improvement":float(sums.sum()/counts.sum()),"interval_95":[float(low),float(high)]}
    return output


def frost_timing(curve):
    if len(curve)!=14 or any(v is None or not math.isfinite(v) for v in curve): return None
    cold=[i for i,v in enumerate(curve) if v<0]
    runs=[]
    for i in cold:
        if not runs or i!=runs[-1]["end_index_exclusive"]:
            runs.append({"start_index":i,"end_index_exclusive":i+1})
        else: runs[-1]["end_index_exclusive"]=i+1
    for r in runs:
        r.update(cold_bins_hours=r["end_index_exclusive"]-r["start_index"],left_censored=r["start_index"]==0,right_censored=r["end_index_exclusive"]==14)
    first=cold[0] if cold else None
    return {"event":bool(cold),"first_cold_sample_index":first,"onset_bracket_indices": [first-1,first] if first is not None and first>0 else None,
            "left_censored":first==0,"cold_bins_hours":len(cold),"episodes":runs,
            "right_censored":bool(cold) and cold[-1]==13,"longest_cold_bins_hours":max((r['cold_bins_hours'] for r in runs),default=0)}


def timed_frost(curve,stamps):
    f=frost_timing(curve)
    if f is None: return None
    i=f["first_cold_sample_index"]
    episodes=[]
    for r in f["episodes"]:
        end=stamps[r["end_index_exclusive"]] if r["end_index_exclusive"]<14 else (datetime.fromisoformat(stamps[-1])+timedelta(hours=1)).isoformat()
        episodes.append({**r,"start_sample_at_utc":stamps[r["start_index"]],"end_bin_boundary_at_utc":end})
    return {**f,"first_cold_sample_at_utc":stamps[i] if i is not None else None,
            "onset_bracket_at_utc":[stamps[j] for j in f["onset_bracket_indices"]] if f["onset_bracket_indices"] else None,"episodes":episodes}


def frost_score(rows,values):
    grouped=defaultdict(list)
    for r,v in zip(rows,values): grouped[r["date"]].append((r,v))
    counts=Counter(hits=0,misses=0,false_alarms=0,correct_negatives=0)
    duration,cold_duration,union_duration,first_errors,interior_errors,min_errors=[],[],[],[],[],[]
    records=[]; excluded=[]
    for evening,items in sorted(grouped.items()):
        items=sorted(items,key=lambda p:p[0]["hour_index"])
        if len(items)!=14: raise ValueError("Missing forecast hours in timing score")
        actual=[r["target"]["temperature"] for r,_ in items]; predicted=[v for _,v in items]
        stamps=[r["valid_at_utc"] for r,_ in items]
        a,p=timed_frost(actual,stamps),timed_frost(predicted,stamps)
        if a is None:
            excluded.append(evening); continue
        key="hits" if a["event"] and p["event"] else "misses" if a["event"] else "false_alarms" if p["event"] else "correct_negatives"
        counts[key]+=1
        error=abs(p["cold_bins_hours"]-a["cold_bins_hours"]); duration.append(error)
        if a["event"]: cold_duration.append(error)
        if a["event"] or p["event"]: union_duration.append(error)
        if key=="hits":
            delta=abs(p["first_cold_sample_index"]-a["first_cold_sample_index"]); first_errors.append(delta)
            if not a["left_censored"] and not p["left_censored"]: interior_errors.append(delta)
        min_errors.append(abs(min(predicted)-min(actual)))
        records.append({"date":evening,"outcome":key,"observed_min_c":min(actual),"predicted_min_c":min(predicted),
                        "observed":a,"predicted":p,"duration_absolute_error_h":error})
    mean=lambda v:sum(v)/len(v) if v else None
    return {"complete_nights":len(duration),"excluded_temperature_nights":excluded,"cold_nights":counts["hits"]+counts["misses"],**counts,
            "night_min_mae_c":mean(min_errors),"cold_bin_duration_mae_all_h":mean(duration),"cold_bin_duration_mae_cold_h":mean(cold_duration),
            "cold_bin_duration_mae_union_h":mean(union_duration),"first_cold_sample_mae_hits_h":mean(first_errors),"first_sample_pairs":len(first_errors),
            "onset_mae_interior_hits_h":mean(interior_errors),"interior_onset_pairs":len(interior_errors),"nights":records}


def compare_timing_cohorts(baseline,corrected):
    by_date={r["date"]:r for r in baseline["nights"]}
    if set(by_date)!={r["date"] for r in corrected["nights"]}: raise ValueError("Timing cohorts differ")
    common_hits=[]; interior=[]
    for c in corrected["nights"]:
        b=by_date[c["date"]]
        if c["outcome"]==b["outcome"]=="hits":
            actual=c["observed"]["first_cold_sample_index"]
            item={"date":c["date"],"baseline_error_h":abs(b["predicted"]["first_cold_sample_index"]-actual),
                  "corrected_error_h":abs(c["predicted"]["first_cold_sample_index"]-actual)}
            common_hits.append(item)
            if not any(f["left_censored"] for f in [c["observed"],b["predicted"],c["predicted"]]): interior.append(item)
    def means(items):
        return {"paired_nights":len(items),"baseline_mae_h":sum(r["baseline_error_h"] for r in items)/len(items) if items else None,
                "corrected_mae_h":sum(r["corrected_error_h"] for r in items)/len(items) if items else None,"nights":items}
    return {"first_sample_common_hits":means(common_hits),"onset_common_interior_hits":means(interior)}


def validate_hourly(rows,protocol,model_protocol):
    folds=[]
    for fold in model_protocol["folds"]:
        print(f"Hourly study: {fold['train_years']} -> {fold['validation_year']}",flush=True)
        train=[r for r in rows if r["year"] in fold["train_years"]]
        valid=[r for r in rows if r["year"]==fold["validation_year"]]
        scores={}; timings={}
        for target in ["temperature","humidity"]:
            scores[target]={}
            for name in protocol[f"{target}_candidates"]:
                m=fit_hourly(name,target,train,model_protocol); p=predict_hourly(m,valid)
                scores[target][name]=hourly_score(target,valid,p)
                if target=="temperature": timings[name]=frost_score(valid,p)
        folds.append({**fold,"role":"validation","scores":scores,"frost":timings})
    winners,pooled={},{}
    for target in ["temperature","humidity"]:
        names=protocol[f"{target}_candidates"]
        pooled[target]={n:sum(f["scores"][target][n]["mae"]*f["scores"][target][n]["paired_hours"] for f in folds)/sum(f["scores"][target][n]["paired_hours"] for f in folds) for n in names}
        winners[target]=min(names,key=lambda n:(pooled[target][n],names.index(n)))
    return folds,winners,pooled


def save_hourly_model(model,directory):
    meta=dict(model["meta"]); path=directory/meta["target"]/meta["name"]
    path.mkdir(parents=True,exist_ok=True)
    if "offset" in model: meta["offset"]=model["offset"]
    elif "offsets" in model: meta["offsets"]=model["offsets"]
    else:
        meta["feature_names"]=TEMPERATURE_NAMES if meta["feature_key"]=="features_temperature" else WEATHER_NAMES
        if meta["name"].startswith("ridge"):
            scaler,ridge=[step[1] for step in model["estimator"].steps]
            meta.update(scaler_mean=scaler.mean_.tolist(),scaler_scale=scaler.scale_.tolist(),coefficients=ridge.coef_.tolist(),intercept=float(ridge.intercept_))
        else:
            model["estimator"].save_model(str(path/"model.cbm")); meta["resolved_parameters"]=model["estimator"].get_all_params()
    write_json(path/"model.json",meta)
    return meta


def load_hourly_model(directory,target,name):
    if target not in ["temperature","humidity"] or name not in (T_CANDIDATES if target=="temperature" else RH_CANDIDATES): raise ValueError("Unknown hourly model")
    path=directory/target/name; meta=json.loads((path/"model.json").read_text(encoding="utf-8"))
    if meta["target"]!=target or meta["name"]!=name: raise ValueError("Hourly model schema mismatch")
    model={"meta":meta}
    if "offset" in meta: model["offset"]=meta["offset"]
    elif "offsets" in meta: model["offsets"]=meta["offsets"]
    else:
        expected=TEMPERATURE_NAMES if name=="ridge_temperature" else WEATHER_NAMES
        if meta["feature_names"]!=expected: raise ValueError("Hourly feature schema mismatch")
        if name.startswith("ridge"): model["linear"]=meta
        else:
            from catboost import CatBoostRegressor
            model["estimator"]=CatBoostRegressor(); model["estimator"].load_model(str(path/"model.cbm"))
    return model


def run_hourly_study(protocol_path,data_dir,report_dir):
    protocol=load_hourly_protocol(protocol_path)
    config=load_single_config(ROOT/"config"/protocol["source_config"])
    model_protocol=load_protocol(ROOT/"config"/protocol["base_model_protocol"])
    hourly,nights,temp_meta,obs_meta=load_inputs(data_dir,config)
    cov,cov_failures,_,cov_meta=load_covariates(data_dir,config)
    rh,rh_failures,rh_downloads,rh_meta=load_humidity(data_dir,config)
    if cov_failures or rh_failures: raise ValueError("Resolve forecast acquisition failures first")
    observations={}
    for year in config["years"]:
        content,_=cached_download(f"https://data.meteostat.net/hourly/{year}/{config['station_id']}.csv.gz",data_dir/"raw"/f"meteostat-{config['station_id']}-{year}.csv.gz",True)
        observations.update(parse_humidity_observations(content,protocol["observation_sources"]))
    rows=build_hourly_rows(hourly,cov,rh,observations,config)
    directory=data_dir.parent/"models/research-hourly"
    lock={"protocol":protocol,"model_protocol":model_protocol,"source_config":config,"protocol_saved_at_utc":datetime.now(UTC).isoformat(),
          "protocol_sha256":hashlib.sha256(protocol_path.read_bytes()).hexdigest(),"model_protocol_sha256":hashlib.sha256((ROOT/"config"/protocol["base_model_protocol"]).read_bytes()).hexdigest(),
          "input_provenance":{"temperature":temp_meta,"covariates":cov_meta,"humidity":rh_meta,"observations":obs_meta},
          "source_sha256":{f:hashlib.sha256((ROOT/"agro_checker"/f).read_bytes()).hexdigest() for f in ["hourly_study.py","humidity_archive.py","gfs_covariates.py","gfs_archive.py","model_study.py","covariate_study.py","single_run.py","pipeline.py"]},
          "feature_schemas":{"temperature":TEMPERATURE_NAMES,"weather":WEATHER_NAMES}}
    write_json(directory/"protocol-lock.json",lock)
    folds,winners,pooled=validate_hourly(rows,protocol,model_protocol)
    pre_path=directory/"validation-selection-pre-2025.json"; preselection=None
    if pre_path.exists():
        preselection=json.loads(pre_path.read_text(encoding="utf-8"))
        if (preselection["protocol_sha256"]!=lock["protocol_sha256"] or preselection["model_protocol_sha256"]!=lock["model_protocol_sha256"]
                or preselection["selected_models"]!=winners or preselection["pooled_validation_mae"]!=pooled):
            raise ValueError("Frozen hourly selection changed; do not tune on 2025")
    train=[r for r in rows if r["year"] in model_protocol["final_train_years"]]
    fitted={target:{n:fit_hourly(n,target,train,model_protocol) for n in protocol[f"{target}_candidates"]} for target in ["temperature","humidity"]}
    models={target:{n:save_hourly_model(m,directory) for n,m in ms.items()} for target,ms in fitted.items()}
    selection={**lock,"selection_saved_at_utc":datetime.now(UTC).isoformat(),"folds":folds,"selected_models":winners,"pooled_validation_mae":pooled,"models":models,
               "independent_preselection":preselection,
               "model_artifact_sha256":{str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
                                        for target,methods in fitted.items() for name in methods for p in (directory/target/name).iterdir() if p.suffix in [".json",".cbm"]}}
    write_json(directory/"selection-before-2025.json",selection)
    check=[r for r in rows if r["year"]==model_protocol["historical_check_year"]]
    predictions={target:{n:predict_hourly(m,check) for n,m in ms.items()} for target,ms in fitted.items()}
    scores={target:{n:hourly_score(target,check,p) for n,p in ps.items()} for target,ps in predictions.items()}
    cold_hours=[i for i,r in enumerate(check) if r["target"]["temperature"] is not None and r["target"]["temperature"]<0 and r["target"]["humidity"] is not None]
    humidity_on_cold={n:hourly_score("humidity",[check[i] for i in cold_hours],[p[i] for i in cold_hours]) for n,p in predictions["humidity"].items()} if cold_hours else {}
    frost={n:frost_score(check,p) for n,p in predictions["temperature"].items()}
    all_predictions={target:predict_hourly(fitted[target][winners[target]],rows) for target in winners}
    output=[]; night_rows=defaultdict(list)
    for i,r in enumerate(rows):
        item={"night_date_local":r["date"],"valid_at_utc":r["valid_at_utc"],"forecast_run_at_utc":r["forecast_run_at_utc"],"issued_at_utc":r["issued_at_utc"],
              "lead_hours":r["lead_hours"],"gfs_temperature_c":r["base"]["temperature"],"corrected_temperature_c":all_predictions["temperature"][i],
              "gfs_rh_pct":r["base"]["humidity"],"raw_gfs_rh_pct":r["raw_gfs_rh_pct"],"corrected_rh_pct":all_predictions["humidity"][i],
              "observed_temperature_c":r["target"]["temperature"],"observed_rh_pct":r["target"]["humidity"],
              "temperature_source":r["source"]["temperature"],"humidity_source":r["source"]["humidity"]}
        output.append(item); night_rows[r["date"]].append(item)
    snapshot=[]
    for evening,items in sorted(night_rows.items()):
        stamps=[r["valid_at_utc"] for r in items]
        snapshot.append({"date":evening,"year":int(evening[:4]),"forecast_run_at_utc":items[0]["forecast_run_at_utc"],"issued_at_utc":items[0]["issued_at_utc"],
                         "gfs_frost":timed_frost([r["gfs_temperature_c"] for r in items],stamps),
                         "selected_frost":timed_frost([r["corrected_temperature_c"] for r in items],stamps),
                         "hindsight_frost":timed_frost([r["observed_temperature_c"] for r in items],stamps),"hours":items})
    summary={**selection,"generated_at_utc":datetime.now(UTC).isoformat(),"historical_scores":scores,"historical_frost":frost,
             "humidity_on_observed_cold_hours":humidity_on_cold,
             "paired_timing_vs_gfs":{n:compare_timing_cohorts(frost["identity"],m) for n,m in frost.items()},
             "historical_bootstrap_vs_gfs":{target:{n:hourly_block_bootstrap(target,check,ps["identity"],p,model_protocol["bootstrap"]) for n,p in ps.items()} for target,ps in predictions.items()},
             "packages":{n:importlib.metadata.version(n) for n in ["eccodes","numpy","scikit-learn","catboost","matplotlib"]},
             "humidity_archive":{"expected_hours":len(hourly),"extracted_hours":len(rh),"message_count":len(rh_downloads),"download_bytes":sum(m["bytes"] for m in rh_downloads),"failures":len(rh_failures)},
             "target_audit":{str(y):{"hours":sum(r["year"]==y for r in rows),"temperature_sources":dict(Counter(r["source"]["temperature"] for r in rows if r["year"]==y)),
                                      "humidity_sources":dict(Counter(r["source"]["humidity"] for r in rows if r["year"]==y)),
                                      "humidity_statuses":dict(Counter(r["humidity_target_status"] for r in rows if r["year"]==y)),
                                      "temperature_usable_hours":sum(r["year"]==y and r["target"]["temperature"] is not None for r in rows),
                                      "humidity_usable_hours":sum(r["year"]==y and r["target"]["humidity"] is not None for r in rows)} for y in config["years"]}}
    processed=data_dir/"processed/hourly-study"
    write_csv(processed/"forecasts.csv",output)
    write_json(processed/"forecast-snapshot.json",{"config":config,"selected_models":winners,"nights":snapshot})
    write_json(processed/"forecast-snapshot.json.meta.json",{"sha256":hashlib.sha256((processed/"forecast-snapshot.json").read_bytes()).hexdigest()})
    report_dir.mkdir(parents=True,exist_ok=True)
    write_json(report_dir/"HOURLY_STUDY_SUMMARY.json",summary)
    plot_hourly(summary,snapshot,report_dir)
    (report_dir/"HOURLY_STUDY_REPORT.md").write_text(report_hourly(summary),encoding="utf-8")
    return summary


def replay_hourly(data_dir,night):
    date.fromisoformat(night)
    path=data_dir/"processed/hourly-study/forecast-snapshot.json"; content=path.read_bytes()
    meta=json.loads(path.with_name(path.name+".meta.json").read_text(encoding="utf-8"))
    if hashlib.sha256(content).hexdigest()!=meta["sha256"]: raise ValueError("Hourly snapshot hash mismatch")
    p=json.loads(content); n=next((n for n in p["nights"] if n["date"]==night),None)
    if n is None: raise ValueError("Night not found")
    role="historical_check_already_viewed" if n["year"]==2025 else "model_selection_replay" if n["year"]==2024 else "in_sample_training_replay"
    keys=["valid_at_utc","gfs_temperature_c","corrected_temperature_c","gfs_rh_pct","corrected_rh_pct"]
    return {"night_date_local":night,"evaluation_status":role,"selected_models":p["selected_models"],"forecast_run_at_utc":n["forecast_run_at_utc"],"issued_at_utc":n["issued_at_utc"],
            "availability_evidence":"archive_timestamp_proxy; first publication unknown","forecast_available_at_utc":None,
            "temperature_target":"station 2m, not flower temperature","humidity_target":"station relative humidity percent",
            "frost_resolution":"one-hour bins within 19:00–09:00 MSK; edge censoring; no continuous-duration claim",
            "gfs_frost":n["gfs_frost"],"selected_frost":n["selected_frost"],"forecast":[{k:r[k] for k in keys} for r in n["hours"]],
            "hindsight_observations":[{k:r[k] for k in ["valid_at_utc","observed_temperature_c","observed_rh_pct","temperature_source","humidity_source"]} for r in n["hours"]],"hindsight_frost":n["hindsight_frost"]}


def plot_hourly(summary,snapshot,report_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout="constrained")
    for ax,target,label in zip(axes,["temperature","humidity"],["Температура, °C","Влажность, п.п."]):
        names=list(summary["pooled_validation_mae"][target]); x=np.arange(len(names))
        ax.bar(x-.17,[summary["pooled_validation_mae"][target][n] for n in names],width=.32,label="2023–2024: выбор")
        ax.bar(x+.17,[summary["historical_scores"][target][n]["mae"] for n in names],width=.32,label="2025: исторический контроль")
        ax.set(xticks=x,xticklabels=names,ylabel="Почасовая MAE",title=label); ax.tick_params(axis="x",labelrotation=30); ax.legend(fontsize=8)
    assets=report_dir/"assets"; assets.mkdir(parents=True,exist_ok=True)
    fig.savefig(assets/"hourly-quality.png",dpi=160); plt.close(fig)
    nights={n["date"]:n for n in snapshot}; examples=summary["protocol"]["report_examples"]
    fig,axes=plt.subplots(len(examples),2,figsize=(14,3.1*len(examples)),layout="constrained")
    for row,night in zip(axes,examples):
        items=nights[night]["hours"]; x=np.arange(14)
        for ax,target,base,corrected,actual,label in [(row[0],"temperature","gfs_temperature_c","corrected_temperature_c","observed_temperature_c","°C"),
                                                   (row[1],"humidity","gfs_rh_pct","corrected_rh_pct","observed_rh_pct","%")]:
            ax.plot(x,[r[base] for r in items],label="GFS",lw=1.5)
            ax.plot(x,[r[corrected] for r in items],label=summary["selected_models"][target],lw=1.5)
            ax.plot(x,[r[actual] if r[actual] is not None else float('nan') for r in items],"o-",label="Факт",markersize=3,lw=1.5)
            ax.set(xticks=[0,3,6,9,12,13],xticklabels=["19","22","01","04","07","08"],xlabel="Час МСК",ylabel=label,title=f"{night} · {target}")
            if target=="temperature": ax.axhline(0,color="grey",ls="--",lw=.8)
            else: ax.set_ylim(0,105)
            ax.legend(fontsize=8)
    fig.savefig(assets/"hourly-examples.png",dpi=160); plt.close(fig)


def report_hourly(s):
    fmt=lambda v:"—" if v is None else f"{v:.3f}" if isinstance(v,float) else str(v)
    selected_t=s["selected_models"]["temperature"]; selected_rh=s["selected_models"]["humidity"]
    base_t=s["historical_scores"]["temperature"]["identity"]; chosen_t=s["historical_scores"]["temperature"][selected_t]
    base_rh=s["historical_scores"]["humidity"]["identity"]; chosen_rh=s["historical_scores"]["humidity"][selected_rh]
    base_f=s["historical_frost"]["identity"]; chosen_f=s["historical_frost"][selected_t]
    paired=s["paired_timing_vs_gfs"][selected_t]["first_sample_common_hits"]
    lines=["# Почасовая температура, влажность и время заморозка", "",f"Расчёт {s['generated_at_utc']}. Краснодар 34929, яблоня, погодный порог `<0 °C`.","",
           "## Результат проверки", "",
           f"В историческом 2025 почасовая MAE T {base_t['mae']:.3f} → {chosen_t['mae']:.3f} °C; RH "
           f"{base_rh['mae']:.3f} → {chosen_rh['mae']:.3f} п.п. Выбор этих моделей сделан по 2023–2024, без настройки на 2025.","",
           f"Качество времени холода: пропуски "
           f"{base_f['misses']} → {chosen_f['misses']} из {base_f['cold_nights']}, ложные тревоги {base_f['false_alarms']} → {chosen_f['false_alarms']}; "
           f"MAE длительности на холодных ночах {base_f['cold_bin_duration_mae_cold_h']:.3f} → {chosen_f['cold_bin_duration_mae_cold_h']:.3f} ч. "
           f"На {paired['paired_nights']} общих попаданиях MAE первого отрицательного отсчёта {paired['baseline_mae_h']:.3f} → {paired['corrected_mae_h']:.3f} ч. "
           "Улучшение общей почасовой MAE не подтверждает улучшение обнаружения или расписания заморозка. "
           "Исследовательские кривые доступны для проверки; модель демонстрации не заменена.","",
           "## Постановка и выбор", "",
           "Прогноз одного выпуска GFS 06 UTC на 14 часов 19:00–08:00 МСК, окно до 09:00 исключая конец. "
           "Отдельные модели уточняют температуру 2 м и относительную влажность 2 м. Прямая база RH взята из `RH:2 m above ground`, "
           "а не вычислена из точки росы. Выпуск, действие, слой, единицы, мгновенный шаг, ближайшая точка и версия объекта проверены; "
           "ETag/Last-Modified совпадают с температурным объектом. Gate — архивный Last-Modified+10 минут, первая публичная доступность неизвестна.","",
           "Протокол `config/hourly_study.json` задан до оценки. Температура: GFS, медиана остатка по часу, Ridge только температуры, "
           "Ridge погоды, CatBoost погоды. Влажность: GFS, медиана по часу, Ridge/CatBoost погоды. "
           "Настройки прежние: Ridge alpha=10/SVD со scaler только на обучении; CatBoost MAE, 200 деревьев, глубина 3, LR=.03, L2=10, фиксированный seed. "
           "Без поиска настроек, early stopping и подбора порога. RH ограничена 0–100% для всех методов по заранее заданному правилу. "
           "Входы — только прогнозы и календарь; измерений и каскада с обученной температурой во входах RH нет.","",
           "Проверка следующих сезонов: 2022 → 2023, 2022–2023 → 2024. Для каждого выхода отдельно выбрана минимальная MAE "
           "объединённых валидационных часов; порядок конфигурации разрешает равенство. Модели для 2025 обучены только на 2022–2023, "
           "без дообучения на 2024. Выбор и модели записаны до расчёта 2025. **2025 ранее просмотрен, это исторический контроль, не слепой тест.**", "",
           f"Выбраны: температура **{s['selected_models']['temperature']}**, влажность **{s['selected_models']['humidity']}**.","",
           "## Цели и полнота", "",
           "Только ISD-lite/METAR; для каждого выхода собственная проверка источника и наличия значения. RH допускается в диапазоне 0–100%. "
           "Станционная RH может быть вычисленной величиной; источник не заменяет независимый QC. Модельные заполнения исключены. "
           "Почасовое обучение/MAE используют все пригодные цели, неполная ночь не заполняется. Для минимума и времени холода нужны все 14 температур; "
           "пропуски RH не исключают пригодную температуру и наоборот.","",
           "| Год | Ожидаемых часов | Пригодная T | Пригодная RH |", "|---|---:|---:|---:|"]
    for y,a in s["target_audit"].items(): lines.append(f"| {y} | {a['hours']} | {a['temperature_usable_hours']} | {a['humidity_usable_hours']} |")
    lines += ["",f"RH: {s['humidity_archive']['extracted_hours']} сообщений, {s['humidity_archive']['download_bytes']/1024**3:.2f} GiB, ошибок {s['humidity_archive']['failures']}. Источники и статусы исключений сохранены в JSON.","",
              "## Почасовое качество", "",
              "| Выход | Метод | MAE 2023–2024 | MAE 2025 | Bias 2025 | Часов 2025 |", "|---|---|---:|---:|---:|---:|"]
    for target,methods in s["historical_scores"].items():
        for n,m in methods.items(): lines.append(f"| {target} ({'°C' if target=='temperature' else 'п.п.'}) | {n} | {s['pooled_validation_mae'][target][n]:.3f} | {m['mae']:.3f} | {m['bias']:.3f} | {m['paired_hours']} |")
    lines += ["", "| Выбранный выход | Снижение MAE относительно GFS | 95% парный диапазон, блок 7 дней |", "|---|---:|---|"]
    for target,name in s["selected_models"].items():
        v=s["historical_bootstrap_vs_gfs"][target][name]["7"]
        lines.append(f"| {target} | {v['improvement']:.3f} | [{v['interval_95'][0]:.3f}, {v['interval_95'][1]:.3f}] |")
    lines += ["", "Bootstrap сохраняет часы вместе в календарных блоках, 5000 повторов; использует вес по пригодным целям, "
              "не сжимает календарь из-за пропусков. Варианты 3/14 дней есть в JSON. Это приблизительная чувствительность "
              "по одному сезону, не гарантия переносимости; несколько сравнений и зависимость событий ограничивают вывод."]
    diagnostic=s["humidity_on_observed_cold_hours"]
    if diagnostic:
        base_rh=diagnostic["identity"]; selected_rh=diagnostic[s["selected_models"]["humidity"]]
        lines += ["",f"Дополнительный диагностический срез: в {base_rh['paired_hours']} часах с фактической T<0 °C MAE влажности "
                  f"{base_rh['mae']:.3f} → {selected_rh['mae']:.3f} п.п. у выбранного метода. "
                  "Это ретроспективная диагностика, не критерий выбора и не настройка по 2025. Улучшение общей RH MAE не гарантирует улучшения в холодные часы."]
    lines += ["", "![Качество почасовых моделей](assets/hourly-quality.png)","", "## Что означает начало и длительность", "",
              "Каждый часовой отсчёт условно представляет следующий час внутри [19:00,09:00). Длительность — число отсчётов T<0, "
              "умноженное на один час; это дискретная оценка, не измеренная непрерывная длительность. Отдельные холодные эпизоды не соединяются "
              "через тёплый промежуток. Первый отрицательный отсчёт задаёт обнаруженный час; при предыдущем неотрицательном отсчёте начало "
              "локализовано только в интервале между ними. Линейная интерполяция и минутная точность не используются.","",
              "Если первый отсчёт уже отрицательный, истинное начало до/в 19:00 неизвестно (left-censored). Если последний отрицательный, "
              "конец после 08:00 неизвестен (right-censored); оценка заканчивается границей окна 09:00. "
              "Ошибка первого холодного часа считается только на попаданиях и отдельно на парах без цензурирования начала. "
              "Пропуски не превращаются в нулевую ошибку времени: показаны отдельно, а ошибка длительности на фактически холодных ночах включает пропуски. "
              "Нулевые тёплые ночи могут скрывать ошибки в общей MAE длительности, поэтому показаны оба среза.","",
              "## Время холода на валидации", "",
              "| Год | Метод T | Пропуски/холодных | Ложные тревоги | MAE длительности холодных ночей ч |", "|---|---|---:|---:|---:|"]
    for fold in s["folds"]:
        for name in dict.fromkeys(["identity",selected_t]):
            m=fold["frost"][name]
            lines.append(f"| {fold['validation_year']} | {name} | {m['misses']}/{m['cold_nights']} | {m['false_alarms']} | {fmt(m['cold_bin_duration_mae_cold_h'])} |")
    lines += ["", "Данные валидации участвуют в выборе метода; их преимущество не является независимым подтверждением.","",
              "## Заморозки и время: исторический 2025", "",
              "| Метод T | Полных ночей | Попадания | Пропуски/холодных | Ложные | MAE минимума °C | MAE длительности все/холодные ч | MAE первого часа на попаданиях ч (n) | MAE начала без границы ч (n) |",
              "|---|---:|---:|---:|---:|---:|---|---|---|"]
    for n,m in s["historical_frost"].items():
        lines.append(f"| {n} | {m['complete_nights']} | {m['hits']} | {m['misses']}/{m['cold_nights']} | {m['false_alarms']} | {fmt(m['night_min_mae_c'])} | "
                     f"{fmt(m['cold_bin_duration_mae_all_h'])} / {fmt(m['cold_bin_duration_mae_cold_h'])} | {fmt(m['first_cold_sample_mae_hits_h'])} ({m['first_sample_pairs']}) | "
                     f"{fmt(m['onset_mae_interior_hits_h'])} ({m['interior_onset_pairs']}) |")
    paired=s["paired_timing_vs_gfs"][s["selected_models"]["temperature"]]
    lines += ["", "Сравнение выбранной модели с GFS на одинаковых ночах (условно на общих попаданиях):", "",
              "| Определение | Одинаковых пар | MAE GFS ч | MAE выбранного ч |", "|---|---:|---:|---:|"]
    for label,k in [("Первый холодный отсчёт","first_sample_common_hits"),("Начало без границы окна у обеих моделей","onset_common_interior_hits")]:
        m=paired[k]
        lines.append(f"| {label} | {m['paired_nights']} | {fmt(m['baseline_mae_h'])} | {fmt(m['corrected_mae_h'])} |")
    lines += ["", "## Холодные ночи и ложные тревоги выбранной модели", "",
              "| Ночь | Исход | Первый холодный отсчёт факт/прогноз МСК | Холодных часов факт/прогноз | Граница начала факт/прогноз |", "|---|---|---|---|---|"]
    selected=s["historical_frost"][s["selected_models"]["temperature"]]
    for r in selected["nights"]:
        if r["outcome"]=="correct_negatives": continue
        def local(f):
            t=f["first_cold_sample_at_utc"]
            return (datetime.fromisoformat(t)+timedelta(hours=3)).strftime("%d.%m %H:%M") if t else "—"
        a,p=r["observed"],r["predicted"]
        lines.append(f"| {r['date']} | {r['outcome']} | {local(a)} / {local(p)} | {a['cold_bins_hours']} / {p['cold_bins_hours']} | {a['left_censored']} / {p['left_censored']} |")
    lines += ["", "![Архивные почасовые прогнозы и факты](assets/hourly-examples.png)","", "## Ограничения и воспроизведение", "",
              "Выбор оптимизирует почасовую MAE, а не обнаружение заморозков. Показанные ошибки времени условны на попаданиях; "
              "нельзя трактовать их без числа пропусков и малого числа пригодных пар начала. Почасовые цели T целочисленны, "
              "соседние часы/холодные ночи зависимы. Температура станции не равна температуре цветков; реальная фаза яблони неизвестна. "
              "Вероятности, интервалы температуры и риск потери урожая не откалиброваны. Новая независимая проверка требуется.","",
              "```powershell", ".\\.venv\\Scripts\\python.exe -m agro_checker humidity-fetch --workers 10",
              ".\\.venv\\Scripts\\python.exe -m agro_checker hourly-study --offline",
              ".\\.venv\\Scripts\\python.exe -m agro_checker hourly-replay --night 2025-03-07", "```", "",
              "Прогнозы всех 368 ночей: `data/processed/hourly-study/forecasts.csv` и проверяемый SHA-256 snapshot; "
              "модели/выбор: `models/research-hourly/`. Прогноз и последующие наблюдения в replay разделены; "
              "обучающие ночи маркируются как in-sample. Прежняя демонстрация не перезаписывается. "
              "[Сводка и происхождение](HOURLY_STUDY_SUMMARY.json).", "",
              "Источник единиц/UTC: [Meteostat](https://dev.meteostat.net/formats); [NOAA GFS](https://www.nco.ncep.noaa.gov/pmb/products/gfs/).", ""]
    return "\n".join(lines)
