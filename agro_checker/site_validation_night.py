"""Read-only night evidence for a selected validation version. No fitting or admission."""
from collections import defaultdict,Counter
from datetime import datetime,date,timedelta,timezone
from functools import lru_cache
import math
import re

from .field_store import stamp
from .site_validation_report import report,local_start,metadata_matches,MeasurementIndex,match


def finite(v):return type(v) in [float,int] and math.isfinite(v)


def night_detail(store,profile_id,zone_id,version,night,now=None):
    now=now or datetime.now(timezone.utc)
    if not isinstance(version,str) or not re.fullmatch('[a-f0-9]{32}',version):raise ValueError('Выберите сохранённую версию периода')
    if not isinstance(night,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',night):raise ValueError('Нужна дата вечера YYYY-MM-DD')
    date.fromisoformat(night)
    d=report(store,profile_id,zone_id,now=now,version=version)
    if night not in {n['night'] for n in d['night_choices']}:raise ValueError('Ночь вне выбранного периода')
    scope=d['plan']['scope'];sid=scope.get('reference_sensor_id');profiles=store.records('profile',profile_id)
    pr=next(r for r in profiles if r['id']==d['selected']['profile_revision']);p=pr['payload']
    zone=next(z for z in p['zones'] if z['id']==zone_id)
    sensor=next((s for s in p['sensors'] if s['id']==sid and s['zone_id']==zone_id),None)
    bound=bool(scope.get('site_profile_revision')==pr['id'] and scope.get('orchard_confirmed') and sid and sid==zone.get('reference_sensor_id'))
    sensor_ok=bool(sensor and sensor['measurement_type']=='air_2m' and sensor['height_m'] is not None and abs(sensor['height_m']-2)<=.2 and sensor['calibration_status']=='checked')
    start=local_start(night).astimezone(timezone.utc);stop=start+timedelta(hours=14)
    raw=[r for r in store.measurements(profile_id) if r['payload'].get('zone_id')==zone_id and r['payload'].get('sensor_id')==sid and r['payload'].get('mode')=='field'] if sid else []
    @lru_cache(maxsize=None)
    def stable(at):return metadata_matches(at,sensor,zone,profiles,profile_id)
    samples=[];unplaced=[]
    for r in raw:
        item=r['payload'];at=item.get('observed_at_utc')
        if not at:
            unplaced.append({'id':r['id'],'batch_id':item.get('batch_id'),'line':item.get('line'),'raw_observed_at':item.get('raw',{}).get('observed_at'),
                             'status':r['effective_status'],'reasons':item.get('reasons',[])});continue
        t=stamp(at)
        if not start<=t<stop:continue
        reasons=[]
        if r['effective_status']!='accepted':reasons.append({'rejected':'Измерение отклонено при QC','suspect':'Измерение требует проверки QC','duplicate':'Повтор измерения'}.get(r['effective_status'],'Статус QC не принят'))
        if not finite(item.get('temperature_c')):reasons.append('Нет численной температуры')
        if t>now:reasons.append('Срок ещё не наступил')
        if not bound:reasons.append('Опорная позиция не закреплена в плане и паспорте')
        if not sensor_ok:reasons.append('Нет проверенного датчика воздуха около 2 м')
        if not stable(r['recorded_at_utc']):reasons.append('Метаданные прибора/участка при загрузке не совпадают с планом')
        samples.append({'id':r['id'],'observed_at_utc':t.isoformat(),'temperature_c':item.get('temperature_c') if finite(item.get('temperature_c')) else None,
                        'status':r['effective_status'],'usable':not reasons,'reasons':list(dict.fromkeys(reasons+item.get('reasons',[]))) if reasons else [],
                        'batch_id':item.get('batch_id'),'line':item.get('line'),'review_note':r.get('review_note')})
    samples.sort(key=lambda r:(r['observed_at_utc'],r['id']))
    by_time=defaultdict(list)
    for r in samples:
        if r['usable']:by_time[r['observed_at_utc']].append(r)
    for group in by_time.values():
        if len({r['temperature_c'] for r in group})>1:
            for r in group:r['usable']=False;r['reasons'].append('Конфликт принятых температур в один срок; точка исключена из графика принятой серии')
    usable=[{'id':r['id'],'payload':r} for r in samples if r['usable']]
    index=MeasurementIndex(usable)
    def targets(count,minutes,tolerance):
        out=[]
        for i in range(count):
            t=start+timedelta(minutes=minutes*i);r=match(index,t,tolerance) if t<=now else None
            out.append({'valid_at_utc':t.isoformat(),'state':'pending' if t>now else 'present' if r else 'missing',
                        'measurement_id':r['id'] if r else None,'temperature_c':r['payload']['temperature_c'] if r else None,
                        'observed_at_utc':r['payload']['observed_at_utc'] if r else None})
        return out
    evidence=next((e for e in d['evidence'] if e['night']==night),None)
    f=next((f for f in store.records('forecast',profile_id) if evidence and f['id']==evidence['forecast_id']),None)
    forecast=None
    if f:
        fp=f['payload'];forecast={'id':f['id'],'issued_at_utc':fp['issued_at_utc'],'run_at_utc':fp['run_at_utc'],'recorded_at_utc':f['recorded_at_utc'],
            'profile_revision':fp.get('capture_profile_revision'),'hours':[{'valid_at_utc':h['valid_at_utc'],'temperature_c':h['temperature_c']} for h in fp['hours']]}
    hourly=targets(14,60,300);slots=targets(168,5,60)
    counts={'rows':len(samples),'usable':len(usable),'excluded':len(samples)-len(usable),
            'hourly':dict(Counter(t['state'] for t in hourly)),'slots':dict(Counter(t['state'] for t in slots))}
    return {'schema_version':1,'read_only':True,'generated_at_utc':now.isoformat(),'night':night,'version':version,'selected':d['selected'],
            'start_at_utc':start.isoformat(),'end_at_utc':stop.isoformat(),'samples':samples,'hourly':hourly,'slots':slots,'counts':counts,
            'forecast':forecast,'evidence':evidence,'plan_status':d['plan']['status'],'sensor':sensor,'automatic_admission':False,
            'unplaced_count':len(unplaced),'unplaced':unplaced[:50],
            'limitations':['Серия принятой температуры учитывает QC и метаданные, но не удостоверяет точность прибора.',
                'Ошибочные строки нового CSV, не сохранённые при предпросмотре, отсутствуют в журнале и на графике.',
                'Наличие кривых не означает включение ночи в оценку; причины исключения показаны отдельно.',
                'Часовые цели 19:00–08:00 и пятиминутные слоты 19:00–08:55; 09:00 — конец окна.']}
