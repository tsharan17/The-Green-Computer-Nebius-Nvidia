# bench: real measurements for calibration

Scripts that produce measurements the app can import. Nothing here guesses a number: a value the
tool cannot measure (for example power on a CPU-only host) is recorded as `null`.

| Script | Purpose | Status |
|---|---|---|
| `reference_models.py` | Build ResNet-50 and MobileNetV2 ONNX graphs (random weights) from their published architectures. Valid for latency/power/memory, not accuracy | Run; parser reproduces the published param/FLOP counts (tested) |
| `probe_host.py` | Measure achievable fp32 GEMM GFLOPS and copy bandwidth, to build a device profile | Run on the development machine |
| `run_benchmark.py` | Time an ONNX model with ONNX Runtime (CUDA if present, else CPU); a separate sustained-load pass samples `nvidia-smi` power/temperature when available; emit `green-computer.bench/1` JSON | Run on the development machine's CPU (CPU only; no GPU or power data) |
| `job_entrypoint.sh` | Entry script injected into a Nebius Serverless Job (installs onnxruntime-gpu, builds the model, runs the benchmark, prints the result between markers) | **Never run on a Nebius GPU**; syntax checked |
| `power_logger.py` | `nvidia-smi` sampler used by `run_benchmark.py` | **Not exercised**: no NVIDIA GPU was available |
| `export_models.py` | FP16 / INT8 variants of an FP32 model | **Not run** |
| `accuracy_check.py` | Top-1 accuracy retention of a variant vs FP32 on your validation data | **Not run** |
| `upload_results.py` | Copy a result to S3-compatible storage | **Not run** (no Nebius account) |
| `Dockerfile` | GPU benchmark image | **Never built** |

## What has actually been measured

`backend/app/data/measurements/` holds real runs on the development machine's **CPU** (Intel Core
Ultra 7 258V, ONNX Runtime 1.31 CPU provider): ResNet-50 at batch 1/2/4/8 and MobileNetV2 at batch
1/2/4/8/16, FP32, latency only. **No GPU and no power measurement exists yet.** Jetson numbers in
the app remain analytical predictions.

## Reproduce the local measurements

```bash
cd bench
python reference_models.py --out models
python probe_host.py
python run_benchmark.py --model models/resnet50.onnx --model-id resnet-50 --device-id host-cpu \
  --hardware-label "My CPU" --batches 1,2,4,8 --runs 20 --out resnet50.json
```

Import the result on the **Verify** tab (format `bench_json`), or `POST /api/v1/measurements/import?format=bench_json`.

## On a GPU host

Install `onnxruntime-gpu`, run the same commands with the device id of the GPU profile (for example
`nebius-l40s`) and `--source bench_job`. `power_logger.py` then records average/peak power and
maximum temperature from `nvidia-smi`. Launching this as a Nebius Serverless Job from the app is not
implemented; the API reports that honestly (`GET /api/v1/benchmarks/status`).
