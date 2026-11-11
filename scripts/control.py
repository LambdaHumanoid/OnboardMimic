"""Explicit local API client. Defaults to read-only status; never starts services."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import math
from pathlib import Path
import secrets
import sys
import time
from onboardmimic.core.client import OnboardClient

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',default='status',choices=['status','prepare','start','estop','stop','forward','backward','turn-left','turn-right','left','right','pose','planner'])
    p.add_argument('--url',default='http://127.0.0.1:18921')
    p.add_argument('--token-file',type=Path,default=Path.home()/'.config/onboardmimic/http.token')
    p.add_argument('--duration',type=float,default=1.,help='Movement duration, 0 < seconds <= 10')
    p.add_argument('--speed',type=float,help='Planner target speed in m/s (0..5); defaults to gateway setting')
    a=p.parse_args()
    if a.speed is not None and (not math.isfinite(a.speed) or not 0<=a.speed<=5):p.error('speed must be in [0,5]')
    if not math.isfinite(a.duration) or not 0<a.duration<=10:p.error('duration must be in (0,10]')
    if a.token_file.stat().st_mode & 0o077:p.error('token file must be private (0600)')
    client=OnboardClient(a.url,a.token_file.read_text().strip())
    if a.action=='status':print(json.dumps(client.status(),ensure_ascii=False,indent=2));return
    if a.action in ('prepare','start','estop','pose','planner'):
        print(json.dumps(client.command({'prepare':'prepare_pose','start':'start_policy','estop':'estop','pose':'select_pose','planner':'select_planner'}[a.action]),ensure_ascii=False,indent=2));return
    ident='mobile-cli-'+secrets.token_hex(8);seq=0;owned=False
    def send(**payload):
        nonlocal seq
        seq+=1
        return client.control(dict(client=ident,seq=seq,**payload))
    if a.action=='stop':send(stop=True);return
    command = {'turn':a.action[5:]} if a.action.startswith('turn-') else {'move':a.action}
    if a.speed is not None:command['speed']=a.speed
    try:
        send(**command);owned=True
        end=time.monotonic()+a.duration
        while time.monotonic()<end:
            time.sleep(.1)
            send(**command)
    finally:
        # A rejected competing client must not stop the existing controller.
        if owned:
            try:send(stop=True)
            except Exception:pass  # Lease expiry also stops requests; never replay old movement.
if __name__=='__main__':main()
