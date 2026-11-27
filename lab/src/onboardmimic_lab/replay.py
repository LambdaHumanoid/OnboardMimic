"""LeRobot v2.1 body recordings; no legacy dataset replay fallback."""
from .contracts import fingerprint as plan_digest
from .lerobot_replay import validate_plan, prepare_plan


def summary(plan):
    validate_plan(plan)
    spec=plan['controller_spec'];frames=plan['frames']
    return dict(plan_sha256=plan_digest(plan),episode=plan['episode'],segment_index=plan['segment_index'],
        frames=len(frames),contract_sha256=plan['contract_sha256'],reference_space=spec['reference_space'],
        duration_s=(frames[-1]['offset_ns']+spec['period_ns'])/1e9)
