import os,unittest
from unittest.mock import patch
from onboardmimic.backends.sonic.sandbox_lifecycle import SandboxLifecycle,require_isolation

class Process:
    pid=12345
    code=None
    killed=0
    def poll(self):return self.code
    def kill(self):self.killed+=1;self.code=-9
    def wait(self,timeout):return self.code

class SandboxTest(unittest.TestCase):
    def test_refuse_host_namespace(self):
        with patch.dict(os.environ,FH_PARENT_NETNS=os.readlink('/proc/self/ns/net')):
            with self.assertRaises(RuntimeError):require_isolation()

    def test_real_logs_gate_transitions_and_estop_kills_only_owned(self):
        proc=Process();logs=[''];launches=[]
        def launch():launches.append(True);return proc
        with patch('onboardmimic.backends.sonic.sandbox_lifecycle.require_isolation'):
            g=SandboxLifecycle([0.]*29,launch,lambda:logs[0]);now=[100.];g.clock=lambda:now[0]
            seq=0
            def call(action):
                nonlocal seq
                seq+=1;s=g.status()
                return g.request('/api/controller',dict(action=action,client='test',seq=seq,epoch=s['epoch'],ticket=s['ticket']))
            call('prepare_pose');now[0]+=100
            self.assertEqual(g.status()['state'],'preparing')
            self.assertEqual(len(launches),1)
            logs[0]='Init Done';self.assertEqual(g.status()['state'],'prepared')
            call('start_policy');now[0]+=10
            self.assertEqual(g.status()['state'],'starting_policy')
            logs[0]+='\nBumpless policy handoff complete'
            self.assertEqual(g.status()['state'],'active')
            old=dict(action='start_policy',client='test',seq=99,epoch=g.epoch,ticket=g.ticket())
            call('estop');self.assertEqual(proc.killed,1)
            self.assertEqual(g.status()['state'],'stopped')
            with self.assertRaises(ValueError):g.request('/api/controller',old)
            self.assertFalse(g.status()['motion_enabled'])

    def test_prepare_can_restart_after_estop_without_stale_ready_log(self):
        logs=[''];instances=[]
        def launch():
            logs[0]='';p=Process();instances.append(p);return p
        with patch('onboardmimic.backends.sonic.sandbox_lifecycle.require_isolation'):
            g=SandboxLifecycle([0.]*29,launch,lambda:logs[0])
            def prepare(seq):
                s=g.status();return g.request('/api/controller',dict(action='prepare_pose',client='test',seq=seq,epoch=s['epoch'],ticket=s['ticket']))
            prepare(1);logs[0]='Init Done';self.assertEqual(g.status()['state'],'prepared')
            g.request('/api/controller',{'action':'estop'})
            prepare(2)
            self.assertEqual(len(instances),2)
            self.assertEqual(instances[0].killed,1)
            self.assertEqual(g.status()['state'],'preparing')
            self.assertEqual(instances[1].killed,0)

    def test_unexpected_process_exit_clears_commands(self):
        proc=Process()
        with patch('onboardmimic.backends.sonic.sandbox_lifecycle.require_isolation'):
            g=SandboxLifecycle([0.]*29,lambda:proc,lambda:'')
            g.process=proc;g.phase='active';g.lease.move='forward';proc.code=1
            self.assertEqual(g.status()['state'],'error')
            self.assertEqual(g.lease.move,'')

if __name__=='__main__':unittest.main()
