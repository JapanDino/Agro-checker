"""Append a separate shadow update, never replacing the frozen field issue."""
from .field_store import stamp,utc_now,digest
from .product_advice import local_update


def update_field(store,profile_id,forecast_id,now=None,decay_hours=6,cap_c=3):
    now=now or utc_now();record=next((r for r in store.records('forecast',profile_id) if r['id']==forecast_id),None)
    if record is None:raise ValueError('Forecast does not belong to selected profile')
    f=record['payload'];before=digest(f)
    if f['mode']!='field' or f['origin']!='live_gfs':return {'status':'demo_not_field','update':None}
    if stamp(f['issued_at_utc'])>now:raise ValueError('Future issue')
    p=f['profile_snapshot'];z=next(z for z in p['zones'] if z['id']==f['zone_id']);sensor=next((s for s in p['sensors'] if s['id']==z.get('reference_sensor_id')),None)
    current=store.profile_record(profile_id)['payload'];current_sensor=next((s for s in current['sensors'] if sensor and s['id']==sensor['id']),None)
    current_zone=next((v for v in current['zones'] if v['id']==z['id']),None)
    if not current_zone or any(z.get(k)!=current_zone.get(k) for k in ['latitude','longitude']):
        return {'status':'reference_zone_changed','update':None}
    if sensor and (not current_sensor or any(current_sensor.get(k)!=sensor.get(k) for k in ['zone_id','measurement_type','height_m','calibration_status'])):
        return {'status':'reference_sensor_changed','update':None}
    night_ids={r['id'] for r in store.records('forecast',profile_id) if r['payload']['zone_id']==f['zone_id'] and r['payload']['night_date_local']==f['night_date_local']}
    decisions=[r['payload'] for r in store.records('journal',profile_id) if r['payload']['forecast_id'] in night_ids and stamp(r['recorded_at_utc'])<=now]
    protection='used' if any(j['action']=='protection' or j.get('protection_status')=='used' for j in decisions) else 'not_used' if any(j.get('protection_status')=='not_used' for j in decisions) else 'unknown'
    update=local_update(f['hours'],store.measurements(profile_id),now.isoformat(),sensor,protection,decay_hours,cap_c)
    if update['hours'] is None:return {'status':update['status'],'update':None}
    update['corrected_targets']=['temperature_c']
    update['other_weather_fields']='Original GFS values; humidity/dewpoint/wind are not jointly corrected by this temperature-only update'
    payload={'forecast_id':forecast_id,'profile_revision':f['profile_revision'],'zone_id':f['zone_id'],'base_forecast_sha256':before,
      'mode':'field','kind':'shadow_local_update','prediction':update,'not_used_for_field_metrics':True}
    saved=store.append('local_update',profile_id,payload,now)
    if digest(next(r['payload'] for r in store.records('forecast',profile_id) if r['id']==forecast_id))!=before:raise ValueError('Original issue unexpectedly changed')
    return {'status':'shadow_only','update':saved}
