# 使用 G1 + SONIC 配置构建 OnboardMimic

> v1.1 的 JP6 开发流程见 [JetPack 6](JETPACK6.md)。下文 JP5 步骤针对已发布基线。

[English](SOURCE_BUILD.md) · 简体中文

> 对于支持的 G1 / JP5 配置，优先使用 [v0.1.0 预编译包](PREBUILT_zh.md)。本指南用于修改或重新构建控制器和引擎。

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
PYTHON=.venv/bin/python bash scripts/test.sh --all
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
PYTHON=.venv-test/bin/python bash scripts/test.sh
```

这些测试不需要 Jetson、机器人连接或电机输出。原生看门狗也可以在没有 ROS2 的情况下测试：

```bash
cmake -S onboardmimic/robots/unitree_g1/native_bridge -B /tmp/sonic-bridge-test -DBUILD_ROS_BRIDGE=OFF
cmake --build /tmp/sonic-bridge-test
/tmp/sonic-bridge-test/core_test
python3 onboardmimic/robots/unitree_g1/native_bridge/differential_test.py --reference onboardmimic/robots/unitree_g1/bridge/lowcmd_transport.py --binary /tmp/sonic-bridge-test/core_test
```

构建完成后按[首次部署指南](DEPLOYMENT_zh.md)配置，再[打开电脑或手机控制台](CONTROL_EXTENSIONS_zh.md)。
