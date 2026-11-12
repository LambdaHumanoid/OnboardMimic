# Build OnboardMimic with the G1 + SONIC profile

English · [简体中文](SOURCE_BUILD_zh.md)

> For the supported G1 / JP5 configuration, start with the [v0.1.1 prebuilt packages](PREBUILT.md). This guide is for modifying or rebuilding the controller and engines.

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
# Optional PICO mapping and full test suite:
.venv/bin/python -m pip install -r onboardmimic/backends/sonic/pico/requirements.txt
PYTHON=.venv/bin/python bash scripts/test.sh --all
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
PYTHON=.venv-test/bin/python bash scripts/test.sh
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
