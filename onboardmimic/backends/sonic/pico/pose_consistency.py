"""Admission-only arm direction check; never modifies the motion reference.

PICO xyz is transformed with the same UP and root orientation as the SMPL
mapping. Bone directions are normalized, so translation and body size do not
influence the result. Thresholds are conservative software defaults, not a
hardware-validated guarantee of tracking accuracy.
"""
import numpy as np
from scipy.spatial.transform import Rotation as R
from .pose_mapping import UP

ARMS = ((16,18,'left_upper_arm'), (18,20,'left_forearm'),
        (17,19,'right_upper_arm'), (19,21,'right_forearm'))
MAX_ANGLE_DEG = 60.
STABLE_SECONDS = .3


def compare_arms(poses, mapped):
    raw = np.asarray(poses, dtype=float)[:, :3]
    joints = np.asarray(mapped['smpl_joints'], dtype=float)
    quat = np.asarray(mapped['body_quat_w'])
    world_to_local = R.from_quat(quat[[1,2,3,0]]).inv() * UP
    angles = {}
    for parent, child, name in ARMS:
        a = world_to_local.apply(raw[child] - raw[parent])
        b = joints[child] - joints[parent]
        lengths = (np.linalg.norm(a), np.linalg.norm(b))
        if not np.isfinite(a).all() or not np.isfinite(b).all() or min(lengths) < .02:
            return dict(valid=False, consistent=False, angles_deg=angles,
                        reason='手臂位置缺失或骨段过短 / Missing arm positions')
        cosine = np.dot(a / lengths[0], b / lengths[1])
        angles[name] = float(np.degrees(np.arccos(np.clip(cosine, -1, 1))))
    return dict(valid=True, consistent=max(angles.values()) <= MAX_ANGLE_DEG,
                angles_deg=angles, threshold_deg=MAX_ANGLE_DEG)


class ArmAdmission:
    def __init__(self):
        self.good_since = self.bad_since = None
        self.result = dict(ready=False, state='pending', reason='等待姿态一致性检查 / Waiting for pose check')

    def update(self, check, now):
        if check['valid'] and check['consistent']:
            self.bad_since = None
            if self.good_since is None: self.good_since = now
            ready = now - self.good_since >= STABLE_SECONDS - 1e-9
            state = 'passed' if ready else 'pending'
            reason = '姿态一致 / Pose consistent' if ready else '等待姿态持续一致 / Waiting for stable pose'
        else:
            self.good_since = None
            if self.bad_since is None: self.bad_since = now
            ready = False
            state = 'blocked' if now - self.bad_since >= STABLE_SECONDS - 1e-9 else 'pending'
            reason = ('追踪或映射异常：手臂方向不一致 / Tracking or mapping mismatch' if state == 'blocked'
                      else '正在检查手臂方向不一致 / Checking arm direction mismatch')
        self.result = dict(check, ready=ready, state=state, reason=reason, stable_seconds=STABLE_SECONDS)
