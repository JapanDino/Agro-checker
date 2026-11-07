"""Append-only validation configuration. Agreement is reported by a user, never model admission."""
from copy import deepcopy
from datetime import datetime,date,time,timedelta,timezone
import json,math,re
from .field_store import digest
from .pipeline import ROOT

KIND='site_validation_plan'
TEMPLATE=ROOT/'config/site_validation_plan.json'
MSK=timezone(timedelta(hours=3))

# Path, human label, display unit, allowed range. Boolean scientific rules are immutable here.
SPECS=[
 ('technical.scheduled_nights_min','Назначенных ночей для проверки сбора','ночей',1,366),
 ('technical.timely_fresh_issue_fraction_min','Выдач вовремя и нужной свежести','fraction',0,1),
 ('technical.complete_primary_truth_nights_fraction_min','Полных часовых целей','fraction',0,1),
 ('technical.frequent_samples_fraction_min','Полнота пятиминутной регистрации','fraction',0,1),
 ('descriptive_review.complete_nights_min','Полных ночей для предварительной оценки','ночей',1,366),
 ('descriptive_review.cold_nights_min','Холодных ночей для предварительной оценки','ночей',1,366),
 ('descriptive_review.cold_groups_min','Групп холода для предварительной оценки','групп',1,366),
 ('human_preparation_support.complete_nights_min','Полных ночей для помощи при подготовке','ночей',1,366),
 ('human_preparation_support.cold_nights_min','Холодных ночей для помощи при подготовке','ночей',1,366),
 ('human_preparation_support.cold_groups_min','Групп холода для помощи при подготовке','групп',1,366),
 ('human_preparation_support.minimum_mae_c_max','MAE минимума всех ночей','°C',0,20),
 ('human_preparation_support.cold_minimum_mae_c_max','MAE минимума холодных ночей','°C',0,20),
 ('human_preparation_support.absolute_bias_c_max','Модуль смещения минимума','°C',0,20),
 ('human_preparation_support.recall_min','Обнаружение холодных ночей','fraction',0,1),
 ('human_preparation_support.severe_misses_max','Допустимые серьёзные пропуски','случаев',0,366),
 ('human_preparation_support.false_alarm_rate_warm_max','Ложные среди тёплых ночей','fraction',0,1),
 ('human_preparation_support.false_alarm_budget_per_30_nights','Бюджет ложных за 30 назначенных ночей','случаев',0,30),
 ('human_preparation_support.required_lead_hours','Время подготовки L','ч',0,48),
 ('human_preparation_support.timely_preparation_fraction_min','Своевременная подготовка по L','fraction',0,1),
 ('correction.minimum_mae_relative_improvement_min','Улучшение MAE минимума всех ночей над GFS','fraction',0,1),
 ('correction.cold_minimum_mae_relative_improvement_min','Улучшение MAE минимума холодных над GFS','fraction',0,1),
 ('correction.paired_block_interval_upper_mae_difference_max','Верхняя граница парного интервала разницы MAE','°C',-20,0),
 ('time_and_range.onset_mae_hours_max','Ошибка начала холода','ч',0,14),
 ('time_and_range.duration_mae_hours_max','Ошибка длительности холода','ч',0,14),
 ('time_and_range.cold_coverage_min','Покрытие холодных диапазоном','fraction',0,1),
 ('time_and_range.cold_full_width_mean_c_max','Средняя полная ширина холодного диапазона','°C',0,40),
 ('time_and_range.cold_full_width_p90_c_max','90-й процентиль полной ширины','°C',0,40)]
NULLABLE={'human_preparation_support.false_alarm_budget_per_30_nights','human_preparation_support.required_lead_hours'}


class Conflict(ValueError):pass
def template():return json.loads(TEMPLATE.read_text(encoding='utf-8'))
def records(store,pid,zid):return [r for r in store.records(KIND,pid) if r['payload']['zone_id']==zid]
def flattened(plan):return {key:plan['proposed_gates'][key.split('.')[0]][key.split('.')[1]] for key,*_ in SPECS}


