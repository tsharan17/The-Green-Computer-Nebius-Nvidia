"""Isolated worker for anything that loads or runs a user's ONNX model.

Always started as a separate process (`python -m app.execution.worker <command> ...`) by
app/execution/runner.py, with a wall-clock timeout, so a pathological model cannot hang or crash
the web server. ONNX Runtime does not execute arbitrary code from a model file (no pickles; custom
operators need a library the worker never loads). Prints one JSON object on stdout.

Commands
  quantize  --src A --dst B             dynamic INT8 weight quantisation (ONNX Runtime)
  compare   --a A --b B [--shape S]     run both on the same synthetic inputs, report agreement
  bench     --src A --providers P ...   latency, throughput, per-operator profile (Phase 6)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import numpy as np

_DTYPES = {"tensor(float)": np.float32, "tensor(float16)": np.float16, "tensor(double)": np.float64,
           "tensor(int64)": np.int64, "tensor(int32)": np.int32, "tensor(int8)": np.int8,
           "tensor(uint8)": np.uint8, "tensor(bool)": np.bool_}  # fmt: skip


NOTES: list[str] = []
_COMPAT: dict[str, str] = {}


def _compatible(path: str) -> str:
    """ONNX Runtime refuses files whose IR version is newer than it knows, even when every
    operator is supported. Retry with the IR version lowered to the runtime's maximum (metadata
    only; opsets are unchanged) and say so in the result."""
    import re

    import onnx
    import onnxruntime as ort

    if path in _COMPAT:
        return _COMPAT[path]
    try:
        ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        _COMPAT[path] = path
        return path
    except Exception as e:  # noqa: BLE001
        m = re.search(r"Unsupported model IR version: (\d+), max supported IR version: (\d+)", str(e))
        if not m:
            raise
        model = onnx.load(path)
        model.ir_version = int(m.group(2))
        out = path + f".ir{m.group(2)}.onnx"
        onnx.save(model, out)
        NOTES.append(f"IR version lowered from {m.group(1)} to {m.group(2)} for this ONNX Runtime (opsets unchanged).")
        _COMPAT[path] = out
        return out


def _session(path: str, providers: list[str] | None = None, profile: bool = False):
    import onnxruntime as ort

    path = _compatible(path)

    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = int(os.environ.get("GC_EXEC_THREADS", "0") or 0)
    if profile:
        so.enable_profiling = True
        so.profile_file_prefix = os.path.join(os.path.dirname(path), "gc_profile")
    return ort.InferenceSession(path, so, providers=providers or ["CPUExecutionProvider"])


def _inputs(sess, shape_hint: list[int] | None, seed: int, batch: int = 1) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    feeds: dict[str, np.ndarray] = {}
    for k, inp in enumerate(sess.get_inputs()):
        dims = []
        for i, d in enumerate(inp.shape):
            if isinstance(d, int) and d > 0:
                dims.append(d)  # fixed by the model
            elif i == 0:
                dims.append(batch)  # dynamic batch dimension
            elif k == 0 and shape_hint and i < len(shape_hint):
                dims.append(shape_hint[i])  # the shape the model was analysed with
            else:
                dims.append(1)
        dt = _DTYPES.get(inp.type, np.float32)
        if np.issubdtype(dt, np.floating):
            feeds[inp.name] = rng.standard_normal(dims).astype(dt)
        else:
            feeds[inp.name] = np.zeros(dims, dtype=dt)
    return feeds


def quantize(src: str, dst: str) -> dict[str, Any]:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    t0 = time.perf_counter()
    quantize_dynamic(src, dst, weight_type=QuantType.QInt8)
    return {"ok": True, "seconds": time.perf_counter() - t0, "src_bytes": os.path.getsize(src),
            "dst_bytes": os.path.getsize(dst), "method": "onnxruntime.quantization.quantize_dynamic "
            "(weights INT8, activations quantised at run time)"}  # fmt: skip


def compare(a: str, b: str, shape: list[int] | None, n: int = 8) -> dict[str, Any]:
    sa, sb = _session(a), _session(b)
    outs: list[dict[str, Any]] = []
    worst_cos, worst_rel = 1.0, 0.0
    for s in range(n):
        feeds = _inputs(sa, shape, seed=1000 + s)
        ra, rb = sa.run(None, feeds), sb.run(None, feeds)
        for k, (x, y) in enumerate(zip(ra, rb, strict=True)):
            x = np.asarray(x, dtype=np.float64).ravel()
            y = np.asarray(y, dtype=np.float64).ravel()
            nx, ny = np.linalg.norm(x), np.linalg.norm(y)
            cos = 1.0 if nx == 0 and ny == 0 else float(x @ y / max(nx * ny, 1e-30))
            rel = 0.0 if nx == 0 and ny == 0 else float(np.linalg.norm(x - y) / max(nx, 1e-30))
            worst_cos, worst_rel = min(worst_cos, cos), max(worst_rel, rel)
            if s == 0:
                outs.append({"output": sa.get_outputs()[k].name, "elements": int(x.size)})
    return {"ok": True, "samples": n, "outputs": outs, "worst_cosine": worst_cos, "worst_relative_l2": worst_rel}


def _peak_rss() -> int | None:
    try:
        import resource

        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # type: ignore[attr-defined]
        return int(r if sys.platform == "darwin" else r * 1024)
    except ImportError:  # Windows
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]  # fmt: skip

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            ok = k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return int(pmc.PeakWorkingSetSize) if ok and pmc.PeakWorkingSetSize else None
        except Exception:  # noqa: BLE001
            return None


def _bench(src: str, providers: list[str], batch: int, warmup: int, runs: int, shape: list[int] | None,
           max_seconds: float) -> dict[str, Any]:  # fmt: skip
    """Latency on synthetic inputs, plus a per-operator profile of one run."""
    import onnxruntime as ort

    sess = _session(src, providers)
    feeds = _inputs(sess, shape, seed=7, batch=batch)
    t_start = time.perf_counter()
    for _ in range(warmup):
        sess.run(None, feeds)
    lat: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        sess.run(None, feeds)
        lat.append((time.perf_counter() - t0) * 1e3)
        if time.perf_counter() - t_start > max_seconds:
            break
    # One profiled run (separate session) for per-operator timings.
    prof_sess = _session(src, providers, profile=True)
    prof_sess.run(None, feeds)
    prof_sess.run(None, feeds)
    prof_path = prof_sess.end_profiling()
    ops: list[dict[str, Any]] = []
    try:
        with open(prof_path, encoding="utf-8") as f:
            events = json.load(f)
        # Keep node events of the last run only (the first profiled run warms caches).
        runs_seen = [e for e in events if e.get("cat") == "Session" and e.get("name") == "model_run"]
        last = runs_seen[-1] if runs_seen else None
        for e in events:
            if e.get("cat") != "Node" or not e.get("name", "").endswith("_kernel_time"):
                continue
            if last and e["ts"] < last["ts"]:
                continue
            args = e.get("args", {})
            ops.append({"name": e["name"].removesuffix("_kernel_time"), "op": args.get("op_name", ""),
                        "start_us": e["ts"] - (last["ts"] if last else 0), "duration_us": e.get("dur", 0),
                        "provider": args.get("provider", "")})  # fmt: skip
    finally:
        try:
            os.remove(prof_path)
        except OSError:
            pass
    arr = np.array(lat) if lat else np.array([np.nan])
    return {
        "ok": True,
        "providers": sess.get_providers(),
        "onnxruntime": ort.__version__,
        "batch": batch,
        "runs": len(lat),
        "warmup": warmup,
        "latency_ms": {"mean": float(arr.mean()), "p50": float(np.percentile(arr, 50)),
                       "p95": float(np.percentile(arr, 95)), "p99": float(np.percentile(arr, 99)),
                       "min": float(arr.min()), "max": float(arr.max())},  # fmt: skip
        "throughput_fps": float(batch * 1e3 / arr.mean()) if lat else None,
        "operators": ops,
        "peak_rss_bytes": _peak_rss(),
        "wall_seconds": time.perf_counter() - t_start,
        "inputs": {k: list(v.shape) for k, v in feeds.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.execution.worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quantize")
    q.add_argument("--src", required=True)
    q.add_argument("--dst", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--shape", default="")
    c.add_argument("--samples", type=int, default=8)
    b = sub.add_parser("bench")
    b.add_argument("--src", required=True)
    b.add_argument("--providers", default="CPUExecutionProvider")
    b.add_argument("--batch", type=int, default=1)
    b.add_argument("--warmup", type=int, default=5)
    b.add_argument("--runs", type=int, default=30)
    b.add_argument("--shape", default="")
    b.add_argument("--max-seconds", type=float, default=60.0)
    a = ap.parse_args(argv)
    shape = [int(x) for x in a.shape.split("x") if x.strip().isdigit()] if getattr(a, "shape", "") else None
    try:
        if a.cmd == "quantize":
            out = quantize(a.src, a.dst)
        elif a.cmd == "compare":
            out = compare(a.a, a.b, shape, a.samples)
        else:
            out = _bench(a.src, a.providers.split(","), a.batch, a.warmup, a.runs, shape, a.max_seconds)
    except Exception as e:  # noqa: BLE001 - reported to the caller as data
        out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:800]}"}
    if NOTES:
        out["notes"] = sorted(set(NOTES))
    sys.stdout.write(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
