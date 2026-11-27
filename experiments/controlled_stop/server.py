"""Operator-only test console. No automatic lifecycle or motion on startup."""
import argparse,json,secrets,time,ipaddress
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def serve(status_file,port,experiment=None):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def answer(self,value,code=200):
            body=json.dumps(value,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def host_ok(self):
            try:ipaddress.ip_address(self.headers.get('Host','').rsplit(':',1)[0]);return True
            except ValueError:return self.headers.get('Host')=='localhost:'+str(port)
        def do_GET(self):
            if not self.host_ok():self.send_error(403);return
            if self.path=='/':
                body=(ROOT/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
            elif self.path=='/api/session':self.answer(dict(token=token))
            elif self.path=='/api/status':
                if experiment is not None:
                    status=experiment.status()
                    try:status['sonic']=experiment.lifecycle.status() if experiment.lifecycle else {}
                    except Exception as e:status['sonic']={};status['lifecycle_error']=str(e)
                    s=status['sonic'];status['can_lower']=bool(status.get('can_lower') and s.get('state')=='active' and s.get('stationary') and s.get('input_mode')=='planner')
                else:
                    try:
                        status=json.loads(status_file.read_text())
                        if not 0<=time.time()-status['updated_wall']<1:status.update(fresh=False,phase='offline')
                    except (OSError,ValueError,KeyError):status=dict(fresh=False,phase='offline',actuation_enabled=False)
                if (ROOT/'LOCKED').exists():
                    status=dict(phase='locked',fresh=False,actuation_enabled=False,read_only=True,
                        error=(ROOT/'LOCKED').read_text().strip(),deployment=str(ROOT),
                        compensation_warning_nm=40,compensation_warning_action='diagnostic_only',
                        updated_wall=time.time())
                self.answer(status)
            else:self.send_error(404)
        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin')!='http://'+self.headers.get('Host','') or self.headers.get('X-Experiment-Token')!=token:
                self.answer({'error':'Invalid origin or experiment token'},403);return
            if self.path!='/api/action' or experiment is None:self.answer({'error':'Read only'},405);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024:raise ValueError('Invalid request size')
                x=json.loads(self.rfile.read(size));client=x.get('client')
                if not isinstance(client,str) or not 8<=len(client)<=80:raise ValueError('Invalid client')
                result=experiment.command(x['action'],client,confirmed=x.get('confirmed') is True)
                self.answer({'ok':True,'result':result})
            except Exception as e:self.answer({'error':str(e)},409)
        def log_message(self,*args):pass
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--status',type=Path,default=ROOT/'status.json');p.add_argument('--port',type=int,default=8789);p.add_argument('--physical',action='store_true');p.add_argument('--runtime-root',type=Path);p.add_argument('--shallow-plan',type=Path);p.add_argument('--coordinated-20',action='store_true');p.add_argument('--direct-six',action='store_true');a=p.parse_args()
    experiment=None
    if a.physical:
        if (ROOT/'LOCKED').exists():p.error('Experiment locked after a hardware incident; physical startup disabled')
        if not a.runtime_root:p.error('--runtime-root is required')
        from worker import Experiment
        experiment=Experiment(a.runtime_root,physical=True,shallow_plan=a.shallow_plan,coordinated20=a.coordinated_20,direct_six=a.direct_six)
    serve(a.status,a.port,experiment)
