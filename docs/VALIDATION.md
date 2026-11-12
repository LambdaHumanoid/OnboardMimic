# Validation scope / 验证范围

OnboardMimic v0.1.0 has completed the full deployment workflow on a physical G1 with the supported configuration: installation, GPU numerical checks, console access, reboot readiness, posture preparation, emergency stop and operator motion acceptance. The package records its source commit and per-file hashes. Installation repeats software and inference checks on the target device.

> OnboardMimic v0.1.0 已在支持配置的 G1 真机上完成全流程部署验证，涵盖安装、GPU 数值检查、控制台访问、开机就绪、准备姿态、急停和现场运动验收。安装包记录源码提交和逐文件哈希；安装时在目标设备上重新执行软件与推理检查。

## Offline checks / 离线检查

- Core lifecycle, command leases, feedback, failover and adapter contracts.
- Strict startup admission, advisory runtime inspection failures, bounded diagnostics,
  and preserved emergency-stop, feedback and joint-fault protections under injected inspection errors.
- Installation and upgrade in isolated directories, preserving credentials and devices; rollback on failure and rejection of active or custom installations.
- Canonical service definitions, shared control ownership and read-only PICO setup.
- Desktop/mobile controls and translations using mock backends.
- Package/source hashes, ARM64 build provenance, and consistent documentation links.

> 离线验证覆盖核心生命周期、指令租约、反馈、故障切换与适配器契约；隔离目录安装升级、凭据和设备配置保留、失败回滚、拒绝运行中或自定义安装；标准服务定义、共享控制权与只读 PICO 配置；模拟后端下的电脑/手机控制与翻译；安装包/源码哈希、ARM64 构建来源和文档链接一致性。

> 同时覆盖严格的启动准入、运行中检查异常只告警、诊断信息限量，以及注入检查异常后急停、反馈与关节故障保护仍然有效。

Run the [source-guide checks](SOURCE_BUILD.md). No robot is required for them.

> 按[源码指南](SOURCE_BUILD_zh.md)运行检查，不需要连接机器人。

## Target-device acceptance / 目标机验收

Validate the exact package with **G1 29-DOF body, Orin NX 16 GB, L4T 35.3.1, CUDA 11.4 and TensorRT 8.5.2**:

1. Clean installation, dependency checks and model/engine numerical validation.
2. Service migration, desktop/mobile access and receive-only PICO setup.
3. Reboot: console/API and optional receiver become ready; no automatic motion.
4. With onsite support: posture preparation and emergency stop.
5. Operator acceptance of Planner walking and PICO Pose teleoperation.

> 使用 **G1 29 自由度本体、Orin NX 16 GB、L4T 35.3.1、CUDA 11.4、TensorRT 8.5.2** 核验完整安装、依赖与模型/引擎数值；服务升级、电脑/手机页面和 PICO 接收；开机只就绪不运动；现场支撑下的准备姿态和急停；Planner 行走与 PICO Pose 遥操。

Record source commit, asset hashes, build reports, platform versions and operator results together. Model/backend attribution and licenses are preserved independently of product naming.

> 将源码提交、制品哈希、构建报告、系统版本和现场验收结果一起记录。项目名称变化不影响模型、后端及第三方许可证的归属。

[Deployment](DEPLOYMENT.md) · [Controls](CONTROL_EXTENSIONS.md) · [Upgrade](UPGRADE.md)

[首次部署](DEPLOYMENT_zh.md) · [控制说明](CONTROL_EXTENSIONS_zh.md) · [升级](UPGRADE_zh.md)

## v0.1.1 installation update / 安装更新

v0.1.1 retains the controller binaries, models and engines from v0.1.0. Its updated installer passed isolated installation, 212 offline tests and 30 model numerical checks on a physical G1.

> v0.1.1 沿用 v0.1.0 的控制程序、模型和引擎。更新后的安装器已在 G1 真机通过独立安装、212 项离线测试和 30 组模型数值检查。
