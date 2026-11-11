"""Unitree MotionSwitcher RPC over the existing ROS2/CycloneDDS transport.

Wire contract: unitree_sdk2_python MotionSwitcherClient / ClientBase (1001/1003).
This client has no LowCmd publisher. Release is an explicit connection step only.
"""
import json
import math
import time


class RpcTimeout(RuntimeError):
    pass


def validate_motors(motors):
    if len(motors) < 29:
        raise RuntimeError('Incomplete motor feedback')
    for i, motor in enumerate(motors[:29]):
        if not math.isfinite(motor.q) or not math.isfinite(motor.dq):
            raise RuntimeError('Invalid motor feedback: %d' % i)
        if motor.motorstate or abs(motor.dq) > 12.5:
            raise RuntimeError('Motor fault or excessive measured velocity: %d' % i)


def release_mode(client, timeout=6., clock=time.monotonic, sleep=time.sleep):
    before = client.check_mode()
    if not before['name']:
        return dict(released=False, before=before, after=before)
    client.check_feedback()
    client.release()  # Once only: do not retry an unconfirmed mutating RPC.
    deadline = clock() + timeout
    while clock() < deadline:
        after = client.check_mode(timeout=min(3., max(.01, deadline-clock())))
        if not after['name']:
            return dict(released=True, before=before, after=after)
        sleep(.1)
    raise RuntimeError('Unitree mode release was not confirmed; bridge not started')


class MotionMode:
    def __init__(self):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        from unitree_api.msg import Request, Response
        from unitree_hg.msg import LowState
        self.ros, self.request_type = rclpy, Request
        self.context = rclpy.context.Context()
        rclpy.init(context=self.context)
        self.node = rclpy.create_node('onboardmimic_mode_connection', context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.responses, self.feedback = {}, None
        self.pending = None
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        # ROS adds the rt/ prefix, matching the official SDK's DDS topic names.
        self.publisher = self.node.create_publisher(Request, '/api/motion_switcher/request', qos)
        self.subscription = self.node.create_subscription(Response, '/api/motion_switcher/response', self._response,
            QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.lowstate = self.node.create_subscription(LowState, '/lowstate', self._feedback,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))

    def _response(self, response):
        identity = response.header.identity
        if (identity.id, identity.api_id) == self.pending:
            self.responses[self.pending] = response

    def _feedback(self, state):
        self.feedback = (time.monotonic(), state)

    def call(self, api, timeout=3.):
        if api not in (1001, 1003):
            raise ValueError('Unsupported MotionSwitcher API')
        deadline = time.monotonic() + timeout
        while not (self.publisher.get_subscription_count() and self.node.count_publishers('/api/motion_switcher/response')):
            if time.monotonic() >= deadline:
                raise RpcTimeout('Unitree MotionSwitcher discovery timed out')
            self.executor.spin_once(timeout_sec=.02)
        request = self.request_type()
        request.header.identity.id = time.monotonic_ns()
        request.header.identity.api_id = api
        request.parameter = '{}'
        self.pending = (request.header.identity.id, api)
        self.publisher.publish(request)
        try:
            while self.pending not in self.responses:
                if time.monotonic() >= deadline:
                    raise RpcTimeout('Unitree MotionSwitcher API %d timed out' % api)
                self.executor.spin_once(timeout_sec=.02)
            response = self.responses.pop(self.pending)
            if response.header.status.code != 0:
                raise RuntimeError('Unitree MotionSwitcher API %d failed: %d' % (api, response.header.status.code))
            return response.data
        finally:
            self.pending = None

    def check_mode(self, timeout=3.):
        # The vendor's best-effort reply can be lost during initial discovery.
        # Only this read-only RPC retries, within the original total time budget.
        deadline = time.monotonic() + timeout
        for attempt in range(3):
            remaining = max(.01, deadline-time.monotonic())
            try:
                data = self.call(1001, min(1., remaining))
                break
            except RpcTimeout:
                if attempt == 2 or time.monotonic() >= deadline: raise
        mode = json.loads(data)
        if not isinstance(mode, dict) or not isinstance(mode.get('name'), str):
            raise RuntimeError('Invalid Unitree CheckMode response')
        return mode

    def release(self):
        self.call(1003)

    def check_feedback(self):
        deadline = time.monotonic() + 3.
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=.02)
            if self.feedback and time.monotonic()-self.feedback[0] < .1:
                validate_motors(self.feedback[1].motor_state)
                return
        raise RuntimeError('Fresh motor feedback unavailable; mode not released')

    def close(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.context.shutdown()