def selected(store,pid,zid,version=None):
    versions=records(store,pid,zid)
    if version is not None:
        record=next((r for r in versions if r['id']==version),None)
        if record is None:raise KeyError('Версия настройки не найдена для этого участка')
    else:record=next((r for r in reversed(versions) if r['payload']['action']=='fix'),versions[-1] if versions else None)
    return deepcopy(record['payload']['plan']) if record else template(),record


def context(store,pid,zid,version=None):
    current=store.profile_record(pid);p=current['payload']
    if p['mode']!='field':raise ValueError('Настройка проверки доступна только для полевого паспорта')
    z=next((z for z in p['zones'] if z['id']==zid),None)
    if z is None:raise KeyError('Участок не найден')
    plan,chosen=selected(store,pid,zid,version);versions=records(store,pid,zid)
    sensors=[{**s,'matches_profile_reference':s['id']==z.get('reference_sensor_id')} for s in p['sensors'] if s['zone_id']==zid]
    return {'profile_id':pid,'zone_id':zid,'profile_revision':current['id'],'latest_version':versions[-1]['id'] if versions else None,
            'site_has_identity_and_geometry':bool(p['name'].strip() and z.get('geometry') and z.get('latitude') is not None),
            'selected_version':chosen['id'] if chosen else None,'plan':plan,'criteria_values':flattened(plan),
            'selected_record':{'id':chosen['id'],'actor':chosen['payload']['actor'],'note':chosen['payload']['agreement_note'],'action':chosen['payload']['action'],'recorded_at_utc':chosen['recorded_at_utc']} if chosen else None,
            'schema':[{'key':k,'label':label,'unit':unit,'min':lo,'max':hi,'nullable':k in NULLABLE} for k,label,unit,lo,hi in SPECS],
            'sensors':sensors,'versions':[{'id':r['id'],'action':r['payload']['action'],'actor':r['payload']['actor'],'recorded_at_utc':r['recorded_at_utc'],
            'start':r['payload']['plan']['scope']['start'],'end':r['payload']['plan']['scope']['end'],'sha256':r['payload']['plan_sha256']} for r in reversed(versions)]}


