"""SONIC controller integration; no hardware I/O during discovery."""
import json
import os
from pathlib import Path
from onboardmimic.core.contracts import S0Backend, LaunchSpec, BackendProgress

DEPLOY = Path(__file__).resolve().parent/'deploy'
RESOURCES = DEPLOY/'onboard'


def controller_args(root, interface, robot=None):
    from onboardmimic.core.paths import ROOT
    # root is the backend resource directory, retained for diagnostic callers.
    deploy = Path(root).parent
    return [str(deploy/'target/release/g1_deploy_onnx_ref'), interface,
        'policy/release/model_decoder.onnx', 'reference/example/',
        '--obs-config','policy/release/observation_config.yaml',
        '--encoder-file','policy/release/model_encoder.onnx',
        '--planner-file','planner/target_vel/V2/planner_orin_compatible.onnx',
        '--input-type','zmq_manager','--output-type','zmq','--hand-type','none',
        '--zmq-host','127.0.0.1','--zmq-port','16686','--zmq-out-port','16687',
        '--zmq-robot-ip','127.0.0.1','--zmq-robot-state-port',str(robot.state_port if robot else 16660),
        '--zmq-robot-cmd-port',str(robot.command_port if robot else 16661),
        '--zmq-robot-lowcmd-udp-port',str(robot.command_udp_port if robot else 16663)]


class SonicBackend(S0Backend):
    id = 'sonic'
    label = 'SONIC'
    compatible_robots = frozenset(('unitree_g1',))
    capabilities = frozenset(('planner','pose'))
    supported_inputs = frozenset(('pico',))

    def installer(self, robot):
        if robot.id not in self.compatible_robots:
            return super().installer(robot)
        from .setup import main
        return main

    def baseline(self, robot):
        return json.loads((RESOURCES/'baseline_q.json').read_text())

    def launch_spec(self, robot):
        interface, hardware_env = robot.controller_environment()
        env = dict(os.environ, SONIC_SIM_GESTURES='0', HAND_TYPE='none', **hardware_env)
        env.pop('SONIC_LOWCMD_ZMQ_FALLBACK',None)
        return LaunchSpec(controller_args(RESOURCES,interface,robot),DEPLOY,env)

    def progress(self, text):
        return BackendProgress(preparing='[LowCmd TX] transport=' in text,
            prepared='Init Done' in text, active='Bumpless policy handoff complete' in text,
            armed='CONTROL armed' in text)

    def decode_status(self, topic, value):
        if topic != 'g1_debug' or not isinstance(value,dict):
            return None
        encoder = value.get('encoder_mode')
        return dict(mode='pose' if encoder == 2 else 'planner' if encoder in (0,1) else None,
                    playing=value.get('play') is True)

    def transport(self, gateway, robot):
        from .transport import Transport
        return Transport(gateway,state_port=robot.state_port)

    def pose_reader(self, input_id, path):
        if input_id != 'pico':
            return super().pose_reader(input_id,path)
        from .pico.pose_reader import PoseReader
        return PoseReader(path)
