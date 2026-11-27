import json, logging, threading, time, sys
from onboardmimic.core.runtime_timing import wait_event

def runtime_event(event, **fields):
    logging.getLogger("runtime_audit").info(json.dumps(dict(event=event, **fields)))

class Transport:
    def __init__(self, gateway, state_port=16660, planner_port=16686):
        self.gateway = gateway
        self.state_port, self.planner_port = state_port, planner_port
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.last_tick = time.monotonic()
        self.error = None
        self.stage = 'initializing'
        self.shutdown_reason = None
        self.feedback_ready = threading.Event()
        self.feedback_thread = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def diagnostic_stage(self, role, stage):
        diagnostics=getattr(self.gateway,'runtime_diagnostics',None)
        if diagnostics is not None:diagnostics.mark(role,stage)

    def receive_feedback(self, ctx):
        import msgpack
        import zmq
        sub = ctx.socket(zmq.SUB)
        try:
            sub.setsockopt(zmq.SUBSCRIBE, b'rt/')
            sub.setsockopt(zmq.RCVHWM, 100)
            sub.connect('tcp://127.0.0.1:%d' % self.state_port)
            self.feedback_ready.set()
            while not self.stop.is_set():
                self.diagnostic_stage('state_receive','poll')
                if not sub.poll(5):
                    continue
                # A finite batch keeps shutdown bounded under continuous input.
                for _ in range(100):
                    try:
                        self.diagnostic_stage('state_receive','receive')
                        parts = sub.recv_multipart(zmq.NOBLOCK)
                        received_at = self.gateway.clock()
                        decode_cpu = time.thread_time_ns()
                        self.diagnostic_stage('state_receive','decode')
                        topic, payload = parts
                        value = msgpack.unpackb(payload, raw=False)
                        decoded_at = self.gateway.clock()
                        decode_cpu = time.thread_time_ns()-decode_cpu
                        self.diagnostic_stage('state_receive','enqueue')
                        self.gateway.feedback_inbox.put(topic.decode(), value, received_at,
                            decoded_at=decoded_at,decode_cpu_ns=decode_cpu)
                    except zmq.Again:
                        break
                    except (ValueError, TypeError, UnicodeDecodeError):
                        continue
        except BaseException as exc:
            self.error = type(exc).__name__+': '+str(exc)
            self.shutdown_reason = 'feedback_receiver_exception'
            self.stop.set()
        finally:
            sub.close(0)
            self.feedback_ready.set()

    def run(self):
        import msgpack
        import zmq
        with self.gateway.lock:
            self.gateway.transport_driven = True
        from .wire import build_command_message, build_planner_message
        ctx = zmq.Context()
        from .feedback import FeedbackInbox
        self.gateway.feedback_inbox = FeedbackInbox(self.gateway.robot.state_topics,clock=self.gateway.clock)
        pub = ctx.socket(zmq.PUB)
        feedback = ctx.socket(zmq.SUB)
        exception_info = None
        native_receiver = None
        try:
            from .native.receiver import Receiver
            native_receiver = Receiver.from_environment()
            self.gateway.native_consumption = native_receiver
            if native_receiver: native_receiver.thread.start()
        except Exception:
            # Optional evidence failure must never terminate motor control.
            runtime_event('native_telemetry_unavailable')
        try:
            self.feedback_thread = threading.Thread(target=self.receive_feedback, args=(ctx,),
                                                    name='sonic-feedback', daemon=True)
            self.feedback_thread.start()
            if not self.feedback_ready.wait(2) or self.stop.is_set():
                raise RuntimeError('Feedback receiver startup failed')
            pub.setsockopt(zmq.SNDHWM, 5)
            pub.bind('tcp://127.0.0.1:%d' % self.planner_port)
            feedback.setsockopt(zmq.SUBSCRIBE, b'g1_debug')
            feedback.setsockopt(zmq.CONFLATE, 1)
            feedback.connect('tcp://127.0.0.1:%d' % (self.planner_port+1))
            last_pose_key = None
            self.ready.set()
            while not self.stop.is_set():
                begin = time.monotonic()
                self.stage = 'feedback_apply'
                self.diagnostic_stage('control','feedback_apply')
                self.gateway.refresh_feedback()
                self.stage = 'policy_feedback'
                self.diagnostic_stage('control','policy_feedback')
                if feedback.poll(0):
                    try:
                        raw = feedback.recv(zmq.NOBLOCK)
                        self.gateway.ingest('g1_debug', msgpack.unpackb(raw[len(b'g1_debug'):], raw=False))
                    except (zmq.Again, ValueError, TypeError): pass
                self.stage = 'gateway_tick'
                self.diagnostic_stage('control','gateway_tick')
                with self.gateway.lock:
                    self.gateway.tick()
                    packet = self.gateway.last_packet
                    if self.gateway.start_pending:
                        pub.send(build_planner_message(0,[0,0,0],[1,0,0],speed=0))
                        pub.send(build_command_message(True,False,True))
                    elif self.gateway.phase == 'active' and self.gateway.input_mode == 'replay':
                        # Keep the underlying live Planner at zero velocity during
                        # external tokens and the confirmed return transition.
                        pub.send(build_planner_message(0,[0,0,0],[1,0,0],speed=0))
                    elif self.gateway.phase == 'active' and (self.gateway.input_mode == 'pose' or self.gateway.mode_pending):
                        is_planner = self.gateway.input_mode == 'planner'
                        if self.gateway.mode_pending:
                            pub.send(build_command_message(True,False,is_planner))
                        if is_planner:
                            pub.send(build_planner_message(0,[0,0,0],[1,0,0],speed=0))
                        else:
                            snap = self.gateway.pose_reader.snapshot()
                            key = (self.gateway.epoch, snap.get('packet'))
                            if snap['ready'] and key != last_pose_key:
                                pub.send(snap['packet']); last_pose_key = key
                                self.gateway.pose_reader.mark_published(snap)
                    elif packet and self.gateway.phase == 'active':
                        pub.send(build_planner_message(
                            packet['mode'],packet['movement_direction'],packet['facing_direction'],speed=packet['speed'],
                            **{k:packet[k] for k in ('height','upper_body_position','upper_body_velocity') if k in packet}))
                self.last_tick = time.monotonic()
                self.stage = 'cadence_wait'
                self.diagnostic_stage('control','wait')
                wait_event(self.stop, max(0, .02-(self.last_tick-begin)))
        except BaseException as exc:
            self.error = type(exc).__name__+': '+str(exc)
            self.shutdown_reason = 'transport_exception'
            exception_info = sys.exc_info()
        finally:
            with self.gateway.lock:
                self.gateway.halt('机载通信循环已停止' + (': '+self.error if self.error else ''))
            if exception_info:
                logging.getLogger('runtime_audit').error('Transport failed at %s',self.stage,exc_info=exception_info)
            runtime_event('transport_exit',stage=self.stage,error=self.error,stop_requested=self.stop.is_set())
            if native_receiver: native_receiver.close()
            self.stop.set()
            if self.feedback_thread is not None:
                self.feedback_thread.join(timeout=2)
            pub.close(0); feedback.close(0); ctx.term()
            self.ready.set()
