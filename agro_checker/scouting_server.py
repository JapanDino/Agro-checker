"""Standalone loopback product server. Archive reads never call external services."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlparse

from . import farm_scouting as scout
from . import farm_workspace as workspace
from .field_store import FieldStore, encoded
from .pipeline import ROOT


def make_server(directory, port=8771):
    store=FieldStore(directory)  # No legacy profile, forecasts or observations are seeded.
    token=secrets.token_urlsafe(32)
    static={'/':('index.html','text/html; charset=utf-8'),
            '/scouting':('index.html','text/html; charset=utf-8'),
            '/scouting/about':('about.html','text/html; charset=utf-8')}
    for f in ['app.js','map.js','plans.js','styles.css','workspace.css','theme.css','interior.css','experience.css','experience-interior.css','drafts.js','hightech.css','design.js','design-controls.css','review-card.js','review-card.css']:
        static['/scouting/'+f]=(f,'text/javascript; charset=utf-8' if f.endswith('.js') else 'text/css; charset=utf-8')
    for f,mime in [('leaflet.js','text/javascript'),('leaflet.css','text/css'),('LEAFLET-LICENSE.txt','text/plain'),
                   ('public-context.json','application/json'),('basemap.jpg','image/jpeg'),('wide-basemap.jpg','image/jpeg'),
                   ('terrain.png','image/png'),('clay.png','image/png'),('ph.png','image/png')]:
        static['/scouting/assets/'+f]=('assets/'+f,mime)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def reply(self,code,body,mime='application/json; charset=utf-8'):
            raw=encoded(body).encode('utf-8') if isinstance(body,(dict,list)) else body
            self.send_response(code)
            for name,value in [('Content-Type',mime),('Content-Length',str(len(raw))),('Cache-Control','no-store'),
                               ('X-Content-Type-Options','nosniff'),('Cross-Origin-Resource-Policy','same-origin'),
                               ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'")]:self.send_header(name,value)
            self.end_headers();self.wfile.write(raw)

        def allowed(self,write=False):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts or self.headers.get('Origin') and self.headers['Origin'] not in {'http://'+h for h in hosts}:
                self.reply(403,{'error':'Только локальный доступ с этого приложения'});return False
            if write and self.headers.get('X-Field-Token')!=token:
                self.reply(403,{'error':'Обновите страницу перед сохранением'});return False
            return True

        def do_GET(self):
            if not self.allowed():return
            try:
                url=urlparse(self.path);path=url.path
                q=parse_qs(url.query,keep_blank_values=True,max_num_fields=6)
                def query(keys):
                    if set(q)!=set(keys) or any(len(v)!=1 or len(v[0])>120 for v in q.values()):raise ValueError('Некорректный запрос')
                    return {k:v[0] for k,v in q.items()}
                if path in static:
                    if q and path in ('/','/scouting','/scouting/about'):
                        if query(['design'])['design'] not in ('atlas','hightech'):raise ValueError('Неизвестный дизайн')
                    else:query([])
                    f,mime=static[path];return self.reply(200,(ROOT/'web/scouting'/f).read_bytes(),mime)
                if path=='/api/scouting/state':
                    query([]);return self.reply(200,{**scout.overview(),**scout.listing(store),'plans':workspace.plans(store),'token':token})
                if path=='/api/scouting/export':
                    query([]);return self.reply(200,scout.listing(store))
                if path=='/api/scouting/workspace':
                    return self.reply(200,workspace.comparison(**query(['period']+(['before','after'] if 'before' in q or 'after' in q else []))))
                if path=='/api/scouting/area':
                    return self.reply(200,workspace.area_detail(**query(['fid','period','before','after'])))
                if path=='/api/scouting/review':
                    from .scouting_review import review
                    return self.reply(200,review(**query(['fid','period','before','after','place_id'])))
                if path=='/api/scouting/detail':
                    return self.reply(200,scout.detail(**query(['fid','period']+(['before','after'] if 'before' in q or 'after' in q else []))))
                if path=='/api/scouting/map':
                    return self.reply(200,scout.map_png(**query(['fid','period','before','after','key','side'])),'image/png')
                if path=='/api/scouting/history':
                    return self.reply(200,scout.place_series(query(['place_id'])['place_id']))
                if path=='/api/scouting/location':
                    v=query(['fid','period','before','after','relative']);relative=json.loads(v.pop('relative'));d=workspace.area_detail(**v)
                    if not d['pair']:raise ValueError('Для места нет пригодной пары')
                    loc=scout.location(v['fid'],relative=relative,crop=d['pair']['crop'])
                    return self.reply(200,{'location':loc,'comparison':scout.window_comparison(loc['id'],v['before'],v['after'])})
                if path=='/api/scouting/plans/export':
                    return self.reply(200,workspace.plan_export(store,query(['id'])['id']))
                match=re.fullmatch(r'/api/scouting/visits/([a-f0-9]{32})/photos/([a-f0-9]{64})',path)
                if match:
                    query([]);data,mime=scout.saved_photo(store,match[1],match[2]);return self.reply(200,data,mime)
                self.reply(404,{'error':'Раздел не найден'})
            except (ValueError,KeyError,TypeError) as error:self.reply(400,{'error':str(error)})
            except OSError:self.reply(503,{'error':'Файлы архива недоступны. Данные не подменены.'})

        def do_POST(self):
            if not self.allowed(True):return
            try:
                url=urlparse(self.path);path=url.path
                actions={'/api/scouting/tasks':scout.save_task,'/api/scouting/visits':scout.save_visit,'/api/scouting/plans':workspace.save_plan}
                if path not in actions:return self.reply(404,{'error':'Действие не найдено'})
                if url.query:raise ValueError('Параметры URL для сохранения не поддерживаются')
                limit=21_000_000 if path.endswith('/visits') else 100_000
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=limit:raise ValueError('Превышен размер запроса')
                body=json.loads(self.rfile.read(length).decode('utf-8'))
                self.reply(200,actions[path](store,body))
            except (ValueError,KeyError,TypeError) as error:self.reply(400,{'error':str(error)})
            except OSError:self.reply(503,{'error':'Не удалось сохранить. Проверьте доступ к папке журнала.'})

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.scouting_store=store
    return server


def main():
    parser=argparse.ArgumentParser(description='AgroPulse — самостоятельная архивная сборка')
    parser.add_argument('--port',type=int,default=8771)
    parser.add_argument('--directory',type=Path,default=ROOT/'data/commission-journal')
    args=parser.parse_args()
    with make_server(args.directory,args.port) as server:
        print(f'AgroPulse: http://127.0.0.1:{server.server_port}/scouting',flush=True)
        print(f'Пустой отдельный журнал: {args.directory.resolve()}',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass


if __name__=='__main__':main()
