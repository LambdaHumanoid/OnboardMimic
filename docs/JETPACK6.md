# JetPack 6 — v1.1 development / v1.1 开发

**Development branch: `feature/jetpack6`. Target-device acceptance is still pending.**
**开发分支：`feature/jetpack6`。尚待 JP6 目标设备验收。**

v1.1 adds a JP6 source-build path while retaining JP5 support. The released
v0.1.0 archives remain JP5-only; do not install those binaries or engine caches on JP6.

v1.1 增加 JP6 源码构建路径，同时保留 JP5 支持。已发布的 v0.1.0 安装包仍仅适用于
JP5；请勿在 JP6 上复用其二进制或引擎缓存。

## Target / 目标环境

G1 29-DOF, Orin NX 16 GB, Ubuntu 22.04 / L4T 36.x, CUDA 12.x,
TensorRT 10.x and ROS2 Humble. Start community testing with JP6.2 / CUDA12.6 /
TensorRT10.3. JP6.0 / TensorRT8.6 is outside this development target.

G1 29 自由度、Orin NX 16 GB、Ubuntu 22.04 / L4T 36.x、CUDA 12.x、
TensorRT 10.x、ROS2 Humble。社区测试优先使用 JP6.2 / CUDA12.6 / TensorRT10.3；
JP6.0 / TensorRT8.6 暂不在目标范围内。

The source installer keeps GCC10 for the pinned Unitree SDK. Humble uses its own
CycloneDDS packages; never overlay a Foxy workspace. Generate engines on the target
Jetson. A desktop GPU, even with matching Ubuntu, is not an equivalent JP6 device.

源码构建保留 GCC10，以兼容固定版本的 Unitree SDK。Humble 使用自身的 CycloneDDS
依赖，不叠加 Foxy 工作空间。引擎必须在目标 Jetson 上生成；相同 Ubuntu 的桌面
GPU 不等同于 JP6 设备。

## Build and inspect / 构建与检查

Use a fresh checkout and workspace. Stop motor control locally and reliably support
the robot before installation. These build/check commands do not start motor output.
Do not upgrade JetPack on a working robot just to try this branch.

使用新的源码目录与工作空间。安装前由现场人员停止运动控制，并可靠支撑机器人。
下列构建与检查命令不会启动运动输出。不要仅为测试本分支而升级在用机器人的 JetPack。

```bash
git clone --branch feature/jetpack6 https://github.com/LambdaHumanoid/OnboardMimic.git OnboardMimic-jp6
cd OnboardMimic-jp6
bash install_scripts/install_orin.sh
bash install_scripts/install_ros_dependencies.sh --workspace "$HOME/.local/share/onboardmimic/ros2_jp6_ws"
export ROS_SETUP=/opt/ros/humble/setup.bash
export UNITREE_ROS_SETUP="$HOME/.local/share/onboardmimic/ros2_jp6_ws/install/setup.bash"
bash onboardmimic/backends/sonic/deploy/build_orin.sh
bash onboardmimic/robots/unitree_g1/native_bridge/build.sh
bash onboardmimic/backends/sonic/deploy/onboard/build_models.sh
PYTHON=.venv/bin/python bash scripts/test.sh --core
.venv/bin/python scripts/validate_source.py --inference --benchmark-seconds 60
```

`var/source-validation.json` records environment versions, file hashes, library
loading, numerical checks and timing. Running without `--inference` only inspects
software/GPU information. `--host-test` permits desktop diagnostics but **cannot
qualify a Jetson installation**. A failed check replaces any earlier success report.
The inference check refuses to run while a recognized controller/bridge is active.

`var/source-validation.json` 记录环境版本、文件哈希、动态库加载、数值检查与时延。
不加 `--inference` 时只检查软件/GPU 信息。`--host-test` 允许桌面诊断，但**不能作为
Jetson 安装验证依据**。检查失败会覆盖旧的成功报告；检测到在运行的控制器或桥接时，
推理检查拒绝执行。

## Configure the console / 配置控制台

After target software checks pass, configure the idle console and API:
目标设备软件检查通过后，配置待机控制台与后台接口：

```bash
.venv/bin/python scripts/setup.py --source --configure-only --access lan --no-pico
```

The wizard checks the report against the current environment and artifact hashes,
then prints the console address. Preparation and SONIC start remain manual.
For an existing installation, follow [upgrade precautions](UPGRADE.md) and use
`--upgrade`; do not run two control stacks together. Re-run validation after changing
source, models, binaries or runtime dependencies.

向导核对报告中的环境和文件哈希，然后显示控制台地址。准备姿态与启动 SONIC 仍需
手动触发。已有部署请先阅读[升级说明](UPGRADE_zh.md)，并使用 `--upgrade`；不要
同时运行两套控制系统。源码、模型、二进制或运行依赖变化后需重新验证。

PICO is optional. Follow [PICO setup](CONTROL_EXTENSIONS.md) to build the ARM64 receiver and install
mapping dependencies, then use `--pico` instead of `--no-pico`. JP6 PICO end-to-end
acceptance is pending too; Planner checks do not qualify Pose mode.

PICO 为可选功能。按 [PICO 指南](CONTROL_EXTENSIONS_zh.md)构建 ARM64 接收器并安装映射依赖，再将
`--no-pico` 换为 `--pico`。JP6 的 PICO 端到端验收也待完成，Planner 检查不能替代 Pose。

## Community acceptance / 社区验收

Please share the commit (`git rev-parse HEAD`), hardware/JetPack versions,
`var/source-validation.json` and `var/source-check-logs/`. Inspect logs for personal
paths/network details before posting. Do not share console credentials or tokens.

请提供提交号（`git rev-parse HEAD`）、硬件/JetPack 版本、`var/source-validation.json`
与 `var/source-check-logs/`。公开日志前检查个人路径与网络信息，不要分享控制台密码或令牌。

1. Clean install: native libraries, engine generation, numerical checks and inference timing.
2. Console/API, restart and reboot: no automatic preparation or policy start.
3. On site with support: state feedback, preparation and emergency stop; check joint pose/sound.
4. With appropriate protection and space: Planner stand/walk/turn, stop and restart.
5. With calibrated PICO: tracking/reference agreement before motion, then Pose and tracking-loss behavior.
6. Sustained onboard load: thermal throttling, timing tails, memory, DDS feedback and disconnect handling.

1. 全新安装：动态库、引擎生成、数值检查与推理时延。
2. 控制台/API、服务重启与开机自启：不得自动回正或启动策略。
3. 现场可靠支撑：状态反馈、准备姿态与急停，观察关节姿态和声音。
4. 具备防护与活动空间：Planner 站立、行走、转向、停止及重新启动。
5. PICO 标定后：先核对人体追踪与参考动作，再测试 Pose 和追踪丢失行为。
6. 板载持续负载：降频、长尾时延、内存、DDS 反馈与断连处理。

See [local evidence and limits](JP6_VALIDATION.md). There is no JP6 release archive yet.
详见[本地测试结果与边界](JP6_VALIDATION.md)。目前尚未提供 JP6 正式安装包。
