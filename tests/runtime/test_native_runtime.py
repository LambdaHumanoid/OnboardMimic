import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from tests.fixtures import NativeLifecycle
from onboardmimic.core.runtime import monitor_runtime
from onboardmimic.backends.sonic.transport import Transport
from onboardmimic.backends.sonic.adapter import controller_args, SonicBackend
from onboardmimic.robots.unitree_g1.guard import physical_guard
from onboardmimic.robots.unitree_g1.adapter import G1Adapter

def controller_command(root):
    spec = SonicBackend().launch_spec(G1Adapter())
    return spec.argv, spec.env
from tests.runtime.test_native_lifecycle import Process


class RuntimeTest(unittest.TestCase):
    def test_watchdog_still_halts_owned_child_and_records_stalled_stage(self):
        proc=Process()
        gateway=NativeLifecycle([0.]*29,lambda:proc,lambda:'',lambda:None)
        gateway.process=proc;gateway.phase='prepared'
        transport=Mock(stop=threading.Event(),last_tick=time.monotonic()-.6,
                       stage='deliberately_stalled',error=None,shutdown_reason=None)
        transport.thread.is_alive.return_value=True
        server=Mock()
        with self.assertLogs('runtime_audit',level='INFO') as events:
            monitor_runtime(gateway,transport,server)
        self.assertEqual(proc.kills,1)
        server.shutdown.assert_called_once_with()
        self.assertTrue(transport.stop.is_set())
        self.assertTrue(any('transport_watchdog' in line and 'deliberately_stalled' in line for line in events.output))

    def test_diagnostic_failure_cannot_prevent_watchdog_stop(self):
        proc=Process()
        gateway=NativeLifecycle([0.]*29,lambda:proc,lambda:'',lambda:None)
        gateway.process=proc;gateway.phase='prepared'
        transport=Mock(stop=threading.Event(),last_tick=time.monotonic()-.6,
                       stage='stalled',error=None,shutdown_reason=None)
        transport.thread.is_alive.return_value=True
        with patch('onboardmimic.core.runtime.runtime_event',side_effect=RuntimeError('diagnostic failure')):
            with self.assertRaisesRegex(RuntimeError,'diagnostic failure'):
                monitor_runtime(gateway,transport,Mock())
        self.assertEqual(proc.kills,1)
        self.assertTrue(transport.stop.is_set())

    def test_conflict_guard_blocks_original_control(self):
        with patch('onboardmimic.robots.unitree_g1.guard.service',return_value={'LoadState':'loaded','ActiveState':'active'}):
            with self.assertRaises(RuntimeError):physical_guard()

    def test_unknown_service_fails_closed(self):
        with patch('onboardmimic.robots.unitree_g1.guard.service',return_value={'error':'timeout'}):
            with self.assertRaises(RuntimeError):physical_guard()

    def test_physical_argv_uses_dedicated_local_bridge_and_no_hands(self):
        args=controller_args(Path('/staged'),'eth1')
        for key,value in [('--hand-type','none'),('--output-type','zmq'),
                          ('--zmq-robot-ip','127.0.0.1'),('--zmq-robot-lowcmd-udp-port','16663')]:
            self.assertEqual(args[args.index(key)+1],value)
        self.assertEqual(args[1],'eth1')

    def test_controller_startup_uses_bridge_dds_and_detected_nic(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'runtime').mkdir()
            template=root/'runtime/command-dds.xml'
            xml='<CycloneDDS xmlns="https://cdds.io/config"><Domain><General><Interfaces><NetworkInterface name="eth0"/></Interfaces><AllowMulticast>spdp</AllowMulticast></General></Domain></CycloneDDS>'
            template.write_text(xml)
            with patch('onboardmimic.core.paths.state_dir',return_value=root/'runtime'), patch('onboardmimic.robots.unitree_g1.bridge.network.detect',return_value='eth1'), patch.dict(os.environ,{'CYCLONEDDS_URI':'file:///wrong.xml','SONIC_LOWCMD_ZMQ_FALLBACK':'1'}):
                args,env=controller_command(root)
            self.assertEqual(args[1],'eth1')
            self.assertEqual(env['UNITREE_DDS_INTERFACE'],'eth1')
            self.assertEqual(env['CYCLONEDDS_URI'],(root/'runtime/controller-dds.xml').as_uri())
            self.assertIn('eth1',(root/'runtime/controller-dds.xml').read_text())
            self.assertEqual(template.read_text(),xml)
            self.assertNotIn('SONIC_LOWCMD_ZMQ_FALLBACK',env)

    def test_controller_startup_refuses_unknown_robot_network(self):
        with patch('onboardmimic.robots.unitree_g1.bridge.network.detect',side_effect=ValueError('No direct robot NIC')), patch('onboardmimic.robots.unitree_g1.bridge.network.configure') as configure:
            with self.assertRaisesRegex(ValueError,'No direct robot NIC'):controller_command(Path('/staged'))
            configure.assert_not_called()

    def test_transport_receives_feedback_and_publishes_only_after_requested_start(self):
        import msgpack,zmq
        ctx=zmq.Context();state=ctx.socket(zmq.PUB);sink=ctx.socket(zmq.SUB)
        state_port=state.bind_to_random_port('tcp://127.0.0.1')
        # Reserve an ephemeral planner port, then hand it to the transport.
        probe=ctx.socket(zmq.PUB);planner_port=probe.bind_to_random_port('tcp://127.0.0.1');probe.close(0)
        sink.setsockopt(zmq.SUBSCRIBE,b'');sink.connect('tcp://127.0.0.1:%d'%planner_port)
        logs=[''];proc=Process()
        g=NativeLifecycle([0.]*29,lambda:proc,lambda:logs[0],lambda:None)
        transport=Transport(g,state_port,planner_port)
        stopped=threading.Event()
        def publish():
            while not stopped.wait(.01):
                state.send_multipart([b'rt/lowstate',msgpack.packb({'motor_state':[{'q':0.,'dq':0.,'motorstate':0}]*29},use_bin_type=True)])
                state.send_multipart([b'rt/secondary_imu',msgpack.packb({'quaternion':[1.,0.,0.,0.]},use_bin_type=True)])
        worker=threading.Thread(target=publish);worker.start();transport.thread.start()
        seq=0
        def command(action):
            nonlocal seq
            seq+=1
            return g.request('/api/controller',dict(client='test',seq=seq,epoch=g.epoch,ticket=g.ticket(),action=action))
        try:
            self.assertTrue(transport.ready.wait(2))
            until=time.monotonic()+2
            while not g.telemetry().get('valid') and time.monotonic()<until:time.sleep(.01)
            self.assertTrue(g.telemetry()['valid'])
            command('prepare_pose');logs[0]='Init Done';g.tick()
            self.assertFalse(sink.poll(100))
            command('start_policy')
            self.assertTrue(sink.poll(1000))
            messages=[sink.recv()]
            while sink.poll(20):
                messages.append(sink.recv())
                if len(messages)>8:break
            self.assertTrue(any(p.startswith(b'command') for p in messages))
            logs[0]+='\nBumpless policy handoff complete'
            g.tick();self.assertEqual(g.phase,'active')
            # The real transport sends streamed poses only after an explicit mode request.
            with g.lock:
                g.pose_reader = Mock()
                g.pose_reader.snapshot.return_value = {'ready': True, 'packet': b'pose-test-frame'}
                g.begin_mode('pose')
            until = time.monotonic()+1
            pose_seen = False
            while time.monotonic()<until:
                if sink.poll(50) and sink.recv() == b'pose-test-frame':
                    pose_seen = True; break
            self.assertTrue(pose_seen)
            with g.lock:
                g.pose_reader.snapshot.return_value = {'ready': False, 'packet': None}
            until = time.monotonic()+1
            while g.input_mode != 'planner' and time.monotonic()<until:time.sleep(.01)
            self.assertEqual(g.input_mode, 'planner')
            self.assertTrue(g.mode_pending)
            self.assertIsNone(proc.poll())
            # Lose feedback while HTTP remains alive: native transport must stop its child.
            stopped.set();worker.join(1)
            until=time.monotonic()+2
            while proc.poll() is None and time.monotonic()<until:time.sleep(.01)
            self.assertEqual(proc.kills,1)
            self.assertEqual(g.phase,'error')
        finally:
            stopped.set();worker.join(1);transport.stop.set();transport.thread.join(4)
            state.close(0);sink.close(0);ctx.term()

    def test_child_dies_when_gateway_parent_is_killed(self):
        launcher=str(Path(__file__).resolve().parents[2]/'onboardmimic/core/owned_exec.py')
        code='import subprocess,os,sys,time; p=subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid()),"/bin/sleep","60"]); print(p.pid,flush=True); time.sleep(60)'
        parent=subprocess.Popen([sys.executable,'-c',code,launcher],stdout=subprocess.PIPE,text=True)
        child=None
        try:
            child=int(parent.stdout.readline())
            time.sleep(.1)
            parent.kill();parent.wait(timeout=2)
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                path=Path('/proc/%d/stat'%child)
                try:
                    if path.read_text().split()[2]=='Z':break
                except (FileNotFoundError, ProcessLookupError):
                    break  # Child can disappear between lookup and read.
                time.sleep(.01)
            else:self.fail('owned child survived gateway termination')
        finally:
            if parent.poll() is None:parent.kill();parent.wait(timeout=2)
            parent.stdout.close()
            if child:
                try:os.kill(child,signal.SIGKILL)
                except ProcessLookupError:pass

if __name__=='__main__':unittest.main()
