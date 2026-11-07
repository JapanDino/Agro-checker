"""Loopback-only UI and bounded JSON API for the shadow field pilot."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import re
from threading import Event, Lock, Thread
from urllib.parse import urlparse, parse_qs

from .field_live import capture_forecast
from .field_store import FieldStore, encoded, seed_demo
from .pipeline import ROOT


def make_server(directory, port=8770):
    store = FieldStore(directory).init(); token = secrets.token_urlsafe(32); jobs = {}; mutex = Lock(); capture_lock = Lock()
    satellite_job = {"status": "idle", "message": "Обновление ещё не запускалось в этом сеансе"}; satellite_mutex = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def reply(self, code, body, mime="application/json; charset=utf-8"):
            data = encoded(body).encode() if mime.startswith("application/json") else body
            self.send_response(code); self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)

        def allowed(self, mutation=False):
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in expected: self.reply(403, {"error": "Local host only"}); return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://"+host for host in expected}: self.reply(403, {"error": "Same origin only"}); return False
            if mutation and self.headers.get("X-Field-Token") != token: self.reply(403, {"error": "Refresh the page"}); return False
            return True

        def do_GET(self):
            if not self.allowed(): return
            path = urlparse(self.path).path
            try:
                if path.startswith('/api/scouting/'):
                    try:from . import farm_scouting as scout
                    except ImportError:return self.reply(503,{'error':'Модуль архивных карт недоступен. Проверьте зависимости requirements-scouting.txt; данные не подменены.'})
                    q = parse_qs(urlparse(self.path).query, keep_blank_values=True, max_num_fields=6)
                    def query(keys):
                        if set(q) != set(keys) or any(len(v)!=1 or len(v[0])>120 for v in q.values()):
                            raise ValueError('Некорректный запрос к архиву осмотров')
                        return {k:v[0] for k,v in q.items()}
                    if path == '/api/scouting/state':
                        query([])
                        from .farm_workspace import plans
                        return self.reply(200, {**scout.overview(), **scout.listing(store), 'plans':plans(store), 'token':token})
                    if path == '/api/scouting/workspace':
                        from .farm_workspace import comparison
                        keys=['period']+(['before','after'] if 'before' in q or 'after' in q else [])
                        return self.reply(200, comparison(**query(keys)))
                    if path == '/api/scouting/area':
                        from .farm_workspace import area_detail
                        return self.reply(200, area_detail(**query(['fid','period','before','after'])))
                    if path == '/api/scouting/plans/export':
                        from .farm_workspace import plan_export
                        return self.reply(200,plan_export(store,query(['id'])['id']))
                    if path == '/api/scouting/export':
                        query([])
                        return self.reply(200, scout.listing(store))
                    if path == '/api/scouting/detail':
                        keys = ['fid','period'] + (['before','after'] if 'before' in q or 'after' in q else [])
                        return self.reply(200, scout.detail(**query(keys)))
                    if path == '/api/scouting/map':
                        return self.reply(200, scout.map_png(**query(['fid','period','before','after','key','side'])), 'image/png')
                    if path == '/api/scouting/history':
                        return self.reply(200, scout.place_series(query(['place_id'])['place_id']))
                    if path == '/api/scouting/location':
                        values=query(['fid','period','before','after','relative'])
                        relative=json.loads(values.pop('relative'))
                        d=scout.detail(**values)
                        loc=scout.location(values['fid'],relative=relative,crop=d['pair']['crop'])
                        return self.reply(200,{'location':loc,'comparison':scout.window_comparison(loc['id'],values['before'],values['after'])})
                    match = re.fullmatch(r'/api/scouting/visits/([a-f0-9]{32})/photos/([a-f0-9]{64})',path)
                    if match:
                        query([]);content,mime = scout.saved_photo(store,match[1],match[2])
                        return self.reply(200,content,mime)
                    return self.reply(404, {'error':'Not found'})
                if path == "/api/state":
                    with mutex: current_jobs = list(jobs.values())
                    return self.reply(200, {**store.state(), "token": token, "jobs": current_jobs, "daily_shadow": getattr(self.server, "daily_shadow", False)})
                if path == "/api/export": return self.reply(200, {"events": store.records(), "state": store.state()})
                if path == '/api/site-validation/night':
                    from .site_validation_night import night_detail
                    try:
                        q=parse_qs(urlparse(self.path).query,keep_blank_values=True,max_num_fields=4)
                        if set(q)!={'profile_id','zone_id','version','night'} or any(len(v)!=1 for v in q.values()):raise ValueError('Выберите паспорт, участок, версию и одну ночь')
                        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',q[k][0]) for k in ['profile_id','zone_id','version']):raise ValueError('Некорректная ссылка на участок или версию')
                        result=night_detail(store,**{k:v[0] for k,v in q.items()})
                    except KeyError as exc:return self.reply(404,{'error':str(exc)})
                    except (ValueError,TypeError) as exc:return self.reply(422,{'error':str(exc)})
                    except OSError:return self.reply(503,{'error':'Журнал ночи недоступен. Учебные данные не подставлены.'})
                    return self.reply(200,result)
                if path == '/api/site-validation':
                    from .site_validation_report import report
                    try:
                        q=parse_qs(urlparse(self.path).query,keep_blank_values=True,max_num_fields=3)
                        if set(q)-{'profile_id','zone_id','version'} or any(len(v)!=1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',v[0]) for v in q.values()):
                            raise ValueError('Выберите один паспорт и участок')
                        result=report(store,q.get('profile_id',[None])[0],q.get('zone_id',[None])[0],version=q.get('version',[None])[0])
                    except KeyError as exc:return self.reply(404,{'error':str(exc)})
                    except (ValueError,TypeError) as exc:return self.reply(422,{'error':str(exc)})
                    except OSError:return self.reply(503,{'error':'План или журнал проверки недоступен. Предыдущие результаты не подставлены.'})
                    return self.reply(200,result)
                if path == "/api/prototype-feedback/export":
                    from .prototype_feedback import feedback_export
                    return self.reply(200, feedback_export(store))
                if path == "/api/land/status":
                    from datetime import datetime, timezone
                    with satellite_mutex: job = dict(satellite_job)
                    return self.reply(200, {"token":token,"job":job,"server_now_utc":datetime.now(timezone.utc).isoformat()})
                if path in ["/api/land/inspections","/api/land/inspections/export"]:
                    from .land_inspections import listing
                    from .land_place_history import groups
                    return self.reply(200,{"items":listing(store),"places":groups(store),"diagnoses_enabled":False})
                if path == '/api/land/inspections/history':
                    from .land_place_history import history
                    try:
                        q=parse_qs(urlparse(self.path).query,keep_blank_values=True,max_num_fields=2)
                        if set(q)!={'reference','window'} or any(len(v)!=1 for v in q.values()) or len(q['reference'][0])>2000 or len(q['window'][0])>40:
                            raise ValueError('Выберите место и версию снимков')
                        reference=json.loads(q['reference'][0])
                        result=history(store,reference,q['window'][0])
                    except (ValueError,KeyError) as exc:return self.reply(422,{'error':str(exc)})
                    except (OSError,ImportError):return self.reply(503,{'error':'Спутниковая история недоступна; пропуски не заполнены'})
                    return self.reply(200,result)
                match=re.fullmatch(r'/api/land/inspections/([a-f0-9]{32})/(comparison|history|photos/([a-f0-9]{64}))',path)
                if match:
                    from .land_inspections import saved_comparison, saved_photo
                    try:
                        if match[2]=='comparison': return self.reply(200,saved_comparison(store,match[1]))
                        if match[2]=='history':
                            from .land_place_history import history
                            return self.reply(200,history(store,record_id=match[1]))
                        photo,mime=saved_photo(store,match[1],match[3]);return self.reply(200,photo,mime)
                    except KeyError: return self.reply(404,{"error":"Запись или приложение не найдены"})
                    except (ValueError,OSError,ImportError): return self.reply(503,{"error":"Запись или приложение недоступны либо не прошли проверку целостности"})
                if path.startswith('/api/land/inspections/'): return self.reply(404,{"error":"Not found"})
                if path in ["/api/land", "/api/land/recent", "/api/land/compare"] or path.startswith(("/api/land/scene/", "/api/land/recent/scene/")):
                    try:
                        from .land_view import overview, scene, recent, recent_scene
                        if path == "/api/land/compare":
                            from .land_change import comparison
                            try: query=parse_qs(urlparse(self.path).query,keep_blank_values=True,max_num_fields=3)
                            except ValueError: return self.reply(422,{"error":"Нужны только период и две съёмки"})
                            if set(query)!={"period","before","after"} or any(len(v)!=1 or not 0<len(v[0])<=100 for v in query.values()):
                                return self.reply(422,{"error":"Выберите период и две съёмки"})
                            try: result=comparison(query['period'][0],query['before'][0],query['after'][0])
                            except ValueError as exc: return self.reply(422,{"error":str(exc)})
                            from .land_inspections import reference_for
                            result['inspection_reference']=reference_for(result,query['period'][0])
                        elif path == "/api/land": result = overview()
                        elif path == "/api/land/recent": result = recent()
                        elif path.startswith("/api/land/recent/scene/"): result = recent_scene(path.removeprefix("/api/land/recent/scene/"))
                        else: result = scene(path.removeprefix("/api/land/scene/"))
                        return self.reply(200, result)
                    except KeyError:
                        return self.reply(404, {"error": "Архивная съёмка не найдена"})
                    except (ImportError, ValueError, OSError):
                        return self.reply(503, {"error": "Архив карт недоступен или не прошёл проверку. Данные не заменены расчётными значениями."})
                files = {"/scouting/about": (ROOT/"web/scouting/about.html", "text/html; charset=utf-8"),
                         "/farm-reconstruction": (ROOT/"web/farm-reconstruction/index.html", "text/html; charset=utf-8"),
                         "/scouting": (ROOT/"web/scouting/index.html", "text/html; charset=utf-8"),
                         "/scouting/app.js": (ROOT/"web/scouting/app.js", "text/javascript; charset=utf-8"),
                         "/scouting/plans.js": (ROOT/"web/scouting/plans.js", "text/javascript; charset=utf-8"),
                         "/scouting/styles.css": (ROOT/"web/scouting/styles.css", "text/css; charset=utf-8"),
                         "/scouting/theme.css": (ROOT/"web/scouting/theme.css", "text/css; charset=utf-8"),
                         "/scouting/interior.css": (ROOT/"web/scouting/interior.css", "text/css; charset=utf-8"),
                         "/scouting/experience.css": (ROOT/"web/scouting/experience.css", "text/css; charset=utf-8"),
                         "/scouting/experience-interior.css": (ROOT/"web/scouting/experience-interior.css", "text/css; charset=utf-8"),
                         "/scouting/hightech.css": (ROOT/"web/scouting/hightech.css", "text/css; charset=utf-8"),
                         "/scouting/design-controls.css": (ROOT/"web/scouting/design-controls.css", "text/css; charset=utf-8"),
                         "/scouting/design.js": (ROOT/"web/scouting/design.js", "text/javascript; charset=utf-8"),
                         "/scouting/pulse.css": (ROOT/"web/scouting/pulse.css", "text/css; charset=utf-8"),
                         "/scouting/pulse-screens.css": (ROOT/"web/scouting/pulse-screens.css", "text/css; charset=utf-8"),
                         "/scouting/pulse.js": (ROOT/"web/scouting/pulse.js", "text/javascript; charset=utf-8"),
                         "/scouting/showcase.js": (ROOT/"web/scouting/showcase.js", "text/javascript; charset=utf-8"),
                         "/scouting/assets/fonts/manrope-cyrillic-wght-normal.woff2": (ROOT/"web/scouting/assets/fonts/manrope-cyrillic-wght-normal.woff2", "font/woff2"),
                         "/scouting/assets/fonts/manrope-latin-wght-normal.woff2": (ROOT/"web/scouting/assets/fonts/manrope-latin-wght-normal.woff2", "font/woff2"),
                         "/scouting/assets/fonts/manrope-OFL.txt": (ROOT/"web/scouting/assets/fonts/manrope-OFL.txt", "text/plain; charset=utf-8"),
                         "/scouting/drafts.js": (ROOT/"web/scouting/drafts.js", "text/javascript; charset=utf-8"),
                         "/scouting/workspace.css": (ROOT/"web/scouting/workspace.css", "text/css; charset=utf-8"),
                         "/scouting/map.js": (ROOT/"web/scouting/map.js", "text/javascript; charset=utf-8"),
                         "/scouting/assets/leaflet.js": (ROOT/"web/scouting/assets/leaflet.js", "text/javascript; charset=utf-8"),
                         "/scouting/assets/leaflet.css": (ROOT/"web/scouting/assets/leaflet.css", "text/css; charset=utf-8"),
                         "/scouting/assets/LEAFLET-LICENSE.txt": (ROOT/"web/scouting/assets/LEAFLET-LICENSE.txt", "text/plain; charset=utf-8"),
                         "/scouting/assets/public-context.json": (ROOT/"web/scouting/assets/public-context.json", "application/json; charset=utf-8"),
                         "/scouting/assets/basemap.jpg": (ROOT/"web/scouting/assets/basemap.jpg", "image/jpeg"),
                         "/scouting/assets/wide-basemap.jpg": (ROOT/"web/scouting/assets/wide-basemap.jpg", "image/jpeg"),
                         "/scouting/assets/terrain.png": (ROOT/"web/scouting/assets/terrain.png", "image/png"),
                         "/scouting/assets/clay.png": (ROOT/"web/scouting/assets/clay.png", "image/png"),
                         "/scouting/assets/ph.png": (ROOT/"web/scouting/assets/ph.png", "image/png"),
                         "/farm-reconstruction/context.js": (ROOT/"web/farm-reconstruction/context.js", "text/javascript; charset=utf-8"),
                         "/farm-reconstruction/context.css": (ROOT/"web/farm-reconstruction/context.css", "text/css; charset=utf-8"),
                         "/farm-reconstruction/context-data.json": (ROOT/"web/farm-reconstruction/context-data.json", "application/json; charset=utf-8"),
                         "/farm-reconstruction/context-terrain.png": (ROOT/"web/farm-reconstruction/context-terrain.png", "image/png"),
                         "/farm-reconstruction/context-clay.png": (ROOT/"web/farm-reconstruction/context-clay.png", "image/png"),
                         "/farm-reconstruction/context-ph.png": (ROOT/"web/farm-reconstruction/context-ph.png", "image/png"),
                         "/farm-reconstruction/context-terrain-scale.png": (ROOT/"web/farm-reconstruction/context-terrain-scale.png", "image/png"),
                         "/farm-reconstruction/context-clay-scale.png": (ROOT/"web/farm-reconstruction/context-clay-scale.png", "image/png"),
                         "/farm-reconstruction/context-ph-scale.png": (ROOT/"web/farm-reconstruction/context-ph-scale.png", "image/png"),
                         "/farm-reconstruction/app.js": (ROOT/"web/farm-reconstruction/app.js", "text/javascript; charset=utf-8"),
                         "/farm-reconstruction/styles.css": (ROOT/"web/farm-reconstruction/styles.css", "text/css; charset=utf-8"),
                         "/farm-reconstruction/data.json": (ROOT/"web/farm-reconstruction/data.json", "application/json; charset=utf-8"),
                         "/farm-reconstruction/satellite-aligned.jpg": (ROOT/"web/farm-reconstruction/satellite-aligned.jpg", "image/jpeg"),
                         "/farm-reconstruction/screenshot.jpg": (ROOT/"web/farm-reconstruction/screenshot.jpg", "image/jpeg"),
                         "/farm-reconstruction/areas.geojson": (ROOT/"data/farm-research/reconstruction/approximate-areas.geojson", "application/geo+json; charset=utf-8"),
                         "/farm-reconstruction/uncertainty.geojson": (ROOT/"data/farm-research/reconstruction/uncertainty-bands.geojson", "application/geo+json; charset=utf-8"),
                         "/": (ROOT/"web/field/index.html", "text/html; charset=utf-8"),
                         "/validation": (ROOT/"web/validation/index.html", "text/html; charset=utf-8"),
                         "/validation.js": (ROOT/"web/validation/app.js", "text/javascript; charset=utf-8"),
                         "/validation-plan.js": (ROOT/"web/validation/plan-editor.js", "text/javascript; charset=utf-8"),
                         "/validation.css": (ROOT/"web/validation/styles.css", "text/css; charset=utf-8"),
                         "/validation-import.js": (ROOT/"web/validation/measurement-import.js", "text/javascript; charset=utf-8"),
                         "/validation-night.js": (ROOT/"web/validation/night-chart.js", "text/javascript; charset=utf-8"),
                         "/validation-template.csv": (ROOT/"config/site_measurements_template.csv", "text/csv; charset=utf-8"),
                         "/land": (ROOT/"web/land/index.html", "text/html; charset=utf-8"),
                         "/land.js": (ROOT/"web/land/app.js", "text/javascript; charset=utf-8"),
                         "/land-journal.js": (ROOT/"web/land/journal.js", "text/javascript; charset=utf-8"),
                         "/land-place-history.js": (ROOT/"web/land/place-history.js", "text/javascript; charset=utf-8"),
                         "/land.css": (ROOT/"web/land/styles.css", "text/css; charset=utf-8"),
                         "/app.js": (ROOT/"web/field/app.js", "text/javascript; charset=utf-8"),
                         "/chart.js": (ROOT/"web/field/chart.js", "text/javascript; charset=utf-8"),
                         "/journey.js": (ROOT/"web/field/journey.js", "text/javascript; charset=utf-8"),
                         "/styles.css": (ROOT/"web/field/styles.css", "text/css; charset=utf-8"),
                         "/template.csv": (ROOT/"config/field_measurements_template.csv", "text/csv; charset=utf-8"),
                         "/archive": (ROOT/"dist/pilot/index.html", "text/html; charset=utf-8"),
                         "/research": (ROOT/"dist/research/index.html", "text/html; charset=utf-8"),
                         "/model": (ROOT/"dist/model/index.html", "text/html; charset=utf-8"),
                         "/cold-range": (ROOT/"dist/cold-range/index.html", "text/html; charset=utf-8"),
                         "/conditional-range": (ROOT/"dist/conditional-range/index.html", "text/html; charset=utf-8")}
                if path not in files: return self.reply(404, {"error": "Not found"})
                file, mime = files[path]
                if path in ["/archive", "/research", "/model", "/cold-range", "/conditional-range"]:
                    # Standalone local documents use embedded scripts/styles; their own policies apply.
                    data = file.read_bytes(); self.send_response(200); self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
                self.reply(200, json.loads(file.read_text(encoding="utf-8")) if mime.startswith("application/json") else file.read_bytes(), mime)
            except (ValueError, OSError, KeyError) as exc: self.reply(400, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed(True): return
            try:
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                limit=21_000_000 if path in ['/api/land/inspections','/api/scouting/visits'] else 3_000_000
                if not 0 < length <= limit: raise ValueError("Превышен допустимый размер запроса")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == '/api/scouting/plans':
                    from .farm_workspace import save_plan
                    result=save_plan(store,body)
                elif path in ['/api/scouting/tasks','/api/scouting/visits']:
                    from .farm_scouting import save_task,save_visit
                    result = save_task(store,body) if path.endswith('/tasks') else save_visit(store,body)
                elif path == '/api/site-validation/plans':
                    from .site_validation_plans import save,Conflict
                    try:result=save(store,body)
                    except Conflict as exc:return self.reply(409,{'error':str(exc)})
                elif path in ['/api/site-validation/measurements/preview','/api/site-validation/measurements/commit']:
                    from .site_measurement_import import preview,public_preview,commit
                    from .site_validation_plans import Conflict
                    try:result=public_preview(preview(store,body)) if path.endswith('/preview') else commit(store,body)
                    except Conflict as exc:return self.reply(409,{'error':str(exc)})
                elif path == '/api/land/inspections':
                    from .land_inspections import save
                    try: result=save(store,body)
                    except ImportError: return self.reply(503,{"error":"Проверка фотографий пока недоступна. Можно сохранить запись без фотографий"})
                elif path == "/api/land/refresh":
                    # A separate job never occupies or changes the weather capture/field journal.
                    with satellite_mutex:
                        if satellite_job["status"] == "running": return self.reply(200, {"job":dict(satellite_job)})
                        satellite_job.clear(); satellite_job.update(status="running",id=secrets.token_hex(8),message="Начинаем обновление Sentinel-2")
                    def refresh_satellite():
                        def progress(message):
                            with satellite_mutex: satellite_job["message"] = message
                        try:
                            from .sentinel_updates import refresh
                            snapshot = refresh(progress=progress)
                            errors=snapshot["statistics"]["extraction_error_dates"]
                            with satellite_mutex: satellite_job.update(status="completed",snapshot_id=snapshot["id"],message=f"История обновлена; ошибок обработки: {errors}")
                        except Exception:
                            with satellite_mutex: satellite_job.update(status="failed",message="Обновление не завершено. Сохранённая история доступна; проверьте соединение и повторите позже. Возможно, обновление уже запущено другим процессом.")
                    Thread(target=refresh_satellite,daemon=True).start()
                    with satellite_mutex: result={"job":dict(satellite_job)}
                elif path == "/api/profile": result = store.save_profile(body["profile"], body.get("revision"))
                elif path == "/api/geometry":
                    r = store.profile_record(body["profile_id"]); p = r["payload"]
                    if r["id"] != body["revision"]: raise ValueError("Паспорт изменился: обновите страницу")
                    geo = json.loads(body["geojson"])
                    if geo.get("type") != "FeatureCollection" or not geo.get("features"): raise ValueError("Нужен GeoJSON FeatureCollection")
                    zones = {z["id"]: z for z in p["zones"]}
                    for feature in geo["features"]:
                        zone = feature.get("properties", {}).get("zone_id")
                        if zone not in zones: raise ValueError("Свойство zone_id контура должно совпадать с кодом участка")
                        zones[zone]["geometry"] = feature["geometry"]
                    result = store.save_profile(p, body["revision"])
                elif path == "/api/import": result = store.import_measurements(body["profile_id"], body["csv"], body.get("filename", "measurements.csv"))
                elif path == "/api/read": result = store.acknowledge_forecast(body["profile_id"], body["forecast_id"])
                elif path == "/api/prototype-feedback":
                    from .prototype_feedback import save_feedback
                    result = save_feedback(store, body["profile_id"], body)
                elif path == "/api/journal": result = store.journal(body["profile_id"], body)
                elif path == "/api/inspection": result = store.inspection(body["profile_id"], body)
                elif path == "/api/qc-review": result = store.review_measurement(body["measurement_id"], body["status"], body["note"])
                elif path == "/api/demo": result = {"profile_id": seed_demo(store)}
                elif path == "/api/fetch":
                    profile_id, zone_id, night = body["profile_id"], body["zone_id"], body.get("night")
                    with mutex:
                        if any(j["status"] == "running" for j in jobs.values()): raise ValueError("Дождитесь текущей загрузки")
                        key = secrets.token_hex(8); jobs[key] = {"id": key, "status": "running", "message": "Начинаем получение GFS", "profile_id": profile_id}
                    def run():
                        def progress(message):
                            with mutex: jobs[key]["message"] = message
                        try:
                            with capture_lock: record = capture_forecast(store, profile_id, zone_id, night, progress=progress)
                            with mutex: jobs[key].update(status="completed", message="Прогноз сохранён", forecast_id=record["id"])
                        except Exception as exc:
                            with mutex: jobs[key].update(status="failed", message=str(exc))
                    Thread(target=run, daemon=True).start(); result = {"job_id": key}
                else: return self.reply(404, {"error": "Not found"})
                self.reply(200, result)
            except (ValueError, KeyError, TypeError, OSError) as exc: self.reply(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.field_store = store
    server.field_capture_lock = capture_lock
    return server


def serve_field(directory, port=8770, daily_shadow=False):
    if not 1024 <= port <= 65535: raise ValueError("Порт 1024–65535")
    with make_server(directory, port) as server:
        server.daily_shadow = daily_shadow
        stop = Event()
        def collect():
            from .field_worker import collect_due
            while not stop.is_set():
                try:
                    with server.field_capture_lock: results = collect_due(server.field_store)
                    for result in results: print(encoded(result), flush=True)
                except Exception as exc: print("Shadow collector: "+str(exc), flush=True)
                stop.wait(300)
        if daily_shadow:
            Thread(target=collect, daemon=True).start()
            print("Daily shadow collection enabled: 17–19 MSK, field profiles with coordinates only.", flush=True)
        print(f"Field shadow pilot: http://127.0.0.1:{port}/", flush=True)
        print("Локальный журнал сохраняется в "+str(Path(directory).resolve())+"; Ctrl+C завершает сервер.", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: stop.set()
