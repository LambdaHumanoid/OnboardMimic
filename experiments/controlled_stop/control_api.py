"""Normal SONIC lifecycle plus explicit, stationary experimental handoff."""
import json,socket,time
from pathlib import Path

def ipc(path,action,**kwargs):
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
        s.settimeout(.08);s.connect(str(path));s.sendall(json.dumps(dict(action=action,**kwargs)).encode());result=json.loads(s.recv(16384))
    if not result.get('ok'):raise ValueError(result.get('error','Bridge IPC failed'))
    return result

class NormalLifecycle:
    def __init__(self,root):
        import sys
        sys.path.insert(0,str(root))
        from onboardmimic.core.client import OnboardClient
        from onboardmimic.console.server import Console
        from onboardmimic.robots.unitree_g1.adapter import G1Adapter
        # Same console connection setup and same production runtime, not a new policy.
        token=(Path.home()/'.config/onboardmimic/http.token').read_text().strip()
        backend=OnboardClient('http://127.0.0.1:18921',token)
        self.console=Console(backend,Path(root)/'onboardmimic/console/static')
    def status(self):return self.console.status()
    def action(self,name):return self.console.command({'action':name})
