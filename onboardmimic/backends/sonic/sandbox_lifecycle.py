"""Real SONIC process lifecycle, restricted to the isolated simulation harness.
No production backend: construction and launch require a loopback-only netns.
"""
import os,socket,math
from onboardmimic.core.gateway import Gateway


def require_isolation():
    parent=os.environ.get('FH_PARENT_NETNS')
    if not parent or os.readlink('/proc/self/ns/net')==parent or [n for _,n in socket.if_nameindex()]!=['lo']:
        raise RuntimeError('Sandbox lifecycle cannot run on the robot host network')


class SandboxLifecycle(Gateway):
    def __init__(self, baseline, launch, read_log):
        require_isolation()
        super().__init__(baseline)
        self.launch=launch
        self.read_log=read_log
        self.process=None
        self.failure=''

    def tick(self):
        with self.lock:
            if self.process is not None:
                if self.process.poll() is not None and self.phase not in ('stopped','error'):
                    self.phase='error';self.failure='隔离控制进程已退出'
                    self.lease.stop()
                elif self.phase in ('preparing','starting_policy'):
                    text=self.read_log()
                    if self.phase=='preparing' and 'Init Done' in text:
                        self.phase='prepared'
                    elif self.phase=='starting_policy' and 'Bumpless policy handoff complete' in text:
                        self.phase='active';self.still_since=self.clock()
            super().tick()

    def status(self):
        with self.lock:
            s=super().status()
            s.update(mode='onboard_sandbox',robot='G1（隔离 MuJoCo）',
                     pids=[self.process.pid] if self.process is not None and self.process.poll() is None else [],
                     workflow_error=self.failure or '仅控制隔离仿真，不连接真机',
                     sandbox_pose_ready=self.phase=='prepared',
                     sandbox_policy_active=self.phase=='active')
            return s

    def request(self, route, x):
        route = {'/api/controller':'/api/controller','/api/control':'/api/control'}.get(route,route)
        with self.lock:
            action=x.get('action') if route=='/api/controller' else None
            if action=='estop':
                # Kill only the owned Popen instance, not a PID pattern/service.
                if self.process is not None and self.process.poll() is None:
                    self.process.kill();self.process.wait(timeout=3)
                return super().request(route,x)
            if action=='prepare_pose' and self.phase=='stopped':
                if self.process is not None and self.process.poll() is None:
                    raise RuntimeError('Previous sandbox process has not exited')
                self.process=None;self.failure=''
            result=super().request(route,x)
            if action=='prepare_pose':
                require_isolation()
                self.until=math.inf
                try:self.process=self.launch()
                except BaseException:
                    self.phase='error';raise
            elif action=='start_policy':
                self.until=math.inf
            return self.status()
