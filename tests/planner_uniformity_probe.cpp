// Actual on-board TensorRT planner; no G1Deploy, DDS, sockets or motor outputs.
#include "g1/g1_deploy_onnx_ref/include/localmotion_kplanner_tensorrt.hpp"
#include <nlohmann/json.hpp>
#include <stdexcept>

int main(int argc, char** argv) {
    try {
        if (argc != 2) return 2;
        PlannerConfig config;
        config.model_path = argv[1];
        config.version = 2;
        LocalMotionPlannerTensorRT planner(false, 0, config);
        planner.planner_state_.enabled = true;
        if (!planner.Initialize({1., 0., 0., 0.}, default_angles)) return 3;
        struct Case { const char* name; int mode; float speed; bool extended; };
        const Case cases[] = {
            {"idle_before", 0, 0.f, false},
            {"slow_030", 1, .3f, true},
            {"idle_after_slow", 0, 0.f, false},
            {"slow_040", 1, .4f, true},
            {"slow_050", 1, .5f, true},
            {"slow_negative", 1, -.1f, false},
            {"normal_walk", 2, .3f, false},
            {"slow_zero_speed", 1, 0.f, false},
            {"slow_030_again", 1, .3f, true},
            {"idle_final", 0, 0.f, false},
        };
        nlohmann::json rows = nlohmann::json::array();
        auto motion = std::shared_ptr<const MotionSequence>(
            &planner.planner_motion_50hz_, [](const MotionSequence*) {});
        for (const auto& item : cases) {
            if (!planner.UpdatePlanning(0, motion, item.mode, item.speed, -1.f,
                    {item.speed > 0.f ? 1.f : 0.f, 0.f, 0.f}, {1.f, 0.f, 0.f})) {
                throw std::runtime_error(std::string("Planner failed: ") + item.name);
            }
            auto& output = planner.planner_motion_50hz_;
            const int n = output.timesteps;
            if (item.extended ? n != 106 : (n != 60 && n != 66 && n != 73)) {
                throw std::runtime_error(std::string("Unexpected horizon: ") + item.name);
            }
            for (int frame = 0; frame < n; ++frame) {
                for (int joint = 0; joint < 29; ++joint) {
                    if (!std::isfinite(output.JointPositions(frame)[joint]) ||
                        !std::isfinite(output.JointVelocities(frame)[joint])) {
                        throw std::runtime_error("Nonfinite planner output");
                    }
                }
            }
            rows.push_back({{"case", item.name}, {"mode", item.mode},
                {"input_speed", item.speed}, {"frames_50hz", n},
                {"model_us", planner.last_timing_.model_duration.count()}});
        }
        std::cout << "ONBOARD_PLANNER_PROBE " << nlohmann::json({
            {"passed", true}, {"motor_output", false}, {"cases", rows}}).dump() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
