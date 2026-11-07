"""Farm records import, draft field risk rules, inspection marks and node series.

What the defense deck describes as «фаза × часы влажного листа × защита» is computed
here from data the prototype actually has:
- farm records the user imports (AgroMon-style CSV) — crop, sowing, phases, treatments, foci;
- hourly ERA5 already stored in the repository (sector grid 0.25°, NOT a field measurement),
  or an uploaded node series for an outline when one exists;
- draft thresholds from config/field_risk_rules.json (status draft_unvalidated).
The queue says where to look first; it is not a diagnosis, damage threshold or treatment advice.
The bundled example is labelled «ПРИМЕР» and can never be saved to a farm journal.
"""
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import io
import json
import math
import re

from .pipeline import ROOT
from .field_store import digest

STREAM = 'farm-scouting-01'
RECORDS = 'farm_records_import'
MARK = 'field_inspection_mark'
SERIES = 'field_sensor_series'
MSK = timezone(timedelta(hours=3))
RULES_PATH = ROOT/'config/field_risk_rules.json'
TEMPLATE_PATH = ROOT/'config/farm_records_template.csv'
EXAMPLE_PATH = ROOT/'config/farm_records_example.csv'
CONTEXT_PATH = ROOT/'web/scouting/assets/public-context.json'
WEATHER_DIR = ROOT/'data/farm-research/scouting-workspace-2026-10-22'
WEATHER_LABELS = {'spring2024': 'Весна 2024', 'recent2026': 'Июль–октябрь 2026'}
ERA5_LABEL = 'ERA5, сетка 0,25°, общая для всех полей сектора — не измерение на поле'
CROPS = ['озимая пшеница', 'озимый ячмень', 'кукуруза', 'подсолнечник', 'соя']
KINDS = ['фунгицид', 'инсектицид', 'гербицид', 'другое']
TYPES = ['поле', 'обработка', 'очаг']
STATUS_ORDER = ['risk', 'check', 'normal', 'no_rules', 'no_data']
STATUS_LABEL = {'risk': 'Риск', 'check': 'Проверить', 'normal': 'Норма',
                'no_rules': 'Нет правил для культуры', 'no_data': 'Нет данных учёта'}
COLUMNS = {  # Russian header -> English snake_case alias
    'тип': 'type', 'обводка': 'outline', 'поле_в_учёте': 'field_name', 'культура': 'crop',
    'дата_сева': 'sowing_date', 'фаза': 'phase', 'дата_фазы': 'phase_date', 'дата': 'date',
    'препарат': 'product', 'вид': 'kind', 'болезнь': 'disease', 'широта': 'latitude', 'долгота': 'longitude'}
SERIES_COLUMNS = {'время': 'time', 'лист_влажный': 'leaf_wet', 'температура': 'temperature',
                  'влажность': 'humidity', 'осадки': 'rain'}
_D = date  # risk_view's public parameter is named `date`
MAX_CSV_CHARS = 2_000_000
MAX_RECORD_ROWS = 5000
MAX_SERIES_ROWS = 20000


# ── Static inputs ──────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def rules():
    r = json.loads(RULES_PATH.read_text(encoding='utf-8'))
    if r.get('status') != 'draft_unvalidated':raise ValueError('Правила риска должны быть помечены как черновые')
    return r


@lru_cache(maxsize=1)
def outlines():
    """Outline id -> bbox (lon_min, lat_min, lon_max, lat_max) from the published geometry."""
    ctx = json.loads(CONTEXT_PATH.read_text(encoding='utf-8'))
    out = {}
    for f in ctx['geometry']['features']:
        pts = []
        def walk(c):
            if c and isinstance(c[0], (int, float)):pts.append(c)
            else:
                for x in c:walk(x)
        walk(f['geometry']['coordinates'])
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out[f['properties']['id']] = (min(xs), min(ys), max(xs), max(ys))
    return dict(sorted(out.items()))


