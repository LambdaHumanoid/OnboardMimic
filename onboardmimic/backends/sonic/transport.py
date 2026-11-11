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
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        import msgpack
        import zmq
        from .wire import build_command_message, build_planner_message
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        pub = ctx.socket(zmq.PUB)
        feedback = ctx.socket(zmq.SUB)
        exception_info = None
        try:
            sub.setsockopt(zmq.SUBSCRIBE, b'rt/')
            sub.setsockopt(zmq.RCVHWM, 100)
            sub.connect('tcp://127.0.0.1:%d' % self.state_port)
            pub.setsockopt(zmq.SNDHWM, 5)
            pub.bind('tcp://127.0.0.1:%d' % self.planner_port)
            feedback.setsockopt(zmq.SUBSCRIBE, b'g1_debug')
            feedback.setsockopt(zmq.CONFLATE, 1)
            feedback.connect('tcp://127.0.0.1:%d' % (self.planner_port+1))
            last_pose_key = None
            self.ready.set()
            while not self.stop.is_set():
                begin = time.monotonic()
                self.stage = 'state_poll'
                if sub.poll(5):
                    self.stage = 'state_receive'
                    for _ in range(100):
                        try:
                            topic, payload = sub.recv_multipart(zmq.NOBLOCK)
                            value = msgpack.unpackb(payload, raw=False)
                            self.gateway.ingest(topic.decode(), value)
                        except zmq.Again:
                            break
                        except (ValueError, TypeError, UnicodeDecodeError):
                            # Preserve last valid timestamp: malformed traffic cannot renew freshness.
                            continue
                self.stage = 'policy_feedback'
                if feedback.poll(0):
                    try:
                        raw = feedback.recv(zmq.NOBLOCK)
                        self.gateway.ingest('g1_debug', msgpack.unpackb(raw[len(b'g1_debug'):], raw=False))
                    except (zmq.Again, ValueError, TypeError): pass
                self.stage = 'gateway_tick'
                with self.gateway.lock:
                    self.gateway.tick()
                    packet = self.gateway.last_packet
                    if self.gateway.start_pending:
                        pub.send(build_planner_message(0,[0,0,0],[1,0,0],speed=0))
                        pub.send(build_command_message(True,False,True))
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
                    elif packet and self.gateway.phase == 'active':
                        pub.send(build_planner_message(
                            packet['mode'],packet['movement_direction'],packet['facing_direction'],speed=packet['speed'],
                            **{k:packet[k] for k in ('height','upper_body_position','upper_body_velocity') if k in packet}))
                self.last_tick = time.monotonic()
                self.stage = 'cadence_wait'
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
            sub.close(0); pub.close(0); feedback.close(0); ctx.term()
            self.ready.set()
