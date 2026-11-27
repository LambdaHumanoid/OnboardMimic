# OnboardMimic 文档 / Documentation

本目录描述当前源码支持的安装、使用和接口。OM 与 Lab 使用仓库根目录 `VERSION`，
同仓库发布、独立服务运行。请按源码、下载包和机载部署的版本标识选择对应指南。

These guides describe the current source. OM and Lab share the root `VERSION`
and run as independent services. Check the source, downloaded package and active
installation identities and use the matching guide.

## 安装与更新 / Installation and updates

JP5、JP6 安装向导默认同时安装 OM 与 Lab；仅需 OM 时使用 `--no-lab`。
Both platform wizards install OM and Lab by default; use `--no-lab` for OM only.

- 首次部署 / Deployment：[中文](DEPLOYMENT_zh.md) · [English](DEPLOYMENT.md)
- JP5 预编译包 / JP5 packages：[中文](PREBUILT_zh.md) · [English](PREBUILT.md)
- 源码构建 / Source builds：[中文](SOURCE_BUILD_zh.md) · [English](SOURCE_BUILD.md)
- [JetPack 6](JETPACK6.md) — JP6 v0.1.1 passed G1 acceptance; v0.2.0 packages / JP6 v0.1.1 已通过 G1 真机验收，提供 v0.2.0 安装包
- [按变更范围部署 / Deployment by change scope](INCREMENTAL_DEPLOYMENT.md)
- 完整安装迁移与回退 / Full migration and rollback：[中文](UPGRADE_zh.md) · [English](UPGRADE.md)
- 发布包构建 / Release packaging：[中文](RELEASE_BUILD_zh.md) · [English](RELEASE_BUILD.md)
- [验证范围 / Validation scope](VALIDATION.md)

## 使用 / Operation

- 控制台与 PICO / Controls and PICO：[中文](CONTROL_EXTENSIONS_zh.md) · [English](CONTROL_EXTENSIONS.md)
- [Lab 数采、数据处理和回放 / Recording, processing and replay](../lab/docs/README.md)
- [录制就绪条件 / Recording readiness](SONIC_RECORD_READY.md)
- [模式无关录制 / Mode-independent recording](SONIC_MODE_INDEPENDENT_RECORDING.md)
- [真机回放协议 / Physical replay protocol](SONIC_REFERENCE_REPLAY.md)

## 架构与接口 / Architecture and interfaces

- 架构 / Architecture：[中文](ARCHITECTURE_zh.md) · [English](ARCHITECTURE.md) · [图示 / Diagram](architecture/README.md)
- [原生桥接 / Native bridge](NATIVE_BRIDGE.md)
- [控制权 / Control ownership](CONTROL_AUTHORITY.md)
- [WBC 异常停止保护 / WBC abnormal-stop protection](WBC_PROTECTION.rst)
- [项目定位与来源 / Project scope and provenance](PROJECT.md)
- [部署贡献 / Deployment contributions](CONTRIBUTIONS.md)
