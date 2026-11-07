"""Preparation, screened local shadow update and physically derived quantities."""
from datetime import datetime,timedelta,timezone
import math
import statistics


def stamp(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset() is None:raise ValueError('Timezone required')
    return d.astimezone(timezone.utc)


def number(v,lo,hi):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Number outside valid range')
    return float(v)


def preparation(forecast,times,asof,lead_hours,issue_at):
    now=stamp(asof);issue=stamp(issue_at);lead=number(lead_hours,0,48) if lead_hours is not None else None
    if now<issue:raise ValueError('Preparation cannot use an issue from the future')
    idx=forecast.get('early_onset_index')
    if forecast.get('onset_left_censored') or idx is None or lead is None:
        return {'status':'unknown','prepare_by_utc':None,'slack_hours':None,'reason':'onset_censored_or_absent_or_lead_unknown'}
    if not 0<=idx<14:raise ValueError('Invalid onset index')
    onset=stamp(times[0])+timedelta(hours=idx);deadline=onset-timedelta(hours=lead);slack=(deadline-now).total_seconds()/3600
    return {'status':'time_available' if slack>=0 else 'late','prepare_by_utc':deadline.isoformat(),'slack_hours':slack,
      'early_onset_at_utc':onset.isoformat(),'required_lead_hours':lead,'source':'research early conditional edge; not exact physical onset',
      'probability_of_damage':None,'automatic_protection':False}


def wet_bulb(t,rh):
    t=number(t,-90,65);rh=number(rh,0,100)
    if rh==100:return {'value_c':t,'method':'saturated air equality','status':'derived_not_observed'}
    if not -20<=t<=50 or not 5<=rh<=99 or (t<0 and rh<20):
        return {'value_c':None,'status':'outside_stull_domain'}
    value=t*math.atan(.151977*math.sqrt(rh+8.313659))+math.atan(t+rh)-math.atan(rh-1.676331)+.00391838*rh**1.5*math.atan(.023101*rh)-4.686035
    return {'value_c':min(t,value),'status':'approximation_standard_pressure','method':'Stull2011, excludes cold/dry corner; not protection control'}


def local_update(hours,measurements,asof,sensor,protection_status,decay_hours=3,cap_c=3):
    """Forecast interpolation only; measurements and record/QC availability never interpolated."""
    now=stamp(asof);decay=number(decay_hours,.5,12);cap=number(cap_c,.1,5)
    if protection_status!='not_used':return {'status':'protection_unknown_or_used','hours':None}
    if not sensor or sensor.get('measurement_type')!='air_2m' or sensor.get('calibration_status')!='checked' or sensor.get('height_m') is None or abs(sensor['height_m']-2)>.2:
        return {'status':'reference_sensor_unverified','hours':None}
    if len(hours)<3 or any(stamp(b['valid_at_utc'])-stamp(a['valid_at_utc'])!=timedelta(hours=1) for a,b in zip(hours,hours[1:])):raise ValueError('Hourly forecast grid needed')
    times=[stamp(h['valid_at_utc']) for h in hours];residuals=[];ids=[];observed=[]
    for r in measurements:
        m=r['payload'];when=stamp(m['observed_at_utc']);received=stamp(r['recorded_at_utc']);reviewed=stamp(r['reviewed_at_utc']) if r.get('reviewed_at_utc') else received
        if m['sensor_id']!=sensor['id'] or r['effective_status']!='accepted' or max(received,reviewed)>now or when>now or now-when>timedelta(hours=3):continue
        if m.get('temperature_c') is None or not times[0]<=when<=times[-1]:continue
        value=number(m['temperature_c'],-60,60)
        i=max(i for i,t in enumerate(times) if t<=when)
        base=hours[i]['temperature_c']
        if when>times[i] and i<len(times)-1:base+=(hours[i+1]['temperature_c']-base)*(when-times[i]).total_seconds()/3600
        residuals.append(value-base);ids.append(r['id']);observed.append(when)
    if len(set(observed))<3 or max(observed)-min(observed)<timedelta(minutes=30) or now-max(observed)>timedelta(minutes=90):return {'status':'insufficient_recent_measurements','hours':None}
    if max(residuals)-min(residuals)>4:return {'status':'inconsistent_recent_residuals','hours':None}
    correction=max(-cap,min(cap,statistics.median(residuals)));updated=[]
    for h,t in zip(hours,times):
        if now<t<=now+timedelta(hours=6):updated.append({**h,'temperature_c':h['temperature_c']+correction*math.exp(-(t-now).total_seconds()/3600/decay)})
    if not updated:return {'status':'no_future_forecast_hours','hours':None}
    return {'status':'shadow_only','hours':updated,'issued_at_utc':now.isoformat(),'measurement_ids':ids,'initial_correction_c':correction,
      'decay_hours':decay,'cap_c':cap,'original_forecast_unchanged':True,'local_accuracy_validated':False,'crop_damage_probability':None}


def daily_eto(inputs):
    if inputs.get('period_hours')!=24:raise ValueError('FAO56 daily formula requires a complete24hour period')
    t=number(inputs['t_mean_c'],-20,50);u=number(inputs['wind_2m_ms'],0,30);rn=number(inputs['net_radiation_mj_m2_day'],-10,40)
    pressure=number(inputs['pressure_kpa'],50,110);es=number(inputs['saturation_vapour_pressure_kpa'],0,15);ea=number(inputs['actual_vapour_pressure_kpa'],0,15)
    if ea>es:raise ValueError('Actual vapour pressure exceeds saturation')
    g=number(inputs.get('soil_heat_mj_m2_day',0),-10,10);delta=4098*(.6108*math.exp(17.27*t/(t+237.3)))/(t+237.3)**2;gamma=.000665*pressure
    value=(.408*delta*(rn-g)+gamma*(900/(t+273))*u*(es-ea))/(delta+gamma*(1+.34*u))
    return {'eto_mm_day':max(0,value),'model':'FAO56 daily reference crop','irrigation_dose_mm':None,'status':'derived_from_supplied_daily_inputs'}


def weather_work_windows(hours,limits):
    wind=number(limits['max_wind_ms'],0,30);gust=number(limits['max_gust_ms'],0,60);rain=number(limits['max_precipitation_mm'],0,100)
    result=[]
    for h in hours:
        missing=[k for k in ['wind_ms','gust_ms','precipitation_mm'] if h.get(k) is None]
        result.append({'valid_at_utc':h['valid_at_utc'],'suitable':None if missing else h['wind_ms']<=wind and h['gust_ms']<=gust and h['precipitation_mm']<=rain,
          'missing_fields':missing,'scope':'weather conditions only; operator rules required'})
    return result


def economic_scenario(inputs):
    cost=number(inputs['protection_cost_rub'],0,1e9);price=number(inputs['season_price_rub'],0,1e9)
    p=inputs.get('damaging_event_probability');benefit=inputs.get('avoided_loss_if_event_rub');n=inputs.get('decisions_per_season')
    if p is None or benefit is None or n is None:return {'status':'missing_assumptions','estimated_season_net_rub':None,'confirmed_savings_rub':None}
    p=number(p,0,1);benefit=number(benefit,0,1e10);n=number(n,0,366)
    return {'status':'assumption_scenario','estimated_season_net_rub':n*(p*benefit-cost)-price,
      'break_even_event_probability':cost/benefit if benefit else None,'confirmed_savings_rub':None,
      'note':'Damage probability and protection effect are supplied assumptions, not the weather model outputs'}
