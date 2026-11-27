"""Isolated monitor injection. Never stops a process or accesses motor transport."""
from .monitor import ProtectionMonitor, OutputEvidence

CASES = ('process_exit', 'output_timeout', 'state_loss', 'intentional_stop', 'estop')

def run_probe(case):
    if case not in CASES:
        raise ValueError('Unknown monitor injection case')
    now=[10.0]
    monitor=ProtectionMonitor(lambda:now[0])
    monitor.begin('isolated-probe','synthetic-wbc');monitor.activate()
    monitor.output(OutputEvidence('isolated-probe',1,now[0]))
    if case=='output_timeout':
        now[0]+=.11;monitor.observe(True,True)
    elif case=='intentional_stop':
        monitor.stop();monitor.observe(False,True)
    elif case=='estop':
        monitor.stop('synthetic_estop',estop=True);monitor.observe(False,True)
    else:monitor.observe(False,case!='state_loss')
    return dict(test_only=True,physical_effect=False,case=case,
                scope='isolated_monitor_not_live_wbc',result=monitor.snapshot())
