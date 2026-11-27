# 使用 G1 + SONIC 配置构建 OnboardMimic

> JP6 同时提供运行包与源码安装器，见 [JetPack 6](JETPACK6.md)。下文构建步骤适用于 JP5。

[English](SOURCE_BUILD.md) · 简体中文

> 对于支持的 G1 / JP5 配置，优先使用 [v0.2.0 预编译包](PREBUILT_zh.md)。本指南用于修改或重新构建控制器和引擎。

使用[部署指南](DEPLOYMENT_zh.md)中支持的 G1 / JetPack 5 平台。原生程序编译与模型构建顺序执行。安装和离线验证不会启动机器人控制。

项目保留了熟悉的 `onboardmimic/backends/sonic/deploy/` 布局，便于查看、修改和重新构建适配代码。

```bash
git clone --branch main https://github.com/LambdaHumanoid/OnboardMimic.git OnboardMimic
cd OnboardMimic
bash install_scripts/install_orin.sh
.venv/bin/python download_from_hf.py
bash onboardmimic/backends/sonic/deploy/build_orin.sh
# 构建桥接前，按 docs/DEPLOYMENT_zh.md 查找并验证以下路径。
export ROS_SETUP=/opt/ros/foxy/setup.bash
export UNITREE_ROS_SETUP=/path/to/workspace/with/unitree_hg/install/setup.bash
bash onboardmimic/robots/unitree_g1/native_bridge/build.sh
bash onboardmimic/backends/sonic/deploy/onboard/build_models.sh
PYTHON=.venv/bin/python bash scripts/test.sh --core
# 可选 PICO 映射依赖和完整测试：
.venv/bin/python -m pip install -r onboardmimic/backends/sonic/pico/requirements.txt
PYTHON=.venv/bin/python bash scripts/test.sh --controller
```

模型来自固定版本的[官方 GEAR SONIC 模型仓库](https://huggingface.co/nvidia/GEAR-SONIC)，并进行 SHA-256 校验。已有匹配模型时，使用 `download_from_hf.py --models-dir /path/to/existing/onboardmimic/backends/sonic/deploy`。

## 仓库布局

适配器契约与扩展步骤见[架构说明](ARCHITECTURE_zh.md)。SONIC 原生源码、策略配置、参考动作和构建工具位于 `onboardmimic/backends/sonic/deploy/`；其中的 `onboard/` 仅包含模型转换、推理验证与缓存工具。G1 通信与桥接构建位于 `onboardmimic/robots/unitree_g1/`。通用运行状态和日志使用 `var/`，不提交到 Git。

本仓库提供控制器的板载部署与运行集成。当前 SONIC 后端支持 Planner 控制与可选的 PICO Pose 遥操。

## 离线测试

```bash
# Ubuntu 缺少 python3-venv 时先安装该组件。
python3 -m venv .venv-test
# 项目面向 Python 3.8，使用兼容的 pip 版本。
.venv-test/bin/python -m pip install --upgrade pip==25.0.1
.venv-test/bin/pip install -r requirements.txt -r onboardmimic/backends/sonic/pico/requirements.txt
PYTHON=.venv-test/bin/python bash scripts/test.sh --controller
```

这些测试不需要 Jetson、机器人连接或电机输出。原生看门狗也可以在没有 ROS2 的情况下测试：

```bash
cmake -S onboardmimic/robots/unitree_g1/native_bridge -B /tmp/sonic-bridge-test -DBUILD_ROS_BRIDGE=OFF
cmake --build /tmp/sonic-bridge-test
/tmp/sonic-bridge-test/core_test
python3 onboardmimic/robots/unitree_g1/native_bridge/differential_test.py --reference onboardmimic/robots/unitree_g1/bridge/lowcmd_transport.py --binary /tmp/sonic-bridge-test/core_test
```

构建完成后按[首次部署指南](DEPLOYMENT_zh.md)配置，再[打开电脑或手机控制台](CONTROL_EXTENSIONS_zh.md)。

## JP5 与 JP6 共用源码

生命周期、控制台、PICO 与 SONIC 的公共逻辑统一在 `main` 开发，不再维护两份
平台代码，也不需要逐项跨平台分支 cherry-pick。平台差异集中在 `platforms.py`、
依赖安装器与原生构建配置。CI 在 Python 3.8 和 3.10 下运行公共测试，包含两套
TensorRT API 和 Foxy/Humble 依赖选择。

两个平台从同一提交分别构建和验证。JP5（Ubuntu 20.04 / TensorRT 8.5 / Foxy）
与 JP6（Ubuntu 22.04 / TensorRT 10 / Humble）不能混用原生运行包或 TensorRT
引擎。v0.2.0 分别提供 JP5 与 JP6 运行包；JP6 文件名含 `-jp6`，
TensorRT 引擎在目标 Jetson 上生成。[JP6 指南](JETPACK6.md)包含运行包和源码两种安装方式。公共源码修改需按目标平台重新构建、安装后才会在
机器人上生效，合并代码不会自动更新服务。

## 配置 OM + Lab / Configure OM + Lab

完成原生构建和目标机推理验证后，使用统一向导配置 OM 与 Lab：
After native builds and target inference validation, configure both services through the unified wizard:

```bash
.venv/bin/python scripts/validate_source.py --inference
.venv/bin/python scripts/setup.py --source --configure-only --yes --access lan --origin http://ROBOT_IP:8769
```

按需添加 `--pico`；默认安装 Lab，`--no-lab` 仅配置 OM。Lab 环境独立安装，服务以空闲状态启动。
Add `--pico` when needed; Lab is included by default, while `--no-lab` selects OM only. Services start idle.
Lab 的独立测试和全链路联调见 [Lab 开发说明](../lab/README.md)。
