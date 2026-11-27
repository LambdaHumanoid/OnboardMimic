#!/bin/bash
# Adapted from FH Navi receiver_ab; use only in a network-none container.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
out=${1:?Pass a data-disk output directory}
baseline=${2:?Pass the baseline Git revision}
mkdir -p "$out/baseline" "$out/poll"
header=onboardmimic/backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/zmq_robot_channel.hpp
git show "$baseline:$header" > "$out/baseline/channel.hpp"
cp "$root/$header" "$out/poll/channel.hpp"
python - "$out/poll/channel.hpp" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]); s = p.read_text()
anchor = '                // Drain a bounded batch and then publish the latest state in that'
assert s.count(anchor) == 1
s = s.replace(anchor, '''                // Experimental only: wake on input, bounded shutdown wait.
                zmq::pollitem_t ready{state_socket_->handle(), 0, ZMQ_POLLIN, 0};
                zmq::poll(&ready, 1, std::chrono::milliseconds(2));
                if (!(ready.revents & ZMQ_POLLIN)) continue;
'''+anchor)
p.write_text(s)
PY
sdk="$root/onboardmimic/backends/sonic/deploy/thirdparty/unitree_sdk2"
inc="$(dirname "$root/$header")"
for variant in baseline poll; do
 g++ -std=c++20 -O2 -pthread "$root/tests/native/receiver_latency/bench.cpp" -I"$out/$variant" -I"$inc" -I"$sdk/include" -I"$sdk/thirdparty/include" -I"$sdk/thirdparty/include/ddscxx" -lzmq -o "$out/$variant/bench" 2>"$out/$variant/build.log"
done
for i in 1 2 3; do
 for variant in baseline poll; do "$out/$variant/bench" > "$out/$variant/normal-$i.log"; done
done
for variant in baseline poll; do "$out/$variant/bench" stall > "$out/$variant/stall.log"; done
python "$root/tests/native/receiver_latency/summarize.py" "$out"
