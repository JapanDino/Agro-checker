"""Read-only agronomic context from a single issue and causally available sensors."""
from datetime import datetime,timedelta
import math
from .field_store import stamp
from .cold_exposure import exposure
from .hourly_study import timed_frost
from .product_advice import wet_bulb,local_update


def number(value,low,high):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and low<=value<=high


def causal_measurements(store,profile_id,asof):
    reviews={r['payload']['measurement_id']:r for r in store.records('qc_review',profile_id) if stamp(r['recorded_at_utc'])<=asof}
    result=[]
    for r in store.records('measurement',profile_id):
        if stamp(r['recorded_at_utc'])>asof:continue
        p=r['payload'];t=p.get('observed_at_utc')
        if not t or stamp(t)>asof:continue
        review=reviews.get(r['id'])
        result.append(r|{'effective_status':review['payload']['status'] if review else p['status'],
                          'reviewed_at_utc':review['recorded_at_utc'] if review else None})
    return result


def sensor_context(store,record,asof):
    f=record['payload'];profile=f['profile_snapshot'];zone=next(z for z in profile['zones'] if z['id']==f['zone_id'])
    sensor=next((s for s in profile['sensors'] if s['id']==zone.get('reference_sensor_id')),None)
    current=[r for r in store.records('profile',record['profile_id']) if stamp(r['recorded_at_utc'])<=asof]
    latest=current[-1] if current else None
    now_sensor=next((s for s in latest['payload']['sensors'] if sensor and s['id']==sensor['id']),None) if latest else None
    now_zone=next((z for z in latest['payload']['zones'] if z['id']==zone['id']),None) if latest else None
    same_location=now_zone and all(zone.get(k)==now_zone.get(k) for k in ['latitude','longitude'])
    compatible=same_location and sensor and now_sensor and all(sensor.get(k)==now_sensor.get(k) for k in ['zone_id','measurement_type','height_m','calibration_status'])
    checked=compatible and sensor['zone_id']==zone['id'] and sensor['measurement_type']=='air_2m' and number(sensor.get('height_m'),1.8,2.2) and sensor['calibration_status']=='checked'
    ms=causal_measurements(store,record['profile_id'],asof) if checked else []
    usable=[r for r in ms if r['effective_status']=='accepted' and r['payload']['sensor_id']==sensor['id'] and r['payload'].get('zone_id')==zone['id'] and number(r['payload'].get('temperature_c'),-60,60)]
    by_time={}
    # Conflicting accepted values at an identical time are not silently averaged.
    for r in usable:by_time.setdefault(r['payload']['observed_at_utc'],[]).append(r)
    usable=[rr[-1] for rr in by_time.values() if len({r['payload']['temperature_c'] for r in rr})==1]
    usable.sort(key=lambda r:r['payload']['observed_at_utc'])
    last=usable[-1] if usable else None
    age=(asof-stamp(last['payload']['observed_at_utc'])).total_seconds()/60 if last else None
    fresh=age is not None and age<=30 # engineering freshness policy, not learned skill
    recent=[r for r in usable if (asof-stamp(r['payload']['observed_at_utc'])).total_seconds()<=3600]
    rate=None;span=None
    if fresh and len(recent)>=3:
        first=recent[0];dt=(stamp(last['payload']['observed_at_utc'])-stamp(first['payload']['observed_at_utc'])).total_seconds()/3600
        if dt>=.5:rate=(last['payload']['temperature_c']-first['payload']['temperature_c'])/dt;span=dt*60
    ids={r['id'] for r in store.records('forecast',record['profile_id']) if r['payload']['zone_id']==zone['id'] and r['payload']['night_date_local']==f['night_date_local']}
    journals=[r['payload'] for r in store.records('journal',record['profile_id']) if r['payload']['forecast_id'] in ids and stamp(r['recorded_at_utc'])<=asof and stamp(r['payload']['decision_at_utc'])<=asof]
    protection='used' if any(j['action']=='protection' or j.get('protection_status')=='used' for j in journals) else 'not_used' if any(j.get('protection_status')=='not_used' for j in journals) else 'unknown'
    update=local_update(f['hours'],usable,asof.isoformat(),sensor if checked else None,protection,6,3)
    return {'status':'fresh_checked' if fresh else 'stale' if last else 'unverified_or_missing',
            'temperature_c':last['payload']['temperature_c'] if fresh else None,
            'observed_at_utc':last['payload']['observed_at_utc'] if last else None,'age_minutes':age,
            'temperature_change_c_per_h':rate,'trend_span_minutes':span,'measurement_count_last_hour':len(recent),
            'protection_status':protection,'local_preview':update,
            'profile_changed_since_issue':bool(latest and latest['id']!=f['profile_revision']),
            'freshness_policy_minutes':30,'not_tissue_temperature':True}