@lru_cache(maxsize=1)
def weather():
    """Hourly ERA5 grouped by Moscow local day. Refuses files whose hash differs from the bundle."""
    ctx = json.loads(CONTEXT_PATH.read_text(encoding='utf-8'))
    days, coverage = {}, []
    for key in ['spring2024', 'recent2026']:
        path = WEATHER_DIR/f'era5-{key}.json'
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ctx['weather'][key]['source']['sha256']:
            raise ValueError(f'Архив погоды {path.name} не совпадает с контрольной суммой. Расчёт остановлен.')
        h = json.loads(raw)['hourly']
        grouped = defaultdict(list)
        for t, temp, rh, rain in zip(h['time'], h['temperature_2m'], h['relative_humidity_2m'], h['precipitation']):
            local = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).astimezone(MSK)
            grouped[local.date()].append((local.hour, temp, rh, rain))
        valid = sorted(d for d, rows in grouped.items()
                       if len({r[0] for r in rows}) == 24 and all(None not in r for r in rows))
        for d in valid:days[d] = sorted(grouped[d])
        if valid:coverage.append({'start': valid[0].isoformat(), 'end': valid[-1].isoformat(), 'label': WEATHER_LABELS[key]})
    return days, coverage


def records_template():return TEMPLATE_PATH.read_bytes()


def records_example():return EXAMPLE_PATH.read_bytes()


# ── Parsing helpers ────────────────────────────────────────────────────────────
def _normal_text(text):return text.lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n')


def _sha(text):return hashlib.sha256(_normal_text(text).encode('utf-8')).hexdigest()


def _date(value, label):
    value = (value or '').strip()
    m = re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{4})', value)
    if m:value = f'{m[3]}-{m[2]}-{m[1]}'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):raise ValueError(f'{label}: нужна дата ГГГГ-ММ-ДД или ДД.ММ.ГГГГ')
    try:d = date.fromisoformat(value)
    except ValueError:raise ValueError(f'{label}: такой даты нет') from None
    if not 2000 <= d.year <= 2100:raise ValueError(f'{label}: год вне 2000–2100')
    return d


def _float(value, low, high, label):
    try:v = float(str(value).strip().replace(',', '.'))
    except ValueError:raise ValueError(f'{label}: нужно число') from None
    if not math.isfinite(v) or not low <= v <= high:raise ValueError(f'{label}: допустимо {low}…{high}')
    return v


def _rows(text, columns, limit, what):
    if not isinstance(text, str) or not text.strip():raise ValueError(f'Пустой файл {what}')
    if len(text) > MAX_CSV_CHARS:raise ValueError(f'Файл {what} больше 2 млн символов')
    text = _normal_text(text)
    header = text.split('\n', 1)[0]
    delimiter = ';' if header.count(';') >= header.count(',') else ','
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    head = [h.strip().lower() for h in next(reader)]
    alias = {v: k for k, v in columns.items()}
    head = [alias.get(h, h) for h in head]
    unknown = [h for h in head if h not in columns]
    if unknown:raise ValueError('Неизвестные столбцы: ' + ', '.join(unknown))
    if len(set(head)) != len(head):raise ValueError('Столбцы повторяются')
    out = []
    for number, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):continue
        if len(values) > len(head):raise ValueError(f'Строка {number}: больше значений, чем столбцов')
        out.append((number, {k: (values[i].strip() if i < len(values) else '') for i, k in enumerate(head)}))
        if len(out) > limit:raise ValueError(f'В файле {what} больше {limit} строк')
    if not out:raise ValueError(f'В файле {what} нет строк с данными')
    return head, out


