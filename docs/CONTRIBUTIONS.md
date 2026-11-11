# Deployment contributions / 部署贡献

**SONIC supplies the robot’s cerebellum.** The pretrained models, motion planner, motion tracking policy, and native controller come from upstream, which already supports onboard deployment. Our contribution is a deployment workflow the community can inspect, install, and operate: onboard integration, reusable packages, control interfaces, and safeguards. The G1 / JP5 stack is the current target for this work.

> **SONIC 提供机器人的“小脑”。** 预训练模型、运动规划器、动作跟踪策略和原生控制器来自上游，官方也已支持板载部署。我们的贡献是社区可以查看、安装和操作的部署流程：板载集成、可复用安装包、控制接口和保护检查。目前面向 G1 / JP5 环境。

| Our work | What it does for deployment |
| --- | --- |
| **Onboard runtime and local control** | Run the planner and policy on the G1's Orin NX, connect the native controller to the robot through a local ROS2/ZMQ bridge, and expose status, preparation, policy start, movement, and stop through an authenticated HTTP API and CLI. See the [runtime](../onboardmimic/core/runtime.py) and [command client](../scripts/control.py). |
| **Startup and runtime safeguards** | Check known conflicting controllers and bridge readiness before launch; enforce the prepare/start sequence; reject stale or repeated requests; expire walking commands when updates stop; terminate the owned controller on invalid feedback, motor faults, or excessive joint speed. See the [lifecycle checks](../onboardmimic/core/lifecycle.py) and [command lease](../onboardmimic/core/commands.py). |
| **Prebuilt deployment packages** | Package the controller, libraries, models, and engines; pin model versions and hashes; check the target environment and inference outputs during installation. See the [prebuilt installer](../install_scripts/install_prebuilt.sh). |
| **JP5 / TensorRT 8.5 compatibility** | Convert the planner's ONNX operators and shapes, adapt native TensorRT APIs and integer inputs, and provide a build path for the documented JP5 stack. See the [model build pipeline](../onboardmimic/backends/sonic/deploy/onboard/build_models.sh) and [native implementation](PROJECT.md). |
| **Inference configuration and validation** | Use FP32 engines with TF32 disabled, give encoder/policy CUDA streams higher priority than the planner, compare converted models and engine outputs against their references, and provide benchmarks and offline regression tests. See [validation scope and results](VALIDATION.md). |

| 我们的工作 | 对部署的作用 |
| --- | --- |
| **板载运行与本机控制** | 在 G1 的 Orin NX 上运行 planner 和 policy，通过本机 ROS2/ZMQ 桥接连接原生控制器与机器人，以需认证的 HTTP API 和 CLI 提供状态、准备、启动、移动与停止。见[运行时](../onboardmimic/core/runtime.py)和[指令客户端](../scripts/control.py)。 |
| **启动与运行保护** | 启动前检查已知控制器冲突及桥接状态，约束准备/启动顺序，拒绝过期或重复请求，让未续租的行走指令到期，在反馈无效、电机故障或关节超速时终止本会话控制器。见[生命周期检查](../onboardmimic/core/lifecycle.py)和[指令租约](../onboardmimic/core/commands.py)。 |
| **预编译部署包** | 打包控制器、库、模型和引擎，固定模型版本和哈希，安装时核对目标环境与推理输出。见[预编译安装器](../install_scripts/install_prebuilt.sh)。 |
| **JP5 / TensorRT 8.5 兼容** | 转换 planner ONNX 算子和形状，适配原生 TensorRT API 与整型输入，为文档中的 JP5 环境提供构建流程。见[模型构建流程](../onboardmimic/backends/sonic/deploy/onboard/build_models.sh)和[原生实现说明](PROJECT.md)。 |
| **推理配置与验证** | 使用禁用 TF32 的 FP32 引擎，encoder/policy CUDA 流优先级高于 planner，将转换后的模型和引擎输出与参考结果对比，提供基准测试和离线回归测试。见[验证范围与结果](VALIDATION.md)。 |

The native controller provides three-second initialization, a one-second handoff and low-level feedback protections. These mechanisms are integrated with the local API lifecycle and deployment checks. See [project scope and provenance](PROJECT.md).

Conflict detection checks the services and processes listed in the code and the dedicated bridge ports. Another deployment may need those lists adjusted; this is not system-wide isolation from every possible motor publisher. See the [deployment guide](DEPLOYMENT.md).

> 原生控制器提供 3 秒初始化、1 秒接管过渡和底层反馈保护，已与本机 API 生命周期及部署检查集成。见[项目范围与来源](PROJECT.md)。
>
> 冲突检测检查代码中列出的服务、进程及专用桥接端口；其他部署环境可能需要调整这些列表。它不能从系统层面隔离所有可能的电机指令发送者。见[部署指南](DEPLOYMENT_zh.md)。
