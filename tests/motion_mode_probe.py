"""Fake MotionSwitcher integration: domain 95 / loopback, no motor commands."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    xml = '<CycloneDDS><Domain><General><NetworkInterfaceAddress>lo</NetworkInterfaceAddress><AllowMulticast>false</AllowMulticast></General><Discovery><Peers><Peer address="127.0.0.1"/></Peers></Discovery></Domain></CycloneDDS>'
    if (os.environ.get('ROS_DOMAIN_ID') != '95' or os.environ.get('CYCLONEDDS_URI') != xml
            or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_cyclonedds_cpp'):
        raise SystemExit('Requires domain 95 and exact loopback-only DDS XML')
    sys.path.insert(0, str(args.root))
    from onboardmimic.robots.unitree_g1.bridge.motion_mode import MotionMode, release_mode
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from unitree_api.msg import Request, Response
    from unitree_hg.msg import LowState

    rclpy.init()
    node = rclpy.create_node('fake_motion_switcher')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    mode, calls = {'name':'normal','reject':False,'drop_read':True}, []
    pub = node.create_publisher(Response, '/api/motion_switcher/response',
        QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT))

    def reply(request):
        api = request.header.identity.api_id
        calls.append(api)
        if api == 1001 and mode['drop_read']:
            mode['drop_read'] = False
            return  # First best-effort response lost during discovery.
        response = Response()
        response.header.identity = request.header.identity
        if api == 1001:
            response.data = json.dumps({'name':mode['name']})
        elif api == 1003:
            if mode['reject']: response.header.status.code = 4201
            else: mode['name'] = ''
        else: response.header.status.code = 9999
        pub.publish(response)

    subscription = node.create_subscription(Request, '/api/motion_switcher/request', reply, 10)
    feedback = node.create_publisher(LowState, '/lowstate',
        QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
    timer = node.create_timer(.02, lambda: feedback.publish(LowState()))
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    client = MotionMode()
    try:
        assert client.check_mode()['name'] == 'normal'
        assert release_mode(client)['released'] is True
        assert mode['name'] == '' and calls.count(1003) == 1
        assert release_mode(client)['released'] is False
        assert calls.count(1003) == 1
        mode.update(name='normal', reject=True)
        try: release_mode(client)
        except RuntimeError as exc: assert '4201' in str(exc)
        else: raise AssertionError('rejected RPC accepted')
        assert mode['name'] == 'normal' and calls.count(1003) == 2
        print(json.dumps(dict(passed=True, domain=95, fake_vendor=True,
                             requests=calls, motor_publishers=0)))
    finally:
        client.close()
        executor.shutdown()
        thread.join(timeout=2)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__': main()
