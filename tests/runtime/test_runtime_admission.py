"""Synthetic inventory/feedback only. No physical SDK or motor output."""
import errno
import unittest
from unittest.mock import patch, Mock
import threading
from types import SimpleNamespace
from onboardmimic.core.admission import AdmissionCheckError, RunningAdmissionObserver
from onboardmimic.core.runtime import monitor_runtime
from onboardmimic.robots.unitree_g1.guard import physical_guard
from tests.runtime import test_native_lifecycle as fixtures

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.events=[]
    def observer(self, check):
        return RunningAdmissionObserver(check,self.events.append,clock=lambda:self.now)
    def test_warning_rate_recovery_and_new_pid(self):
        failures=[True]
        def check(pid):
            if failures[0]:raise ProcessLookupError(errno.ESRCH,'No such process')
        o=self.observer(check)
        for n in range(21):self.now=n*.5;o.inspect(123)
        self.assertEqual(len(self.events),3)
        self.assertEqual([e['occurrences'] for e in self.events],[1,11,21])
        self.assertTrue(all(e['action']=='keep_current_controller' for e in self.events))
        failures[0]=False;o.inspect(123);o.inspect(123)
        self.assertEqual(len(self.events),4);self.assertEqual(self.events[-1]['event'],'running_admission_recovered')
        failures[0]=True;o.inspect(123);o.inspect(124)
        self.assertEqual(self.events[-1]['owned_pid'],124)
        self.assertEqual(self.events[-1]['occurrences'],1)
        o.inspect(None);self.assertEqual(len(self.events),6)
    def service(self,name,user=False):
        if name=='onboardmimic-bridge.service':return dict(LoadState='loaded',ActiveState='active',MainPID='456')
        return dict(LoadState='loaded',ActiveState='inactive')
    def test_same_inventory_refused_at_start_but_active_controller_survives(self):
        for running,errors in [([dict(pid=999,argv=['/bin/sonic_command_bridge','--token','SECRET'])],[]),
                               ([],[dict(pid=999,error='[Errno 3] No such process')])]:
            with self.subTest(errors=errors), patch('onboardmimic.robots.unitree_g1.guard.service',side_effect=self.service), patch('onboardmimic.robots.unitree_g1.guard.processes',return_value=(running,errors)):
                with self.assertRaises(AdmissionCheckError):physical_guard()
                f=fixtures.NativeTest();f.setUp();f.activate()
                o=self.observer(physical_guard)
                for _ in range(60):
                    o.inspect(f.proc.pid);f.now+=.02;f.refresh();f.g.tick()
                    self.assertEqual(f.proc.kills,0)
                    self.assertEqual(f.g.phase,'active')
                    self.assertIsNotNone(f.g.last_packet)
                self.assertNotIn('SECRET',str(self.events))
                self.assertEqual(self.events[-1]['details']['check'],'process_inventory')
                f.command('estop');self.assertEqual(f.proc.kills,1)
    def test_warning_does_not_disable_motor_fault_or_speed_protection(self):
        def unavailable(pid):raise RuntimeError('inventory unavailable')
        for dq,fault in [(12.51,0),(0.,1),(float('nan'),0)]:
            f=fixtures.NativeTest();f.setUp();f.activate()
            self.observer(unavailable).inspect(f.proc.pid)
            f.refresh(dq=dq,fault=fault);f.g.tick()
            self.assertEqual(f.proc.kills,1)
    def test_logging_is_bounded_even_when_failure_changes(self):
        def check(pid):raise RuntimeError(str(self.now))
        o=self.observer(check)
        for n in range(100):self.now=n/100.;o.inspect(123)
        self.assertEqual(len(self.events),1)

    def test_monitor_keeps_owned_controller_through_repeated_inventory_errors(self):
        f=fixtures.NativeTest();f.setUp();f.activate()
        transport=SimpleNamespace(stop=threading.Event(),last_tick=f.now,
            thread=Mock(),shutdown_reason=None,stage='publishing',error=None)
        transport.thread.is_alive.return_value=True
        server=Mock();ticks=[]
        def wait(stop, timeout):
            if len(ticks)==60:return True
            f.now+=1.;f.refresh();f.g.tick();transport.last_tick=f.now
            ticks.append(f.g.last_packet)
            return False
        with patch('onboardmimic.robots.unitree_g1.guard.service',side_effect=self.service), \
             patch('onboardmimic.robots.unitree_g1.guard.processes',return_value=([],[
                 dict(pid=999,error='[Errno 3] No such process')])), \
             patch.object(f.g.robot,'guard',side_effect=physical_guard) as guard, \
             patch('onboardmimic.core.runtime.wait_event',side_effect=wait), \
             patch('onboardmimic.core.runtime.time.monotonic',side_effect=lambda:f.now), \
             self.assertLogs('runtime_audit',level='WARNING') as logs:
            monitor_runtime(f.g,transport,server)
        self.assertEqual(guard.call_count,60)
        self.assertEqual(f.g.phase,'active');self.assertEqual(f.proc.kills,0)
        self.assertTrue(all(packet is not None for packet in ticks))
        self.assertTrue(any('running_admission_warning' in line for line in logs.output))
        server.shutdown.assert_not_called()
        # Advisory inventory failures do not disable real stale-feedback safety.
        f.now+=.501;f.g.tick();self.assertEqual(f.proc.kills,1)

    def test_inventory_error_still_blocks_lifecycle_prepare(self):
        f=fixtures.NativeTest();f.setUp();f.g.guard=physical_guard
        with patch('onboardmimic.robots.unitree_g1.guard.service',side_effect=self.service), \
             patch('onboardmimic.robots.unitree_g1.guard.processes',return_value=([],[
                 dict(pid=999,error='permission denied')])):
            with self.assertRaises(AdmissionCheckError):f.command('prepare_pose')
        self.assertEqual(f.launches,0)

    def test_inventory_details_are_bounded_and_exclude_arguments(self):
        running=[dict(pid=n+1000,argv=['/opt/robot_command_bridge','--token','SECRET'])
                 for n in range(20)]
        errors=[dict(pid=n+2000,error='x'*400) for n in range(20)]
        with patch('onboardmimic.robots.unitree_g1.guard.service',side_effect=self.service), \
             patch('onboardmimic.robots.unitree_g1.guard.processes',return_value=(running,errors)):
            self.observer(physical_guard).inspect(123)
        details=self.events[0]['details']
        self.assertEqual(details['unexpected_count'],20)
        self.assertEqual(details['inspection_error_count'],20)
        self.assertEqual(len(details['unexpected_processes']),16)
        self.assertEqual(len(details['inspection_errors']),16)
        self.assertEqual(len(details['inspection_errors'][0]['error']),300)
        self.assertNotIn('SECRET',str(self.events))

if __name__=='__main__':unittest.main()
