# OnboardMimic: positioning, scope and SONIC provenance / 定位、范围与 SONIC 来源

## Agreed identity / 已确定的项目定位

| Item / 项目 | Agreed wording / 确定表述 |
| --- | --- |
| Project name / 正式名称 | **OnboardMimic** |
| Positioning / 定位 | **人形机器人的板载 S0 平台** / **An Onboard S0 Platform for Humanoid Robots** |
| Main slogan / 主要宣传语 | **板载优秀的控制器，让它更好用** / **Bring great controllers onboard.** |
| Team / 团队署名 | **By LambdaHumanoid** |
| Authors / 作者 | **[Yiming Jiang](https://iridescentjiang.github.io/) · [Yilun Chen](https://yilunchen.com/about/)\* · [codex](https://openai.com/codex/)** |
| Naming convention / 命名约定 | Display and repository name: `OnboardMimic`; Python package and code directory: `onboardmimic` / 展示与仓库名称使用前者，Python 包与代码目录使用后者 |

\* Project Leader

OnboardMimic integrates existing humanoid motion controllers on the robot itself,
providing unified deployment, runtime management and teleoperation interfaces for
low-latency motion control and outdoor mobile teleoperation. It currently supports
Unitree G1 and SONIC.

> OnboardMimic 将已有的人形运动控制器集成到机器人本机，提供统一的部署、运行管理与遥操接口，
> 面向低时延运动控制和室外移动遥操。当前支持 Unitree G1 与 SONIC。

Here, S0 means the low-level motion layer responsible for balance, whole-body
execution and teleoperation. The platform's contribution is deployment and
integration; controller algorithms and pretrained models remain attributed to
their original authors. SONIC is the first backend. Future supported controllers
must also run onboard, with robot and backend adapters keeping the platform
independent of a particular controller. Upper-level task planning remains outside
this layer.

> S0 指负责平衡、全身动作执行与遥操的底层运动层。平台贡献是部署、运行管理和统一接入；
> 控制算法与预训练模型明确归属原作者。SONIC 是首个后端，后续接入的控制器也以板载运行为要求，
> 通过本体与后端适配器支持更多组合。上层任务规划由其他系统提供。

Low latency refers to the local motion-control loop. Remote commands and video
still depend on the network. Outdoor mobile teleoperation is a target use case,
not a claim that every outdoor condition has been qualified.

> 低时延指机器人本机运控闭环；远程指令和视频仍受网络影响。室外移动遥操是面向的使用场景，
> 具体环境的适用范围以实际验证为准。

The project directory is `OnboardMimic/`, and the Python package is `onboardmimic`.
README, documentation and both consoles use the identity above. Source development
is on the `main` branch of the existing GitHub repository. See the
[architecture](ARCHITECTURE.md), [source guide](SOURCE_BUILD.md) and
[validation scope](VALIDATION.md).

> 工程目录为 `OnboardMimic/`，Python 包为 `onboardmimic`。README、文档和电脑/手机页面
> 使用上述统一名称与定位。源码位于 GitHub 仓库的 `main` 分支。参见[架构](ARCHITECTURE_zh.md)、[源码构建](SOURCE_BUILD_zh.md)和[验证范围](VALIDATION.md)。

## Source provenance / 源码来源

The layout reference is NVIDIA's `NVlabs/GR00T-WholeBodyControl` at `45c03facbf848e0c743091b7d9c17952096ff908`. This is **not** a claim that our deployment sources are an unmodified copy of that commit.

> 目录布局参考 NVIDIA `NVlabs/GR00T-WholeBodyControl` 的 `45c03facbf848e0c743091b7d9c17952096ff908` 提交；这**不表示**本项目部署源码是该提交的未修改副本。

The native source here is the tested SONIC deployment snapshot from our onboard deployment, including its ZMQ bridge protocol, initialization/handoff and feedback safety changes. The original imported deployment snapshot did not retain an upstream Git commit identity; we cannot supply an exact upstream merge base. `source_manifest.json` records the imported native source hashes before release packaging changes. Existing NVIDIA notices remain in place. The ROS2 bridge and HTTP adapter are the deployment integration used by this project.

> 本项目原生源码来自经过验证的 SONIC 板载部署快照，包含 ZMQ 桥接协议、初始化/接管与反馈保护修改。导入快照未保留上游 Git 提交标识，因此无法提供准确的上游合并基点。`source_manifest.json` 记录发布打包修改前的原生源码哈希；原有 NVIDIA 声明保留。ROS2 桥接与 HTTP 适配器用于本项目的部署集成。

The release intentionally preserves `onboardmimic/backends/sonic/deploy/src`, `cmake`, `policy/release`, `planner/target_vel/V2`, `reference/example`, and `target/release`. Jetson-specific utilities are grouped under `onboardmimic/backends/sonic/deploy/onboard`. This is a deployment-only distribution, not the complete training/VR repository.

> 发布保留 `onboardmimic/backends/sonic/deploy/` 下的 `src`、`cmake`、`policy/release`、`planner/target_vel/V2`、`reference/example` 和 `target/release`。Jetson 专用工具集中在该目录的 `onboard/` 下。这是部署发行版，不包含完整训练/VR 仓库。

JP5 native implementation / JP5 原生实现：

- TensorRT 8.5 enum/API compatibility and planner integer host buffers.
- GCC 10 bit-pattern finite checks via memcpy (retains behavior under fast-math).
- Disable TF32 on native engine rebuilds; strict numerical validation before cache promotion.
- High-priority CUDA streams for encoder/policy, low priority for planner.
- Real-hardware safety remains enabled even for loopback robot endpoints.
- Existing three-second initialization, one-second bumpless handoff, measured feedback, stale-input protection and UDP LowCmd transport are retained.

> 对应实现包括：TensorRT 8.5 枚举/API 兼容与 planner 整型主机缓冲；使用 memcpy 的 GCC 10 位模式有限值检查（保留 fast-math 下的行为）；原生引擎重建禁用 TF32，缓存投入使用前严格验证数值；encoder/policy 使用高优先级 CUDA 流，planner 使用低优先级；即使机器人端点为 loopback，真机保护仍启用；保留 3 秒初始化、1 秒平滑接管、实测反馈、过期输入保护和 UDP LowCmd 传输。

Deployment tooling / 部署工具：

- Relocatable build/runtime paths instead of a particular home directory.
- Pinned model repository revision and SHA-256 validation; downloadable SDK and build dependencies.
- Expiring Planner motion requests, a 0.5 m/s default target speed, and a local authenticated API.
- A bilingual setup wizard, ROS2/network configuration, service migration and an explicit command client.
- Offline lifecycle/transport tests and release portability tests.
- No robot addresses, login secrets, tokens, calibration captures, scanned environments, running services or TensorRT binaries copied into Git.

> 部署工具支持可迁移的构建/运行路径，固定模型仓库版本与 SHA-256 校验，以及 SDK 和构建依赖下载；Planner 请求会到期，默认目标速度为 0.5 m/s，并提供需认证的本机 API。配套中英文安装向导、ROS2/网络配置、服务迁移、显式指令客户端、离线生命周期/传输测试和发布包可移植性测试。不将机器人地址、登录凭据、token、标定采集、扫描环境、运行服务或 TensorRT 二进制提交到 Git。

Downloaded dependencies retain their licenses: Unitree SDK2, cppzmq (MIT), ONNX Runtime, CUDA/TensorRT and the robot ROS2/SDK environment. Model weights use NVIDIA's model license. The small example reference motions retain the original upstream distribution terms.

> 下载的依赖保留各自许可证，包括 Unitree SDK2、cppzmq（MIT）、ONNX Runtime、CUDA/TensorRT 和机器人 ROS2/SDK 环境。模型权重遵循 NVIDIA 模型许可证，少量示例参考动作保留上游分发条款。

## Project scope and naming / 项目范围与命名

The supported control interfaces are Planner and PICO Pose. PICO mapping source
hashes and third-party licenses are recorded
in `onboardmimic/backends/sonic/pico/pose_sources.json` and its `licenses/` directory.

> 支持的控制接口是 Planner 与 PICO Pose。PICO 映射源码哈希及第三方许可证记录在 `onboardmimic/backends/sonic/pico/pose_sources.json` 及其 `licenses/` 目录。

The agreed public name is **OnboardMimic**; the controller backend remains **SONIC**. The `onboardmimic` systemd unit names and
`~/.config/onboardmimic` paths are the current installation identifiers used by the
code. Keep those literal names when following service commands. G1 admission checks other motion services, controller processes and DDS writers. Imported source hashes,
upstream notices and dependency licenses are preserved.

> 公开名称为 **OnboardMimic**，控制器后端仍称 **SONIC**。当前安装标识为 `onboardmimic` systemd 服务名和 `~/.config/onboardmimic` 路径，执行命令时须保留这些字面名称。G1 准入检查其他运动服务、控制器进程与 DDS 发送源。导入源码哈希、上游声明和依赖许可证均保留。
