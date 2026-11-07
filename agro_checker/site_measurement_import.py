"""Read-only CSV preview and explicit atomic import for a pinned validation period.

No interpolation, model changes, sensor calibration or automatic admission.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import PureWindowsPath
import csv
import hashlib
import io
import math
import re

from .field_store import digest, stamp
from .site_validation_plans import selected, Conflict
from .site_validation_report import MeasurementIndex, match, local_start, period

BASE_KEYS={'profile_id','zone_id','version','csv','filename','unit','time_basis','delimiter'}
MSK=timezone(timedelta(hours=3))
TIME_PATTERN=r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?'


def import_context(store, pid, zid, version):
    if not isinstance(version,str) or not re.fullmatch('[a-f0-9]{32}',version):
        raise ValueError('Сначала сохраните настройку с периодом и опорным датчиком')
    plan, record=selected(store,pid,zid,version)
    scope=plan['scope'];sid=scope.get('reference_sensor_id')
    if not scope.get('start') or not scope.get('end') or not sid:
        raise ValueError('В выбранной версии нужны обе даты и зарегистрированный опорный датчик')
    current=store.profile_record(pid)
    pinned=next((r for r in store.records('profile',pid) if r['id']==scope.get('site_profile_revision')),None)
    if pinned is None or pinned['payload']['mode']!='field':raise ValueError('Не найдена закреплённая версия полевого паспорта')
    p=pinned['payload'];sensor=next((s for s in p['sensors'] if s['id']==sid and s['zone_id']==zid),None)
    z=next((z for z in p['zones'] if z['id']==zid),None)
    cp=current['payload'];cs=next((s for s in cp['sensors'] if s['id']==sid),None);cz=next((z for z in cp['zones'] if z['id']==zid),None)
    if not sensor or sensor['measurement_type']!='air_2m' or sensor['height_m'] is None or abs(sensor['height_m']-2)>.2:
        raise ValueError('Для этой проверки нужен датчик температуры воздуха около 2 м')
    if not z or not cz or cs!=sensor or any(z.get(k)!=cz.get(k) for k in ['latitude','longitude','geometry','reference_sensor_id']):
        raise Conflict('Описание датчика или участка изменилось после настройки. Выберите подходящую версию периода')
    return plan,record,current,sensor


def preview(store, raw, now=None):
    now=now or datetime.now(timezone.utc)
    if not isinstance(raw,dict) or set(raw)!=BASE_KEYS:raise ValueError('Нужны CSV, его единицы, время и выбранная версия периода')
    pid,zid=raw['profile_id'],raw['zone_id']
    if any(not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',v) for v in [pid,zid]):raise ValueError('Некорректный паспорт или участок')
    plan,record,current,sensor=import_context(store,pid,zid,raw['version'])
    content=raw['csv']
    if not isinstance(content,str) or not 0<len(content.encode('utf-8'))<=2_000_000:raise ValueError('Нужен непустой CSV UTF-8 размером не больше 2 МБ')
    if '\x00' in content:raise ValueError('CSV содержит нулевые байты; сохраните текст в UTF-8')
    if not isinstance(raw['filename'],str) or not 1<=len(raw['filename'])<=250:raise ValueError('Проверьте имя файла')
    unit=raw['unit'];basis=raw['time_basis'];separator=raw['delimiter']
    if unit not in ['C','F','K'] or basis not in ['offset','msk','utc'] or separator not in [',',';','\t']:
        raise ValueError('Явно выберите единицы, правило времени и разделитель')
    column={'C':'temperature_c','F':'temperature_f','K':'temperature_k'}[unit]
    reader=csv.DictReader(io.StringIO(content.lstrip('\ufeff')),delimiter=separator,strict=True)
    try:
        headers=reader.fieldnames or []
        if len(headers)!=len(set(headers)) or set(headers)-{'sensor_id','observed_at',column,'rh_pct'} or not {'observed_at',column}<=set(headers):
            raise ValueError('Нужны observed_at и '+column+'; допустимы также sensor_id и rh_pct. Проверьте единицы и разделитель')
        source=[]
        for row in reader:
            if len(source)>=10000:raise ValueError('Один импорт ограничен 10000 строками данных')
            source.append(row)
    except csv.Error as exc:raise ValueError('Некорректная структура CSV: '+str(exc)) from None
    if not 1<=len(source)<=10000:raise ValueError('CSV должен содержать от 1 до 10000 строк данных')
    scope=plan['scope'];sid=sensor['id'];start=local_start(scope['start']);stop=local_start(scope['end'])+timedelta(hours=14)
    old=[r for r in store.measurements(pid) if r['payload'].get('sensor_id')==sid and r['payload'].get('zone_id')==zid]
    existing=defaultdict(list)
    for r in old:
        item=r['payload']
        if r['effective_status'] in ['accepted','suspect'] and item.get('observed_at_utc'):
            existing[stamp(item['observed_at_utc']).isoformat()].append({**item,'status':r['effective_status']})
    rows=[];seen=defaultdict(list);unsorted=False;last=None
    def number(value,label,optional=False):
        v=(value or '').strip()
        if not v and optional:return None
        if separator!=',':v=v.replace(',','.')
        try:n=float(v)
        except ValueError:raise ValueError('Нет числа: '+label) from None
        if not math.isfinite(n):raise ValueError('Нефинитное число: '+label)
        return n
    for line, row in enumerate(source,2):
        item={'line':line,'sensor_id':sid,'observed_at_utc':None,'temperature_c':None,'rh_pct':None,'status':'ready','reasons':[]}
        try:
            if None in row or any(v is None for v in row.values()):raise ValueError('Число полей не совпадает с заголовком')
            if 'sensor_id' in row and row['sensor_id'].strip()!=sid:raise ValueError('ID датчика не совпадает с опорным датчиком периода')
            text=row['observed_at'].strip()
            if not re.fullmatch(TIME_PATTERN,text):raise ValueError('Нужна дата ISO YYYY-MM-DD HH:MM[:SS] с явным правилом часового пояса')
            t=datetime.fromisoformat(text.replace('Z','+00:00'))
            if basis=='offset' and t.tzinfo is None:raise ValueError('Время без Z или смещения; выберите MSK/UTC для времени без пояса')
            if basis!='offset':
                if t.tzinfo is not None:raise ValueError('Выбран режим времени без пояса, но в строке есть смещение')
                t=t.replace(tzinfo=MSK if basis=='msk' else timezone.utc)
            t=t.astimezone(timezone.utc);item['observed_at_utc']=t.isoformat()
            if t>now:raise ValueError('Измерение находится в будущем')
            if t.year<2000:raise ValueError('Дата измерения раньше 2000 года')
            value=number(row[column],'температура')
            c=value if unit=='C' else (value-32)*5/9 if unit=='F' else value-273.15
            if not -60<=c<=60:raise ValueError('Температура после перевода должна быть от −60 до 60 °C')
            item['temperature_c']=round(c,8);item['rh_pct']=number(row.get('rh_pct'),'влажность',True)
            if item['rh_pct'] is not None and not 0<=item['rh_pct']<=100:raise ValueError('Влажность должна быть от 0 до 100 %')
            if last is not None and t<last:unsorted=True
            last=t
            if not start<=t<stop or t.astimezone(MSK).hour in range(9,19):
                item['status']='outside';item['reasons'].append('Вне ночных окон 19:00–09:00 выбранного периода; не сохраняется')
            else:seen[item['observed_at_utc']].append(item)
        except (ValueError,TypeError) as exc:item['status']='invalid';item['reasons'].append(str(exc))
        rows.append(item)
    for at,items in seen.items():
        values={(i['temperature_c'],i['rh_pct']) for i in items};prior=existing[at]
        if len(values)>1 or any((p.get('temperature_c'),p.get('rh_pct')) not in values or p.get('status')=='suspect' for p in prior):
            for i in items:i['status']='conflict';i['reasons'].append('Разные значения или непроверенная запись в один срок; исправьте источник, прошлые данные не переписываются')
        else:
            for i in items[0 if prior else 1:]:i['status']='duplicate';i['reasons'].append('Точный повтор в файле или журнале; повторно не сохраняется')
    # Same screening as the existing importer, including the closest prior and next saved sample.
    timeline={at:items[0] for at,items in seen.items() if items[0]['status']=='ready'}
    for at,items in existing.items():
        accepted=[i for i in items if i.get('status')=='accepted' and i.get('temperature_c') is not None]
        if accepted:timeline.setdefault(at,accepted[0])
    previous=None
    for at,item in sorted(timeline.items()):
        if previous:
            minutes=(stamp(at)-stamp(previous[0])).total_seconds()/60
            if 0<minutes<=30 and abs(item['temperature_c']-previous[1]['temperature_c'])/minutes>.5:
                for target in [item,previous[1]]:
                    if 'line' in target and target['status']=='ready':target['status']='suspect';target['reasons'].append('Скачок более 0,5 °C/мин; проверьте исходные данные')
        previous=(at,item)
    ready=[r for r in rows if r['status']=='ready'];counts=dict(Counter(r['status'] for r in rows))
    complete,bounds=period(plan,now)
    accepted=[r for r in old if r['effective_status']=='accepted' and r['payload'].get('temperature_c') is not None]
    combined=accepted+[{'payload':r} for r in ready]
    index=MeasurementIndex(combined);before=MeasurementIndex(accepted)
    nights=[]
    for night in complete:
        t=local_start(night)
        def found(idx,step,count,tolerance):return sum(match(idx,t+timedelta(minutes=step*i),tolerance) is not None for i in range(count))
        hours=found(index,60,14,300);slots=found(index,5,168,60)
        nights.append({'night':night,'hourly_before':found(before,60,14,300),'hourly_after':hours,'hourly_missing':14-hours,'slots_after':slots,'slots_missing':168-slots})
    blocked=any(counts.get(s,0) for s in ['invalid','conflict','suspect'])
    result={'schema_version':1,'profile_id':pid,'zone_id':zid,'version':record['id'],'profile_revision':current['id'],'sensor_id':sid,
            'period':bounds,'plan_action':record['payload']['action'],'sensor_checked':sensor['calibration_status']=='checked',
            'filename':PureWindowsPath(raw['filename'].replace('/','\\')).name,'source_sha256':hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'unit':unit,'time_basis':basis,'delimiter':separator,'counts':counts,'rows_total':len(rows),'rows':rows,'nights':nights,
            'unsorted':unsorted,'can_commit':bool(ready) and not blocked,'automatic_admission':False,
            'journal_sha256':digest(old),'sensor_snapshot':sensor,'pinned_profile_revision':scope['site_profile_revision']}
    result['preview_sha256']=digest(result)
    return result


def commit(store,raw,now=None):
    now=now or datetime.now(timezone.utc)
    extra={'preview_sha256','request_id','actor','confirmed_source'}
    if not isinstance(raw,dict) or set(raw)!=BASE_KEYS|extra:raise ValueError('Нужен проверенный CSV и явное подтверждение источника')
    if not isinstance(raw['request_id'],str) or not re.fullmatch('[a-f0-9]{32}',raw['request_id']):raise ValueError('Некорректный идентификатор сохранения')
    if raw['confirmed_source'] is not True or not isinstance(raw['actor'],str) or not 1<=len(raw['actor'].strip())<=120:
        raise ValueError('Укажите автора и подтвердите, что файл содержит реальные измерения выбранного прибора')
    request_hash=digest(raw)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        batches=[r for r in store.records('import_batch',raw['profile_id']) if r['payload'].get('request_id')==raw['request_id']]
        if batches:
            if batches[0]['payload']['request_sha256']!=request_hash:raise Conflict('Запрос уже сохранён с другим содержимым')
            return {'batch_id':batches[0]['id'],'counts':batches[0]['payload']['counts'],'replayed':True}
        checked=preview(store,{k:raw[k] for k in BASE_KEYS},now)
        if checked['preview_sha256']!=raw['preview_sha256']:raise Conflict('CSV, паспорт или журнал изменились после проверки. Выполните предпросмотр заново')
        if not checked['can_commit']:raise ValueError('Исправьте ошибки, конфликты или скачки; нужны новые проверенные строки')
        ready=[r for r in checked['rows'] if r['status']=='ready']
        batch={'filename':checked['filename'],'csv_sha256':checked['source_sha256'],'raw_csv':raw['csv'],'counts':checked['counts'],'mode':'field',
               'qc_policy':'pinned-period CSV v1; screening is not sensor calibration','validation_version':raw['version'],
               'zone_id':raw['zone_id'],'sensor_id':checked['sensor_id'],'sensor_snapshot':checked['sensor_snapshot'],
               'profile_revision':checked['profile_revision'],'source_unit':raw['unit'],'time_basis':raw['time_basis'],'delimiter':raw['delimiter'],
               'actor':raw['actor'].strip(),'source_attested':True,'identity_verified':False,'automatic_admission':False,
               'request_id':raw['request_id'],'request_sha256':request_hash,'preview_sha256':checked['preview_sha256'],'imported_rows':len(ready)}
        saved=store.append('import_batch',raw['profile_id'],batch,now,c)
        for r in ready:
            store.append('measurement',raw['profile_id'],{**r,'status':'accepted','mode':'field','zone_id':raw['zone_id'],
                'batch_id':saved['id'],'validation_version':raw['version'],'sensor_snapshot':checked['sensor_snapshot'],
                'profile_revision':checked['profile_revision']},now,c)
        return {'batch_id':saved['id'],'counts':checked['counts'],'imported_rows':len(ready),'replayed':False,'automatic_admission':False}


def public_preview(result):
    """Bound row detail in HTTP responses; the full digest is checked on commit."""
    priority={'invalid':0,'conflict':1,'suspect':2,'outside':3,'duplicate':4,'ready':5}
    shown=sorted(result['rows'],key=lambda r:(priority[r['status']],r['line']))[:200]
    return {**result,'rows':shown,'rows_shown':len(shown)}
