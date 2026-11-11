"""Fail closed before creating a physical command publisher; never stop a process."""
from pathlib import Path
import sys
from onboardmimic.robots.unitree_g1.preflight import processes, service, SERVICES

def check():
    running,errors=processes()
    if running or errors:
        raise SystemExit('Existing controller/bridge or incomplete process inspection; stop the old deployment first.')
    for user in (False,True):
        for name in SERVICES:
            s=service(name,user=user)
            if s.get('LoadState') not in ('loaded','not-found') or s.get('ActiveState') not in ('inactive','failed'):
                raise SystemExit('Conflicting or unknown motion service; stop the existing deployment first.')

if __name__=='__main__':check()
