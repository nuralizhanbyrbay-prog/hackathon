"""Offline browser dashboard backed by the real audit environment. Loopback only."""
import argparse
import copy
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from .client import AuditEnv
from .generator import TYPES
from .policies import run_policy

class Dashboard:
    def __init__(self):
        self.token=secrets.token_urlsafe(32);self.env=AuditEnv();self.lock=threading.Lock()
        self.initial=None;self.events=[];self.done=False;self.report=None
        self.reset_args=None;self.started_at=None;self.comparisons=[];self.comparison_events=[]
    def close(self):self.env.close()
    def snapshot(self):
        return copy.deepcopy({'initial':self.initial,'events':self.events,'done':self.done,'report':self.report,
                'comparisons':self.comparisons,
                'remaining_seconds':max(0,60-(time.monotonic()-self.started_at)) if self.started_at and not self.done else 0,
                'remaining_steps':self.events[-1]['observation']['remaining_steps'] if self.events else 100}
        )
    def compare(self):
        if self.reset_args is None:raise ValueError('Сначала создайте компанию')
        if not self.done:raise ValueError('Завершите текущий аудит перед сравнением агентов')
        comparisons=[];events=[]
        with AuditEnv() as env:
            for policy in ('reconcile','random','flag_all'):
                initial,_=env.reset(**self.reset_args,mode='train');step_number=0
                def advance(action):
                    nonlocal step_number
                    obs,reward,term,trunc,info=env.step(action);step_number+=1
                    events.append({'episode_id':initial['manifest']['episode_id'],'policy':policy,'step':step_number,
                        'action':action,'observation':obs,'reward':reward,'terminated':term,'truncated':trunc})
                    return obs,reward,term,trunc,info
                run_policy(policy,initial,advance)
                report=env.report()
                comparisons.append({'policy':policy,'episode_id':initial['manifest']['episode_id'],
                    **{k:report[k] for k in ('TP','FP','FN','P','R','F1','reward','steps','seconds','truncated')}})
        self.comparisons=comparisons;self.comparison_events=events
    def step(self,action):
        if self.initial is None:raise ValueError('Сначала создайте компанию')
        if self.done:raise ValueError('Аудит завершён. Создайте новый эпизод')
        obs,reward,term,trunc,info=self.env.step(action)
        self.events.append({'episode_id':self.initial['manifest']['episode_id'],'step':len(self.events)+1,
            'action':action,'observation':obs,'reward':reward,'terminated':term,'truncated':trunc})
        self.done=term or trunc
        if self.done:self.report=self.env.report()
    def dispatch(self,path,data):
        with self.lock:
            if path=='/api/reset':
                seed=data.get('seed',2026);n=data.get('invoices',60);scenario=data.get('scenario','fx_cutoff')
                if type(seed) is not int or not 0<=seed<=2**31-1:raise ValueError('Seed: целое от 0 до 2147483647')
                if scenario not in ('clean','mixed','fx_cutoff','allocation_cutoff'):raise ValueError('Некорректный сценарий')
                config={'invoices':n,'errors':dict.fromkeys(TYPES,0 if scenario=='clean' else 1),
                        'interaction':scenario if scenario.endswith('cutoff') else None}
                self.initial,_=self.env.reset(seed,config,mode='train');self.events=[];self.done=False;self.report=None
                self.reset_args={'seed':seed,'config':config};self.started_at=time.monotonic()
                self.comparisons=[];self.comparison_events=[]
            elif path=='/api/step':self.step(data.get('action'))
            elif path=='/api/run':
                policy=data.get('policy','reconcile')
                if policy not in ('reconcile','random','flag_all'):raise ValueError('Неизвестный агент')
                if self.initial is None or self.done:raise ValueError('Создайте новый эпизод перед запуском')
                def advance(a):
                    if self.done:
                        ev=self.events[-1]
                        return ev['observation'],0.,ev['terminated'],ev['truncated'],{}
                    self.step(a);ev=self.events[-1]
                    return ev['observation'],ev['reward'],ev['terminated'],ev['truncated'],{}
                run_policy(policy,self.initial,advance)
            elif path=='/api/compare':self.compare()
            elif path=='/api/export-comparison':
                if not self.comparison_events:raise ValueError('Сначала сравните агентов')
                return {'filename':self.initial['manifest']['episode_id']+'-comparison.jsonl',
                        'content':''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in self.comparison_events)}
            elif path=='/api/state':pass
            else:raise ValueError('Неизвестный запрос')
            return self.snapshot()

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address):
        super().__init__(address,Handler);self.dashboard=Dashboard()
    def server_close(self):
        self.dashboard.close();super().server_close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,status,body,content_type='application/json; charset=utf-8'):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
        self.end_headers();self.wfile.write(body)
    def allowed_host(self):
        port=self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')
    def do_GET(self):
        if not self.allowed_host():return self.respond(403,{'error':'Неверный адрес'})
        if self.path!='/':return self.respond(404,{'error':'Не найдено'})
        page=Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8')
        page=page.replace('__API_TOKEN__',self.server.dashboard.token)
        self.respond(200,page.encode('utf-8'),'text/html; charset=utf-8')
    def do_POST(self):
        d=self.server.dashboard
        if not self.allowed_host() or not secrets.compare_digest(self.headers.get('X-AuditLab-Token',''),d.token):
            return self.respond(403,{'error':'Нет доступа. Откройте страницу приложения заново'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=200000:return self.respond(413,{'error':'Слишком большой запрос'})
            self.connection.settimeout(5)
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('Ожидается JSON-объект')
            result=d.dispatch(self.path,data)
            self.respond(200,result)
        except (ValueError,TypeError,KeyError) as exc:self.respond(400,{'error':str(exc)[:180]})
        except Exception:self.respond(500,{'error':'Сбой среды. Перезапустите приложение'})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8765)
    p.add_argument('--open',action='store_true',help='Open the local dashboard in the default browser')
    args=p.parse_args()
    with Server(('127.0.0.1',args.port)) as server:
        print(f'AuditLab: http://127.0.0.1:{server.server_port}',flush=True)
        print('Откройте адрес в браузере. Остановка: Ctrl+C. Данные полностью синтетические.',flush=True)
        if args.open:
            import webbrowser
            threading.Timer(.5,lambda:webbrowser.open(f'http://127.0.0.1:{server.server_port}')).start()
        try:server.serve_forever()
        except KeyboardInterrupt:pass

if __name__=='__main__':main()
