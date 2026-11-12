# Orin NX 预编译包

[English](PREBUILT.md) · 简体中文

预编译版本面向 **G1 29-DOF 身体 / Jetson Orin NX 16GB / Ubuntu 20.04 / L4T 35.3.1 / CUDA 11.4 / TensorRT 8.5.2**，不支持手部执行器。其他 GPU、8GB Orin NX、JetPack 6 或 TensorRT 10 尚未验证，需要源码适配、构建与目标设备验证。

上述配置仍是真机验证基线。新版源码安装器也允许 **L4T35.x、CUDA11.4 及之后的 11.x、定制载板名称**，保留 Ubuntu20.04、ARM64 Orin 和 16GB 级别内存要求。随包引擎仍要求 TensorRT8.5.2。相近配置继续进行动态库加载与模型数值验证，通过版本检查不等于所有设备都已验收。现有 v0.1.0 压缩包在重新打包前仍保留原检查。

## 安装前准备

- 在机器人普通用户下操作，需要网络和 sudo 权限；`python3-venv` 与 venv 内的 pip 由脚本自动安装/更新。
- 向导自动查找 ROS2 Foxy 和 `unitree_hg`、`unitree_api` 工作空间；缺失时提示运行[依赖修复脚本](#缺少-ros2-或宇树消息包)。
- 安装会执行 GPU 推理验证，请在 SONIC 和其他 GPU 密集任务停止时进行。

## 下载与解压

下载完整的 [OnboardMimic v0.1.0 安装包](https://github.com/LambdaHumanoid/OnboardMimic/releases/tag/v0.1.0)。运行包、模型包和引擎包共同组成一次安装，需要一起解压。

- `OnboardMimic-v0.1.0-runtime.tar.gz`：源码、操作脚本、ARM64 控制程序、ONNX Runtime / CycloneDDS 运行库与许可证。
- `OnboardMimic-v0.1.0-models.tar.gz`：三个原始 ONNX、TensorRT 8.5 兼容 planner ONNX。
- `OnboardMimic-v0.1.0-engines.tar.gz`：三个 FP32 TensorRT 引擎。
- `prebuilt_manifest.json`：环境要求、来源、逐文件 SHA-256。
- `SHA256SUMS`：下载文件校验值。

安装包包含的功能以对应 Release 的说明和源码版本为准；最新源码的修改不会自动进入已有下载包。

私有仓库需要读取权限。已登录 GitHub CLI 的用户可一次下载：

```bash
mkdir onboardmimic-download
cd onboardmimic-download
gh release download v0.1.0 --repo LambdaHumanoid/OnboardMimic --pattern 'OnboardMimic-v0.1.0-*.tar.gz' --pattern prebuilt_manifest.json --pattern SHA256SUMS
sha256sum -c SHA256SUMS
```

在一个新目录中解压三个包，它们共同组成 `OnboardMimic/`。不要覆盖已有运行中的部署：

```bash
tar -xzf OnboardMimic-v0.1.0-runtime.tar.gz
tar -xzf OnboardMimic-v0.1.0-models.tar.gz
tar -xzf OnboardMimic-v0.1.0-engines.tar.gz
cd OnboardMimic
python3 scripts/setup.py
```

预留至少 **6 GB 可用空间**用于解压后的文件、依赖和原生缓存；下载压缩包另占空间。安装不编译 C++、不重新生成 TensorRT 引擎，但仍需安装少量依赖、校验文件、做纯推理数值验证，并生成原生缓存文件。

向导询问访问方式（`lan` 支持电脑和手机直连；`local` 使用 SSH 隧道）及是否启用 PICO。它会配置通信、启用控制台/API 开机自启并立即打开空闲后台，最后打印实际访问地址。**连接服务、准备姿态和启动 SONIC 仍由你手动操作。**

交给 agent 或非交互安装时：

```bash
python3 scripts/setup.py --yes --access lan --pico
# 不使用 PICO：将 --pico 换成 --no-pico
```

需要指定非标准工作空间时加 `--unitree-ros-setup /实际路径/install/setup.bash`。向导保留已有 token 和桥接附加参数；检测到控制器/指令桥接运行时拒绝安装，不停止其他程序。已有服务需要按[升级指南](UPGRADE_zh.md)停止后迁移。

## 向导的验证步骤

1. 安装 libzmq / Python venv 运行依赖。
2. 检查逐文件哈希、CPU / GPU 型号、内存、L4T / CUDA / TensorRT 版本，使用动态加载器检查控制器、原生指令桥接和状态接收器的 ELF 依赖，不执行这些程序的入口。
3. 创建独立 Python venv，自动安装兼容 Python 3.8 的 pip 25.0.1，再安装依赖；使用系统的 TensorRT 8.5.2 Python 包。
4. 运行基础离线测试（无需 SciPy）、encoder/decoder 数值检查、planner 24 组数值检查。选择 PICO 后额外安装 SciPy 并运行 PICO 测试。
5. 验证通过后，给三个原始引擎添加 SONIC 所需的缓存头；这一步是文件写入，不是重新编译引擎。

6. 自动选取机器人直连网口，生成 DDS/ROS 配置和服务；只启动空闲 API、网页及所选的只接收 PICO 服务。PICO 头显仍需按[指南](CONTROL_EXTENSIONS_zh.md#pico-setup)连接和标定。

**这些步骤不启动控制器或桥接，不释放原控制模式，不发送电机命令。** 验证期间不要启动其他控制程序或 GPU 密集任务。看到 `Console ready / 控制台就绪` 后，按打印地址打开网页，进入[手动启动步骤](DEPLOYMENT_zh.md#4-按网页顺序启动)；无需再编译或修改服务文件。

只读诊断用 `python3 scripts/setup.py --check`。隔离验收用 `--install-only`，只安装并验证、不改服务；之后用 `--configure-only` 完成配置。高级用户仍可单独运行 `install_scripts/install_prebuilt.sh [--with-pico]`，这条底层命令不配置服务，需要先设置 `ROS_SETUP` 和 `UNITREE_ROS_SETUP`。

## 缺少 ROS2 或宇树消息包

在机器人普通用户下执行：

```bash
bash install_scripts/install_ros_dependencies.sh
python3 scripts/setup.py
```

脚本按 [ROS Foxy 官方安装方式](https://docs.ros.org/en/foxy/Installation/Ubuntu-Install-Debians.html)补齐 apt 依赖，再参考 [Unitree ROS2](https://github.com/unitreerobotics/unitree_ros2)构建固定版本的 CycloneDDS 0.10.2、Foxy RMW、`unitree_hg` 和 `unitree_api`。源码提交固定在脚本中，工作空间位于 `~/.local/share/onboardmimic/ros2_ws`，不覆盖原工作空间，不构建或运行电机控制示例。需要网络、sudo 和额外编译时间。

系统 apt 源不可用时应先解决报错；脚本不会用忽略签名校验的方式绕过。Foxy 的依赖修复仅针对上述 Ubuntu 20.04 ARM64 配置，不表示支持任意系统。

如检查不通过，不要忽略报错后直接运行控制器；该机器可能需要源码构建。CPU/GPU 型号和版本匹配也不替代首次现场接管验证。

## 来源与适用范围

ARM64 程序从当前原生源码重新交叉编译，使用 GCC 10.5 和 JP5 / L4T 35.3.1 的头文件与系统库。打包时再修改 ELF 库搜索路径。源码哈希、编译信息和原始二进制哈希记录在 `native_build_report.json`，打包后哈希记录在 `prebuilt_manifest.json`。

模型和引擎身份根据发布清单核验。打包不代表通过目标机推理或真实运动验收，详见[验证范围](VALIDATION.md)；安装时会重新执行无电机输出的推理检查。

运行库、模型与源码的许可证随包提供。CUDA、TensorRT、ROS2、宇树机器人消息包和 Python SDK 仍使用目标机器现有环境，不随包重分发系统镜像。

已有旧部署时按[升级与回退](UPGRADE_zh.md)迁移服务，勿覆盖原目录。

包内包含原生指令桥接、状态接收器及 `bridge_build_report.json`。ROS2 Foxy 和宇树消息工作空间由目标环境提供；技术细节见[原生桥接说明](NATIVE_BRIDGE.md)。

## TensorRT 自动补齐

新版安装脚本会先识别 JetPack；JP5 缺少 TensorRT 时自动补齐 **8.5.2** 运行库与系统 Python 绑定。已有匹配版本保留，不自动升级或降级。软件源需可联网访问；版本冲突或对应 NVIDIA 源缺失时会说明原因。

已经下载的 v0.1.0 安装包不会随仓库代码更新。可在解压后的 `OnboardMimic/` 目录执行一次补装，再运行原来的安装向导，无需重新下载模型和引擎：

```bash
curl -fL https://raw.githubusercontent.com/LambdaHumanoid/OnboardMimic/main/scripts/install_tensorrt.py -o scripts/install_tensorrt.py
/usr/bin/python3 scripts/install_tensorrt.py --profile jp5
python3 scripts/setup.py
```

添加 `--check` 可只检查缺失组件；源码编译用户添加 `--development` 补齐头文件与 `trtexec`。该工具只补齐 TensorRT，不刷机、不切换 JetPack、不启动运动控制。CUDA 本身缺失时仍需安装当前 JetPack 对应的 CUDA 组件。
