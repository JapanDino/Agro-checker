"""Read-only site validation reporting. Never train, approve, import or invent observations."""
from collections import Counter
from bisect import bisect_left,bisect_right
from datetime import datetime, date, time, timedelta, timezone
from functools import lru_cache
import json

from .pipeline import ROOT
from .field_store import stamp, digest, readiness

PLAN = ROOT/'config/site_validation_plan.json'
MSK = timezone(timedelta(hours=3))


def mean(values):return sum(values)/len(values) if values else None
def ratio(a,b):return a/b if b else None
def local_start(night):return datetime.combine(date.fromisoformat(night),time(19),MSK)


def metadata_matches(recorded_at,sensor,zone,profiles,profile_id):
    prior=[r for r in profiles if r['profile_id']==profile_id and stamp(r['recorded_at_utc'])<=stamp(recorded_at)]
    if not prior or not sensor:return False
    at=prior[-1]['payload'];ss=next((s for s in at['sensors'] if s['id']==sensor['id']),None)
    zz=next((v for v in at['zones'] if v['id']==zone['id']),None)
    return ss==sensor and zz is not None and all(zz.get(k)==zone.get(k) for k in ['latitude','longitude','geometry','reference_sensor_id'])


def period(plan, now):
    scope=plan['scope'];start,end=scope.get('start'),scope.get('end')
    if start is None or end is None:return [],None
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if b<a or (b-a).days>365:raise ValueError('Период проверки должен быть от 1 до 366 ночей')
    days=[(a+timedelta(days=i)).isoformat() for i in range((b-a).days+1)]
    return [d for d in days if local_start(d)+timedelta(hours=14)<=now],{'start':start,'end':end,'planned_nights':len(days)}


class MeasurementIndex:
    def __init__(self,rows):
        self.rows=sorted(rows,key=lambda r:stamp(r['payload']['observed_at_utc']))
        self.times=[stamp(r['payload']['observed_at_utc']) for r in self.rows]


def match(rows, when, seconds):
    if isinstance(rows,MeasurementIndex):
        near=rows.rows[bisect_left(rows.times,when-timedelta(seconds=seconds)):bisect_right(rows.times,when+timedelta(seconds=seconds))]
    else:near=[r for r in rows if abs((stamp(r['payload']['observed_at_utc'])-when).total_seconds())<=seconds]
    if not near:return None
    best=min(near,key=lambda r:(abs((stamp(r['payload']['observed_at_utc'])-when).total_seconds()),stamp(r['payload']['observed_at_utc'])))
    at=best['payload']['observed_at_utc']
    if len({r['payload']['temperature_c'] for r in near if r['payload']['observed_at_utc']==at})>1:return None
    return best


def cold_groups(nights):
    days=sorted(date.fromisoformat(d) for d in nights)
    return sum(i==0 or (d-days[i-1]).days>2 for i,d in enumerate(days))


