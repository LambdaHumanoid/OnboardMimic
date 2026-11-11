"""HTTP adapter for the onboard contract. Not selected by the live web server.
The selected gateway owns process lifecycle; this adapter never launches local controllers.
"""
import json
import secrets
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class OnboardClient:
    def __init__(self, url, token, timeout=1.):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Use the localhost SSH tunnel for the onboard gateway')
        self.url = url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.client = 'onboard-console-' + secrets.token_hex(12)
        self.seq = 0
        self.generation = 0
        self.lock = threading.Lock()

    def call(self, path, data=None):
        payload = None if data is None else json.dumps(data, allow_nan=False).encode()
        request = Request(self.url + path, data=payload,
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as e:
            try:
                message = json.load(e).get('error', str(e))
            except (ValueError, AttributeError):
                message = str(e)
            raise RuntimeError(message) from e

    def status(self):
        return self.call('/api/controller')

    def command(self, action):
        # An emergency stop never waits behind an in-flight movement request.
        if action == 'estop':
            self.generation += 1
            return self.call('/api/controller', {'action': action})
        with self.lock:
            generation = self.generation
            s = self.status()
            if generation != self.generation:
                raise RuntimeError('请求已被急停取消')
            self.seq += 1
            return self.call('/api/controller', {'action': action, 'client': self.client,
                                           'seq': self.seq, 'epoch': s['epoch'], 'ticket': s['ticket']})

    def control(self, command):
        with self.lock:
            generation = self.generation
            s = self.status()
            if generation != self.generation:
                raise RuntimeError('请求已被急停取消')
            # Preserve browser client and sequence; never retry a motion POST.
            x = dict(command, epoch=s['epoch'], ticket=s['ticket'])
            return self.call('/api/control', x)
