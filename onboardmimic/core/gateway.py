"""Onboard HTTP contract harness. MOCK ONLY: no process, DDS or motor output.
Uses the real command lease; emits diagnostics, not LowCmd.
"""
import argparse
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

from onboardmimic.core.commands import PlannerLease


class Gateway:
    def __init__(self, baseline, clock=time.monotonic):
        self.clock = clock
        self.baseline = baseline
        self.lock = threading.RLock()
        self.lease = PlannerLease()
        self.phase = 'stopped'
        self.until = 0.
        self.epoch = secrets.token_hex(16)
        self.secret = secrets.token_bytes(32)
        self.sequence = {}
        self.last_packet = None
        self.still_since = clock()

    def tick(self):
        with self.lock:
            now = self.clock()
            if self.phase in ('preparing', 'starting_policy') and now >= self.until:
                self.phase = 'prepared' if self.phase == 'preparing' else 'active'
                self.still_since = now
            if self.phase != 'active':
                self.last_packet = None
                return
            mode, move, facing, speed = self.lease.sample(now)
            if self.lease.move or self.lease.turn:
                self.still_since = now
            self.last_packet = {'mode': mode, 'movement_direction': move,
                                'facing_direction': facing, 'speed': speed}

    def ticket(self):
        stamp = str(self.clock())
        signature = hmac.new(self.secret, (self.epoch + ':' + stamp).encode(), hashlib.sha256).hexdigest()
        return stamp + ':' + signature

    def validate(self, request):
        if request.get('epoch') != self.epoch:
            raise ValueError('控制会话已失效，请刷新状态')
        try:
            ticket = request.get('ticket')
            if not isinstance(ticket, str) or len(ticket) > 128:
                raise ValueError('无效请求凭据')
            stamp, signature = ticket.split(':')
            expected = hmac.new(self.secret, (self.epoch + ':' + stamp).encode(), hashlib.sha256).hexdigest()
            fresh = 0 <= self.clock() - float(stamp) <= .25
        except (KeyError, ValueError, TypeError):
            raise ValueError('无效请求凭据')
        if not fresh or not hmac.compare_digest(signature, expected):
            raise ValueError('请求已超时，未续租')
        client, seq = request.get('client'), request.get('seq')
        if not isinstance(client, str) or not 1 <= len(client) <= 80 or type(seq) is not int or seq < 0:
            raise ValueError('无效控制会话')
        if seq <= self.sequence.get(client, -1):
            raise ValueError('过期控制请求')
        if len(self.sequence) >= 512 and client not in self.sequence:
            raise ValueError('会话数量超过上限')
        self.sequence[client] = seq

    def status(self):
        with self.lock:
            self.tick()
            return {'state': self.phase, 'mode': 'onboard_mock', 'robot': 'OnboardMimic mock',
                    'motion_enabled': False, 'control_transferred': False, 'pids': [],
                    'pose_ready': False, 'prepare_available': False,
                    'workflow_error': '仅接口模拟，未连接电机或真机控制进程',
                    'epoch': self.epoch, 'ticket': self.ticket(), 'ticket_ttl_ms': 250,
                    'lease_remaining_ms': max(0, round((self.lease.deadline - self.clock()) * 1000)),
                    'diagnostic_packet': self.last_packet}

    def request(self, route, x):
        with self.lock:
            if route not in ('/api/controller', '/api/control'):
                raise ValueError('未知接口')
            for flag in ('stop',):
                if flag in x and type(x[flag]) is not bool:
                    raise ValueError('布尔控制参数无效')
            self.tick()
            if route == '/api/controller' and x.get('action') == 'estop':
                self.phase = 'stopped'
                self.lease.stop()
                self.last_packet = None
                self.epoch = secrets.token_hex(16)
                self.sequence.clear()
                return self.status()
            self.validate(x)
            now = self.clock()
            if route == '/api/controller':
                action = x.get('action')
                if action == 'prepare_pose' and self.phase == 'stopped':
                    self.phase, self.until = 'preparing', now + 3.
                elif action == 'start_policy' and self.phase == 'prepared':
                    self.phase, self.until = 'starting_policy', now + 1.
                else:
                    raise ValueError('当前模拟状态不允许此操作')
            elif route == '/api/control':
                if x.get('stop') is True:
                    self.lease.accept(x, now)
                elif self.phase != 'active':
                    raise ValueError('模拟策略尚未启动')
                else:
                    self.lease.accept(x, now)
            else:
                raise ValueError('未知接口')
            return self.status()


def make_server(gateway, token, port=0):
    if len(token) < 32:
        raise ValueError('HTTP token must contain at least 32 characters')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, code=200):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.respond({'error': 'unauthorized'}, 401)
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path != '/api/controller':
                return self.respond({'error': 'not found'}, 404)
            self.respond(gateway.status())

        def do_POST(self):
            if not self.authorized():
                return
            self.connection.settimeout(1.)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('无效请求长度')
                x = json.loads(self.rfile.read(size))
                if not isinstance(x, dict):
                    raise ValueError('请求必须是对象')
                self.respond(gateway.request(self.path, x))
            except (ValueError, TypeError, TimeoutError, RuntimeError) as e:
                self.respond({'error': str(e)}, 409)

    # Hardwired loopback; remote use requires an authenticated SSH tunnel.
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--baseline', help='Optional reference for interface-only tests')
    parser.add_argument('--port', type=int, default=18921)
    args = parser.parse_args()
    gateway = Gateway(json.loads(Path(args.baseline).read_text()) if args.baseline else [])
    server = make_server(gateway, Path(args.token_file).read_text().strip(), args.port)
    def tick():
        while True:
            gateway.tick()
            time.sleep(.02)
    threading.Thread(target=tick, daemon=True).start()
    print(json.dumps({'port': server.server_port, 'mode': 'mock', 'motor_output': False}), flush=True)
    server.serve_forever()

if __name__ == '__main__':
    run()
