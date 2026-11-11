#!/usr/bin/env bash
# Build only. Never runs the robot executable.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(uname -m)" == aarch64 ]] || { echo 'Native build requires aarch64 Jetson.'; exit 1; }
export HAS_ROS2=0
export onnxruntime_ROOT="$root/onboard/deps/onnxruntime-linux-aarch64-1.16.3"
export CUDAToolkit_ROOT="${CUDAToolkit_ROOT:-/usr/local/cuda-11.4}"
cmake -S "$root" -B "$root/build" -DCMAKE_CXX_COMPILER=g++-10 -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-I$root/onboard/deps/cppzmq"
cmake --build "$root/build" --target g1_deploy_onnx_ref -j"${BUILD_JOBS:-2}"
