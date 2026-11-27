# Build OnboardMimic with the G1 + SONIC profile

> JP6 offers both a runtime package and a source installer; see [JetPack 6](JETPACK6.md). The build instructions below target JP5.

English · [简体中文](SOURCE_BUILD_zh.md)

> For the supported G1 / JP5 configuration, start with the [v0.2.0 prebuilt packages](PREBUILT.md). This guide is for modifying or rebuilding the controller and engines.

Use the supported G1 / JetPack 5 platform described in the [deployment guide](DEPLOYMENT.md). Run native and model builds sequentially. Installation and offline validation do not start robot control.

We kept the familiar `onboardmimic/backends/sonic/deploy/` layout so you can inspect, modify, and rebuild the adaptation.

```bash
git clone --branch main https://github.com/LambdaHumanoid/OnboardMimic.git OnboardMimic
cd OnboardMimic
bash install_scripts/install_orin.sh
.venv/bin/python download_from_hf.py
bash onboardmimic/backends/sonic/deploy/build_orin.sh
# Find/verify these paths using docs/DEPLOYMENT.md before building the bridge.
export ROS_SETUP=/opt/ros/foxy/setup.bash
export UNITREE_ROS_SETUP=/path/to/workspace/with/unitree_hg/install/setup.bash
bash onboardmimic/robots/unitree_g1/native_bridge/build.sh
bash onboardmimic/backends/sonic/deploy/onboard/build_models.sh
PYTHON=.venv/bin/python bash scripts/test.sh --core
# Optional PICO mapping and controller test suite:
.venv/bin/python -m pip install -r onboardmimic/backends/sonic/pico/requirements.txt
PYTHON=.venv/bin/python bash scripts/test.sh --controller
```

Models come from the pinned [official GEAR SONIC model repository](https://huggingface.co/nvidia/GEAR-SONIC), with SHA-256 checks. Have matching models already? Use `download_from_hf.py --models-dir /path/to/existing/onboardmimic/backends/sonic/deploy`.

## Repository layout

See [Architecture](ARCHITECTURE.md) for contracts and extension steps.
Native SONIC source, policy configuration, example references and build tools live
under `onboardmimic/backends/sonic/deploy/`. Its `onboard/` directory contains only
model conversion, inference validation and cache tools. G1 communication and
bridge builds live under `onboardmimic/robots/unitree_g1/`. Generic runtime state and
logs use `var/`, which is excluded from Git.

This repository provides onboard controller deployment and runtime integration. The current SONIC backend supports Planner control and optional PICO Pose teleoperation.



## Offline tests

```bash
# Ubuntu: install python3-venv first if it is missing.
python3 -m venv .venv-test
# This project targets Python 3.8; use a pip release that supports it.
.venv-test/bin/python -m pip install --upgrade pip==25.0.1
.venv-test/bin/pip install -r requirements.txt -r onboardmimic/backends/sonic/pico/requirements.txt
PYTHON=.venv-test/bin/python bash scripts/test.sh --controller
```

These tests do not require a Jetson, robot connection, or motor output.
The native watchdog can also be tested without ROS2:

```bash
cmake -S onboardmimic/robots/unitree_g1/native_bridge -B /tmp/sonic-bridge-test -DBUILD_ROS_BRIDGE=OFF
cmake --build /tmp/sonic-bridge-test
/tmp/sonic-bridge-test/core_test
python3 onboardmimic/robots/unitree_g1/native_bridge/differential_test.py --reference onboardmimic/robots/unitree_g1/bridge/lowcmd_transport.py --binary /tmp/sonic-bridge-test/core_test
```

After building, follow the [first-run deployment guide](DEPLOYMENT.md), then [open the desktop or mobile console](CONTROL_EXTENSIONS.md).

## One source tree for JP5 and JP6

Develop shared lifecycle, console, PICO and SONIC logic on `main`. Do not maintain
parallel JP5/JP6 copies or cherry-pick each feature between platform branches.
Platform selection belongs in `platforms.py`, dependency installers and native
build configuration. CI runs the shared tests on Python 3.8 and 3.10, including
both TensorRT API paths and Foxy/Humble dependency selection.

Build and validate each platform separately from the same source commit.
JP5 (Ubuntu 20.04 / TensorRT 8.5 / Foxy) and JP6 (Ubuntu 22.04 /
TensorRT 10 / Humble) cannot share native runtime archives or TensorRT engines.
v0.2.0 provides separate JP5 and JP6 runtime packages; JP6 filenames include
`-jp6`, and its TensorRT engines are generated on the target Jetson.
The [JP6 guide](JETPACK6.md) covers both runtime-package and source installation. A shared source change reaches installed robots
only after rebuilding/reinstalling for their platform; merging does not update services.

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
