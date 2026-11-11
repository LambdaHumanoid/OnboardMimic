"""Isolated timing probe: random loopback sockets, no ROS or native controller."""
import argparse
import ctypes
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
from tests.fixtures import NativeLifecycle
from onboardmimic.core.runtime import monitor_runtime
from onboardmimic.backends.sonic.transport import Transport
from onboardmimic.core.runtime_timing import wait_event, join_thread


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--legacy-file',type=Path)
    parser.add_argument('--event-baseline',action='store_true')
    args=parser.parse_args()
    libc=ctypes.CDLL(None)
    def mark():
        if libc.prctl(15,b'sonic-clocktest',0,0,0):raise RuntimeError('prctl failed')
    if args.event_baseline:
        mark();begin=time.monotonic();threading.Event().wait(.02)
        print(json.dumps({'event_wait_seconds':time.monotonic()-begin}));return
    cls=Transport
    if args.legacy_file:
        spec=importlib.util.spec_from_file_location('legacy_runtime_probe',args.legacy_file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cls=module.Transport
    import zmq
    ctx=zmq.Context();source=ctx.socket(zmq.PUB)
    source_port=source.bind_to_random_port('tcp://127.0.0.1')
    reserve=ctx.socket(zmq.PUB);planner_port=reserve.bind_to_random_port('tcp://127.0.0.1');reserve.close(0)
    def forbidden_launch():raise AssertionError('No controller may launch in this probe')
    gateway=NativeLifecycle([0.]*29,forbidden_launch,lambda:'',lambda:None)
    transport=cls(gateway,source_port,planner_port)
    if not hasattr(transport,'stage'):transport.stage='legacy_cadence'
    if not hasattr(transport,'shutdown_reason'):transport.shutdown_reason=None
    def worker():mark();transport.run()
    transport.thread=threading.Thread(target=worker,daemon=True)
    shutdown=threading.Event();server=SimpleNamespace(shutdown=shutdown.set)
    monitor=threading.Thread(target=monitor_runtime,args=(gateway,transport,server),daemon=True)
    try:
        transport.thread.start()
        assert wait_event(transport.ready,5) and not transport.error
        monitor.start()
        wait_event(shutdown,1.2)
        result={'watchdog_shutdown':shutdown.is_set(),'tick_age_seconds':time.monotonic()-transport.last_tick,
                'controller_created':gateway.process is not None,'transport_error':transport.error}
        print(json.dumps(result),flush=True)
    finally:
        transport.stop.set();join_thread(transport.thread,3)
        if monitor.ident:join_thread(monitor,2)
        source.close(0);ctx.term()


if __name__=='__main__':main()
