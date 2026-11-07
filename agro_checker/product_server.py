"""Local model workbench; archival facts have a separate result endpoint."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlparse,parse_qs
from .pipeline import ROOT
from .product_advice import preparation,wet_bulb,daily_eto,economic_scenario,weather_work_windows
from .field_store import FieldStore,digest,utc_now
from .agronom_brief import brief as agronom_brief,field_brief
from .agronom_rehearsal import save_decision as save_rehearsal,result as rehearsal_result
from .product_field_bridge import update_field
from .product_data import sha
from .product_domain import threshold_order_valid


def make_server(port=8790,field_directory=None,rehearsal_directory=None):
    summary=json.loads((ROOT/'docs/PRODUCT_RISK_SUMMARY.json').read_text(encoding='utf-8'))
    file=ROOT/'data/processed/product-risk/night-forecasts.json'
    if sha(file)!=summary['forecast_sha256']:raise ValueError('Workbench forecast integrity failed')
    frames={f['date']:f for f in json.loads(file.read_text(encoding='utf-8'))}
    coherent_path=ROOT/'docs/COHERENT_COLD_SUMMARY.json'
    coherent=None;coherent_frames={}
    if coherent_path.exists():
        coherent=json.loads(coherent_path.read_text(encoding='utf-8'))
        coherent_file=ROOT/'data/processed/coherent-cold/night-forecasts.json'
        coherent_lock=ROOT/'models/research-coherent-cold/selection-before-controls.json'
        if sha(coherent_file)!=coherent['forecast_sha256'] or sha(coherent_lock)!=coherent['selection_sha256']:
            raise ValueError('Coherent CDF integrity failed')
        coherent_frames={f['date']:f for f in json.loads(coherent_file.read_text(encoding='utf-8'))}
        if set(coherent_frames)!=set(frames):raise ValueError('Coherent date alignment failed')
        for date,f in coherent_frames.items():
            if f['legacy_detector']!=frames[date]['legacy_detector'] or f['legacy_point']!=frames[date]['legacy_point'] or not threshold_order_valid(f['threshold_forecasts']):
                raise ValueError('Coherent CDF/reference invariants failed')
    calibration_path=ROOT/'docs/COLD_CALIBRATION_SUMMARY.json'
    calibration=None;calibration_frames={}
    if calibration_path.exists():
        calibration=json.loads(calibration_path.read_text(encoding='utf-8'))
        calibration_file=ROOT/'data/processed/cold-calibration/night-forecasts.json'
        calibration_lock=ROOT/'models/research-cold-calibration/selection-before-controls.json'
        if sha(calibration_file)!=calibration['forecast_sha256'] or sha(calibration_lock)!=calibration['selection_sha256']:
            raise ValueError('Calibration snapshot integrity failed')
        calibration_frames={f['date']:f for f in json.loads(calibration_file.read_text(encoding='utf-8'))}
        if set(calibration_frames)!=set(frames):raise ValueError('Calibration date alignment failed')
        for date,f in calibration_frames.items():
            if f['legacy_detector']!=frames[date]['legacy_detector'] or f['legacy_point']!=frames[date]['legacy_point'] or not threshold_order_valid(f['threshold_forecasts']):
                raise ValueError('Calibration/reference invariants failed')
            if calibration['selection']['winner']=='identity' and f['threshold_forecasts']['0']['probability']!=f['legacy_detector']['night_event_model_score']:
                raise ValueError('Identity calibration changed original score')
    weather={f['date']:f['baseline'] for f in json.loads((ROOT/'data/processed/scenario-study/night-forecasts.json').read_text(encoding='utf-8'))}
    local=json.loads((ROOT/'docs/LOCAL_UPDATE_SUMMARY.json').read_text(encoding='utf-8'));local_file=ROOT/'data/processed/local-update/night-forecasts.json'
    if sha(local_file)!=local['forecast_sha256']:raise ValueError('Local-update snapshot changed')
    updates={r['date']:r for r in json.loads(local_file.read_text(encoding='utf-8'))}
    audit=json.loads((ROOT/'docs/PRODUCT_DATA_AUDIT.json').read_text(encoding='utf-8'));token=secrets.token_urlsafe(32)
    gefs_file=ROOT/'data/processed/product-gefs/2023-03-01.json'
    gefs=json.loads(gefs_file.read_text(encoding='utf-8')) if gefs_file.exists() else None
    store=FieldStore(field_directory or ROOT/'data/field')
    demo_directory=rehearsal_directory or (store.directory/'agronom-rehearsal')
    exposure_path=ROOT/'docs/COLD_EXPOSURE_SUMMARY.json';exposure_summary=None;exposure_frames={}
    if exposure_path.exists():
        exposure_summary=json.loads(exposure_path.read_text(encoding='utf-8'))
        exposure_file=ROOT/'data/processed/cold-exposure/night-forecasts.json'
        if sha(exposure_file)!=exposure_summary['forecast_sha256']:raise ValueError('Exposure snapshot changed')
        exposure_frames={f['date']:f for f in json.loads(exposure_file.read_text(encoding='utf-8'))}

    def public(f):
        n=f['sample'];baseline=weather[f['date']]
        return {'date':f['date'],'year':f['year'],'run_at_utc':n['run_at_utc'],'issued_at_utc':n['issued_at_utc'],'times':n['times'],
          'gfs_temperature_c':n['gfs_temperature_c'],'legacy_point':f['legacy_point'],'legacy_detector':f['legacy_detector'],
          'threshold_forecasts':f['threshold_forecasts'],'wet_bulb':[wet_bulb(t,rh) for t,rh in zip(n['gfs_temperature_c'],baseline['humidity'])],
          'threshold_order_valid':threshold_order_valid(f['threshold_forecasts']),
          'coherent_forecasts':coherent_frames[f['date']]['threshold_forecasts'] if coherent else None,
          'calibrated_forecasts':calibration_frames[f['date']]['threshold_forecasts'] if calibration else None,
          'wind_ms':baseline['wind'],'case_scope':'station archive, not selected orchard'}

    presets={}
    for f in frames.values():
        values=f['sample']['target_temperature_c']
        if any(v is None for v in values):continue
        cold=min(values)<0;pred=any(f['forecasts']['candidate']['mask']);kind='hit' if cold and pred else 'miss' if cold else 'false_alarm' if pred else None
        if kind and kind not in presets:presets[kind]=f['date']
    def archive_case(date):
        f=frames[date];n=f['sample'];baseline=weather[date]
        return {'date':date,'run_at_utc':n['run_at_utc'],'issued_at_utc':n['issued_at_utc'],'times':n['times'],
                'gfs_temperature_c':n['gfs_temperature_c'],
                'hours':[{'valid_at_utc':t,'temperature_c':v,'rh_pct':rh,'dewpoint_c':dp,'wind_ms':w}
                         for t,v,rh,dp,w in zip(n['times'],n['gfs_temperature_c'],baseline['humidity'],n['gfs_dewpoint_c'],baseline['wind'])],
                'legacy_point':f['legacy_point'],'legacy_detector':f['legacy_detector'],
                'calibrated_forecasts':calibration_frames[date]['threshold_forecasts'] if calibration else None,
                'exposure_prediction':exposure_frames[date]['predictions']['selected'] if exposure_summary else None,
                'source_scope':'public station archive; orchard location and phase not selected'}

    def archive_brief(date,threshold,lead):
        case=archive_case(date)
        value=agronom_brief(case['hours'],case['run_at_utc'],case['issued_at_utc'],case['issued_at_utc'],threshold,lead,
                            reference_event=any(case['legacy_detector']['cold_mask']))
        return {'case':case,'forecast_sha256':digest(case),'brief':value}

    agronom_presets={}
    for date,f in frames.items():
        values=f['sample']['target_temperature_c']
        if any(v is None for v in values):continue
        fact=min(values)<0;gfs=min(f['sample']['gfs_temperature_c'])<0;det=any(f['legacy_detector']['cold_mask'])
        kind='agreement' if fact and gfs and det else 'disagreement' if fact and not gfs and det else 'verification' if not fact and det else None
        if kind and kind not in agronom_presets:agronom_presets[kind]=date

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value,mime='application/json; charset=utf-8'):
            raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode() if mime.startswith('application/json') else value
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(raw)
        def allowed(self,mutation=False):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts or self.headers.get('Origin') not in [None,*['http://'+h for h in hosts]]:
                self.reply(403,{'error':'Local same-origin only'});return False
            if mutation and self.headers.get('X-Product-Token')!=token:self.reply(403,{'error':'Reload workbench'});return False
            return True
        def do_GET(self):
            if not self.allowed():return
            u=urlparse(self.path);query=parse_qs(u.query)
            try:
                if u.path=='/api/agronom/state':
                    forecasts=[{'forecast_id':r['id'],'profile_id':r['profile_id'],'night':r['payload']['night_date_local'],'zone_id':r['payload']['zone_id'],'mode':r['payload']['mode']} for r in store.records('forecast')]
                    profiles=[{'profile_id':r['profile_id'],'name':r['payload']['name'],'mode':r['payload']['mode'],
                               'location_selected':any(z['latitude'] is not None and z['longitude'] is not None for z in r['payload']['zones'])} for r in store.records('profile')]
                    latest={p['profile_id']:p for p in profiles}
                    history=[{'id':r['id'],'date':r['payload']['case_date'],'threshold_c':r['payload']['threshold_c'],'lead_hours':r['payload']['preparation_hours_assumed'],
                              'action':r['payload']['action'],'recorded_at_utc':r['recorded_at_utc']} for r in FieldStore(demo_directory).records('archive_decision')]
                    return self.reply(200,{'token':token,'dates':list(frames),'presets':agronom_presets,'profiles':list(latest.values()),'forecasts':forecasts,'rehearsal_decisions':history,
                                          'case_count':len(frames),'detector_scope':'station research, not transferred to orchard','field_site_nights':store.state()['field_metrics']['unique_site_nights'],
                                          'exposure_quality':{'winner':exposure_summary['selection']['winner'],'seasons':exposure_summary['seasons']} if exposure_summary else None})
                if u.path=='/api/agronom/brief':
                    threshold=float(query.get('threshold',['0'])[0]);lead=float(query['lead'][0]) if query.get('lead') else None
                    return self.reply(200,archive_brief(query['date'][0],threshold,lead))
                if u.path=='/api/agronom/field':return self.reply(200,field_brief(store,query['forecast_id'][0],utc_now()))
                if u.path=='/api/agronom/decision':
                    r=next((r for r in FieldStore(demo_directory).records('archive_decision') if r['id']==query['decision_id'][0]),None)
                    if r is None:raise ValueError('Unknown rehearsal decision')
                    date=r['payload']['case_date'];case=r['payload'].get('forecast_snapshot') or archive_case(date)
                    if digest(case)!=r['payload']['forecast_sha256']:raise ValueError('Saved rehearsal source checksum failed')
                    return self.reply(200,{'decision':r,'case':case,'forecast_sha256':digest(case),'brief':r['payload']['brief_snapshot']})
                if u.path=='/api/agronom/result':
                    r=next((r for r in FieldStore(demo_directory).records('archive_decision') if r['id']==query['decision_id'][0]),None)
                    if r is None:raise ValueError('Save a rehearsal decision before opening its result')
                    date=r['payload']['case_date'];source_case=r['payload'].get('forecast_snapshot') or archive_case(date)
                    return self.reply(200,rehearsal_result(demo_directory,r['id'],source_case,frames[date]['sample']['target_temperature_c']))
                if u.path=='/api/state':
                    field=[{'forecast_id':r['id'],'profile_id':r['profile_id'],'night':r['payload']['night_date_local'],'mode':r['payload']['mode']} for r in store.records('forecast')]
                    return self.reply(200,{'token':token,'cases':[public(f) for f in frames.values()],'presets':presets,
                      'quality':{'risk':summary['seasons'],'local':local['seasons'],'winner':summary['selection']['winner'],
                        'validation':summary['selection']['validation'],'diagnostic':summary['selection']['diagnostic']},
                      'coherent_quality':{'seasons':coherent['seasons'],'winner':coherent['selection']['winner'],
                        'validation':{name:coherent['selection']['validation'][name] for name in ['legacy','previous_candidate',coherent['selection']['diagnostic']]},
                        'decision_threshold':float(coherent['selection']['diagnostic'].split('@')[1]),
                        'timing_updated':False,'field_promoted':False} if coherent else None,
                      'calibration_quality':{'seasons':calibration['seasons'],'winner':calibration['selection']['winner'],
                        'diagnostic':calibration['selection']['diagnostic'],
                        'validation':{name:calibration['selection']['validation'][name] for name in ['identity','ordinal',calibration['selection']['diagnostic']]},
                        'gates':calibration['selection']['gates'],
                        'detector_changed':False,'timing_updated':False,'field_promoted':False} if calibration else None,
                      'stations':audit['stations'],'local_parameters':local['selection']['selected_decay_cap'],'field_forecasts':field,
                      'gefs_sample':{k:gefs[k] for k in ['status','member_count','requested_member_count','temporal_step_hours','grid_degrees','used_in_fitting']} if gefs else None})
                if u.path=='/api/result':
                    f=frames[query['date'][0]];values=f['sample']['target_temperature_c']
                    return self.reply(200,{'date':f['date'],'temperature_c':values,'complete':all(v is not None for v in values),
                      'actual_minimum_c':min(values) if all(v is not None for v in values) else None})
                if u.path=='/api/local-preview':
                    date=query['date'][0]
                    if date not in updates:return self.reply(200,{'status':'not_scored','reason':'Incomplete observations or failed recent-data screening'})
                    r=updates[date]
                    # Future truth is intentionally not returned while advancing to an intermediate cutoff.
                    return self.reply(200,{'status':'historical_simulation','date':date,'asof':r['asof'],'indexes':r['indexes'],
                      'updated':r['updated'],'first_three_observations':frames[date]['sample']['target_temperature_c'][:3],'assumption':r['assumption']})
                files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/styles.css':('styles.css','text/css; charset=utf-8'),
                       '/agronom':('agronom.html','text/html; charset=utf-8'),'/agronom.js':('agronom.js','text/javascript; charset=utf-8'),'/agronom.css':('agronom.css','text/css; charset=utf-8')}
                if u.path in files:
                    name,mime=files[u.path];return self.reply(200,(ROOT/'web/support'/name).read_bytes(),mime)
                return self.reply(404,{'error':'Not found'})
            except (ValueError,KeyError,TypeError,OSError) as exc:self.reply(400,{'error':str(exc)})
        def do_POST(self):
            if not self.allowed(True):return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=100000:raise ValueError('Request too large')
                body=json.loads(self.rfile.read(length));path=urlparse(self.path).path
                if path=='/api/agronom/decision':
                    b=archive_brief(body['date'],body.get('threshold_c',0),body.get('lead_hours'))
                    result=save_rehearsal(demo_directory,b['case'],b['brief'],body)
                elif path=='/api/preparation':
                    f=frames[body['date']];threshold=body['threshold_c'];risk=f['threshold_forecasts'][str(threshold)]
                    result=preparation(risk,f['sample']['times'],body.get('asof',f['sample']['issued_at_utc']),body.get('lead_hours'),f['sample']['issued_at_utc']) if threshold_order_valid(f['threshold_forecasts']) else {'status':'unknown','prepare_by_utc':None,'slack_hours':None,'reason':'inconsistent_threshold_order'}
                    result.update(research_only=True,phase_confirmed=False,protocol_reviewed=False)
                elif path=='/api/economics':
                    result=economic_scenario(body)
                    if result['status']=='assumption_scenario':
                        n,benefit=body['decisions_per_season'],body['avoided_loss_if_event_rub']
                        result['protection_break_even_probability_excluding_service']=result['break_even_event_probability']
                        result['break_even_event_probability']=(body['protection_cost_rub']+body['season_price_rub']/n)/benefit if n>0 and benefit>0 else None
                        result['break_even_includes_season_price']=True
                elif path=='/api/eto':result=daily_eto(body)
                elif path=='/api/field-update':result=update_field(store,body['profile_id'],body['forecast_id'],decay_hours=local['selection']['selected_decay_cap'][0],cap_c=local['selection']['selected_decay_cap'][1])
                else:return self.reply(404,{'error':'Not found'})
                self.reply(200,result)
            except (ValueError,KeyError,TypeError,OSError) as exc:self.reply(400,{'error':str(exc)})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8790);a=p.parse_args()
    with make_server(a.port) as server:
        print(f'Model workbench: http://127.0.0.1:{a.port}/',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