def parse_records(text):
    """Return (parsed, errors, warnings). parsed = {fields, treatments, foci}."""
    errors, warnings = [], []
    head, rows = _rows(text, COLUMNS, MAX_RECORD_ROWS, 'учёта')
    for need in ['тип', 'обводка']:
        if need not in head:raise ValueError(f'Нет обязательного столбца «{need}»')
    phases = rules()['phases']
    known = outlines()
    fields, treatments, foci = {}, [], []
    seen = set()
    for n, r in rows:
        try:
            kind = r.get('тип', '').lower()
            if kind not in TYPES:raise ValueError('тип должен быть «поле», «обработка» или «очаг»')
            fid = r.get('обводка', '').upper()
            if fid not in known:raise ValueError(f'обводка «{r.get("обводка", "")}» не найдена среди R01–R27')
            key = tuple(sorted(r.items()))
            if key in seen:warnings.append(f'Строка {n}: повтор предыдущей строки, пропущена');continue
            seen.add(key)
            if kind == 'поле':
                crop = r.get('культура', '').lower()
                if crop not in CROPS:raise ValueError('культура: ' + ' | '.join(CROPS))
                sowing = _date(r.get('дата_сева'), 'дата_сева')
                name = r.get('поле_в_учёте', '')
                if len(name) > 120:raise ValueError('поле_в_учёте: до 120 символов')
                phase, pdate = r.get('фаза', '').lower(), r.get('дата_фазы', '')
                if bool(phase) != bool(pdate):raise ValueError('фаза и дата_фазы указываются вместе')
                f = fields.setdefault(fid, {'id': fid, 'field_name': name, 'crop': crop, 'sowing_date': sowing.isoformat(), 'phases': []})
                if f['crop'] != crop or f['sowing_date'] != sowing.isoformat():
                    raise ValueError(f'для {fid} уже указаны другая культура или дата сева')
                if name and not f['field_name']:f['field_name'] = name
                if phase:
                    if phase not in phases:raise ValueError('фаза: ' + ' | '.join(phases))
                    pd = _date(pdate, 'дата_фазы')
                    if pd < sowing:raise ValueError('дата_фазы раньше даты сева')
                    if any(p['date'] == pd.isoformat() for p in f['phases']):raise ValueError(f'для {fid} на {pd.isoformat()} уже указана фаза')
                    f['phases'].append({'phase': phase, 'date': pd.isoformat()})
            elif kind == 'обработка':
                d = _date(r.get('дата'), 'дата')
                k = r.get('вид', '').lower()
                if k not in KINDS:raise ValueError('вид: ' + ' | '.join(KINDS))
                product = r.get('препарат', '')
                if len(product) > 120:raise ValueError('препарат: до 120 символов')
                treatments.append({'id': fid, 'date': d.isoformat(), 'product': product, 'kind': k})
            else:
                d = _date(r.get('дата'), 'дата')
                disease = r.get('болезнь', '')
                if not disease or len(disease) > 120:raise ValueError('болезнь: укажите название, до 120 символов')
                lat, lon = r.get('широта', ''), r.get('долгота', '')
                if bool(lat) != bool(lon):raise ValueError('широта и долгота указываются вместе')
                point = None
                if lat:
                    lat, lon = _float(lat, -90, 90, 'широта'), _float(lon, -180, 180, 'долгота')
                    x0, y0, x1, y1 = known[fid];pad = 0.005
                    if not (x0-pad <= lon <= x1+pad and y0-pad <= lat <= y1+pad):raise ValueError(f'точка очага вне обводки {fid}')
                    point = {'latitude': lat, 'longitude': lon}
                foci.append({'id': fid, 'date': d.isoformat(), 'disease': disease, 'point': point})
        except ValueError as error:
            errors.append(f'Строка {n}: {error}')
    for f in fields.values():f['phases'].sort(key=lambda p: p['date'])
    for t in treatments:
        if t['id'] not in fields:warnings.append(f'Обработка {t["date"]} для {t["id"]}: поле не описано строкой «поле»')
    parsed = {'fields': sorted(fields.values(), key=lambda f: f['id']),
              'treatments': sorted(treatments, key=lambda t: (t['id'], t['date'])),
              'foci': sorted(foci, key=lambda f: (f['id'], f['date']))}
    return parsed, errors, warnings


def records_preview(store, raw):
    if not isinstance(raw, dict) or set(raw) != {'filename', 'csv'}:raise ValueError('Нужны имя файла и содержимое CSV')
    _filename(raw['filename'])
    parsed, errors, warnings = parse_records(raw['csv'])
    example = _sha(raw['csv']) == _sha(records_example().decode('utf-8'))
    if example:warnings.insert(0, 'Это демонстрационный пример. Его можно посмотреть, но нельзя сохранить в журнал хозяйства.')
    return {**parsed, 'errors': errors, 'warnings': warnings, 'sha256': _sha(raw['csv']), 'is_example': example,
            'counts': {k: len(v) for k, v in parsed.items()}}


def _filename(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or re.search(r'[\\/\x00-\x1f]', value):
        raise ValueError('Некорректное имя файла')
    return value.strip()


def _append(store, kind, raw, payload, now):
    key = raw.get('request_id')
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key):raise ValueError('Обновите форму перед сохранением')
    h = digest(raw)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for rowkey, body in c.execute('SELECT id,payload FROM events WHERE kind=? AND profile_id=?', (kind, STREAM)):
            old = json.loads(body)
            if old['request_id'] == key:
                if old['request_sha256'] != h:raise ValueError('Этот запрос уже сохранён с другим содержимым')
                return next(v for v in store.records(kind, STREAM) if v['id'] == rowkey)
        payload.update(request_id=key, request_sha256=h, schema_version=1, automatic_diagnosis=None,
                       model_training_eligible=False, forecast_scoring_eligible=False)
        return store.append(kind, STREAM, payload, now, c)