def brief(hours,run_at,issued_at,asof,threshold=0,lead=None,scope='station_archive',context=None,reference_event=None,sensor=None):
    if not number(threshold,-10,5):raise ValueError('Weather threshold -10..5 required')
    if lead is not None and not number(lead,0,48):raise ValueError('Preparation hours0..48 or unknown')
    run,issue,now=map(stamp,[run_at,issued_at,asof]);context=context or {}
    reasons=[];tasks=[]
    times=[]
    try:times=[stamp(h['valid_at_utc']) for h in hours]
    except (ValueError,KeyError,TypeError):reasons.append('invalid_timestamps')
    valid_times=len(times)==14 and all((b-a).total_seconds()==3600 for a,b in zip(times,times[1:]))
    same_run=all(not h.get('run_at_utc') or stamp(h['run_at_utc'])==run for h in hours)
    values=[h.get('temperature_c') for h in hours]
    complete=len(values)==14 and all(number(v,-90,65) for v in values)
    valid=valid_times and complete and same_run and run<=issue and issue<times[0] and issue<=now
    if not valid_times:reasons.append('incomplete_hourly_window')
    if not complete:reasons.append('temperature_missing_or_invalid')
    if not same_run:reasons.append('mixed_runs')
    if run>issue or issue>now:reasons.append('future_source_or_issue')
    if valid_times and issue>=times[0]:reasons.append('issue_not_before_night')
    phase=context.get('phase')
    phase_at=context.get('phase_recorded_at_utc')
    phase_known=bool(phase and phase_at and stamp(phase_at)<=issue)
    location_known=number(context.get('latitude'),-90,90) and number(context.get('longitude'),-180,180)
    protocol_known=context.get('review_status')=='reviewed' and context.get('reviewed_at_utc') and stamp(context['reviewed_at_utc'])<=issue and bool(context.get('agreed_protocol'))
    if not phase_known:tasks.append('Подтвердить текущую фазу яблони с агрономом')
    if not protocol_known:tasks.append('Согласовать погодный порог и регламент хозяйства')
    if lead is None:tasks.append('Измерить время подготовки людей и оборудования')
    if scope!='field':tasks.append('Подключить выбранный сад и проверить перенос прогноза локальными датчиками')
    if scope=='field' and not location_known:tasks.append('Координаты участка не заданы: сохранённая ячейка не подтверждает прогноз для сада')
    if scope=='field' and (not sensor or sensor['status']!='fresh_checked'):tasks.append('Получить свежее измерение проверенного датчика воздуха на 2 м')
    if sensor and sensor.get('profile_changed_since_issue'):tasks.append('Паспорт изменён после выдачи: получить прогноз с актуальным контекстом участка')
    cold=timed_frost([v-threshold for v in values],[h['valid_at_utc'] for h in hours]) if valid else None
    end=times[-1]+timedelta(hours=1) if valid_times else None
    state='invalid' if not valid else 'past' if now>=end else 'in_progress' if now>=times[0] else 'upcoming'
    measured_cold=bool(sensor and sensor.get('status')=='fresh_checked' and number(sensor.get('temperature_c'),-60,60) and sensor['temperature_c']<threshold)
    matching_forecast=None
    observed_at=stamp(sensor['observed_at_utc']) if sensor and sensor.get('observed_at_utc') else None
    if valid and measured_cold and observed_at and times[0]<=observed_at<=times[-1]:
        i=max(i for i,t in enumerate(times) if t<=observed_at);matching_forecast=values[i]
        if observed_at>times[i] and i<13:matching_forecast+=(values[i+1]-values[i])*(observed_at-times[i]).total_seconds()/3600
    observed_disagreement=matching_forecast is not None and matching_forecast>=threshold
    reference=reference_event if threshold==0 else None
    disagreement=bool(valid and reference is not None and reference!=cold['event'])
    if disagreement:tasks.insert(0,'GFS и станционный детектор расходятся: проверить локальную температуру и обновление прогноза')
    if valid and cold['event']:tasks.insert(0,'Обсудить подготовку по регламенту хозяйства и проверить ограничения оборудования')
    bracket=cold['onset_bracket_at_utc'] if cold else None
    deadline=stamp(bracket[0])-timedelta(hours=lead) if bracket and lead is not None else None
    if measured_cold:
        tasks.insert(0,'Холод уже измерен: проверить текущую ситуацию и согласованные действия с агрономом')
        if observed_disagreement:tasks.insert(1,'Датчик уже ниже порога, GFS на это время выше: проверить отдельное локальное уточнение')
        deadline=None
    if valid and cold['left_censored']:tasks.insert(0,'Холод уже на границе окна: точное раннее начало неизвестно')
    if deadline and deadline<issue:tasks.insert(0,'В исходной выдаче недостаточно времени на заданную подготовку')
    if state=='past':tasks=['Проверить полные измерения этой ночи и сравнить с сохранённым прогнозом',
                           'Дополнить журнал фактическими действиями и осмотром',
                           'Для текущего решения получить новый полный выпуск GFS']
    headline='Данные требуют проверки' if not valid else 'Архивная ночь завершена' if state=='past' else 'Измерен холод: требуется внимание' if measured_cold else 'Ночь требует внимания' if cold['event'] or reference else 'Проверить локальные условия'
    ready=bool(location_known and phase_known and protocol_known and lead is not None and sensor and sensor['status']=='fresh_checked' and not sensor.get('profile_changed_since_issue'))
    return {'scope':scope,'asof_utc':now.isoformat(),'headline':headline,'data_valid':valid,'data_reasons':list(dict.fromkeys(reasons)),
            'run_age_at_issue_hours':(issue-run).total_seconds()/3600,'run_age_asof_hours':(now-run).total_seconds()/3600,
            'window_state':state,'operational_actionable':valid and state!='past' and scope=='field' and ready,
            'location_known':location_known,'planning_context_complete':ready,'deadline_operational_authorized':False,
            'threshold_c':threshold,'required_lead_hours':lead,'phase_known_at_issue':phase_known,'phase':phase if phase_known else None,
            'protocol_reviewed_at_issue':bool(protocol_known),'agreed_protocol':context.get('agreed_protocol') if protocol_known else None,
            'gfs_minimum_c':min(values) if valid else None,'gfs_weather_event':cold['event'] if cold else None,'reference_detector_event_at0':reference,
            'signals_disagree':disagreement,'observed_threshold_disagreement':observed_disagreement,
            'matching_gfs_at_observation_c':matching_forecast,
            'exposure_by_threshold':{str(q):exposure(values,q) if valid else {'status':'unavailable','degree_hours_c_h':None,'cold_bins_h':None} for q in sorted({-3,-2,-1,0,threshold})},
            'onset_bracket_at_utc':bracket,'cold_bins_h':cold['cold_bins_hours'] if cold else None,
            'left_censored':cold['left_censored'] if cold else None,'right_censored':cold['right_censored'] if cold else None,
            'prepare_by_utc':deadline.isoformat() if deadline else None,'slack_hours':(deadline-now).total_seconds()/3600 if deadline else None,
            'deadline_source':'GFS hourly crossing earlier bracket edge; user preparation assumption' if deadline else None,
            'conditions':{'wind_10m_range_ms':range_or_none(hours,'wind_ms',0,100),'cloud_range_pct':range_or_none(hours,'cloud_pct',0,100),
                          'wet_bulb_minimum_c':min_wet_bulb(hours),'inversion_verified':False},
            'sensor':sensor,'next_checks':list(dict.fromkeys(tasks)),'crop_damage_probability':None,'all_clear_allowed':False,
            'protection_start_stop_instruction':None,'local_transfer_validated':False,
            'sensor_accuracy_verified_for_orchard':False,'local_error_range_verified':False,
            'interpretation':'weather air-temperature index, not flower temperature or yield loss'}


