# JetPack 6 — v1.1 community preview / v1.1 社区测试

Branch / 分支：`feature/jetpack6`。JP6 hardware testing is pending / 尚待 JP6 真机测试。

## Install / 安装

For G1 with an Orin NX 16 GB, Ubuntu22.04 / JetPack6 (L4T36.x), CUDA12 and
TensorRT10. JP6.2 is the preferred starting point. JP6.0 with TensorRT8.6 is not yet
covered. Use the source installer below, not the JP5 v0.1.0 prebuilt archives.

适用于 G1 的 Orin NX 16GB、Ubuntu22.04 / JetPack6（L4T36.x）、CUDA12、TensorRT10，
建议优先使用 JP6.2。JP6.0 / TensorRT8.6 暂不支持。请使用下面的源码安装入口，
不要下载 JP5 的 v0.1.0 预编译包。

```bash
git clone --branch feature/jetpack6 https://github.com/LambdaHumanoid/OnboardMimic.git OnboardMimic-jp6
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
information. Maintainer test evidence and remaining technical checks are in
[JP6 validation](JP6_VALIDATION.md), not additional volunteer requirements.

自动诊断结果位于 `var/source-validation.json` 与 `var/source-check-logs/`，需要排查时
再提供，分享前检查私人信息。[本地验证记录](JP6_VALIDATION.md)中的完整技术检查由
维护者负责，不是额外要求社区用户执行的任务。