def save(store,raw,now=None):
    now=now or datetime.now(timezone.utc)
    keys={'profile_id','zone_id','profile_revision','expected_latest','source_version','request_id','action','start','end','reference_sensor_id','season','orchard_confirmed','actor','agreement_note','confirmed_agreement','criteria'}
    if not isinstance(raw,dict) or set(raw)!=keys:raise ValueError('Нужны период, опорная позиция, автор и критерии выбранной версии')
    pid,zid=raw['profile_id'],raw['zone_id'];rid=raw['request_id']
    if not all(isinstance(v,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',v) for v in [pid,zid]):raise ValueError('Некорректный паспорт или участок')
    if not isinstance(rid,str) or not re.fullmatch('[a-f0-9]{32}',rid):raise ValueError('Обновите идентификатор сохранения')
    fingerprint=digest(raw)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        versions=records(store,pid,zid)
        old=next((r for r in versions if r['payload']['request_id']==rid),None)
        if old:
            if old['payload']['request_sha256']!=fingerprint:raise Conflict('Этот запрос уже сохранён с другим содержимым')
            return old
        if (versions[-1]['id'] if versions else None)!=raw['expected_latest']:raise Conflict('Настройка уже изменена в другой вкладке. Обновите версии; ваш ввод сохранён в форме')
        current=store.profile_record(pid);p=current['payload']
        if current['id']!=raw['profile_revision']:raise Conflict('Паспорт изменён. Обновите данные перед сохранением настройки')
        if p['mode']!='field':raise ValueError('Демонстрационный паспорт не подходит для местной проверки')
        z=next((z for z in p['zones'] if z['id']==zid),None)
        if z is None:raise ValueError('Участок не найден')
        if raw['source_version'] is not None and not any(r['id']==raw['source_version'] for r in versions):raise ValueError('Исходная версия не принадлежит этому участку')
        if raw['action'] not in ['draft','fix'] or raw['season'] not in ['spring','technical']:raise ValueError('Неизвестный режим настройки')
        if any(type(raw[k]) is not bool for k in ['orchard_confirmed','confirmed_agreement']):raise ValueError('Подтверждения должны быть указаны явно')
        actor=raw['actor'];note=raw['agreement_note']
        if not isinstance(actor,str) or not 1<=len(actor.strip())<=120:raise ValueError('Укажите автора настройки')
        if not isinstance(note,str) or len(note)>3000:raise ValueError('Комментарий должен быть не длиннее 3000 символов')
        if (raw['start'] is None)!=(raw['end'] is None):raise ValueError('Укажите обе даты периода')
        start=end=None
        if raw['start'] is not None:
            if any(not isinstance(raw[k],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw[k]) for k in ['start','end']):raise ValueError('Нужны календарные даты периода')
            start,end=date.fromisoformat(raw['start']),date.fromisoformat(raw['end'])
            if not 2000<=start.year<=2100 or end<start or (end-start).days>365:raise ValueError('Период должен содержать от 1 до 366 ночей')
            if raw['season']=='spring' and any((start+timedelta(days=i)).month not in [3,4,5] for i in range((end-start).days+1)):raise ValueError('Весенняя проверка ограничена мартом–маем. Для проверки сбора выберите технический режим')
        sensor=next((s for s in p['sensors'] if s['id']==raw['reference_sensor_id'] and s['zone_id']==zid),None)
        if raw['reference_sensor_id'] is not None and sensor is None:raise ValueError('Выберите зарегистрированный датчик этого участка; вымышленные ID не сохраняются')
        vals=raw['criteria']
        if not isinstance(vals,dict) or set(vals)!={s[0] for s in SPECS}:raise ValueError('Нужен полный набор численных критериев')
        plan=template()
        for key,label,unit,lo,hi in SPECS:
            v=vals[key]
            if v is None and key in NULLABLE and raw['action']=='draft':pass
            elif type(v) not in [int,float] or not math.isfinite(v) or not lo<=v<=hi or unit in ['ночей','групп','случаев'] and v!=int(v):raise ValueError('Проверьте критерий: '+label)
            a,b=key.split('.');plan['proposed_gates'][a][b]=v
        if raw['action']=='fix':
            if start is None or datetime.combine(start,time(19),MSK)<=now:raise ValueError('Фиксация возможна только до начала первой ночи будущего периода')
            if not raw['confirmed_agreement'] or len(note.strip())<5:raise ValueError('Нужны явное подтверждение согласования и его основание')
            if not raw['orchard_confirmed'] or not p['name'].strip() or not z.get('geometry') or z.get('latitude') is None:raise ValueError('Сначала подтвердите участок, название/код и контур в паспорте')
            if not sensor or sensor['measurement_type']!='air_2m' or sensor['height_m'] is None or abs(sensor['height_m']-2)>.2 or sensor['calibration_status']!='checked':raise ValueError('Нужен проверенный датчик воздуха около 2 м этого участка')
            if z.get('reference_sensor_id')!=sensor['id']:raise ValueError('Сначала назначьте выбранный датчик опорным в паспорте и сохраните его. Старые выдачи не переписываются')
            if any(r['payload']['action']=='fix' and start<=date.fromisoformat(r['payload']['plan']['scope']['end']) and end>=date.fromisoformat(r['payload']['plan']['scope']['start']) for r in versions):raise ValueError('Период пересекается с уже зафиксированным. Для нового протокола назначьте отдельный будущий период')
        plan['scope'].update(start=raw['start'],end=raw['end'],site_profile_revision=current['id'],reference_sensor_id=raw['reference_sensor_id'],
                             orchard_confirmed=raw['orchard_confirmed'],validation_season=raw['season'],planned_year=start.year if start else None,profile_id=pid,zone_id=zid)
        plan.update(status='criteria_fixed' if raw['action']=='fix' else 'draft',approved_by=actor.strip() if raw['action']=='fix' else None,approved_at=now.isoformat() if raw['action']=='fix' else None)
        payload={'schema_version':1,'zone_id':zid,'action':raw['action'],'actor':actor.strip(),'agreement_note':note.strip(),'agreement_attested':raw['confirmed_agreement'] if raw['action']=='fix' else False,
                 'identity_verified':False,'automatic_admission':False,'plan':plan,'plan_sha256':digest(plan),'template_sha256':digest(template()),
                 'source_version':raw['source_version'],'previous_version':raw['expected_latest'],'request_id':rid,'request_sha256':fingerprint}
        return store.append(KIND,pid,payload,now,c)
