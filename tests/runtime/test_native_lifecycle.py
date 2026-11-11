import math
import unittest
from tests.fixtures import NativeLifecycle, feedback

class Process:
    pid = 123
    code = None
    kills = 0
    def poll(self): return self.code
    def kill(self): self.kills += 1; self.code = -9
    def wait(self, timeout): return self.code

class NativeTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.logs = ''
        self.launches = 0
        self.guards = 0
        self.proc = None
        def launch():
            self.launches += 1
            self.logs = ''
            self.proc = Process()
            return self.proc
        def guard(): self.guards += 1
        self.g = NativeLifecycle([0.]*29, launch, lambda:self.logs, guard, clock=lambda:self.now)
        self.seq = 0
        self.refresh()
    def refresh(self, dq=0., fault=0):
        self.g.ingest('rt/lowstate', {'motor_state':[{'q':0., 'dq':dq,'motorstate':fault} for _ in range(29)]})
        self.g.ingest('rt/secondary_imu', {'quaternion':[1.,0.,0.,0.]})
    def packet(self, **kw):
        self.seq += 1
        return dict(client='test',seq=self.seq,epoch=self.g.epoch,ticket=self.g.ticket(),**kw)
    def command(self, action): return self.g.request('/api/controller',self.packet(action=action))
    def activate(self):
        self.command('prepare_pose')
        self.logs = 'Init Done'
        self.command('start_policy')
        self.logs += '\nCONTROL armed\nBumpless policy handoff complete'
        self.g.tick()
    def test_prepare_never_starts_policy_and_requires_real_completion(self):
        self.command('prepare_pose')
        self.now += 4; self.refresh(); self.g.tick()
        self.assertEqual(self.g.phase,'loading')
        self.assertFalse(self.g.start_pending)
        self.logs='Init Done';self.g.tick()
        self.assertEqual(self.g.phase,'prepared')
        self.assertFalse(self.g.start_pending)
    def test_missing_feedback_or_conflict_never_launches(self):
        self.g.latest.clear()
        with self.assertRaises(ValueError): self.command('prepare_pose')
        self.refresh()
        self.g.guard=lambda: (_ for _ in ()).throw(RuntimeError('conflict'))
        with self.assertRaises(RuntimeError): self.command('prepare_pose')
        self.assertEqual(self.launches,0)
    def test_start_before_prepare_rejected(self):
        with self.assertRaises(ValueError):self.command('start_policy')
        self.assertEqual(self.launches,0)
    def test_stale_feedback_kills_owned_process(self):
        self.activate();self.now += .501; self.g.tick()
        self.assertEqual(self.proc.kills,1)
        self.assertEqual(self.g.phase,'error')
        self.assertFalse(self.g.start_pending)
    def test_fault_and_overspeed_kill(self):
        for dq,fault in [(12.51,0),(0.,1),(float('nan'),0)]:
            self.setUp();self.activate();self.refresh(dq,fault);self.g.tick()
            self.assertEqual(self.proc.kills,1)
    def test_estop_invalidates_old_prepare_and_restart_has_fresh_log(self):
        self.activate();old=self.packet(action='prepare_pose')
        self.command('estop')
        with self.assertRaises(ValueError):self.g.request('/api/controller',old)
        self.command('prepare_pose')
        self.assertEqual(self.g.phase,'loading')
        self.assertEqual(self.launches,2)
        self.assertFalse(self.g.start_pending)
    def test_unrequested_policy_start_kills(self):
        self.command('prepare_pose');self.logs='CONTROL armed';self.g.tick()
        self.assertEqual(self.proc.kills,1)
    def test_lease_loss_clears_motion(self):
        self.activate()
        self.g.request('/api/control',self.packet(move='forward'))
        self.assertEqual(self.g.last_packet['speed'],.5)
        self.now += .56;self.refresh();self.g.tick()
        self.assertEqual(self.g.last_packet['speed'],0)
        self.assertEqual(self.g.phase,'active')
    def test_exit_clears_lease_and_never_restarts(self):
        self.activate();self.proc.code=1;self.g.tick()
        self.assertEqual(self.g.phase,'error')
        self.assertEqual(self.launches,1)
    def test_malformed_feedback_is_invalid(self):
        for value in [None,{}, {'motor_state':None}, {'motor_state':[{}]*29}]:
            self.g.ingest('rt/lowstate',value)
            self.assertFalse(self.g.telemetry().get('valid',False))
    def test_load_timeout_does_not_turn_into_prepared(self):
        self.command('prepare_pose');self.now+=151;self.refresh();self.g.tick()
        self.assertEqual(self.g.phase,'error');self.assertEqual(self.proc.kills,1)


class PoseModeTest(NativeTest):
    def pose_ready(self):
        from unittest.mock import Mock
        self.activate();self.now+=.6;self.refresh();self.g.tick()
        self.g.pose_reader=Mock();self.g.pose_reader.snapshot.return_value={'ready':True,'packet':b'pose'}
        self.g.ingest('g1_debug',{'encoder_mode':0,'play':True})
    def test_switch_keeps_process_and_requires_policy_ack(self):
        self.pose_ready();pid=self.g.process.pid
        self.command('select_pose');self.assertTrue(self.g.mode_pending)
        self.assertEqual(self.g.input_mode,'pose');self.assertEqual(self.g.process.pid,pid)
        self.now+=.12;self.refresh();self.g.ingest('g1_debug',{'encoder_mode':2,'play':True});self.g.tick()
        self.assertFalse(self.g.mode_pending);self.assertIsNone(self.g.last_packet)
        with self.assertRaises(ValueError):self.g.request('/api/control',self.packet(move='forward'))
        self.command('select_planner');self.now+=.12;self.refresh()
        self.g.ingest('g1_debug',{'encoder_mode':0,'play':True});self.g.tick()
        self.assertFalse(self.g.mode_pending);self.assertEqual(self.g.last_packet['speed'],0)
        self.assertEqual(self.launches,1);self.assertEqual(self.proc.kills,0)
    def test_pico_loss_returns_planner_and_never_auto_resumes(self):
        self.pose_ready();self.command('select_pose');self.g.pose_reader.snapshot.return_value={'ready':False}
        self.g.tick();self.assertEqual(self.g.input_mode,'planner');self.assertTrue(self.g.mode_pending)
        self.g.pose_reader.snapshot.return_value={'ready':True};self.g.tick();self.assertEqual(self.g.input_mode,'planner')
    def test_stop_in_pose_requests_planner(self):
        self.pose_ready();self.command('select_pose');self.g.request('/api/control',self.packet(stop=True))
        self.assertEqual(self.g.input_mode,'planner');self.assertEqual(self.proc.kills,0)
    def test_no_pico_or_moving_refuses_switch(self):
        self.pose_ready();self.g.pose_reader.snapshot.return_value={'ready':False}
        with self.assertRaises(ValueError):self.command('select_pose')
        self.g.pose_reader.snapshot.return_value={'ready':True};self.g.request('/api/control',self.packet(move='forward'))
        with self.assertRaises(ValueError):self.command('select_pose')
    def test_ack_timeout_falls_back_and_invalidates_queued_move(self):
        self.pose_ready();old=self.packet(move='forward');self.command('select_pose')
        self.now+=2.01;self.refresh();self.g.tick();self.assertEqual(self.g.input_mode,'planner')
        with self.assertRaises(ValueError):self.g.request('/api/control',old)

if __name__=='__main__': unittest.main()
