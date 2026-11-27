"""Independent recording owner, served only on a private Unix socket."""
import argparse
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import stat

from .collection import Collector, load_config
from .runtime_status import RuntimeStatus


def default_socket():
    return Path.home()/'.local/state/onboardmimic-lab/collector.sock'


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=3); self.path = str(path)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try: self.sock.connect(self.path)
        except BaseException:
            self.sock.close(); raise


class CollectorClient:
    def __init__(self, path): self.path = path
    def call(self, route, payload=None):
        connection = UnixConnection(self.path)
        try:
            connection.request('GET' if payload is None else 'POST', route,
                body=None if payload is None else json.dumps(payload, allow_nan=False),
                headers={'Content-Type':'application/json'})
            response = connection.getresponse(); value = json.loads(response.read())
            if response.status != 200: raise ValueError(value.get('error','collector request failed'))
            return value
        finally: connection.close()
    def status(self): return self.call('/api/collection')
    def command(self, payload): return self.call('/api/collection', payload)
    def runtime_status(self): return self.call('/api/runtime')


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


def make_server(collector, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
        raise ValueError('collector socket directory must be owned by this user and private (0700)')
    lock = (path.parent/(path.name+'.lock')).open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode): raise ValueError('socket path contains a non-socket file')
            path.unlink()  # Ownership lock proves no other Lab server is listening.
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def reply(self, value, status=200):
                raw=json.dumps(value,allow_nan=False).encode(); self.send_response(status)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                try:
                    if self.path=='/api/collection': return self.reply(collector.status())
                    if self.path=='/api/runtime': return self.reply(collector.runtime_status())
                    self.reply({'error':'not found'},404)
                except Exception: self.reply({'error':'runtime status unavailable'},503)
            def do_POST(self):
                if self.path!='/api/collection': return self.reply({'error':'not found'},404)
                try:
                    self.connection.settimeout(3)
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Transfer-Encoding') or not 0<size<=4096: raise ValueError('invalid request size')
                    payload=json.loads(self.rfile.read(size))
                    if not isinstance(payload,dict): raise ValueError('expected an object')
                    self.reply(collector.command(payload))
                except (ValueError,KeyError,TypeError) as exc: self.reply({'error':str(exc)},400)
                except Exception: self.reply({'error':'collector operation failed'},503)
        server = UnixServer(str(path), Handler)
        os.chmod(path,0o600)
        original_close=server.server_close
        def close():
            original_close()
            if not lock.closed:
                path.unlink(missing_ok=True);lock.close()
        server.server_close=close
        return server
    except BaseException:
        lock.close();raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--socket',type=Path,default=default_socket())
    args=parser.parse_args()
    config=load_config(args.config)
    collector=Collector(config,RuntimeStatus(config))
    server=None
    def stop(*_): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop)
    try:
        server=make_server(collector,args.socket)
        print('Lab collector: '+str(args.socket),flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt: pass
    finally:
        if server: server.server_close()
        collector.close()


if __name__=='__main__': main()
