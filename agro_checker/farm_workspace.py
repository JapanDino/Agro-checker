"""Comparable archive dates and human survey plans; no forecasts or labels."""
from datetime import datetime, timezone
from functools import lru_cache
import math
from . import farm_scouting as scout

PLAN = 'farm_scouting_plan'
PURPOSES = {'inspection': 'Обследование изменений', 'sensor_survey': 'Обследование мест под датчики'}
SITE_CHECKS = ['Характер поверхности и растительность', 'Доступ и безопасность',
               'Связь и питание', 'Условия монтажа и обслуживания']


@lru_cache(maxsize=4)
def catalog(period):
    scout.period_id(period)
    _, summary, _, sources = scout.archive()
    counts = {}
    ids = set()
    for area in summary['features'].values():
        for r in area['history']:
            if r['period'] != period or r['source_status'] != 'ready':
                continue
            ids.add(r['id'])
            if (r.get('ndvi') or {}).get('base') is not None and (r.get('ndmi') or {}).get('base') is not None:
                counts[r['id']] = counts.get(r['id'], 0) + 1
    rows = sorted([{'id': sid, 'at': sources[sid]['acquired_at_utc'],
                    'baseline': sources[sid].get('processing_baseline'), 'usable_areas': counts.get(sid, 0)}
                   for sid in ids], key=lambda r: (r['at'], r['id']))
    # Dates/coverage only. Never choose using the sign or size of an index change.
    default = None
    for late in reversed(rows):
        if late['usable_areas'] < math.ceil(.7 * len(summary['features'])) or not late['baseline']:
            continue
        early = [r for r in rows if r['baseline'] == late['baseline'] and r['usable_areas'] >= math.ceil(.7 * len(summary['features']))
                 and 7 <= (scout.stamp(late['at'])-scout.stamp(r['at'])).total_seconds()/86400 <= 21]
        if early:
            first = min(early, key=lambda r: (abs((scout.stamp(late['at'])-scout.stamp(r['at'])).total_seconds()/86400-14), -r['usable_areas'], r['id']))
            default = {'before': first['id'], 'after': late['id']}
            break
    return {'period': period, 'scenes': rows, 'default': default,
            'selection_rule': 'Latest scene with >=70% individually usable areas, same processing baseline, earlier date 7–21 days before and closest to 14 days; no outcome/index-size selection.'}


def checked_pair(period, before, after):
    by_id = {r['id']: r for r in catalog(period)['scenes']}
    if before not in by_id or after not in by_id:
        raise ValueError('Обе съёмки должны принадлежать выбранному архивному периоду')
    a, b = by_id[before], by_id[after]
    if scout.stamp(a['at']) >= scout.stamp(b['at']):
        raise ValueError('Вторая дата должна быть позже первой')
    return a, b


@lru_cache(maxsize=128)
def area_detail(fid, period, before, after):
    a, b = checked_pair(period, before, after)
    scout.fragment(fid)
    history = scout.fragment(fid)[1]['history']
    usable = {r['id'] for r in history if r['period']==period and
              (r.get('ndvi') or {}).get('base') is not None and (r.get('ndmi') or {}).get('base') is not None}
    if before not in usable or after not in usable:
        # Keep selectable dates, but do not return evidence from a different pair.
        d = {**scout.detail(fid, period), 'pair': None, 'default_place_comparison': None}
        return {**d, 'unavailable_reason': 'Недостаточно пригодных данных одной из выбранных дат'}
    d = scout.detail(fid, period, before, after)
    p = d['pair']
    if not p or not p['same_baseline'] or any(p['metrics'][k]['base'] is None for k in ['ndvi', 'ndmi']):
        why = 'Разные или неизвестные версии обработки' if not p or not p['same_baseline'] else 'Недостаточно общих пригодных пикселей двух дат'
        return {**d, 'pair': None, 'default_place_comparison': None, 'unavailable_reason': why}
    return d


@lru_cache(maxsize=12)
def comparison(period, before=None, after=None):
    cat = catalog(period)
    if before is None and after is None:
        if not cat['default']:
            return {**cat, 'pair': None, 'rows': [], 'available_areas': 0, 'total_areas': len(scout.archive()[2])}
        before, after = cat['default']['before'], cat['default']['after']
    a, b = checked_pair(period, before, after)
    rows = []
    for fid in scout.archive()[2]:
        d = area_detail(fid, period, before, after)
        p = d['pair']
        rows.append({'id': fid, 'status': 'ready' if p else 'missing', 'reason': d.get('unavailable_reason'),
                     'coverage': p['coverage'] if p else None,
                     'ndvi': p['metrics']['ndvi'] if p else None, 'ndmi': p['metrics']['ndmi'] if p else None})
    return {**cat, 'pair': {'before': a, 'after': b, 'days': (scout.stamp(b['at'])-scout.stamp(a['at'])).total_seconds()/86400},
            'rows': rows, 'available_areas': sum(r['status']=='ready' for r in rows), 'total_areas': len(rows),
            'kind': 'observed_surface_change_not_disease_priority', 'version': scout.archive()[1]['_scouting_version']}


def plans(store):
    return store.records(PLAN, scout.STREAM)


def save_plan(store, raw, now=None):
    if not isinstance(raw, dict) or set(raw) != {'request_id', 'title', 'task_ids', 'purpose'}:
        raise ValueError('Нужны название, цель и 1–3 задачи плана')
    title = scout._text(raw['title'], 160, 'Назовите план, до 160 символов')
    if raw['purpose'] not in PURPOSES:
        raise ValueError('Выберите цель плана')
    ids = raw['task_ids']
    if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Выберите от одной до трёх разных задач')
    tasks = {t['id']: t for t in store.records(scout.TASK, scout.STREAM)}
    if any(i not in tasks for i in ids):
        raise ValueError('Одна из задач не найдена. Обновите список')
    if len({tasks[i]['payload']['place']['id'] for i in ids}) != len(ids):
        raise ValueError('Повторные задачи одного места включите в разные планы')
    payload = {'title': title, 'purpose': raw['purpose'], 'purpose_label': PURPOSES[raw['purpose']],
               'task_ids': ids, 'task_sha256': {i: tasks[i]['sha256'] for i in ids},
               'site_checks': SITE_CHECKS if raw['purpose']=='sensor_survey' else [],
               'status': 'planned_not_visited', 'route_optimized': False, 'confirmed_boundaries': False}
    return scout._append(store, PLAN, raw, payload, now or datetime.now(timezone.utc))


def plan_export(store, plan_id):
    plan = next((v for v in plans(store) if v['id']==plan_id), None)
    if not plan:
        raise ValueError('Не найден сохранённый план')
    all_tasks = {t['id']:t for t in store.records(scout.TASK, scout.STREAM)}
    tasks = [all_tasks[i] for i in plan['payload']['task_ids']]
    if any(t['sha256'] != plan['payload']['task_sha256'][t['id']] for t in tasks):
        raise ValueError('Не совпадает версия основания плана')
    return {'schema':1, 'plan':plan, 'tasks':tasks,
            'visits':[v for v in store.records(scout.VISIT, scout.STREAM) if v['payload']['place']['id'] in {t['payload']['place']['id'] for t in tasks}],
            'automatic_diagnoses':False, 'model_training_enabled':False,
            'notice':'План обследования составлен человеком. Места идут в порядке выбора. Полевые записи требуют отдельной проверки для постановки диагноза.'}
