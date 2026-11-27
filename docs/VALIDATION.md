# Validation scope / 验证范围

Validation applies to a specific source, model, build and platform combination.
Record the source commit or complete source-manifest identity, native build reports,
model/engine hashes, platform versions and operator observations together.

验证结论绑定具体源码、模型、构建和平台。每次发布应同时记录提交或完整源码快照标识、
原生构建报告、模型/引擎哈希、平台版本及本次现场结果。

## Validated platforms and installation paths / 已验证平台与安装路径

**JP5 v0.2.0 and JP6 v0.1.1 have completed real-robot testing on Unitree G1.**

**JP5 v0.2.0 与 JP6 v0.1.1 均已完成 Unitree G1 真机测试。**

The v0.2.0 JP6 runtime has completed ARM64 compilation, linking, QEMU startup
probes, library resolution and archive checks. Installation generates and validates
engines on the target. Commission the device using the acceptance workflow below.

v0.2.0 JP6 运行包已完成 ARM64 编译、链接、QEMU 启动探针、动态库和归档检查。
安装器在目标机生成并校验引擎，现场调试按下方目标机验收流程进行。

- G1 29-DOF body, Orin NX 16 GB, JP5 / L4T 35.3.1, CUDA 11.4 and TensorRT 8.5.2:
  v0.2.0 completed target installation, 522 controller tests, 115 Lab tests and
  Encoder/Decoder/Planner inference checks. The onsite operator reported successful robot testing.
- G1, Orin NX 16 GB, Ubuntu 22.04, JP6 / **L4T 36.4.7**, **CUDA 12.6**:
  v0.1.1 passed real-robot acceptance on this configuration and uses the
  [JP6 installation path](JETPACK6.md). The historical record identifies the
  platform fields listed here. The v0.2.0 binary profile uses TensorRT 10.3.
- Record workstation tests, Jetson inference and onsite motion checks separately.
  Generate TensorRT engines for the target Jetson environment.

JP5 v0.2.0 已通过目标机安装、522 项控制端测试、115 项 Lab 测试及三套模型推理检查，现场用户确认真机测试正常。
JP6 v0.1.1 真机验收配置为 G1、Orin NX 16 GB、Ubuntu 22.04、**L4T 36.4.7、CUDA 12.6**。
历史记录标识上述平台字段；v0.2.0 预编译包使用 TensorRT 10.3。
JP6 使用独立依赖与构建路径，引擎在目标 Jetson 环境生成。

## Software checks / 软件检查

[Source-build checks](SOURCE_BUILD.md) cover lifecycle and control ownership,
feedback freshness, command expiry, protection, installer behavior, source identity
and UI behavior. Lab checks cover recording eligibility, camera/state requirements,
LeRobot export, replay contracts and independent service lifetimes.

[源码检查](SOURCE_BUILD_zh.md)覆盖控制生命周期、反馈与权限、保护、安装和界面；
Lab 检查覆盖录制就绪、相机和 state、数据处理、保存格式、回放契约及独立服务。
模拟控制器、软件关节后端和离线测试用于软件逻辑验证，物理动作按下方现场流程检查。

## Target-device acceptance / 目标机验收

1. Check exact platform dependencies, source/binary identity, models and inference outputs.
2. Check service installation, desktop/mobile access and optional receive-only PICO setup.
3. Verify boot opens idle services without starting robot motion.
4. With onsite support, verify preparation, operator stop and emergency stop.
5. Validate the release's enabled Planner/Pose, recording/video, data processing and
   replay paths. Replay includes first-frame entry and return to Planner.
6. Validate enabled protective actions on supported hardware. Record trajectory
   completion, observed ground contact and measured impact separately.

现场验收按安装、空闲启动、控制、数采、数据处理与回放逐项记录。保护动作单独验证；
分别记录服务状态、接口结果、仿真结果和现场运动观察。

[Deployment / 部署](DEPLOYMENT.md) · [Controls / 控制](CONTROL_EXTENSIONS.md) ·
[Protection / 保护](WBC_PROTECTION.rst) · [Deployment updates / 更新](INCREMENTAL_DEPLOYMENT.md)
