"""Separate archive rehearsal: never counted as field records or user interviews."""
from .field_store import FieldStore,digest,encoded,utc_now,stamp
from .cold_exposure import exposure


def save_decision(directory,case,brief,raw,now=None):
    if raw.get('action') not in ['observe','prepare','verify_data']:raise ValueError('Choose a rehearsal action')
    notes=str(raw.get('notes','')).strip()
    if not 8<=len(notes)<=3000:raise ValueError('Describe reason and next check,8..3000 characters')
    if not brief['data_valid']:raise ValueError('Cannot bind a rehearsal decision to invalid forecast data')
    fingerprint=digest(case)
    if raw.get('forecast_sha256')!=fingerprint:raise ValueError('Forecast changed; reload before deciding')
    payload={'case_date':case['date'],'forecast_sha256':fingerprint,'source_issue_at_utc':case['issued_at_utc'],
             'simulated_asof_at_utc':brief['asof_utc'],'threshold_c':brief['threshold_c'],'preparation_hours_assumed':brief['required_lead_hours'],
             'action':raw['action'],'reason_and_next_check':notes,'scope':'archive_rehearsal','mode':'demo',
             'not_field_metrics':True,'not_user_interview':True,'phase_unknown':not brief['phase_known_at_issue'],
             'brief_snapshot':brief,'forecast_snapshot':case,'not_prospective_validation':True}
    store=FieldStore(directory)
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        import json
        reads=[(key,json.loads(body)) for key,body in connection.execute("SELECT id,payload FROM events WHERE kind='archive_result_read'")]
        seen=[key for key,value in reads if value['forecast_sha256']==fingerprint]
        payload.update(fact_visible_when_saved=bool(seen),prior_rehearsal_fact_read_ids=seen)
        return store.append('archive_decision','archive-rehearsal',payload,now or utc_now(),connection)


def result(directory,decision_id,case,truth,now=None):
    store=FieldStore(directory);r=next((r for r in store.records('archive_decision') if r['id']==decision_id),None)
    if not r or r['payload']['case_date']!=case['date'] or r['payload']['forecast_sha256']!=digest(case):raise ValueError('Decision/result issue binding failed')
    p=r['payload'];q=p['threshold_c'];fact=exposure(truth,q);gfs=exposure(case['gfs_temperature_c'],q)
    complete=fact['status']=='complete';actual_event=fact['cold_bins_h']>0 if complete else None
    pred=gfs['cold_bins_h']>0
    comparison='unknown' if not complete else 'hit' if actual_event and pred else 'miss' if actual_event else 'false_alarm' if pred else 'correct_negative'
    read=store.append('archive_result_read','archive-rehearsal',{'decision_id':r['id'],'case_date':case['date'],'forecast_sha256':digest(case),
                                                              'scope':'archive_rehearsal','not_field_metrics':True},now or utc_now())
    return {'decision':r,'result_read_id':read['id'],'date':case['date'],'temperature_c':truth,'actual_minimum_c':min(truth) if complete else None,
            'source_forecast_canonical_json':encoded(case),'source_forecast_sha256':digest(case),
            'decision_canonical_payload_json':encoded(r['payload']),'decision_payload_sha256':r['sha256'],
            'actual_exposure':fact,'gfs_exposure':gfs,'gfs_weather_comparison':comparison,'fact_complete':complete,
            'decision_effect_on_crop':None,'confirmed_savings_rub':None,'site_evidence':'public station archive, not orchard inspection',
            'not_field_metrics':True,'not_user_interview':True}