def metrics(pairs, required_lead):
    minimum=[];cold_errors=[];hourly=[];bias=[];outcomes=Counter();cold_dates=[];severe=0;onset=[];duration=[];timely=0;unknown_lead=0
    for f,a in pairs:
        predicted=[h['temperature_c'] for h in f['payload']['hours']];actual=[h['temperature_c'] for h in a['observations']]
        p,t=min(predicted),min(actual);minimum.append(abs(p-t));bias.append(p-t)
        hourly.extend(abs(x-y) for x,y in zip(predicted,actual));outcomes[a['outcome']]+=1
        if t<0:
            cold_dates.append(f['payload']['night_date_local']);cold_errors.append(abs(p-t));duration.append(a['duration_absolute_error_h'])
            if a['outcome']=='miss' and t<=-1:severe+=1
            lead=a['decision_lead_to_onset_lower_bound_h']
            if required_lead is None or lead is None:unknown_lead+=1
            elif lead>=required_lead:timely+=1
        # Conditional timing is disclosed separately from all cold nights/misses.
        if a['outcome']=='hit' and a['hindsight_frost']['first_cold_sample_index']>0 and f['payload']['weather_frost']['first_cold_sample_index']>0:
            onset.append(a['first_sample_absolute_error_h'])
    tp,fn,fp,tn=(outcomes[k] for k in ['hit','miss','false_alarm','correct_negative']);n=len(pairs);cold=tp+fn
    return {'nights':n,'cold_nights':cold,'cold_groups':cold_groups(cold_dates),'minimum_mae_c':mean(minimum),'cold_minimum_mae_c':mean(cold_errors),
            'bias_c':mean(bias),'hourly_mae_c':mean(hourly),'tp':tp if n else None,'fn':fn if n else None,'fp':fp if n else None,'tn':tn if n else None,
            'recall':ratio(tp,cold),'false_alarm_share_alerts':ratio(fp,tp+fp),'false_alarm_rate_warm':ratio(fp,fp+tn),
            'severe_misses':severe if cold else None,'onset_mae_hits_h':mean(onset),'onset_pairs':len(onset),'cold_duration_mae_h':mean(duration),
            'timely_preparation_fraction':ratio(timely,cold) if required_lead is not None else None,'timely_preparation_count':timely,
            'unknown_lead_cold_nights':unknown_lead,'full_width_c':None,'cold_coverage':None}


