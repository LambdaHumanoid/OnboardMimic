"""Versioned physical layout for G1/SONIC datasets, independent of runtime code."""
G1_JOINT_NAMES = tuple(
    [side+"_"+joint+"_joint" for side in ("left", "right") for joint in
     ("hip_pitch", "hip_roll", "hip_yaw", "knee", "ankle_pitch", "ankle_roll")]
    + ["waist_"+axis+"_joint" for axis in ("yaw", "roll", "pitch")]
    + [side+"_"+joint+"_joint" for side in ("left", "right") for joint in
       ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_roll", "wrist_pitch", "wrist_yaw")]
)


def dataset_layout():
    return {
        "schema_version": 1,
        "robot_id": "unitree_g1", "backend_id": "sonic",
        "joint_names": list(G1_JOINT_NAMES),
        "fields": {
            "observation.body_joint_position": {"shape": [29], "unit": "rad"},
            "observation.body_joint_velocity": {"shape": [29], "unit": "rad/s"},
            "observation.root_orientation": {"shape": [4], "order": "wxyz", "unit": "unit quaternion"},
            "observation.image": {"encoding": "JPEG", "value": "relative path under aligned/"},
            "action.motion_token": {"shape": [64], "space": "sonic.motion_token.v1",
                                    "unit": "un-normalized encoder output; model-pair dependent"},
            "source.monotonic_ns": {"unit": "ns", "clock": "recorder and SONIC same OS"},
        },
        "training_windows": "must not cross segment_index boundaries",
    }
