# JetPack 6 installation / JetPack 6 安装

JP5 and JP6 share the `main` branch; platform dependencies and builds are selected separately.
JP5 与 JP6 共用 `main` 分支，分别选择平台依赖与构建环境。

The v0.2.0 JP6 package has completed ARM64 compilation, linking, startup-probe and library checks. Target inference runs during installation; follow [target-device acceptance](VALIDATION.md#target-device-acceptance--目标机验收) for commissioning.

v0.2.0 JP6 包已完成 ARM64 编译、链接、启动探针与动态库检查。安装时执行目标机推理检查，现场调试按[目标机验收流程](VALIDATION.md#target-device-acceptance--目标机验收)进行。

## Install / 安装

For G1 with an Orin NX 16 GB, Ubuntu22.04 / JetPack6 (L4T36.x), CUDA12 and
TensorRT10. Use JP6.2 or a matching CUDA 12.6 / TensorRT 10.3 installation. The JP6 runtime package contains AArch64 programs built for Ubuntu 22.04,
CUDA 12.6 and TensorRT 10.3. It generates TensorRT engines on the target Jetson at first installation.

适用于 G1 Orin NX 16GB、Ubuntu22.04 / JP6（L4T36.x）。预编译包面向 CUDA12.6 / TensorRT10.3；
首次安装在本机生成和验证 TensorRT 引擎。

### Runtime package / 运行包

Download both files from [v0.2.0](https://github.com/LambdaHumanoid/OnboardMimic/releases/tag/v0.2.0):

- [JP6 runtime / 运行包](https://github.com/LambdaHumanoid/OnboardMimic/releases/download/v0.2.0/OnboardMimic-v0.2.0-jp6-runtime.tar.gz)
- [JP6 models / 模型包](https://github.com/LambdaHumanoid/OnboardMimic/releases/download/v0.2.0/OnboardMimic-v0.2.0-jp6-models.tar.gz)
- [SHA256SUMS / 校验值](https://github.com/LambdaHumanoid/OnboardMimic/releases/download/v0.2.0/SHA256SUMS)

These links become available when the release assets are uploaded. Download the two JP6 archives listed above.
这些链接在发布制品上传后生效。下载上方两个 JP6 压缩包即可。

```bash
sha256sum --ignore-missing -c SHA256SUMS
mkdir OnboardMimic-jp6-release
tar -xzf OnboardMimic-v0.2.0-jp6-runtime.tar.gz -C OnboardMimic-jp6-release
tar -xzf OnboardMimic-v0.2.0-jp6-models.tar.gz -C OnboardMimic-jp6-release
cd OnboardMimic-jp6-release/OnboardMimic
bash install_scripts/install_jp6.sh --check
bash install_scripts/install_jp6.sh
```

The installer detects the JP6 manifest and reuses its native programs. It installs system dependencies,
builds engines locally, verifies inference, and installs OM and Lab together. Lab uses its own environment and services; the wizard configures recording, protected replay and the embedded page. Use `--no-lab` for an OM-only installation, or `--origin http://ROBOT_IP:8769` to select the browser address. See [Lab installation](../lab/README.md#v020-部署配置准备).
安装器识别 JP6 清单，复用包内控制器与桥接程序；安装系统依赖，在本机生成引擎并校验推理，
默认同时安装 OM 与 Lab，配置独立环境、数采、受保护回放和网页嵌入，只启动空闲后台。仅安装 OM 可加 `--no-lab`；用 `--origin http://ROBOT_IP:8769` 指定访问地址。详见 [Lab 安装](../lab/README.md#v020-部署配置准备)。

### Source installation / 源码安装

```bash
git clone --branch v0.2.0 https://github.com/LambdaHumanoid/OnboardMimic.git OnboardMimic-jp6
cd OnboardMimic-jp6
bash install_scripts/install_jp6.sh --check
bash install_scripts/install_jp6.sh
```

`--check` only checks the actual platform/runtime versions and reports missing TensorRT components. If components are missing, run without `--check` to install them automatically. Installation then
handles dependencies, ROS2 Humble, native programs, model download, engine
compilation, automatic software checks, PICO and console setup. It prints the
console URL at the end. Preparation and SONIC start remain manual.

`--check` 只检测实际平台和运行库版本，报告缺少的 TensorRT 组件；缺失时不加 `--check` 运行即可自动补齐。安装脚本随后自动完成依赖安装、ROS2 Humble、
原生程序编译、模型下载、引擎生成、软件检查、PICO 和控制台配置，最后显示控制台地址。
准备姿态和启动 SONIC 仍由用户手动操作。

Run as the normal robot user. The script requests sudo when needed; do not run the
whole command with sudo. Before installation, stop existing motion control with
reliable support on site. The installer does not stop another controller for you.

请以机器人普通用户运行，脚本需要时会调用 sudo，不要给整条命令加 sudo。
安装前请在现场可靠支撑机器人并停止现有运动控制；安装脚本不会替你停止其他控制器。

- Planner only / 只安装 Planner：`bash install_scripts/install_jp6.sh --no-pico`
- Update stopped existing services / 更新已停止的现有服务：add / 添加 `--upgrade`，see / 参见 [upgrade guide / 升级指南](UPGRADE.md).

System Python is used so Conda does not hide JetPack libraries. Detection accepts
L4T36.x / TensorRT10.x patch versions and does not require a specific carrier-board
label. Missing TensorRT10 libraries, system Python bindings and build tools are installed from the matching NVIDIA Jetson apt source before validation. Matching installed versions are preserved. Conflicting versions or missing CUDA components still produce a clear error before lengthy builds.

使用系统 Python，避免 Conda 遮蔽 JetPack 运行库。检测允许 L4T36.x / TensorRT10.x
的小版本差异，不要求载板名称完全一致。缺少的 TensorRT10 运行库、系统 Python 绑定和编译工具会在验证前从对应 NVIDIA Jetson 源自动安装；已有匹配版本保留。版本冲突或 CUDA 本身缺失仍会在长时间编译前明确报错。

### Validated hardware and software / 已验证软硬件配置

JP6 v0.1.1 real-robot validation passed on **Unitree G1 with Jetson Orin NX 16 GB,
Ubuntu 22.04, JetPack 6 / L4T 36.4.7 and CUDA 12.6**.
The recorded platform fields are listed in [validation scope](VALIDATION.md); the v0.2.0 binary profile uses TensorRT 10.3.

JP6 v0.1.1 真机验收已在以下配置上通过：**Unitree G1、Jetson Orin NX 16 GB、
Ubuntu 22.04、JetPack 6 / L4T 36.4.7、CUDA 12.6**。
该次记录的平台字段见[验证范围](VALIDATION.md)；v0.2.0 预编译包使用 TensorRT 10.3。

The installer requires TensorRT 10. If it is missing, the installer obtains it
from the matching Jetson repository and builds engines on the device. An existing
ROS2 Humble installation can be reused; Unitree messages use a separate workspace.
The device needs access to the package repositories.

安装器要求 TensorRT 10；缺失时会从匹配的 Jetson 软件源安装，并在本机生成引擎。
已有 ROS2 Humble 可以复用，宇树消息包使用独立工作空间；设备需能访问软件源。

## Tell us three things / 社区反馈三件事就够了

1. **Installation:** did installation finish, and can you open the console?
2. **Planner:** can you start it and control standing, walking and turning normally?
3. **PICO Pose:** can it start and follow your movements normally?

1. **能装上：**安装是否完成，控制台能否打开。
2. **Planner 能用：**能否启动，站立、行走和转向是否正常。
3. **PICO Pose 能用：**能否启动，动作跟随是否正常。

No benchmark report, reboot campaign or long-duration test is required from
volunteers. Just tell us your JetPack version and these three results. If something
fails, send the error text or a screenshot; we will help investigate.

不要求志愿者提交性能报告、反复重启或做长时间压力测试。告诉我们 JetPack 版本和
上述三项结果即可。遇到问题时，把报错文字或截图发给我们，我们协助排查。

Automatic diagnostic files are saved in `var/source-validation.json` and
`var/source-check-logs/`; share them only if needed, after checking for private
information. The [validation scope](VALIDATION.md) distinguishes installer checks from acceptance
of the exact target build; these are not additional volunteer requirements.

自动诊断结果位于 `var/source-validation.json` 与 `var/source-check-logs/`，需要排查时
再提供，分享前检查私人信息。[验证范围](VALIDATION.md)区分安装检查和具体目标版本的验收；完整技术检查由维护者
负责，不是额外要求社区用户执行的任务。
