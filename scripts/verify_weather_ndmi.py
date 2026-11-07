"""Validate source hashes, chronology, no test selection and unchanged forecasts."""
from pathlib import Path
import sys
from datetime import datetime,timedelta
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.farm_context_data import read,dump,digest
from agro_checker import weather_ndmi_experiment as exp
from agro_checker import ndmi_error_experiment as ref
from agro_checker.sector_weather_data import D,load_hourly,qc,enrich

if __name__=='__main__':
    f=read(D/'frozen-selection.json');r=read(D/'report.json');p=read(D/'protocol.json');diag=read(D/'diagnostic-freeze.json')
    assert digest(D/'frozen-selection.json')==r['frozen_sha256']==diag['primary_frozen_sha256']
    assert digest(D/'protocol.json')==f['protocol_sha256']
    assert digest(ROOT/'agro_checker/weather_ndmi_experiment.py')==f['code_sha256']
    assert digest(ROOT/'agro_checker/sector_weather_data.py')==f['weather_code_sha256']
    assert digest(ROOT/'scripts/weather_ndmi_diagnostics.py')==diag['diagnostic_code_sha256']
    assert digest(ref.OLD)==f['legacy_sha256']
    assert digest(D/'weather-summary-2024.json')==f['weather_2024_summary_sha256']
    assert digest(D/'weather-locations.json')==f['locations_sha256']
    choices,primary=exp.choose(read(D/'selection-table.json'),p)
    assert primary==f['primary']
    assert all(choices[k]['candidate']==f['choices'][k]['candidate'] for k in choices)
    for keys in (f['residual_fit_keys'],f['calibration_keys'],diag['all_fit_keys_2024'],diag['calibration_keys_2024']):
        assert all(k[1].startswith('2024-') and k[2].startswith('2024-') and k[0] not in ref.HOLDOUT for k in keys)
    assert max(k[2] for k in f['residual_fit_keys'])<min(k[1] for k in f['calibration_keys'])
    for fold in f['outer_folds']:
        assert fold['residual_fit_max_target']<fold['calibration_first_issue']
        assert fold['calibration_max_target']<fold['start']
    for year in (2024,2026):
        summary=read(D/f'weather-summary-{year}.json');load_hourly(year)
        for g in summary['grids']:
            data=read(D/f'sources/{year}/{g["grid_id"]}.json');data=data[0] if isinstance(data,list) else data
            qc(data,summary['start'],summary['end'])
    for path in (D/'sources/2026').glob('*.meta.json'):
        retrieved=datetime.fromisoformat(read(path)['retrieved_at_utc'])
        assert datetime.fromisoformat(diag['created_at_utc'])<retrieved
        assert datetime.fromisoformat(f['created_at_utc'])<retrieved
    old=[q for q in read(ROOT/'data/farm-research/field-index-model/predictions.json') if q['split'] in ['temporal2026','spatial2026']]
    oldmap={ref.key(q):q for q in old};preds=read(D/'test-predictions.json');radius=read(ref.OLD)['interval']['radius']
    for family in exp.FAMILIES:
        rows=[q for q in preds if q['split']=='all2026' and q['family']==family]
        assert len(rows)==499 and len({ref.key(q) for q in rows})==499 and set(map(ref.key,rows))==set(oldmap)
        for q in rows:
            baseline=oldmap[ref.key(q)];point=np.array(baseline['prediction']);now=np.array(baseline['persistence'])
            np.testing.assert_allclose(q['point'],point,rtol=0,atol=1e-12)
            np.testing.assert_array_equal(q['actual'],baseline['actual'])
            np.testing.assert_array_equal(q['detector'],point-now<=-.10)
            np.testing.assert_allclose([q['ndvi_low'],q['ndvi_high']],np.clip([point[0]-radius[0],point[0]+radius[0]],-1,1),rtol=0,atol=1e-12)
            a=q['weather_audit'];assert a['complete']
            cutoff=datetime.fromisoformat(q['issue'])-timedelta(hours=48)
            assert datetime.fromisoformat(a['cutoff_utc'])==cutoff
            assert datetime.fromisoformat(a['newest_valid_utc'])<cutoff
    diagnostic=read(D/'diagnostic-predictions.json')
    for name in diag['models']:
        rows=[q for q in diagnostic if q['split']=='all2026' and q['variant']==name]
        assert len(rows)==499 and set(map(ref.key,rows))==set(oldmap)
    for q in read(D/'training-2024.json'):
        assert q['weather_audit']['complete'] and q['issue'].startswith('2024-') and q['fid'] not in ref.HOLDOUT
        assert datetime.fromisoformat(q['weather_audit']['newest_valid_utc'])<datetime.fromisoformat(q['issue'])-timedelta(hours=48)
    protected=read(D/'preservation-before.json')
    changed=[path for path,value in protected.items() if not (ROOT/path).is_file() or digest(ROOT/path)!=value]
    assert not changed,changed[:5]
    result=dict(source_years=[2024,2026],tuning_years=[2024],weather_48h_cutoff_verified=True,
        first_historical_delivery_proven=False,weather_source_hashes_valid=True,
        diagnostic_frozen_before_2026_download=True,only_2024_fit_calibration=True,holdout_excluded=True,
        primary_choice_reproduced=True,unchanged_cohort_pairs=499,legacy_points_and_both_detectors_match=True,
        ndvi_bounds_match=True,all_diagnostics_keep_cohort=True,protected_files=len(protected),changed=changed,
        field_store_files=sum(q.is_file() for q in (ROOT/'data/field').rglob('*')),production_promoted=False,
        frozen_sha256=digest(D/'frozen-selection.json'),diagnostic_frozen_sha256=digest(D/'diagnostic-freeze.json'))
    dump(D/'verification.json',result);print(result)
