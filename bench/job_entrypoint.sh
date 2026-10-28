#!/usr/bin/env bash
# Entrypoint for a Nebius Serverless Job. The backend injects this file and the benchmark scripts
# into a stock CUDA image with `--inject-file`, so no custom image has to be built or pushed.
#
#   job_entrypoint.sh MODEL_ID PRECISION BATCHES DEVICE_ID RUNS
#
# The result JSON is printed between GC_RESULT_BEGIN / GC_RESULT_END so the backend can read it
# back from `nebius ai job logs` without needing object storage.
#
# STATUS: never run on a Nebius GPU (no account was available when this was written).
set -euo pipefail
MODEL_ID="$1"; PRECISION="$2"; BATCHES="$3"; DEVICE_ID="$4"; RUNS="${5:-50}"

cd /bench
export DEBIAN_FRONTEND=noninteractive
if ! command -v python3 >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq python3 python3-pip
fi
PIP="python3 -m pip install -q"
$PIP numpy onnx onnxruntime-gpu onnxconverter-common 2>/dev/null \
  || $PIP --break-system-packages numpy onnx onnxruntime-gpu onnxconverter-common

LABEL="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1 || echo unknown-gpu)"
echo "GPU: ${LABEL}"
nvidia-smi

case "$MODEL_ID" in
  resnet-50)    FILE=resnet50.onnx ;;
  mobilenet-v2) FILE=mobilenet_v2.onnx ;;
  *) echo "unsupported model for jobs: $MODEL_ID" >&2; exit 2 ;;
esac

python3 reference_models.py --out models
MODEL="models/${FILE}"
if [ "$PRECISION" = "fp16" ]; then
  python3 - <<PY
import onnx
from onnxconverter_common import float16
m = onnx.load("models/${FILE}")
onnx.save(float16.convert_float_to_float16(m, keep_io_types=True), "models/fp16.onnx")
PY
  MODEL="models/fp16.onnx"
elif [ "$PRECISION" != "fp32" ]; then
  echo "unsupported precision for jobs: $PRECISION" >&2; exit 2
fi

python3 run_benchmark.py --model "$MODEL" --model-id "$MODEL_ID" --device-id "$DEVICE_ID" \
  --hardware-label "$LABEL" --precision "$PRECISION" --batches "$BATCHES" --runs "$RUNS" \
  --source bench_job --out result.json

echo "GC_RESULT_BEGIN"
cat result.json
echo
echo "GC_RESULT_END"
