"""Explicit adapter selection. Unknown or incompatible pairs fail before I/O."""
from importlib import import_module
from .contracts import RobotAdapter, S0Backend, TrackingInput

ROBOTS = {'unitree_g1': 'onboardmimic.robots.unitree_g1.adapter:G1Adapter'}
BACKENDS = {'sonic': 'onboardmimic.backends.sonic.adapter:SonicBackend'}
DEFAULT_ROBOT = 'unitree_g1'
DEFAULT_BACKEND = 'sonic'


def register_robot(name, factory):
    if name in ROBOTS:
        raise ValueError('Robot already registered: '+name)
    ROBOTS[name] = factory


def register_backend(name, factory):
    if name in BACKENDS:
        raise ValueError('Backend already registered: '+name)
    BACKENDS[name] = factory


def construct(registry, name, expected):
    if name not in registry:
        raise ValueError('Unknown adapter: '+name)
    factory = registry[name]
    if isinstance(factory, str):
        module, symbol = factory.split(':')
        factory = getattr(import_module(module), symbol)
    value = factory()
    if not isinstance(value, expected) or value.id != name:
        raise ValueError('Invalid adapter contract: '+name)
    return value


def select(robot=DEFAULT_ROBOT, backend=DEFAULT_BACKEND):
    body = construct(ROBOTS, robot, RobotAdapter)
    controller = construct(BACKENDS, backend, S0Backend)
    _validate_pair(body, controller)
    return body, controller


def _validate_pair(body, controller):
    if body.id not in controller.compatible_robots:
        raise ValueError('Unsupported robot/backend pair: '+body.id+'/'+controller.id)
    import math
    if type(body.joint_count) is not int or body.joint_count <= 0:
        raise ValueError('Robot must define a positive joint count')
    if not controller.capabilities <= {'planner', 'pose'} or 'planner' not in controller.capabilities:
        raise ValueError('Lifecycle requires the Planner idle/fallback capability')
    if type(controller.max_speed) not in (int,float) or not math.isfinite(controller.max_speed) or controller.max_speed < 0:
        raise ValueError('Backend must define a finite nonnegative speed limit')
    target = controller.baseline(body)
    if len(target) != body.joint_count or not all(type(q) in (int,float) and math.isfinite(q) for q in target):
        raise ValueError('Backend reference does not match robot joints')


def catalog():
    """Public adapter metadata only; registration is not deployment readiness.

    Use the same compatibility checks as runtime selection. Never instantiate a
    transport, connection workflow, installer or model to populate the console.
    """
    robots = [construct(ROBOTS, name, RobotAdapter) for name in sorted(ROBOTS)]
    backends = [construct(BACKENDS, name, S0Backend) for name in sorted(BACKENDS)]
    pairs = []
    for body in robots:
        for controller in backends:
            if body.id in controller.compatible_robots:
                _validate_pair(body, controller)
                pairs.append({'robot_id': body.id, 'backend_id': controller.id})
    return {
        'robots': [{'id': r.id, 'label': r.label, 'joint_count': r.joint_count} for r in robots],
        'backends': [{'id': b.id, 'label': b.label,
                      'capabilities': sorted(b.capabilities),
                      'supported_inputs': sorted(b.supported_inputs),
                      'max_speed': b.max_speed} for b in backends],
        'pairs': pairs,
    }


INPUTS = {'pico': 'onboardmimic.inputs.pico.adapter:PicoInput'}


def register_input(name, factory):
    if name in INPUTS:
        raise ValueError('Input already registered: '+name)
    INPUTS[name] = factory


def select_input(name):
    return construct(INPUTS, name, TrackingInput)
