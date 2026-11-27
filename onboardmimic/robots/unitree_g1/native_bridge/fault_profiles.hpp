#pragma once
#include <array>
namespace sonic_bridge { namespace fault_profiles {
// User-selected all-joint trial, matching FH all-v2.
// Seven scenes failed the initial screen; enabling them does not erase that evidence.
// 0x200 is a profile selector, not a universal diagnosis of motor failure.
inline constexpr std::array<bool,29> standing_fold = {
 true, // 0 left_hip_pitch
 true, // 1 left_hip_roll
 true, // 2 left_hip_yaw
 true, // 3 left_knee
 true, // 4 left_ankle_pitch
 true, // 5 left_ankle_roll
 true, // 6 right_hip_pitch
 true, // 7 right_hip_roll
 true, // 8 right_hip_yaw
 true, // 9 right_knee
 true, // 10 right_ankle_pitch
 true, // 11 right_ankle_roll
 true, // 12 waist_yaw
 true, // 13 waist_roll
 true, // 14 waist_pitch
 true, // 15 left_shoulder_pitch
 true, // 16 left_shoulder_roll
 true, // 17 left_shoulder_yaw
 true, // 18 left_elbow
 true, // 19 left_wrist_roll
 true, // 20 left_wrist_pitch
 true, // 21 left_wrist_yaw
 true, // 22 right_shoulder_pitch
 true, // 23 right_shoulder_roll
 true, // 24 right_shoulder_yaw
 true, // 25 right_elbow
 true, // 26 right_wrist_roll
 true, // 27 right_wrist_pitch
 true, // 28 right_wrist_yaw
};
inline constexpr const char* revision = "om-joint-fault-all-20261123-v1";
}}
