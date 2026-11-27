"""Standalone Lab UI; recording lives in a separate collector process."""
from .release_assets import select as select_assets, pin as pin_assets, status as asset_status
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import ipaddress
import hmac
import json
from pathlib import Path
import secrets
import re
import signal
from urllib.parse import urlsplit, parse_qs

from .collector_service import CollectorClient, default_socket
from .replay_console import ReplayConsole


def embed_origin(value):
    """Accept exact HTTP(S) origins only, never CSP expressions or URL paths."""
    if not isinstance(value,str) or not re.fullmatch(r'https?://(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?',value):
        raise ValueError('embed origin must be an exact http(s)://host[:port] origin')
    u=urlsplit(value)
    if u.port is not None and not 1<=u.port<=65535: raise ValueError('invalid embed origin port')
    try: ipaddress.ip_address(u.hostname)
    except ValueError:
        if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in u.hostname.split('.')):
            raise ValueError('invalid embed origin hostname')
    return u.scheme+'://'+u.netloc.lower()


def make_server(client, host='127.0.0.1', port=0, runtime_console_url=None, embed_origins=(), replay=None):
    replay = replay or ReplayConsole()
    ancestors=' '.join(dict.fromkeys(embed_origin(value) for value in embed_origins)) or "'none'"
    assets=Path(__file__).parent/'static'
    link=''
    if runtime_console_url:
        u=urlsplit(runtime_console_url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise ValueError('invalid runtime console URL')
        link='<a target="_blank" rel="noopener" href="'+html.escape(runtime_console_url,quote=True)+'">打开 OnboardMimic 遥操控制台 ↗</a>'
    def pages(selected, version):
        page=('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>OnboardMimic Lab</title><link rel="stylesheet" href="/collection.css"><link rel="stylesheet" href="/lab.css">
    <body><main><h1>OnboardMimic Lab</h1><p data-zh="数采 · 数据管理" data-en="Collection · Dataset management">数采 · 数据管理</p>'''+link+'''
    <p id="runtime-state" role="status">正在读取遥操状态…</p>
    <nav class="lab-tabs" role="tablist" aria-label="Lab"><button id="tab-collection" role="tab" aria-selected="true" aria-controls="collection-view" data-zh="数采" data-en="Collect">数采</button><button id="tab-replay" role="tab" aria-selected="false" aria-controls="replay-view" tabindex="-1" data-zh="回放" data-en="Replay">回放</button><span id="tab-recording" hidden data-zh="数采进行中" data-en="Recording in progress">数采进行中</span></nav>
    <div id="collection-view" role="tabpanel" aria-labelledby="tab-collection"><section id="collection-panel">'''+(selected/'collection-panel.html').read_text()+'''</section>
    <p data-zh="录制由独立服务管理，关闭或重启本页面不会结束录制。" data-en="Recording runs independently and continues when this page is closed or reloaded.">录制由独立服务管理，关闭或重启本页面不会结束录制。</p></div>
    <section id="replay-view" role="tabpanel" aria-labelledby="tab-replay" hidden>'''+(selected/'replay-panel.html').read_text()+'''</section></main>
    <script src="/collection.js"></script><script src="/lab.js"></script><script src="/replay.js"></script><script src="/native-replay.js"></script></body></html>''').encode()
        embedded=page.replace(b'<body>',('<body class="embedded" data-embed-origins="'+html.escape(' '.join(embed_origin(v) for v in embed_origins),quote=True)+'">').encode())
        embedded=embedded.replace(('<h1>OnboardMimic Lab</h1><p data-zh="数采 · 数据管理" data-en="Collection · Dataset management">数采 · 数据管理</p>'+link).encode(),b'')
        return pin_assets(page.decode(),version).encode(),pin_assets(embedded.decode(),version).encode()
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,raw,status=200,mime='application/json',embeddable=False):
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; frame-ancestors "+(ancestors if embeddable else "'none'")+"; base-uri 'none'")
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers();self.wfile.write(raw)
        def send_file(self,path):
            size=path.stat().st_size;start,end=0,size-1;status=200
            value=self.headers.get('Range')
            if value:
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',value)
                if not match:return self.reply({'error':'unsupported range'},416)
                start=int(match[1]);end=min(int(match[2]),end) if match[2] else end
                if start>end:return self.reply({'error':'invalid range'},416)
                status=206
            self.send_response(status)
            self.send_header('Content-Type','video/mp4' if path.suffix=='.mp4' else 'application/json')
            self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            if status==206:self.send_header('Content-Range','bytes %s-%s/%s'%(start,end,size))
            self.end_headers()
            with path.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(65536,remaining))
                    if not chunk:break
                    try:self.wfile.write(chunk)
                    except (BrokenPipeError,ConnectionResetError):return
                    remaining-=len(chunk)
        def reply(self,value,status=200):self.send(json.dumps(value,allow_nan=False).encode(),status)
        def valid_host(self):
            try:
                host=urlsplit('http://'+self.headers.get('Host','')).hostname
                if host=='localhost': return True
                ipaddress.ip_address(host); return True
            except (ValueError,TypeError): return False
        def do_GET(self):
            if not self.valid_host():return self.reply({'error':'use localhost or the server IP address'},403)
            try:
                if self.path=='/':
                    selected,version=select_assets(assets,'lab/src/onboardmimic_lab/static',self.path)
                    return self.send(pages(selected,version)[0],mime='text/html; charset=utf-8')
                if self.path=='/embed':
                    selected,version=select_assets(assets,'lab/src/onboardmimic_lab/static',self.path)
                    return self.send(pages(selected,version)[1],mime='text/html; charset=utf-8',embeddable=True)
                if self.path=='/api/deployment-assets':return self.reply(asset_status())
                if self.path=='/api/session':return self.reply({'token':token})
                if self.path=='/api/collection':return self.reply(client.status())
                if self.path=='/api/runtime':return self.reply(client.runtime_status())
                if self.path=='/api/replay':return self.reply(replay.status())
                if self.path=='/api/replay/real':return self.reply(replay.real.poll() if replay.real else dict(configured=False))
                if urlsplit(self.path).path=='/api/replay/preview':
                    query=parse_qs(urlsplit(self.path).query)
                    return self.send_file(replay.preview(query.get('episode',[''])[0]))
                if urlsplit(self.path).path=='/api/replay/file':
                    query=parse_qs(urlsplit(self.path).query)
                    path=replay.file(query.get('id',[''])[0],query.get('name',[''])[0])
                    return self.send_file(path)
                names={'/collection.js':'text/javascript','/lab.js':'text/javascript','/collection.css':'text/css','/lab.css':'text/css','/replay.js':'text/javascript','/native-replay.js':'text/javascript'}
                route=urlsplit(self.path).path
                if route in names:
                    selected,_=select_assets(assets,'lab/src/onboardmimic_lab/static',self.path)
                    return self.send((selected/route[1:]).read_bytes(),mime=names[route])
                self.reply({'error':'not found'},404)
            except (OSError,ValueError):self.reply({'error':'数采服务不可用，请检查 collector 服务'},503)
        def do_POST(self):
            if not self.valid_host():return self.reply({'error':'invalid host'},403)
            if self.path not in ('/api/collection','/api/replay'):return self.reply({'error':'not found'},404)
            # No CORS. Require exact same-origin and a per-web-process session token.
            if (self.headers.get('Origin')!='http://'+self.headers.get('Host','')
                    or not hmac.compare_digest(self.headers.get('X-Control-Token',''),token)):
                return self.reply({'error':'unauthorized'},403)
            try:
                self.connection.settimeout(3)
                size=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Transfer-Encoding') or not 0<size<=4096:raise ValueError('invalid request size')
                value=json.loads(self.rfile.read(size))
                if not isinstance(value,dict):raise ValueError('expected an object')
                if self.path=='/api/replay' and value.get('action')=='real_start':
                    if client.status().get('state') in ('arming','recording','stopping','saving'):
                        raise ValueError('Finish collection before physical replay')
                self.reply(client.command(value) if self.path=='/api/collection' else replay.command(value))
            except (ValueError,KeyError,TypeError) as exc:self.reply({'error':str(exc)},400)
            except OSError:self.reply({'error':'服务连接或文件读取失败；请查询当前状态，不要重复提交 / Service or file unavailable'},503)
    return ThreadingHTTPServer((host,port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collector-socket',type=Path,default=default_socket())
    parser.add_argument('--runtime-console-url',help='link to the separately managed motion console')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8773)
    parser.add_argument('--embed-origin',action='append',default=[],type=embed_origin,help='allowed parent origin for /embed; repeat for multiple origins; embedding disabled by default')
    parser.add_argument('--replay-root',type=Path,help='dataset root containing saved episodes')
    parser.add_argument('--simulation-config',type=Path,help='local simulation model/config JSON; omit to disable execution')
    parser.add_argument('--real-replay-config',type=Path,help='explicit OM gateway/token config for reference-plan v2 physical replay')
    parser.add_argument('--replay-output',type=Path,help='simulation job directory')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();client=CollectorClient(args.collector_socket)
    if args.check:
        print(json.dumps(client.status(),indent=2));return
    from .native_replay_client import from_config
    real=from_config(args.real_replay_config) if args.real_replay_config else None
    replay=ReplayConsole(args.replay_root,args.simulation_config,args.replay_output,real)
    server=make_server(client,args.host,args.port,args.runtime_console_url,args.embed_origin,replay)
    def stop(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop)
    try:
        print('Lab console: http://%s:%s'%(args.host,server.server_port),flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:
        server.server_close();replay.close()  # Deliberately does not stop the collector.


if __name__=='__main__':main()
