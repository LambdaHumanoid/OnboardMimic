# Wrist mapping extracted from the imported body_transforms.py; see pose_sources.json.
from __future__ import annotations
import numpy as np
from scipy.spatial.transform import Rotation as sRot

def _quaternion_multiply_np(a, b):
    """Hamilton product of quaternions (wxyz format), batched."""
    aw, ax, ay, az = np.split(a, 4, axis=1)
    bw, bx, by, bz = np.split(b, 4, axis=1)
    ow = aw * bw - ax * bx - ay * by - az * bz
    ox = aw * bx + ax * bw + ay * bz - az * by
    oy = aw * by - ax * bz + ay * bw + az * bx
    oz = aw * bz + ax * by - ay * bx + az * bw
    return np.concatenate([ow, ox, oy, oz], axis=1)

def _decompose_rotation_aa(rotation_aa, v2):
    """Decompose axis-angle rotation into twist and swing components."""
    angle = np.linalg.norm(rotation_aa, axis=1)[:, None]
    angle = np.clip(angle, 1e-8, None)
    w = np.cos(angle / 2)
    v = np.sin(angle / 2) * rotation_aa / angle
    q = np.concatenate([w, v], axis=1)
    v_twist = np.dot(v, v2)[:, None] * v2
    q_twist = np.concatenate([w, v_twist], axis=1)
    q_twist = q_twist / np.linalg.norm(q_twist, axis=1)[:, None]
    q_twist_inv = q_twist * np.array([1, -1, -1, -1])
    q_swing = _quaternion_multiply_np(q_twist_inv, q)
    return q_twist, q_swing

def compute_wrist_joints(smpl_pose_63: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute G1 wrist joint angles from SMPL body pose.
    Matches pico_manager_thread_server.py lines 1406-1464.

    Args:
        smpl_pose_63: (63,) axis-angle body pose (21 joints × 3)

    Returns:
        left_wrist: (3,) [roll, pitch, yaw]
        right_wrist: (3,) [roll, pitch, yaw]
    """
    body_pose = smpl_pose_63.reshape(1, 21, 3)

    SMPL_L_ELBOW_IDX = 17
    SMPL_L_WRIST_IDX = 19
    SMPL_R_ELBOW_IDX = 18
    SMPL_R_WRIST_IDX = 20

    smpl_l_elbow_aa = body_pose[:, SMPL_L_ELBOW_IDX]
    smpl_l_wrist_aa = body_pose[:, SMPL_L_WRIST_IDX]
    smpl_r_elbow_aa = body_pose[:, SMPL_R_ELBOW_IDX]
    smpl_r_wrist_aa = body_pose[:, SMPL_R_WRIST_IDX]

    g1_l_elbow_axis = np.array([0, 1, 0])
    _, g1_l_elbow_q_swing = _decompose_rotation_aa(smpl_l_elbow_aa, g1_l_elbow_axis)

    g1_r_elbow_axis = np.array([0, 1, 0])
    _, g1_r_elbow_q_swing = _decompose_rotation_aa(smpl_r_elbow_aa, g1_r_elbow_axis)

    l_elbow_swing_euler = sRot.from_quat(
        g1_l_elbow_q_swing[:, [1, 2, 3, 0]]
    ).as_euler("XYZ", degrees=False)
    r_elbow_swing_euler = sRot.from_quat(
        g1_r_elbow_q_swing[:, [1, 2, 3, 0]]
    ).as_euler("XYZ", degrees=False)

    l_wrist_euler = sRot.from_rotvec(smpl_l_wrist_aa).as_euler("XYZ", degrees=False)
    r_wrist_euler = sRot.from_rotvec(smpl_r_wrist_aa).as_euler("XYZ", degrees=False)

    g1_l_wrist_roll = l_elbow_swing_euler[:, 0] + l_wrist_euler[:, 0]
    g1_l_wrist_pitch = -l_wrist_euler[:, 1]
    g1_l_wrist_yaw = l_elbow_swing_euler[:, 2] + l_wrist_euler[:, 2]

    g1_r_wrist_roll = -(r_elbow_swing_euler[:, 0] + r_wrist_euler[:, 0])
    g1_r_wrist_pitch = -r_wrist_euler[:, 1]
    g1_r_wrist_yaw = r_elbow_swing_euler[:, 2] + r_wrist_euler[:, 2]

    # pico_manager negates l_wrist_pitch when writing to joint_pos
    left_wrist = np.array([
        g1_l_wrist_roll[0], -g1_l_wrist_pitch[0], g1_l_wrist_yaw[0],
    ], dtype=np.float32)
    right_wrist = np.array([
        g1_r_wrist_roll[0], g1_r_wrist_pitch[0], g1_r_wrist_yaw[0],
    ], dtype=np.float32)

    return left_wrist, right_wrist