def report(store, profile_id=None, zone_id=None, plan=None, now=None, version=None):
    now=now or datetime.now(timezone.utc)
    explicit_plan=plan is not None
    plan=plan if plan is not None else json.loads(PLAN.read_text(encoding='utf-8'))
    expected={'primary_hourly_points':14,'minimum_complete_points':14,'nearest_match_minutes':5,'registration_minutes':5,
              'night_start_local':'19:00','night_end_local':'09:00','weather_event_strict_below_c':0,'severe_miss_actual_minimum_lte_c':-1,'interpolate':False}
    if any(plan['truth'].get(k)!=v for k,v in expected.items()) or plan['forecast_selection'].get('preferred_complete_by_local')!='18:00' or plan['forecast_selection'].get('cutoff_local_exclusive')!='19:00':
        raise ValueError('Правила плана отличаются от поддерживаемой версии отчёта; нужна отдельная проверка расчёта')
    all_profiles=store.records('profile')
    latest_profiles={r['profile_id']:r for r in all_profiles}
    profiles={pid:r for pid,r in latest_profiles.items() if r['payload']['mode']=='field'}
    choices=[{'id':r['profile_id'],'name':r['payload']['name'] or 'Паспорт будущего сада',
              'zones':[{'id':z['id'],'name':z['name']} for z in r['payload']['zones']]} for r in profiles.values()]
    if profile_id is None:profile_id=next(iter(profiles),None)
    if profile_id not in profiles:raise KeyError('Выберите реальный полевой паспорт; демонстрации исключены')
    current=profiles[profile_id];zone_id=zone_id or current['payload']['zones'][0]['id'];configuration=None;chosen=None
    if not explicit_plan:
        from .site_validation_plans import context,records
        configuration=context(store,profile_id,zone_id,version)
        plan=configuration['plan'];chosen=next((r for r in records(store,profile_id,zone_id) if r['id']==configuration['selected_version']),None)
    scope=plan['scope'];revision=scope.get('site_profile_revision')
    pinned=next((r for r in all_profiles if r['id']==revision and r['profile_id']==profile_id and r['payload']['mode']=='field'),None)
    p=(pinned or current)['payload'];zones={z['id']:z for z in p['zones']}
    zone_id=zone_id or next(iter(zones))
    if zone_id not in zones:raise KeyError('Участок отсутствует в выбранной версии паспорта')
    z=zones[zone_id];sid=scope.get('reference_sensor_id')
    sensor=next((s for s in p['sensors'] if s['id']==sid and s['zone_id']==zone_id),None)
    completed,bounds=period(plan,now);scheduled=set(completed)
    bound=bool(pinned and scope.get('orchard_confirmed') and sid and sid==z.get('reference_sensor_id'))
    sensor_ok=bool(sensor and sensor['measurement_type']=='air_2m' and sensor['height_m'] is not None and abs(sensor['height_m']-2)<=.2 and sensor['calibration_status']=='checked')
    raw=store.measurements(profile_id)
    relevant=[r for r in raw if r['payload'].get('zone_id')==zone_id and (sid is None or r['payload'].get('sensor_id')==sid)]
    # Coverage counters belong to the selected period, not every import in the sensor's history.
    outside_period_rows=0
    if bounds:
        first=local_start(bounds['start']);last=local_start(bounds['end'])+timedelta(hours=14)
        scoped=[r for r in relevant if r['payload'].get('observed_at_utc') and first<=stamp(r['payload']['observed_at_utc'])<last and stamp(r['payload']['observed_at_utc']).astimezone(MSK).hour not in range(9,19)]
        outside_period_rows=len(relevant)-len(scoped);relevant=scoped
    rows=[r for r in relevant if r['effective_status']=='accepted' and r['payload']['mode']=='field' and r['payload']['temperature_c'] is not None and stamp(r['payload']['observed_at_utc'])<=now]
    # Old import schema has no immutable sensor snapshot. Verify the profile at import time.
    @lru_cache(maxsize=None)
    def stable_at(recorded_at):
        return metadata_matches(recorded_at,sensor,z,all_profiles,profile_id)
    accepted=[r for r in rows if bound and sensor_ok and stable_at(r['recorded_at_utc'])]
    indexed=MeasurementIndex(accepted);accepted_ids={r['id'] for r in accepted}
    truth_complete=0;frequent_found=0
    if bounds:
        for night in completed:
            start=local_start(night)
            truth_complete+=all(match(indexed,start+timedelta(hours=i),300) is not None for i in range(14))
            # Unique fixed slots: dense uploads cannot inflate completeness; no interpolation.
            frequent_found+=sum(match(indexed,start+timedelta(minutes=5*i),60) is not None for i in range(168))
    forecasts=[f for f in store.records('forecast',profile_id) if f['payload']['mode']=='field' and f['payload']['origin']=='live_gfs' and f['payload']['zone_id']==zone_id]
    latest={}
    for f in forecasts:
        night=f['payload']['night_date_local']
        if night not in latest or stamp(f['payload']['issued_at_utc'])>=stamp(latest[night]['payload']['issued_at_utc']):latest[night]=f
    evidence=[];pairs=[];timely_fresh=0;false_dates=[]
    for night,f in sorted(latest.items()):
        a=store.assess(f,now);reasons=list(a['exclusion_reasons']);start=local_start(night)
        if not bounds or night not in scheduled:reasons.append('Вне завершённого назначенного периода')
        if not bound:reasons.append('Участок, версия паспорта и опорная позиция не закреплены в плане')
        if chosen and chosen['payload']['action']=='draft':reasons.append('Черновик условий не является зафиксированным контрольным периодом')
        if f['payload'].get('capture_profile_revision')!=revision:reasons.append('Другая версия паспорта в выдаче')
        if scope.get('validation_season')=='spring' and date.fromisoformat(night).month not in [3,4,5]:reasons.append('За пределами весенней области проверки')
        if p['decision']['weather_threshold_c']!=0:reasons.append('Порог выдачи отличается от погодного порога проверки 0 °C')
        if stamp(f['payload']['issued_at_utc'])>=start or stamp(f['recorded_at_utc'])>=start:reasons.append('Выдача сохранена после начала ночи')
        if any(h.get('measurement_id') and h['measurement_id'] not in accepted_ids for h in a['observations']):reasons.append('Метаданные прибора при импорте не подтверждают опорную позицию')
        if bounds and night in scheduled and not all(match(indexed,stamp(h['valid_at_utc']),300) is not None for h in f['payload']['hours']):
            reasons.append('Нет полной непротиворечивой серии опорной температуры')
        included=not reasons and a['prospective_field_score_eligible']
        if included:pairs.append((f,a))
        age=(stamp(f['payload']['issued_at_utc'])-stamp(f['payload']['run_at_utc'])).total_seconds()/3600
        if bounds and night in scheduled and bound and f['payload'].get('capture_profile_revision')==revision and 0<=age<=plan['forecast_selection']['proposed_max_run_age_hours_at_issue'] and stamp(f['payload']['issued_at_utc'])<=start-timedelta(hours=1) and stamp(f['recorded_at_utc'])<=start-timedelta(hours=1):timely_fresh+=1
        if included and a['outcome']=='false_alarm':false_dates.append(date.fromisoformat(night))
        evidence.append({'night':night,'forecast_id':f['id'],'included':included,'matched_hours':a['matched_hours'],'outcome':a['outcome'] if included else None,'reasons':reasons})
    n_scheduled=len(completed) if bounds else None
    raw_metrics=metrics(pairs,plan['proposed_gates']['human_preparation_support']['required_lead_hours'])
    # A 30-night budget requires a complete calendar window; partial windows are not zeros.
    max_false=max((sum(d<=f<d+timedelta(days=30) for f in false_dates) for d in [date.fromisoformat(v) for v in completed[:-29]]),default=None)
    raw_metrics['max_false_alarms_30_nights']=max_false if pairs and n_scheduled and len(pairs)==n_scheduled and n_scheduled>=30 else None
    coverage={'scheduled_completed_nights':n_scheduled,'captured_nights':len(latest),'registered_captured_nights':sum(n in scheduled for n in latest) if bounds else None,
              'missing_forecast_nights':len(scheduled-set(latest)) if bounds else None,'complete_truth_nights':truth_complete if bounds else None,
              'eligible_nights':len(pairs),'cold_nights':raw_metrics['cold_nights'],'cold_groups':raw_metrics['cold_groups'],
              'measurement_rows':len(relevant),'outside_period_rows':outside_period_rows,'accepted_temperature_rows':len(rows),'unverified_metadata_rows':len(rows)-len(accepted),
              'qc_counts':dict(Counter(r['effective_status'] for r in relevant)),
              'timely_fresh_issue_fraction':ratio(timely_fresh,n_scheduled),'timely_fresh_issues':timely_fresh if bounds else None,
              'complete_truth_fraction':ratio(truth_complete,n_scheduled),'frequent_slots_found':frequent_found if bounds else None,
              'frequent_slots_expected':168*n_scheduled if bounds else None,'frequent_fraction':ratio(frequent_found,168*n_scheduled if bounds else None),
              'missing_forecast_dates':sorted(scheduled-set(latest)) if bounds else []}
    gates=[]
    def gate(key,group,label,value,limit=None,direction='min',unit='',reason='',state=None,count=False):
        if state is None:
            state='not_agreed' if limit is None else 'insufficient_data' if value is None or count and value<limit else 'met' if (value>=limit if direction=='min' else value<=limit) else 'not_met'
        gates.append({'id':key,'group':group,'label':label,'value':value,'limit':limit,'direction':direction,'unit':unit,'status':state,'reason':reason})
    fixed=bool(chosen and chosen['payload']['action']=='fix')
    gate('agreement','setup','Критерии согласованы и зафиксированы',None,state='met' if fixed else 'not_agreed',reason='Согласование заявлено автором записи; личность не проверена. Фиксация условий не даёт допуска модели.' if fixed else 'Критерии пока не зафиксированы. Фиксация доступна только до начала будущего периода.')
    gate('binding','setup','Подтверждены участок, версия и опорный датчик',None,state='met' if bound and sensor_ok else 'insufficient_data',reason='Выбор ориентира и отметка checked не заменяют проверку установки и точности прибора.')
    gate('instrument','setup','Проверка прибора, часов и восстановление копии',None,state='requires_review',reason='Нужны документы проверки и отдельный разбор пограничных температур возле 0 °C.')
    tech=plan['proposed_gates']['technical'];sup=plan['proposed_gates']['human_preparation_support'];desc=plan['proposed_gates']['descriptive_review']
    for key,label,value,limit,unit,iscount in [
        ('technical_nights','Назначенных завершённых ночей',n_scheduled,tech['scheduled_nights_min'],'ночей',True),
        ('timely','Полные выдачи до 18:00 и возрастом ≤18 ч',coverage['timely_fresh_issue_fraction'],tech['timely_fresh_issue_fraction_min'],'fraction',False),
        ('hourly_complete','Ночей с 14 проверенными измерениями',coverage['complete_truth_fraction'],tech['complete_primary_truth_nights_fraction_min'],'fraction',False),
        ('frequent_complete','Полнота пятиминутных слотов ±1 мин',coverage['frequent_fraction'],tech['frequent_samples_fraction_min'],'fraction',False)]:gate(key,'technical',label,value,limit,unit=unit,count=iscount)
    for group,limits in [('descriptive',desc),('support',sup)]:
        for key,label,value in [('complete_nights','Полных проверяемых ночей',len(pairs)),('cold_nights','Холодных ночей',raw_metrics['cold_nights']),('cold_groups','Групп похолоданий',raw_metrics['cold_groups'])]:
            gate(group+'_'+key,group,label,value,limits[key+'_min'],unit='ночей' if key!='cold_groups' else 'групп',count=True,reason='Минимальный объём не доказывает независимость погодных эпизодов.')
    for key,label,value,limit,unit,dr in [
        ('minimum_mae','MAE минимума',raw_metrics['minimum_mae_c'],sup['minimum_mae_c_max'],'°C','max'),
        ('cold_mae','MAE минимума холодных ночей',raw_metrics['cold_minimum_mae_c'],sup['cold_minimum_mae_c_max'],'°C','max'),
        ('bias','Модуль смещения минимума',abs(raw_metrics['bias_c']) if raw_metrics['bias_c'] is not None else None,sup['absolute_bias_c_max'],'°C','max'),
        ('recall','Обнаружение холодных ночей',raw_metrics['recall'],sup['recall_min'],'fraction','min'),
        ('severe','Пропуски при факте ≤−1 °C',raw_metrics['severe_misses'],sup['severe_misses_max'],'случаев','max'),
        ('false_rate','Ложные среди тёплых ночей',raw_metrics['false_alarm_rate_warm'],sup['false_alarm_rate_warm_max'],'fraction','max'),
        ('budget','Ложные за 30 назначенных ночей',raw_metrics['max_false_alarms_30_nights'],sup['false_alarm_budget_per_30_nights'],'случаев','max'),
        ('lead','Своевременная подготовка по L',raw_metrics['timely_preparation_fraction'],sup['timely_preparation_fraction_min'] if sup['required_lead_hours'] is not None else None,'fraction','min')]:gate(key,'support',label,value,limit,unit=unit,direction=dr)
    gate('uncertainty','support','Устойчивость по эпизодам и зонам',None,state='requires_review',reason='Блочные интервалы и независимость эпизодов автоматически не подтверждены; минимум наблюдений не отменяет этот разбор.')
    co=plan['proposed_gates']['correction']
    for key,label,lim,unit in [('improvement','Улучшение MAE минимума всех ночей',co['minimum_mae_relative_improvement_min'],'fraction'),('cold_improvement','Улучшение MAE минимума холодных',co['cold_minimum_mae_relative_improvement_min'],'fraction'),('no_regression','Нет роста пропусков/ложных в каждой зоне',None,''),('paired_interval','Верхняя граница парного интервала MAE ≤0',co['paired_block_interval_upper_mae_difference_max'],'°C')]:
        gate(key,'correction',label,None,lim,unit=unit,direction='max' if key=='paired_interval' else 'min',state='not_available',reason='Сопоставимых выдач зафиксированного локального кандидата нет. GFS не скопирован в колонку модели.')
    tr=plan['proposed_gates']['time_and_range']
    for key,label,value,limit,unit,dr in [
        ('onset','Ошибка первого холодного отсчёта на попаданиях',raw_metrics['onset_mae_hits_h'],tr['onset_mae_hours_max'],'ч','max'),
        ('duration','Ошибка длительности на холодных ночах',raw_metrics['cold_duration_mae_h'],tr['duration_mae_hours_max'],'ч','max'),
        ('range_coverage','Холодное покрытие диапазона',None,tr['cold_coverage_min'],'fraction','min'),
        ('range_width','Средняя полная ширина холодных',None,tr['cold_full_width_mean_c_max'],'°C','max'),
        ('range_p90','90-й процентиль полной ширины',None,tr['cold_full_width_p90_c_max'],'°C','max')]:gate(key,'time_range',label,value,limit,unit=unit,direction=dr,state='requires_review' if key in ['onset','duration'] and value is not None else None,reason='Часовой отсчёт не является точным временем начала; границы и цензурирование требуют разбора.' if key=='onset' else 'Часовая длительность — число холодных отсчётов, не измеренная непрерывная длительность; требуется разбор частого ряда.' if key=='duration' else '')
    night_counts=Counter()
    for r in relevant:
        at=r['payload'].get('observed_at_utc')
        if at:
            local=stamp(at).astimezone(MSK)
            if local.hour>=19 or local.hour<9:night_counts[(local.date() if local.hour>=19 else local.date()-timedelta(days=1)).isoformat()]+=1
    night_choices=[]
    if bounds:
        for i in range(bounds['planned_nights']):
            night=(date.fromisoformat(bounds['start'])+timedelta(days=i)).isoformat()
            night_choices.append({'night':night,'measurement_rows':night_counts[night],'has_gfs':night in latest,'completed':night in scheduled})
    return {'schema_version':1,'generated_at_utc':now.isoformat(),'read_only':True,'night_choices':night_choices,'choices':choices,'selected':{'profile_id':profile_id,'zone_id':zone_id,'profile_name':p['name'] or 'Паспорт будущего сада','zone_name':z['name'],
            'profile_revision':(pinned or current)['id'],'latest_profile_revision':current['id'],'reference_sensor_id':sid,'latitude':z['latitude'],'longitude':z['longitude']},
            'plan':{'status':plan['status'],'sha256':digest(plan),'approved_at':plan.get('approved_at'),'scope':scope,'baseline':'raw_gfs','candidate':plan['models'].get('local_candidate'),'version_id':chosen['id'] if chosen else None,'actor':chosen['payload']['actor'] if chosen else None,'agreement_note':chosen['payload']['agreement_note'] if chosen else None},
            'configuration':configuration,
            'period':bounds,'coverage':coverage,'gfs':raw_metrics,'candidate':None,'difference':None,'gates':gates,'evidence':evidence,
            'overall':{'status':'not_admitted','title':'Местная точность не подтверждена','reason':'Численные критерии, достаточность данных и независимый разбор не дают автоматического допуска. До согласования и проверки — исходный GFS и теневой журнал.','automatic_control_allowed':False},
            'missing_setup':readiness(p)['missing'],'limitations':['Архивные метрики Краснодара и демонстрации исключены.','Проценты без знаменателя не вычисляются.','Пятиминутная полнота — наличие принятых точек в слотах, не сертификат точности прибора.','Применение защиты и неизвестный статус исключают оценку естественной температуры.','Порог 0 °C описывает воздух, а не повреждение яблони.']}
