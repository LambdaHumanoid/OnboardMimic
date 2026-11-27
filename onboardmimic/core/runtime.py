"""Onboard HTTP/feedback transport. No controller starts until prepare_pose.
Physical mode requires explicit --physical; default execution refuses to run.
Physical bridge setup remains an explicit commissioning step, not a GET effect.
"""
import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .process_launcher import ProcessLauncher
from .gateway import make_server
from .lifecycle import Lifecycle
from .registry import select, select_input, DEFAULT_ROBOT, DEFAULT_BACKEND
from .paths import ROOT, state_dir
from .runtime_timing import wait_event, join_thread
from .admission import RunningAdmissionObserver

def runtime_event(event, **fields):
    # Only lifecycle/timing diagnostics: never tokens, requests or joint data.
    logging.getLogger('runtime_audit').info(json.dumps(
        dict(event=event, pid=os.getpid(), monotonic=time.monotonic(), **fields),
        ensure_ascii=False))


def configure_runtime_log(root):
    folder=root/'logs';folder.mkdir(exist_ok=True)
    logger=logging.getLogger('runtime_audit');logger.setLevel(logging.INFO);logger.propagate=False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler);handler.close()
    for handler in (RotatingFileHandler(folder/'runtime-events.log',maxBytes=1048576,backupCount=2),
                    logging.StreamHandler()):
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)


def sample_runtime_diagnostics(diagnostics, stop):
    # Inventory checks can take longer than the state freshness budget. Keep
    # scheduler sampling independent so evidence spans that entire interval.
    while not wait_event(stop, .1):
        diagnostics.sample_scheduler()


