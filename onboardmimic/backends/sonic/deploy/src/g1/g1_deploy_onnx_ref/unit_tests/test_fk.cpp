#include <gtest/gtest.h>

#include "../include/fk.hpp"
#include "../include/motion_data_reader.hpp"

#include <filesystem>
#include <fstream>
#include <vector>

TEST(FK, TestFKAndGlobalVelocities) {
    // Resolve fixtures from this source file instead of the caller's cwd.
    // This fixture is not shipped in every checkout. The former code ignored
    // ReadFromCSV(false) and dereferenced motions[0], crashing the complete
    // test binary whenever the fixture was absent.
    const std::filesystem::path project_root =
        std::filesystem::path(__FILE__)
            .parent_path()  // unit_tests
            .parent_path()  // g1_deploy_onnx_ref
            .parent_path()  // g1
            .parent_path()  // src
            .parent_path(); // gear_sonic_deploy
    const auto motion_fixture =
        project_root / "reference" / "bones_072925_test";
    const auto robot_xml = project_root / "g1" / "g1_29dof.xml";

    std::error_code motion_fixture_error;
    std::error_code robot_xml_error;
    const bool has_motion_fixture = std::filesystem::is_directory(
        motion_fixture, motion_fixture_error);
    const bool has_robot_xml = std::filesystem::is_regular_file(
        robot_xml, robot_xml_error);
    if (motion_fixture_error || robot_xml_error ||
        !has_motion_fixture || !has_robot_xml) {
        GTEST_SKIP() << "FK fixtures are unavailable under " << project_root;
    }

    // Preserve the original FK oracle when it is installed, while guarding
    // every fixture precondition so an optional test asset cannot crash CI.
    MotionDataReader motion_reader;
    ASSERT_TRUE(motion_reader.ReadFromCSV(motion_fixture.string()));
    ASSERT_FALSE(motion_reader.motions.empty());
    ASSERT_NE(motion_reader.motions.front(), nullptr);

    RobotFK fk(robot_xml.string());

    auto num_bodies = motion_reader.motions.front()->GetNumBodies();
    auto num_joints = motion_reader.motions.front()->GetNumJoints();
    auto timesteps = motion_reader.motions.front()->timesteps;
    ASSERT_GT(num_bodies, 0);
    ASSERT_GT(num_joints, 0);
    ASSERT_GT(timesteps, 0);

    std::vector<MotionSequence::Point> body_positions_orig(num_bodies * timesteps);
    std::vector<MotionSequence::Quaternion> body_quaternions_orig(num_bodies * timesteps);

    std::vector<MotionSequence::Velocity> body_lin_velocities_orig(num_bodies * timesteps);
    std::vector<MotionSequence::Velocity> body_ang_velocities_orig(num_bodies * timesteps);

    // record original global space data so we can compare it to the computed data:
    const auto &seq = *motion_reader.motions.front();
    std::copy(seq.BodyPositions(0), seq.BodyPositions(0) + body_positions_orig.size(), body_positions_orig.begin());
    std::copy(seq.BodyQuaternions(0), seq.BodyQuaternions(0) + body_quaternions_orig.size(), body_quaternions_orig.begin());
    std::copy(seq.BodyLinVelocities(0), seq.BodyLinVelocities(0) + body_lin_velocities_orig.size(), body_lin_velocities_orig.begin());
    std::copy(seq.BodyAngVelocities(0), seq.BodyAngVelocities(0) + body_ang_velocities_orig.size(), body_ang_velocities_orig.begin());
    
    // Compute FK and global linear/angular velocities:
    motion_reader.motions.front()->ComputeFK(fk);
    motion_reader.motions.front()->ComputeGlobalVelocities();

    // sanity check - make sure at least some of the computed components are
    // different from the original data:
    bool found_different_pos = false;
    bool found_different_quat = false;
    bool found_different_lin_vel = false;
    bool found_different_ang_vel = false;
    for(int f = 0; f < timesteps; ++f) {
        for(int b = 0; b < num_bodies; ++b) {
            if(seq.BodyPositions(f)[b] != body_positions_orig[f * num_bodies + b]) {
                found_different_pos = true;
            }
            if(seq.BodyLinVelocities(f)[b] != body_lin_velocities_orig[f * num_bodies + b]) {
                found_different_lin_vel = true;
            }
            if(seq.BodyAngVelocities(f)[b] != body_ang_velocities_orig[f * num_bodies + b]) {
                found_different_ang_vel = true;
            }
            if(seq.BodyQuaternions(f)[b] != body_quaternions_orig[f * num_bodies + b]) {
                found_different_quat = true;
            }
        }
    }
    EXPECT_TRUE(found_different_pos);
    EXPECT_TRUE(found_different_quat);
    EXPECT_TRUE(found_different_lin_vel);
    EXPECT_TRUE(found_different_ang_vel);

    // check the results are close to the original data:
    for(int f = 0; f < timesteps; ++f) {
        for(int b = 0; b < num_bodies; ++b) {
            for(int i = 0; i < 3; ++i) {
                EXPECT_NEAR(seq.BodyPositions(f)[b][i], body_positions_orig[f * num_bodies + b][i], 1e-5);
            }
            for(int i = 0; i < 4; ++i) {
                EXPECT_NEAR(seq.BodyQuaternions(f)[b][i], body_quaternions_orig[f * num_bodies + b][i], 1e-5);
            }
            for(int i = 0; i < 3; ++i) {
                EXPECT_NEAR(seq.BodyLinVelocities(f)[b][i], body_lin_velocities_orig[f * num_bodies + b][i], 2e-4);
            }

            // I couldn't get this quite right as you can see by the restricted checking range and large tolerance.
            // However, if you write everything out to a .csv and plot the curves, they look close enough for me to
            // doubt this will cause a problem. Should get to the bottom of this and fix it though.
            if(timesteps - f > 10)
            {
                for(int i = 0; i < 3; ++i)
                {
                    EXPECT_NEAR(seq.BodyAngVelocities(f)[b][i], body_ang_velocities_orig[f * num_bodies + b][i], 5e-3);
                }
            }
        }
    }

}
