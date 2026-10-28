"""Benchmark an ONNX model with ONNX Runtime and emit Green Computer measurement records.

    python run_benchmark.py --model models/resnet50.onnx --model-id resnet-50 \
        --device-id host-cpu --hardware-label "My CPU" --batches 1,2,4 --out results.json

Two passes per batch size:
  1. latency: `--runs` timed iterations, no sampling overhead;
  2. power (only when nvidia-smi exists): the model runs flat out for `--power-seconds` while
     power and temperature are sampled, so the average reflects sustained load.
Uses the CUDA provider when available, otherwise CPU. Anything that cannot be measured (power on
a CPU-only host) is recorded as null, never guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort

from power_logger import PowerLogger


def percentile(xs: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(xs), q))


def make_session(model: Path, threads: int | None, provider: str):
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
    return ort.InferenceSession(str(model), so, providers=[provider])


def bench_one(
    sess, batch: int, warmup: int, runs: int, power_seconds: float
) -> tuple[list[float], dict]:
    inp = sess.get_inputs()[0]
    shape = [batch if not isinstance(d, int) else d for d in inp.shape]
    x = np.random.rand(*shape).astype(np.float32)
    for _ in range(warmup):
        sess.run(None, {inp.name: x})
    lat = []
    for _ in range(runs):
        t = time.perf_counter()
        sess.run(None, {inp.name: x})
        lat.append((time.perf_counter() - t) * 1e3)

    power = {"power_w_avg": None, "power_w_peak": None, "temperature_c_max": None}
    logger = PowerLogger()
    if logger.available and power_seconds > 0:
        logger.start()
        time.sleep(0.5)  # let the sampler produce its first rows
        end = time.perf_counter() + power_seconds
        while time.perf_counter() < end:
            sess.run(None, {inp.name: x})
        power = {k: v for k, v in logger.stop().items() if k != "power_samples"}
    return lat, power


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--hardware-label", required=True)
    ap.add_argument("--precision", default="fp32", choices=["fp32", "fp16", "int8"])
    ap.add_argument("--batches", default="1")
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--power-seconds", type=float, default=8.0)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--ambient-c", type=float, default=None)
    ap.add_argument("--power-mode", default=None)
    ap.add_argument("--source", default="local_bench", choices=["local_bench", "bench_job"])
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()

    have_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    provider = "CUDAExecutionProvider" if have_cuda else "CPUExecutionProvider"
    sess = make_session(a.model, a.threads, provider)
    # Fail loudly if CUDA was expected but ORT fell back to CPU for this model.
    active = sess.get_providers()[0]
    records = []
    for b in (int(x) for x in a.batches.split(",")):
        lat, power = bench_one(sess, b, a.warmup, a.runs, a.power_seconds)
        mean = float(np.mean(lat))
        measured_at = datetime.now(UTC).isoformat()
        rec = {
            "device_id": a.device_id,
            "model_id": a.model_id,
            "precision": a.precision,
            "batch": b,
            "power_mode_id": a.power_mode,
            "source": a.source,
            "hardware_label": a.hardware_label,
            "ambient_c": a.ambient_c,
            "latency_ms_mean": round(mean, 4),
            "latency_ms_p50": round(percentile(lat, 50), 4),
            "latency_ms_p95": round(percentile(lat, 95), 4),
            "latency_ms_p99": round(percentile(lat, 99), 4),
            "throughput_fps": round(b / mean * 1e3, 3),
            "n_runs": a.runs,
            "measured_at": measured_at,
            "notes": f"onnxruntime {ort.__version__} {active}; threads={a.threads or 'default'}",
            **power,
            "power_boundary": "gpu_board" if power["power_w_avg"] is not None else None,
        }
        key = json.dumps([rec["device_id"], rec["model_id"], rec["precision"], b, measured_at])
        rec["external_id"] = hashlib.sha1(key.encode()).hexdigest()[:16]
        records.append(rec)
        pw = f", {power['power_w_avg']} W" if power["power_w_avg"] is not None else ""
        print(f"{a.model_id} {a.precision} batch {b}: {mean:.2f} ms, {rec['throughput_fps']:.1f} fps{pw}")

    out = {
        "schema": "green-computer.bench/1",
        "environment": {
            "python": platform.python_version(),
            "onnxruntime": ort.__version__,
            "provider": active,
            "platform": platform.platform(),
        },
        "records": records,
    }
    a.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
