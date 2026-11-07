"""Pre-test addendum: matched architectures, no test-driven selection or promotion."""
from pathlib import Path
import argparse,sys
from datetime import datetime,timezone
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.farm_context_data import read,dump,digest
from agro_checker import weather_ndmi_experiment as exp
from agro_checker import ndmi_error_experiment as ref
from agro_checker.full_season_intervals import meta_split
from agro_checker.field_index_experiment import predict
from agro_checker.sector_weather_data import D,enrich


def freeze():
    if (D/'diagnostic-freeze.json').exists() or (D/'sources/2026').exists() or (D/'report.json').exists():
        raise ValueError('Diagnostics must freeze once before any2026 weather or evaluation')
    f=read(D/'frozen-selection.json');p=read(D/'protocol.json')
    plan=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
        reason='All 2024 per-family choices selected identical no-weather envelope. Main choice unchanged. Add matched-architecture diagnostics before any2026 input is downloaded.',
        primary_frozen_sha256=digest(D/'frozen-selection.json'),original_protocol_sha256=digest(D/'protocol.json'),
        variants=[dict(family=family,style=style) for style in ['quantile','scenario'] for family in exp.FAMILIES],
        selection='None: fit/evaluate all eight already-defined learned variants, compare rain/soil/both to none with same architecture. No new hyperparameters and no diagnostic can replace primary.',
        promotion_allowed=False,tuning_years=[2024])
    dump(D/'diagnostic-protocol-before-2026.json',plan)
    archive=read(D/'residuals-2024.json')['rows'];meta,cal=meta_split(archive,'2025-01-01');old=read(ref.OLD)
    models={}
    for v in plan['variants']:
        name=v['family']+'__'+v['style'];model=exp.fit_tail(meta,v['family'],v['style'],p,D/'diagnostic-models',old['interval']['radius'][1])
        models[name]=dict(model=model,calibration=exp.calibrate(model,cal),
            passed_2024=not bool(read(D/'selection-table.json')[name]['rejection_reasons']))
    result=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),models=models,
        plan_sha256=digest(D/'diagnostic-protocol-before-2026.json'),primary_frozen_sha256=plan['primary_frozen_sha256'],
        model_code_sha256=digest(ROOT/'agro_checker/weather_ndmi_experiment.py'),
        diagnostic_code_sha256=digest(__file__),all_fit_keys_2024=[ref.key(r) for r in meta],
        calibration_keys_2024=[ref.key(r) for r in cal],promotion_allowed=False)
    dump(D/'diagnostic-freeze.json',result);print('Eight matched diagnostic variants frozen before2026',flush=True)


def evaluate():
    f=read(D/'diagnostic-freeze.json')
    if digest(D/'frozen-selection.json')!=f['primary_frozen_sha256'] or digest(__file__)!=f['diagnostic_code_sha256']:
        raise ValueError('Changed diagnostic or main choice')
    if digest(D/'diagnostic-protocol-before-2026.json')!=f['plan_sha256'] or digest(ROOT/'agro_checker/weather_ndmi_experiment.py')!=f['model_code_sha256']:
        raise ValueError('Changed diagnostic protocol/code')
    rows=enrich(read(ROOT/'data/farm-research/field-decline-model/data-2026.json'),2026)
    old=read(ref.OLD);groups=dict(all2026=rows,spatial2026=[r for r in rows if r['fid'] in ref.HOLDOUT])
    output={};predictions=[]
    for group,rr in groups.items():
        point=predict(old['selected_model'],rr);output[group]={}
        for name,v in f['models'].items():
            low,high=exp.bounds(v['model'],v['calibration'],rr,point)
            output[group][name]=dict(passed_2024=v['passed_2024'],metrics=ref.metrics(rr,low,high))
            predictions.extend(dict(split=group,variant=name,fid=r['fid'],issue=r['issue'],target=r['target'],low=float(low[i]),high=float(high[i])) for i,r in enumerate(rr))
    dump(D/'diagnostic-predictions.json',predictions)
    dump(D/'diagnostic-report.json',dict(results=output,selection_on_2026=False,promotion_allowed=False,
        frozen_sha256=digest(D/'diagnostic-freeze.json'),primary_unchanged_sha256=digest(D/'frozen-selection.json')))
    for name,v in output['all2026'].items():print(name,v,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['freeze','evaluate'])
    {'freeze':freeze,'evaluate':evaluate}[p.parse_args().stage]()
