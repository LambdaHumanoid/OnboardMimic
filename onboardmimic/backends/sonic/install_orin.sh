#!/usr/bin/env bash
# Dependency installation only; never starts a controller or service.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
[[ "$(uname -m)" == aarch64 ]] || { echo 'Run this installer on the Jetson (aarch64).'; exit 1; }
[[ -f /etc/nv_tegra_release ]] || { echo 'Jetson environment required.'; exit 1; }
source /etc/os-release
case "$VERSION_ID" in
  20.04) compiler=g++-10 ;;
  22.04) compiler=g++-10 ;;
  *) echo 'Source build supports Ubuntu 20.04 / 22.04 only.'; exit 1 ;;
esac
sudo apt-get update
sudo apt-get install -y "$compiler" cmake git curl libmsgpack-dev libboost-dev nlohmann-json3-dev libzmq3-dev libgtest-dev libeigen3-dev zlib1g-dev pkg-config python3-venv python3-dev
python3 -m venv --system-site-packages "$root/.venv"
# Upgrade only the isolated environment; retain Python 3.8 compatibility.
"$root/.venv/bin/python" -m pip install --upgrade pip==25.0.1
"$root/.venv/bin/python" -m pip install -r "$root/requirements.txt"
"$root/.venv/bin/python" "$root/install_scripts/fetch_dependencies.py"
echo 'Dependencies installed. No robot process or service was started. / 依赖安装完成；未启动机器人进程或服务。'
echo 'Next: complete the source build and first-run setup: docs/DEPLOYMENT.md / 下一步：完成源码构建和首次部署：docs/DEPLOYMENT_zh.md'
echo 'Console: see README → Open the console, or docs/CONTROL_EXTENSIONS.md. / 控制台：请查看 README 的“打开控制台”章节，或 docs/CONTROL_EXTENSIONS_zh.md。'
