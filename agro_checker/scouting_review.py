"""Read-only inspection evidence: observed window, uncertain outline, separate shadow forecast."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
from pathlib import Path

from .pipeline import ROOT
from . import farm_scouting as scout,farm_workspace as workspace

STUDY=ROOT/'data/prospective-scouting'
MOSCOW=timezone(timedelta(hours=3))


def instant(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timestamp needs timezone')
    return result.astimezone(timezone.utc)


def checked(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    raw=json.dumps(value['payload'],sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if hashlib.sha256(raw).hexdigest()!=value['sha256']:raise ValueError('Forecast record checksum mismatch')
    return value


def latest_release(as_of,directory):
    directory=Path(directory)
    paths=sorted((directory/'releases').glob('????-??-??.json'))
    if not paths:
        return None
    envelope=checked(paths[-1]);release=envelope['payload'];protocol=checked(directory/'frozen/protocol.json')
    if release['protocol_sha256']!=protocol['sha256'] or release['kind']!='prospective_satellite_comparison':
        raise ValueError('Wrong forecast protocol')
    if protocol['payload']['geometry_sha256']!=scout.archive()[0]['contours_sha256']:
        raise ValueError('Different forecast geometry')
    if instant(release['issued_at_utc'])>as_of:
        return None
    pool={v['fid'] for v in release['candidates']}
    if (set(release['selected'])!={'baseline','candidate'} or
        any(len(v)!=release['slots_each'] or len(set(v))!=len(v) or not set(v)<=pool for v in release['selected'].values())):
        raise ValueError('Invalid frozen forecast lists')
    return envelope,protocol,pool


def today(as_of=None,directory=STUDY):
    """Expose the active reference shortlist. The candidate remains in shadow evaluation."""
    as_of=as_of or datetime.now(timezone.utc)
    result={'date':as_of.astimezone(MOSCOW).date().isoformat(),'as_of_utc':as_of.isoformat(),
            'status':'no_current_signals','suggestions':[]}
    try:
        saved=latest_release(as_of,directory)
        if not saved:
            return result
        release=saved[0]['payload']
        window=release['target_window']
        if as_of>=instant(window['end_exclusive_utc']):
            return result
        candidates={row['fid']:row for row in release['candidates']}
        scenes={scene['id']:scene for scene in release['source_scenes']}
        suggestions=[]
        for fid in release['selected']['baseline']:
            row=candidates[fid]
            scene=scenes[row['issue_scene']]
            acquired=instant(scene['acquired_at_utc'])
            if acquired>instant(release['issued_at_utc']):
                raise ValueError('Input acquisition after release')
            ndvi,ndmi=row['x'][2:4]
            if not finite(ndvi) or not finite(ndmi):
                raise ValueError('Invalid input slopes')
            if min(ndvi,ndmi)>=0:
                continue
            index='NDVI' if ndvi<=ndmi else 'NDMI'
            suggestions.append({'place':scout.reference_location(fid),
                'reason':f'{index} снизился в ядре площади между двумя пригодными съёмками. Проверьте состояние растений на месте.',
                'acquired_at_utc':acquired.isoformat(),'target_window':window})
        result.update(status='ready' if suggestions else 'no_supported_places',suggestions=suggestions[:3])
    except (OSError,ValueError,KeyError,TypeError):
        result['status']='unavailable'
    return result


def forecast(fid,as_of,directory=STUDY):
    """JSON only: archive-only UI must not require sklearn/joblib or fit/load a model."""
    directory=Path(directory)
    try:
        saved=latest_release(as_of,directory)
        if not saved:return dict(status='not_available',reason='Нет доступного на момент просмотра выпуска')
        envelope,p,pool=saved;r=envelope['payload']
        result=dict(status='pending',issued_at_utc=r['issued_at_utc'],
            target_window=r['target_window'],input_acquisition_date=r['anchor_acquisition_date'],
            scope='fragment_core_not_selected_60m_window',release_sha256=envelope['sha256'],
            protocol_sha256=p['sha256'],candidate_pool=len(pool),budget_each=r['slots_each'],
            input_available=fid in pool,selected_baseline=fid in r['selected']['baseline'] if fid in pool else None,
            selected_candidate=fid in r['selected']['candidate'] if fid in pool else None,
            meaning='future_satellite_decline_not_inspection_usefulness',outcome=None)
        final=directory/'evaluations'/(r['anchor_acquisition_date']+'.json')
        if final.exists():
            evaluation=checked(final)['payload']
            if evaluation['release_sha256']!=envelope['sha256']:raise ValueError('Evaluation/release mismatch')
            outcome=next((v for v in evaluation['outcomes'] if v['fid']==fid),None)
            result.update(status='finalised',outcome=outcome)
        elif as_of>=instant(r['target_window']['finalisation_not_before_utc']):result['status']='awaiting_evaluation_data'
        if not result['input_available']:result['status']='not_in_input_pool'
        return result
    except (OSError,ValueError,KeyError,TypeError):
        return dict(status='unavailable',reason='Не удалось проверить сохранённый прогноз; данные не подставлены')


def finite(value):return isinstance(value,(int,float)) and math.isfinite(value)


def review(fid,period,before,after,place_id,as_of=None):
    as_of=as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:raise ValueError('Time requires timezone')
    if scout.cell_indices(place_id)[0]!=fid:raise ValueError('Место относится к другой площади')
    detail=workspace.area_detail(fid,period,before,after)
    pair=detail['pair']
    if not pair:return dict(status='comparison_unavailable',reason=detail.get('unavailable_reason'),as_of_utc=as_of.isoformat())
    window=scout.window_comparison(place_id,before,after)
    supported=all(finite(window[k]) for k in ('ndvi','ndmi'))
    history=scout.place_series(place_id)['rows']
    eligible=[r for r in history if finite(r['ndvi']) and finite(r['ndmi']) and instant(r['at'])<=as_of]
    latest=max(eligible,key=lambda r:instant(r['at']),default=None)
    boundaries={}
    for key in ('ndvi','ndmi'):
        metric=pair['metrics'][key];lo,hi=metric['all_scenario_min'],metric['all_scenario_max']
        complete=pair['same_baseline'] and finite(lo) and finite(hi)
        boundaries[key]=dict(min=lo,max=hi,complete=complete,
            direction='decrease' if complete and hi<0 else 'increase' if complete and lo>0 else 'mixed' if complete else 'unknown',
            scope='whole_fragment_not_60m_window',statistical_interval=False)
    question=('Сравнить выбранное место с соседним участком: что заметно на месте и какие работы проводились между датами съёмки?'
              if supported else 'Осмотреть выбранное место и сравнить с соседним участком. Для этого окна на выбранной паре снимков недостаточно пригодных данных; причину и состояние нужно проверить на месте.')
    return dict(status='window_evidence_available' if supported else 'window_comparison_missing',
        as_of_utc=as_of.isoformat(),fragment=fid,place_id=place_id,period=period,
        archive_end_exclusive_utc=max(scout.archive()[0]['periods'],key=lambda p:instant(p['end']))['end'],
        observation=dict(before=pair['before'],after=pair['after'],days=pair['days'],
            pair_age_days=max(0.,(as_of-instant(pair['after']['at'])).total_seconds()/86400),
            area=dict(coverage=pair['coverage'],ndvi=pair['metrics']['ndvi']['base'],ndmi=pair['metrics']['ndmi']['base']),
            window=window,boundary_sensitivity=boundaries),
        latest_usable_place_now=dict(at=latest['at'],coverage=latest['coverage'],
            age_days=(as_of-instant(latest['at'])).total_seconds()/86400) if latest else None,
        question=question,forecast=forecast(fid,as_of),confirmed_boundaries=False,
        priority_usefulness_validated=False,automatic_diagnosis=None)
