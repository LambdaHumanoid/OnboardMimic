"""Offline, measured-posture shallow target generation. No hardware transport.

Depth is a rigid-body kinematic target, not measured physical displacement or
evidence of balance. Keep both foot poses and the measured pelvis orientation;
move only the twelve leg joints. Never substitute the old nominal standing pose.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from native_model import NativeModel


def rotation_error(target, actual):
    return sum((np.cross(actual[:, i], target[:, i]) for i in range(3)), np.zeros(3)) * .5


def generate(model, q, quat, depth=.03, duration=6., count=121):
    q = np.asarray(q, dtype=float).copy()
    quat = np.asarray(quat, dtype=float)
    if q.shape != (29,) or not np.isfinite(q).all():
        raise ValueError('Invalid standing joints')
    if not 0 < depth <= .03 or duration < 6 or count < 61:
        raise ValueError('This commissioning generator permits at most 3 cm over at least 6 seconds')
    limits = np.asarray(model.spec['limits'])
    initial = model.calculate(q, np.zeros(29), quat, np.zeros(3))
    poses = [q.copy()]
    times = np.linspace(0, duration, count)
    maximum_position_error = maximum_rotation_error = 0.
    for t in times[1:]:
        u = t / duration
        drop = depth * (10*u**3 - 15*u**4 + 6*u**5)
        feet = initial['feet'] + np.array([0., 0., drop])
        for _ in range(100):
            r = model.calculate(q, np.zeros(29), quat, np.zeros(3))
            pe = feet-r['feet']
            re = np.array([rotation_error(a, b) for a, b in zip(initial['foot_rotations'], r['foot_rotations'])])
            if np.max(np.abs(pe)) < 1e-6 and np.max(np.abs(re)) < 1e-5:
                break
            error = np.concatenate([np.r_[pe[i], re[i]] for i in range(2)])
            jac = r['jacobian'][:, 6:18]
            delta = jac.T @ np.linalg.solve(jac @ jac.T + 1e-8*np.eye(12), error)
            delta *= min(1., .02 / max(np.max(np.abs(delta)), 1e-12))
            q[:12] += delta
            if np.any(q < limits[:, 0]+.01) or np.any(q > limits[:, 1]-.01):
                raise ValueError('Kinematic target exceeds joint margin')
        else:
            raise ValueError('Foot-pose IK did not converge')
        maximum_position_error = max(maximum_position_error, float(np.max(np.abs(pe))))
        maximum_rotation_error = max(maximum_rotation_error, float(np.max(np.abs(re))))
        poses.append(q.copy())
    poses = np.asarray(poses)
    speed = float(np.max(np.abs(np.diff(poses, axis=0)/np.diff(times)[:, None])))
    if speed > .25:
        raise ValueError('Plan target velocity exceeds 0.25 rad/s')
    return dict(q=poses, time=times, quaternion=quat, depth_m=np.array(depth)), dict(
        target_depth_m=depth, duration_s=duration, max_target_speed_rad_s=speed,
        max_foot_position_error_m=maximum_position_error,
        max_foot_orientation_error=maximum_rotation_error,
        upper_body_unchanged=bool(np.array_equal(poses[:,12:], np.broadcast_to(poses[0,12:], poses[:,12:].shape))),
        max_joint_change_rad=float(np.max(np.abs(poses[-1]-poses[0]))),
        validation='kinematics_only_not_balance_or_hardware_acceptance')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('sample', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    sample = json.loads(a.sample.read_text())
    frame = sample['raw_feedback']
    q = [x['q'] for x in frame['motor_state'][:29]]
    plan, report = generate(NativeModel(Path(__file__).with_name('model.json')), q, frame['imu_state']['quaternion'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.output, **plan)
    report['source_sample_wall'] = sample['wall']
    a.output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