def save_records(store, raw, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(raw, dict) or set(raw) != {'request_id', 'filename', 'csv', 'confirm_farm_data'}:
        raise ValueError('Нужны файл учёта и подтверждение')
    if raw['confirm_farm_data'] is not True:raise ValueError('Подтвердите, что это выгрузка учёта вашего хозяйства')
    filename = _filename(raw['filename'])
    if _sha(raw['csv']) == _sha(records_example().decode('utf-8')) or _sha(raw['csv']) == _sha(records_template().decode('utf-8')):
        raise ValueError('Демонстрационный пример и образец нельзя сохранить в журнал хозяйства')
    if re.search(r'ПРИМЕР|ОБРАЗЕЦ', raw['csv']):raise ValueError('В файле остались строки «ПРИМЕР» или «ОБРАЗЕЦ». Замените их данными хозяйства.')
    parsed, errors, warnings = parse_records(raw['csv'])
    if errors:raise ValueError('Исправьте файл: ' + '; '.join(errors[:5]) + (f' и ещё {len(errors)-5}' if len(errors) > 5 else ''))
    payload = {'filename': filename, 'sha256': _sha(raw['csv']), **parsed, 'warnings': warnings,
               'source': 'manual_upload_unverified', 'purpose': 'farm_records_for_draft_risk_queue'}
    return _append(store, RECORDS, raw, payload, now)


def save_mark(store, raw, now=None):
    now = now or datetime.now(timezone.utc)
    keys = {'request_id', 'fid', 'observed_on', 'result', 'disease', 'notes', 'actor', 'real_visit_confirmed'}
    if not isinstance(raw, dict) or set(raw) != keys:raise ValueError('Заполните отметку осмотра')
    if raw['real_visit_confirmed'] is not True:raise ValueError('Подтвердите, что поле осмотрено на самом деле')
    if raw['fid'] not in outlines():raise ValueError('Обводка не найдена')
    observed = _date(raw['observed_on'], 'Дата осмотра')
    if observed > now.astimezone(MSK).date():raise ValueError('Дата осмотра не может быть в будущем')
    if raw['result'] not in ['clean', 'focus']:raise ValueError('Выберите «чисто» или «нашёл очаг»')
    for k, limit in [('disease', 120), ('notes', 2000)]:
        if not isinstance(raw[k], str) or len(raw[k]) > limit:raise ValueError(f'Поле {k}: до {limit} символов')
    actor = raw['actor'] if isinstance(raw['actor'], str) else ''
    if not actor.strip() or len(actor) > 120:raise ValueError('Укажите, кто осматривал поле')
    payload = {'fid': raw['fid'], 'observed_on': observed.isoformat(), 'result': raw['result'],
               'disease': raw['disease'].strip(), 'notes': raw['notes'].strip(), 'actor': actor.strip(),
               'observation_source': 'manual_unverified_report', 'real_visit_confirmed': True,
               'purpose': 'labelled_inspection_for_future_model_update'}
    return _append(store, MARK, raw, payload, now)


def save_sensor_series(store, raw, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(raw, dict) or set(raw) != {'request_id', 'fid', 'node_name', 'csv'}:raise ValueError('Нужны обводка, имя узла и файл')
    if raw['fid'] not in outlines():raise ValueError('Обводка не найдена')
    node = raw['node_name'] if isinstance(raw['node_name'], str) else ''
    if not node.strip() or len(node) > 60:raise ValueError('Имя узла: 1–60 символов')
    head, rows = _rows(raw['csv'], SERIES_COLUMNS, MAX_SERIES_ROWS, 'узла')
    missing = [c for c in SERIES_COLUMNS if c not in head]
    if missing:raise ValueError('Нет столбцов: ' + ', '.join(missing))
    out, seen = [], set()
    for n, r in rows:
        try:
            value = r['время']
            try:moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:raise ValueError('время: нужен формат ГГГГ-ММ-ДДTЧЧ:ММ') from None
            if moment.tzinfo is None:moment = moment.replace(tzinfo=MSK)
            moment = moment.astimezone(timezone.utc)
            if moment > now:raise ValueError('время в будущем')
            if moment in seen:raise ValueError('время повторяется')
            seen.add(moment)
            if r['лист_влажный'] not in ['0', '1']:raise ValueError('лист_влажный: 0 или 1')
            out.append([moment.isoformat(), int(r['лист_влажный']), _float(r['температура'], -40, 55, 'температура'),
                        _float(r['влажность'], 0, 100, 'влажность'), _float(r['осадки'], 0, 200, 'осадки')])
        except ValueError as error:raise ValueError(f'Строка {n}: {error}') from None
    out.sort()
    payload = {'fid': raw['fid'], 'node_name': node.strip(), 'rows': out, 'sha256': _sha(raw['csv']),
               'start_utc': out[0][0], 'end_utc': out[-1][0], 'source': 'manual_upload_unverified'}
    return _append(store, SERIES, raw, payload, now)


# ── Risk calculation ───────────────────────────────────────────────────────────
def _sensor_days(store):
    """(fid, local date) -> (node, hourly rows [(hour, temp, wet, rain)]) for days with enough hours."""
    need = rules()['wet_hour']['sensor_min_hours_per_day']
    out = {}
    for rec in store.records(SERIES, STREAM) if store else []:
        p = rec['payload'];hours = defaultdict(list)
        for t, wet, temp, rh, rain in p['rows']:
            local = datetime.fromisoformat(t).astimezone(MSK)
            hours[(local.date(), local.hour)].append((wet, temp, rain))
        per_day = defaultdict(list)
        for (d, hour), vals in hours.items():
            per_day[d].append((hour, sum(v[1] for v in vals)/len(vals), sum(v[0] for v in vals)/len(vals) >= .5, sum(v[2] for v in vals)))
        for d, vals in per_day.items():
            if len(vals) >= need:out[(p['fid'], d)] = (p['node_name'], sorted(vals))  # later uploads win
    return out


def _day_hours(fid, day, sensors):
    """Hourly (hour, temp, wet, rain) and source for one outline/day, or (None, None)."""
    if (fid, day) in sensors:
        node, rows = sensors[(fid, day)];return rows, 'sensor:' + node
    days, _ = weather()
    if day not in days:return None, None
    w = rules()['wet_hour']['era5']
    return [(h, t, rh >= w['relative_humidity_min_pct'] or rain >= w['precipitation_min_mm'], rain) for h, t, rh, rain in days[day]], 'era5_sector'


def _rain_after(fid, day, sensors):
    p = rules()['protection'];start = p['assumed_treatment_hour_local'];total, known = 0.0, 0
    for offset in (0, 1):
        rows, _ = _day_hours(fid, day+timedelta(days=offset), sensors)
        if rows is None:continue
        for h, t, wet, rain in rows:
            elapsed = offset*24+h-start
            if 0 <= elapsed < p['washoff_window_hours']:total += rain;known += 1
    return round(total, 1), known >= p['washoff_window_hours']


def _phase_on(field, day):
    current = None
    for p in field['phases']:
        if date.fromisoformat(p['date']) <= day:current = p
    return current


def _fmt(d):return d.strftime('%d.%m')


def _temp(values):
    lo, hi = round(min(values)), round(max(values))
    sign = lambda v: f'+{v}' if v > 0 else str(v)
    mean = round(sum(values)/len(values))
    return f'{sign(lo)} °C' if lo == hi else f'{sign(lo)}…{sign(hi)} °C, в среднем {sign(mean)} °C'


def _protections(fid, treatments, sensors):
    p = rules()['protection'];out = []
    for t in treatments:
        if t['id'] != fid or t['kind'] not in p['kinds']:continue
        d = date.fromisoformat(t['date']);rain, known = _rain_after(fid, d, sensors)
        voided = known and rain >= p['washoff_rain_mm']
        out.append({'date': t['date'], 'product': t['product'], 'protected_until': (d+timedelta(days=p['days']-1)).isoformat(),
                    'voided': voided, 'rain_after_mm': rain if known else None})
    return out


def evaluate(field, day, treatments, foci, marks, sensors):
    """Status for one recorded field on one local day."""
    r = rules();fid = field['id']
    base = {'id': fid, 'field_name': field.get('field_name', ''), 'crop': field['crop'], 'sowing_date': field['sowing_date'],
            'phase': None, 'phase_date': None, 'inspect_by': None, 'diseases': [], 'reasons': [],
            'data_source': 'era5_sector', 'data_source_label': ERA5_LABEL, 'last_fungicide': None}
    phase = _phase_on(field, day)
    if phase:base.update(phase=phase['phase'], phase_date=phase['date'])
    protections = [p for p in _protections(fid, treatments, sensors) if date.fromisoformat(p['date']) <= day]
    if protections:base['last_fungicide'] = protections[-1]
    if field['crop'] not in r['crops']:
        base.update(status='no_rules', data_source=None, data_source_label=None,
                    reasons=[f'Для культуры «{field["crop"]}» правил болезней листа пока нет'])
        return base
    order = {p: i for i, p in enumerate(r['phases'])}
    lookback = [day-timedelta(days=i) for i in range(r['lookback_days']-1, -1, -1)]
    hours, sources = {}, set()
    for d in lookback:
        rows, src = _day_hours(fid, d, sensors)
        if rows is not None:hours[d] = rows;sources.add(src)
    sensor = sorted(s for s in sources if s.startswith('sensor:'))
    if sensor:
        name = sensor[-1].split(':', 1)[1];n = sum(1 for d in hours if (fid, d) in sensors)
        base['data_source'] = sensor[-1]
        base['data_source_label'] = f'Узел «{name}»: {n} из {len(hours)} дней' + ('' if n == len(hours) else '; остальные дни — ERA5 по сектору')
    statuses, inspect, reasons = [], [], []
    for dz in r['diseases']:
        events = []
        for d in sorted(hours):
            wet = [t for h, t, w, rain in hours[d] if w]
            if not wet:continue
            mean = sum(wet)/len(wet)
            in_range = dz['temp_min'] <= mean <= dz['temp_max']
            full = len(wet) >= dz['wet_hours'] and in_range
            partial = not full and in_range and len(wet) >= math.ceil(dz['wet_hours']*r['partial_fraction'])
            if not (full or partial):continue
            ph = _phase_on(field, d)
            susceptible = ph is not None and order[ph['phase']] >= order[dz['susceptible_from']]
            cover = next((p for p in protections if not p['voided'] and p['date'] <= d.isoformat() <= p['protected_until']), None)
            events.append({'date': d.isoformat(), 'wet_hours': len(wet), 'temp_min': round(min(wet), 1), 'temp_max': round(max(wet), 1),
                           'full': full, 'susceptible': susceptible, 'phase_known': ph is not None,
                           'protected_by': cover['date'] if cover else None, '_temps': wet, '_day': d, '_cover': cover})
        status, by, text = 'normal', None, None
        open_full = [e for e in events if e['full'] and e['susceptible'] and not e['protected_by']]
        covered = [e for e in events if e['full'] and e['susceptible'] and e['protected_by']]
        open_partial = [e for e in events if not e['full'] and e['susceptible'] and not e['protected_by']]
        unknown = [e for e in events if e['full'] and not e['phase_known']]
        early = [e for e in events if e['full'] and e['phase_known'] and not e['susceptible']]
        if open_full:
            e = max(open_full, key=lambda e: e['wet_hours']);status = 'risk'
            by = open_full[0]['_day']+timedelta(days=dz['inspect_after_days'])
            text = f'{dz["name"]}: лист влажный {e["wet_hours"]} ч при {_temp(e["_temps"])} {_fmt(e["_day"])} — условия заражения есть'
        else:
            ending = [e for e in covered if 0 <= (date.fromisoformat(e['_cover']['protected_until'])-day).days <= r['protection']['ending_soon_days']]
            if ending:
                status = 'check';by = date.fromisoformat(ending[0]['_cover']['protected_until'])
                text = f'{dz["name"]}: условия {_fmt(ending[0]["_day"])} под защитой фунгицида {_fmt(date.fromisoformat(ending[0]["protected_by"]))}, защита заканчивается {_fmt(by)}'
            elif open_partial:
                e = max(open_partial, key=lambda e: e['wet_hours']);status = 'check'
                by = open_partial[0]['_day']+timedelta(days=dz['inspect_after_days'])
                text = f'{dz["name"]}: лист влажный {e["wet_hours"]} ч при {_temp(e["_temps"])} {_fmt(e["_day"])} — условия близки к порогу {dz["wet_hours"]} ч'
            elif unknown:
                status = 'check';by = unknown[0]['_day']+timedelta(days=dz['inspect_after_days'])
                text = f'{dz["name"]}: условия {_fmt(unknown[0]["_day"])}, но фаза на эту дату в учёте не указана'
            elif covered:
                text = f'{dz["name"]}: условия ' + ', '.join(_fmt(e['_day']) for e in covered) + f' пришлись на защиту фунгицидом {_fmt(date.fromisoformat(covered[0]["protected_by"]))}'
            elif early:
                text = f'{dz["name"]}: условия ' + ', '.join(_fmt(e['_day']) for e in early) + f' — до фазы «{dz["susceptible_from"]}»'
        statuses.append(status)
        if by:inspect.append(by)
        if text and status != 'normal':reasons.append(text)
        elif text:base.setdefault('_notes', []).append(text)
        base['diseases'].append({'id': dz['id'], 'name': dz['name'], 'status': status, 'inspect_by': by.isoformat() if by else None,
                                 'events': [{k: v for k, v in e.items() if not k.startswith('_')} for e in events]})
    limit = r['focus']['memory_days']
    recent = [f for f in foci if f['id'] == fid and 0 <= (day-date.fromisoformat(f['date'])).days <= limit]
    recent += [{'date': m['observed_on'], 'disease': m['disease'] or 'очаг', 'mark': True} for m in marks
               if m['fid'] == fid and m['result'] == 'focus' and 0 <= (day-date.fromisoformat(m['observed_on'])).days <= limit]
    if recent:
        f = max(recent, key=lambda f: f['date'])
        statuses.append('check');reasons.append(f'Очаг «{f["disease"]}» {date.fromisoformat(f["date"]).strftime("%d.%m.%Y")}' + (' по отметке осмотра' if f.get('mark') else ' в учёте хозяйства'))
        if not inspect:inspect.append(day+timedelta(days=r['focus']['inspect_after_days']))
    status = min(statuses, key=STATUS_ORDER.index) if statuses else 'normal'
    tail = []
    if base['phase']:tail.append(f'фаза — {base["phase"]}')
    else:tail.append('фаза на дату не указана')
    if not protections:tail.append('фунгицида не было')
    else:
        p = protections[-1]
        if p['voided']:tail.append(f'фунгицид {_fmt(date.fromisoformat(p["date"]))} смыт дождём {str(p["rain_after_mm"]).replace(".", ",")} мм')
        elif date.fromisoformat(p['protected_until']) >= day:tail.append(f'защита фунгицидом до {_fmt(date.fromisoformat(p["protected_until"]))}')
        else:tail.append(f'фунгицид {_fmt(date.fromisoformat(p["date"]))}, защита закончилась {_fmt(date.fromisoformat(p["protected_until"]))}')
    notes = base.pop('_notes', [])
    if status == 'normal':reasons = notes or [f'Условий для болезней листа за {r["lookback_days"]} дней нет']
    if not hours:reasons.insert(0, 'Нет погодных данных за период расчёта')
    base.update(status=status, reasons=reasons + ['; '.join(tail).capitalize()],
                inspect_by=min(inspect).isoformat() if status in ('risk', 'check') and inspect else None)
    return base


def _active_records(store, source):
    if source == 'example':
        parsed, errors, _ = parse_records(records_example().decode('utf-8'))
        if errors:raise ValueError('Демонстрационный пример повреждён')
        return parsed, {'kind': 'example', 'label': 'ПРИМЕР для демонстрации — не данные хозяйства, в журнал не сохраняется',
                        'filename': EXAMPLE_PATH.name, 'sha256': _sha(records_example().decode('utf-8'))}
    imports = store.records(RECORDS, STREAM) if store else []
    if not imports:return {'fields': [], 'treatments': [], 'foci': []}, {'kind': 'none', 'label': 'Учёт хозяйства ещё не загружен'}
    last = imports[-1];p = last['payload']
    return p, {'kind': 'journal', 'label': f'Учёт хозяйства: {p["filename"]}', 'filename': p['filename'],
               'imported_at': last['recorded_at_utc'], 'sha256': p['sha256']}


def _status_on(fid, day, records, marks, sensors):
    field = next((f for f in records['fields'] if f['id'] == fid), None)
    if not field:return 'no_data'
    return evaluate(field, day, records['treatments'], records['foci'], marks, sensors)['status']


def risk_view(store, date, source):
    if source not in ['journal', 'example']:raise ValueError('Источник: journal или example')
    day = _date(date, 'Дата расчёта')
    days, coverage = weather()
    if day not in days:
        raise ValueError('Дата вне архива погоды. Доступно: ' + '; '.join(f'{c["label"]} {c["start"]} — {c["end"]}' for c in coverage))
    r = rules()
    records, meta = _active_records(store, source)
    marks = [m['payload'] for m in (store.records(MARK, STREAM) if store else [])]
    use_marks = marks if source == 'journal' else []
    sensors = _sensor_days(store)
    fields = []
    for fid in outlines():
        field = next((f for f in records['fields'] if f['id'] == fid), None)
        if field:item = evaluate(field, day, records['treatments'], records['foci'], use_marks, sensors)
        else:
            item = {'id': fid, 'field_name': '', 'crop': None, 'phase': None, 'phase_date': None, 'sowing_date': None,
                    'status': 'no_data', 'reasons': ['Нет данных учёта по этой обводке'], 'inspect_by': None,
                    'data_source': None, 'data_source_label': None, 'last_fungicide': None, 'diseases': []}
        item['status_label'] = STATUS_LABEL[item['status']]
        item['marks'] = [{'observed_on': m['observed_on'], 'result': m['result'], 'disease': m['disease']}
                         for m in sorted(marks, key=lambda m: m['observed_on']) if m['fid'] == fid]
        fields.append(item)
    fields.sort(key=lambda f: (STATUS_ORDER.index(f['status']), f['inspect_by'] or '9999', f['id']))
    window = r['marks']['signal_window_days']
    signals = [f for f in fields if f['status'] in ('risk', 'check')]
    with_marks = [f for f in signals if any(0 <= (_D.fromisoformat(m['observed_on'])-day).days <= window for m in f['marks'])]
    focus_marks = [m for m in marks if m['result'] == 'focus']
    after_signal = 0
    for m in focus_marks:
        seen = _D.fromisoformat(m['observed_on'])
        for back in range(1, window+1):
            d = seen-timedelta(days=back)
            if d in days and _status_on(m['fid'], d, records, use_marks, sensors) in ('risk', 'check'):after_signal += 1;break
    return {
        'date': day.isoformat(), 'source': meta,
        'rules': {'status': r['status'], 'version': r['version'], 'note': r['note'], 'lookback_days': r['lookback_days'],
                  'wet_hour': r['wet_hour']['era5']['label'],
                  'protection': f'Фунгицид защищает {r["protection"]["days"]} дней; дождь ≥ {r["protection"]["washoff_rain_mm"]} мм за {r["protection"]["washoff_window_hours"]} ч после обработки отменяет защиту',
                  'diseases': [{k: d[k] for k in ['id', 'name', 'wet_hours', 'temp_min', 'temp_max', 'inspect_after_days', 'susceptible_from']} for d in r['diseases']]},
        'weather': {'label': ERA5_LABEL, 'coverage': coverage},
        'summary': {s: sum(f['status'] == s for f in fields) for s in STATUS_ORDER},
        'fields': fields,
        'marks_summary': {'signals_total': len(signals), 'signals_with_marks': len(with_marks),
                          'focus_total': len(focus_marks), 'focus_after_signal': after_signal,
                          'definition': f'Сигнал — статус «риск» или «проверить» на дату расчёта; отметка учитывается в течение {window} дней после неё. '
                                        f'«Очаг после сигнала» — отметка «нашёл очаг», перед которой за {window} дней по этой обводке был сигнал.'},
        'limitations': [
            'Пороги — черновик команды, не проверенная модель болезней: проверяем на пилоте 2027 с агрономом.',
            'Погода ERA5 одна на весь сектор (сетка 0,25°): поля различаются только фазой, защитой и данными узла, если он загружен.',
            'Влажность листа по ERA5 оценивается косвенно (влажность воздуха ≥ 90 % или осадки) — это не измерение на поле.',
            'Статус означает «где осмотреть первым», а не диагноз, порог повреждения или рекомендацию обработки. Решение — за агрономом.',
            'Отметки осмотра сохраняются как размеченные данные; модель по ним пока не переобучается.']
            + (['Используется демонстрационный ПРИМЕР учёта, а не данные хозяйства.'] if meta['kind'] == 'example' else []),
    }
