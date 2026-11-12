#!/usr/bin/env bash
# Validate/install only. Does not install/start services, release modes or drive motors.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$root"
with_pico=0
case "${1:-}" in '') ;; --with-pico) with_pico=1 ;; *) echo 'Usage / 用法: install_prebuilt.sh [--with-pico]' >&2; exit 2 ;; esac
[[ "$(uname -m)" == aarch64 ]] || { echo 'This prebuilt package requires Jetson Orin NX aarch64.'; exit 1; }
[[ -f prebuilt_manifest.json ]] || { echo 'Extract all three release archives first.'; exit 1; }
# Native bridge depends on the target ROS2 and unitree_hg runtime, not bundled system libraries.
: "${ROS_SETUP:?Set ROS_SETUP to ROS2 setup.bash / 请指定 ROS2 环境脚本}"
: "${UNITREE_ROS_SETUP:?Set UNITREE_ROS_SETUP to unitree_hg workspace setup.bash / 请指定消息工作空间}"
# ROS setup scripts can reference unset variables.
set +u
source "$ROS_SETUP"
source "$UNITREE_ROS_SETUP"
set -u
/usr/bin/python3 scripts/install_tensorrt.py --profile jp5
sudo apt-get update
sudo apt-get install -y libzmq5 python3-venv
# Packaged libraries + platform CUDA are used for validation subprocesses.
export LD_LIBRARY_PATH="$root/onboardmimic/backends/sonic/deploy/target/lib:/usr/local/cuda-11.4/targets/aarch64-linux/lib:${LD_LIBRARY_PATH:-}"
/usr/bin/python3 scripts/check_prebuilt.py
/usr/bin/python3 -m venv --system-site-packages .venv
# Ubuntu 20.04 pip 20 can report success with incompatible inherited packages.
# pip 25.0.1 supports Python 3.8 and resolves these dependencies correctly.
.venv/bin/python -m pip install --upgrade pip==25.0.1
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import tensorrt; assert tensorrt.__version__.startswith("8.5.2"), tensorrt.__version__'
test_mode=--core
if [[ "$with_pico" == 1 ]]; then
 .venv/bin/python -m pip install -r onboardmimic/backends/sonic/pico/requirements.txt
 test_mode=--all
fi
PYTHON=.venv/bin/python bash scripts/test.sh "$test_mode"
cd onboardmimic/backends/sonic/deploy/onboard
mkdir -p logs
# These tests only load engines and synthetic inputs; no controller or ROS2 bridge.
"$root/.venv/bin/python" validate_engines.py > logs/prebuilt-policy-validation.log
"$root/.venv/bin/python" validate_planner_engine.py engines/planner_fp32.engine planner_engine_validation.json --extended > logs/prebuilt-planner-validation.log
"$root/.venv/bin/python" pack_native_cache.py > logs/prebuilt-cache.log
printf 'Prebuilt inference validation passed. No robot service or controller was started. / 预编译包推理验证通过；未启动机器人服务或控制器。\n'
printf 'Next: configure ROS2 and follow docs/DEPLOYMENT.md for explicit commissioning. / 下一步：配置 ROS2，按照 docs/DEPLOYMENT_zh.md 手动完成首次部署与调试。\n'

printf "Optional PICO receiver setup: docs/CONTROL_EXTENSIONS.md / 可选 PICO 接收服务配置：见 docs/CONTROL_EXTENSIONS_zh.md（镜像需单独构建）。\n"