def range_or_none(hours,key,low,high):
    values=[h.get(key) for h in hours]
    return [min(values),max(values)] if len(values)==14 and all(number(v,low,high) for v in values) else None


def min_wet_bulb(hours):
    if len(hours)!=14 or any(not number(h.get('temperature_c'),-90,65) or not number(h.get('rh_pct'),0,100) for h in hours):return None
    values=[wet_bulb(h['temperature_c'],h['rh_pct'])['value_c'] for h in hours]
    return min(values) if all(v is not None for v in values) else None


def field_brief(store,forecast_id,now):
    record=next((r for r in store.records('forecast') if r['id']==forecast_id),None)
    if not record:raise ValueError('Unknown saved issue')
    f=record['payload'];p=f['profile_snapshot'];zone=next(z for z in p['zones'] if z['id']==f['zone_id'])
    sensor=sensor_context(store,record,now) if f['mode']=='field' and f['origin']=='live_gfs' else None
    scope='field' if f['mode']=='field' and f['origin']=='live_gfs' else 'synthetic_demo'
    result=brief(f['hours'],f['run_at_utc'],f['issued_at_utc'],now.isoformat(),p['decision']['weather_threshold_c'],p['decision']['required_lead_hours'],scope,zone|p['decision'],sensor=sensor)
    result.update(forecast_id=record['id'],profile_id=record['profile_id'],profile_revision=f['profile_revision'],zone_id=f['zone_id'])
    return result
