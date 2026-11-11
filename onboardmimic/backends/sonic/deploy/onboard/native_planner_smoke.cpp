// Native planner integration only: no G1Deploy, DDS publisher, socket or LowCmd.
#include "g1/g1_deploy_onnx_ref/include/localmotion_kplanner_tensorrt.hpp"
#include <filesystem>
int main(int argc,char** argv) {
 if(argc!=2)return 2;
 PlannerConfig config;config.model_path=argv[1];config.version=2;
 LocalMotionPlannerTensorRT planner(false,0,config);
 planner.planner_state_.enabled=true;
 if(!planner.Initialize({1.,0.,0.,0.},default_angles))return 3;
 if(planner.planner_motion_50hz_.timesteps<=0)return 4;
 std::cout<<"NATIVE_PLANNER_SMOKE_OK frames="<<planner.planner_motion_50hz_.timesteps<<std::endl;
 return 0;
}
