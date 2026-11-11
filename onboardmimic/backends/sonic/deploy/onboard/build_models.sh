#!/usr/bin/env bash
# Inference-only conversion, engine generation and numerical validation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
root="$(cd ../../../../.. && pwd)"
python="${PYTHON:-$root/.venv/bin/python}"
trtexec="${TRTEXEC:-/usr/src/tensorrt/bin/trtexec}"
mkdir -p engines logs
model_dir=../planner/target_vel/V2
"$python" "$root/download_from_hf.py"
"$python" convert_layernorm.py "$model_dir/planner_sonic.onnx" "$model_dir/planner_orin.onnx" > logs/layernorm-convert.log
"$python" fold_topk_shape.py "$model_dir/planner_orin.onnx" "$model_dir/planner_orin_static.onnx" > logs/topk-fold.log
"$python" convert_einsum.py "$model_dir/planner_orin_static.onnx" "$model_dir/planner_orin_compatible.onnx" > logs/einsum-convert.log
"$python" validate_layernorm.py "$model_dir/planner_sonic.onnx" "$model_dir/planner_orin.onnx" > logs/layernorm-validate.log
"$python" validate_planner.py > logs/planner-validate.log
for model in encoder decoder; do
 "$trtexec" --onnx="../policy/release/model_${model}.onnx" --saveEngine="engines/${model}_fp32.engine" --workspace=2048 --noTF32 --buildOnly > "logs/${model}-build.log" 2>&1
done
"$trtexec" --onnx="$model_dir/planner_orin_compatible.onnx" --saveEngine=engines/planner_strict_fp32.engine --workspace=2048 --noTF32 --buildOnly > logs/planner-build.log 2>&1
"$python" validate_planner_engine.py engines/planner_strict_fp32.engine planner_engine_validation_strict.json --extended > logs/planner-validation.log 2>&1
cp engines/planner_strict_fp32.engine engines/planner_fp32.engine
cp planner_engine_validation_strict.json planner_engine_validation.json
"$python" validate_engines.py > logs/policy-validation.log
"$python" pack_native_cache.py > logs/cache-pack.log
"$python" validate_safety_source.py