def monitor_runtime(gateway, transport, server):
    observer = RunningAdmissionObserver(gateway.robot.guard, lambda event:
        logging.getLogger('runtime_audit').warning(json.dumps(event, ensure_ascii=False)))
    last_conflict_check = 0.
    last_mono, last_wall = time.monotonic(), time.time()
    while not wait_event(transport.stop, .1):
        mono, wall = time.monotonic(), time.time()
        clock_step = (wall-last_wall)-(mono-last_mono)
        if abs(clock_step) > .25:
            runtime_event('wall_clock_step',delta_seconds=clock_step,phase=gateway.phase)
        last_mono, last_wall = mono, wall
        age = time.monotonic()-transport.last_tick
        alive = transport.thread.is_alive()
        if not alive or age > .5:
            transport.shutdown_reason = transport.shutdown_reason or 'transport_watchdog'
            phase = gateway.phase
            with gateway.lock: gateway.halt('机载通信线程失去心跳')
            transport.stop.set()
            runtime_event('transport_watchdog',thread_alive=alive,tick_age_seconds=age,
                          stage=transport.stage,error=transport.error,phase=phase)
            server.shutdown()
            break
        if time.monotonic()-last_conflict_check >= 1.:
            last_conflict_check = time.monotonic()
            with gateway.lock:
                owned_pid = gateway.process.pid if gateway.alive() else None
            # Startup still requires strict admission. Inspection failures or
            # newcomers must not evict an already-owned, healthy controller.
            observer.inspect(owned_pid)


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--physical', action='store_true')
    parser.add_argument('--enable-reference-replay', action='store_true', help='Enable SONIC reference-plan v2; requires native telemetry and protected writer')
    parser.add_argument('--allow-wbc-exit-tests', action='store_true',
                        help='Commissioning only: allow owned-WBC SIGTERM/SIGKILL buttons. No landing takeover; robot must be mechanically supported.')
    parser.add_argument('--experimental-protection-socket', type=Path,
                        help='Explicit experimental native protection writer. Requires separately commissioned bridge; may execute a real roll.')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--pose-status', type=Path, help='Optional live PICO receiver status.json')
    parser.add_argument('--default-speed', type=float, default=.5, help='Default Planner target speed in m/s (0..5)')
    parser.add_argument('--robot', default=DEFAULT_ROBOT)
    parser.add_argument('--backend', default=DEFAULT_BACKEND)
    parser.add_argument('--input', default='pico')
    args = parser.parse_args()
    robot, backend = select(args.robot,args.backend)
    if args.pose_status:
        select_input(args.input)
        if args.input not in backend.supported_inputs:
            parser.error('Selected backend does not support this tracking input')
    state = state_dir()
    if not args.physical:
        parser.error('This runtime requires explicit --physical. Use the offline test suite for no-hardware validation.')
    configure_runtime_log(state)
    runtime_event('runtime_start',python=sys.version.split()[0],pico_enabled=bool(args.pose_status))
    if args.token_file.stat().st_mode & 0o077:
        parser.error('Token file must be private (0600)')
    token = args.token_file.read_text().strip()
    if len(token) < 32:
        parser.error('Token must contain at least 32 characters')
    # Held across lifecycle incl. preparation, prevents two gateway owners.
    owner_lock = (state/'onboardmimic.core.runtime.lock').open('a')
    fcntl.flock(owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    audit_path=state/'logs/control-events.log'
    audit_path.parent.mkdir(exist_ok=True)
    audit=logging.getLogger('control_audit');audit.setLevel(logging.INFO);audit.propagate=False
    handler=RotatingFileHandler(audit_path,maxBytes=1048576,backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));audit.addHandler(handler)
    log = state/'logs/native-runtime.log'
    log.parent.mkdir(exist_ok=True)
    replay_path=None
    replay_directory=None
    if args.enable_reference_replay:
        if backend.id!='sonic' or not args.experimental_protection_socket or os.environ.get('OM_SONIC_NATIVE_RECEIVER')!='1' or not os.environ.get('OM_SONIC_NATIVE_TELEMETRY_PORT'):
            parser.error('Reference replay requires SONIC native telemetry and experimental protection')
        import tempfile
        replay_directory=tempfile.TemporaryDirectory(prefix='om-replay-')
        replay_path=Path(replay_directory.name)/'control.sock'
    def spawn():
        # No shell, tmux, pattern killing, or implicit bridge/native-mode handoff.
        spec = backend.checked_launch_spec(backend.launch_spec(robot))
        if replay_path:
            replay_path.unlink(missing_ok=True)
            spec.env['OM_SONIC_REPLAY_SOCKET']=str(replay_path)
        with log.open('w') as output:
            return subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('owned_exec.py')),str(os.getpid())] + spec.argv,
                cwd=spec.cwd, env=spec.env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT)
    # PR_SET_PDEATHSIG follows the creating Linux thread, not just its process.
    launch = ProcessLauncher(spawn)
    def read_log():
        # Bounded tail avoids reading growing logs on every 50Hz control tick.
        if not log.exists(): return ''
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-262144))
            return f.read().decode(errors='replace')
    gateway = Lifecycle(backend.baseline(robot), launch, read_log, robot.guard,
                        physical=True, robot=robot, backend=backend)
    from .runtime_diagnostics import RuntimeDiagnostics
    gateway.runtime_diagnostics = RuntimeDiagnostics()
    gateway.allow_wbc_exit_tests = args.allow_wbc_exit_tests
    if args.experimental_protection_socket:
        from .protection.client import ProtectionClient
        gateway.protection_executor = ProtectionClient(args.experimental_protection_socket)
    if replay_path:
        gateway.reference_replay=backend.reference_replay(gateway,replay_path)
    from .commands import PlannerLease
    gateway.lease = PlannerLease(args.default_speed, max_speed=backend.max_speed)
    # Optional adapters are never implicitly enabled by installing this package.
    if args.pose_status:
        os.environ['OPENBLAS_NUM_THREADS'] = '1'
        gateway.pose_reader = backend.pose_reader(args.input,args.pose_status)
        gateway.pose_reader.runtime_diagnostics = gateway.runtime_diagnostics
        gateway.pose_reader.mapping_enabled = lambda: gateway.input_mode != 'replay'
        gateway.pose_reader.thread.start()
    transport = backend.transport(gateway,robot)
    gateway.transport = transport  # Read-only timing diagnostics; no query calls into transport.
    transport.thread.start()
    if not wait_event(transport.ready, 5) or transport.error:
        raise RuntimeError('Transport failed: '+str(transport.error))
    server = make_server(gateway, token, 18921)
    exit_reason = ['server_returned']
    def shutdown(signum, _frame):
        exit_reason[0] = 'signal_'+signal.Signals(signum).name
        phase = gateway.phase
        with gateway.lock: gateway.halt('reference_replay:runtime_shutdown' if getattr(gateway,'reference_replay',None) and gateway.reference_replay.busy else '')
        transport.stop.set()
        runtime_event('stop_signal',signal=signal.Signals(signum).name,phase=phase)
        if gateway.pose_reader:
            gateway.pose_reader.stop.set()
            join_thread(gateway.pose_reader.thread, 1)
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=sample_runtime_diagnostics,
        args=(gateway.runtime_diagnostics,transport.stop),daemon=True).start()
    threading.Thread(target=monitor_runtime,args=(gateway,transport,server),daemon=True).start()
    runtime_event('runtime_ready',phase=gateway.phase)
    try:
        server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        pass
    except BaseException:
        exit_reason[0] = 'exception'
        logging.getLogger('runtime_audit').exception('Runtime failed')
        raise
    finally:
        gateway.runtime_diagnostics.close()
        if gateway.pose_reader:
            gateway.pose_reader.stop.set()
            join_thread(gateway.pose_reader.thread, 1)
        transport.stop.set()
        with gateway.lock:
            if getattr(gateway,'reference_replay',None) and gateway.reference_replay.busy:
                gateway.halt('reference_replay:runtime_shutdown')
            elif gateway.protection_executor and not exit_reason[0].startswith('signal_'):
                gateway.halt('机载运行端异常退出')
            else:gateway.halt()
        if getattr(gateway,'reference_replay',None):gateway.reference_replay.close()
        launch.close()
        if gateway.protection_executor:gateway.protection_executor.close()
        joined=join_thread(transport.thread, 4)
        server.server_close()
        owner_lock.close()
        reason=exit_reason[0] if exit_reason[0]!='server_returned' else transport.shutdown_reason or 'server_returned'
        runtime_event('runtime_exit',reason=reason,transport_joined=joined,
                      transport_error=transport.error)


if __name__ == '__main__':
    run()
