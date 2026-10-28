"""Per-layer view: where the time goes, and which layers are risky to quantise.

Times come from the same per-layer roofline the whole tool uses (so they sum to the predicted
batch time at the chosen operating point). The quantisation-sensitivity score is a HEURISTIC prior
from layer type and position, not a measurement; `quant_sensitivity_script` generates a script that
measures it for real on your own data.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.engine import assumptions as A
from app.engine.roofline import roofline_at
from app.engine.workload.onnx_parser import parse_onnx
from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile
from app.schemas.model import ModelProfile

# Prior sensitivity of an op to int8 quantisation: 0 = robust, 1 = fragile.
_OP_RISK = {
    "Softmax": 0.9, "LayerNormalization": 0.8, "ReduceMean": 0.5, "Gelu": 0.6, "Sigmoid": 0.5,
    "Div": 0.5, "Sqrt": 0.6, "Exp": 0.7, "Gemm": 0.35, "MatMul": 0.4, "Conv": 0.25,
    "DepthwiseConv": 0.55, "Add": 0.3, "Relu": 0.05, "Clip": 0.1, "MaxPool": 0.05,
    "AveragePool": 0.1, "GlobalAveragePool": 0.2, "BatchNormalization": 0.1,
}


def _risk(layer_name: str, op: str, idx: int, n: int, params: float, flops: float) -> float:
    r = _OP_RISK.get(op, 0.3)
    if op == "Conv" and params > 0 and flops / max(params, 1.0) < 4:  # depthwise-like reuse
        r = _OP_RISK["DepthwiseConv"]
    if idx == 0 or idx >= n - 2:  # first and last layers hurt accuracy most
        r = min(1.0, r + 0.4)
    return round(r, 2)


_REFERENCE = {"resnet-50": "resnet50", "mobilenet-v2": "mobilenet_v2"}


@lru_cache(maxsize=8)
def _reference_graph(model_id: str) -> ModelProfile | None:
    """Layers of a library model rebuilt from the reference architecture (needs the repo's bench/)."""
    fn = _REFERENCE.get(model_id)
    path = Path(__file__).resolve().parents[3] / "bench" / "reference_models.py"
    if fn is None or not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("gc_reference_models", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        data = getattr(mod, fn)(materialize=False)
        return parse_onnx(data, name=model_id, dynamic_dim_default=1).profile
    except Exception:  # noqa: BLE001 - the layer view is optional; never break analysis for it
        return None


def layer_profile(
    model: ModelProfile, hw: HardwareProfile, cfg: Config, eta_scale: float = 1.0
) -> dict[str, Any]:
    source = "your model's graph" if model.layers else ""
    if not model.layers:
        ref = _reference_graph(model.id)
        if ref is not None:
            model, source = ref, "the published reference architecture of this model"
    mode = hw.mode(cfg.power_mode_id)
    rf = roofline_at(
        model, hw, mode.freq_scale * cfg.clock_trim, mode.mem_scale, cfg.precision, cfg.batch,
        cfg.resolution_scale, eta_scale, cfg.sparsity, detail=True,
    )  # fmt: skip
    t_l, t_c, t_m = rf.per_layer  # type: ignore[misc]
    layers = model.layers
    total_layers = float(t_l.sum())
    total_t = rf.t_batch_s
    if not layers:
        return {
            "has_graph": False,
            "note": "This model has no parsed graph (library or manual entry), so no per-layer view. "
                    "Upload an ONNX file to see where time goes.",
            "total_ms": total_t * 1e3, "layers": [], "by_op": [],
        }
    n = len(layers)
    flops = np.array([layer.flops for layer in layers])
    rows: list[dict[str, Any]] = []
    for i, layer in enumerate(layers):
        compute_bound = bool(t_c[i] >= t_m[i])
        rows.append({
            "index": i, "name": layer.name, "op": layer.op,
            "time_ms": float(t_l[i] * 1e3),
            "share": float(t_l[i] / total_t) if total_t > 0 else 0.0,
            "bound": "compute" if compute_bound else "memory",
            "flops_share": float(flops[i] / max(flops.sum(), 1.0)),
            "weights_kb": float(layer.params * A.BYTES_PER_ELEM[cfg.precision] / 1e3),
            "quant_risk": _risk(layer.name, layer.op, i, n, layer.params, layer.flops),
        })  # fmt: skip
    by_op: dict[str, dict[str, float]] = defaultdict(lambda: {"time_ms": 0.0, "count": 0})
    for r in rows:
        by_op[r["op"]]["time_ms"] += r["time_ms"]
        by_op[r["op"]]["count"] += 1
    ops = sorted(
        ({"op": k, "time_ms": v["time_ms"], "count": int(v["count"]),
          "share": v["time_ms"] / (total_t * 1e3) if total_t > 0 else 0.0} for k, v in by_op.items()),
        key=lambda d: -float(d["time_ms"]),  # type: ignore[arg-type]
    )  # fmt: skip
    top = sorted(rows, key=lambda r: -r["time_ms"])[:10]
    overhead_ms = rf.t_overhead_s * 1e3
    fragile = [r for r in rows if r["quant_risk"] >= 0.6]
    mem_bound_share = sum(r["time_ms"] for r in rows if r["bound"] == "memory") / max(
        total_layers * 1e3, 1e-12
    )
    hints = []
    if overhead_ms > 0.25 * total_t * 1e3:
        hints.append(
            f"{overhead_ms:.2f} ms of {total_t * 1e3:.2f} ms is per-layer launch overhead "
            f"({n} layers): fusing layers or using a graph runtime (TensorRT, OpenVINO, XNNPACK) "
            "attacks this, not quantisation."
        )
    if mem_bound_share > 0.6:
        hints.append(
            f"{mem_bound_share:.0%} of layer time is memory-bound: lower precision, smaller "
            "activations (resolution) and fusing layers help more than extra compute."
        )
    elif mem_bound_share < 0.3:
        hints.append("Mostly compute-bound: lower precision or pruning give the biggest gain.")
    if fragile:
        hints.append(
            f"{len(fragile)} layer(s) look fragile to int8 (first/last layers, softmax, "
            "normalisation, depthwise): keep them in fp16 if accuracy drops, and measure."
        )
    return {
        "has_graph": True, "graph_source": source, "total_ms": total_t * 1e3, "overhead_ms": overhead_ms,
        "n_layers": n, "config": cfg.model_dump(), "layers": rows, "top": top, "by_op": ops,
        "fragile": fragile[:12], "hints": hints,
        "note": "Times use the same roofline as the rest of the tool (analytical, not profiled). "
                "Quantisation risk is a heuristic prior.",
    }  # fmt: skip


def quant_sensitivity_script() -> str:
    return '''"""Measure per-layer int8 sensitivity of an ONNX model on YOUR data.

    pip install onnx onnxruntime numpy
    python quant_sensitivity.py --model model_fp32.onnx --data samples.npy --top 15

`samples.npy` holds 50-500 real input batches shaped like the model input. For each Conv/Gemm/MatMul
node the script quantises ONLY that node's weights to int8 (per-tensor, symmetric), runs the samples
and reports how much the output moves (relative L2 error against fp32; cosine similarity). Layers at the
top are the ones to keep in fp16 (mixed precision). Output similarity is a proxy: confirm with your
real accuracy metric before shipping.
"""

import argparse

import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper


def run(sess, name, xs):
    return np.concatenate([sess.run(None, {name: x[None, ...].astype(np.float32)})[0].ravel() for x in xs])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    xs = np.load(a.data)[:100]
    base = onnx.load(a.model)
    sess0 = ort.InferenceSession(a.model, providers=["CPUExecutionProvider"])
    name = sess0.get_inputs()[0].name
    ref = run(sess0, name, xs)
    inits = {i.name: i for i in base.graph.initializer}
    rows = []
    for node in base.graph.node:
        if node.op_type not in ("Conv", "Gemm", "MatMul"):
            continue
        wname = next((i for i in node.input[1:] if i in inits), None)
        if wname is None:
            continue
        m = onnx.ModelProto(); m.CopyFrom(base)
        for init in m.graph.initializer:
            if init.name == wname:
                w = numpy_helper.to_array(init).astype(np.float32)
                s = max(float(np.abs(w).max()) / 127.0, 1e-12)
                q = (np.round(w / s).clip(-127, 127) * s).astype(np.float32)   # fake-quantised
                init.CopyFrom(numpy_helper.from_array(q, wname))
        sess = ort.InferenceSession(m.SerializeToString(), providers=["CPUExecutionProvider"])
        out = run(sess, name, xs)
        rel = float(np.linalg.norm(out - ref) / max(np.linalg.norm(ref), 1e-12))
        cos = float(out @ ref / max(np.linalg.norm(out) * np.linalg.norm(ref), 1e-12))
        rows.append((rel, cos, node.name or node.output[0], node.op_type))
    rows.sort(reverse=True)
    print("rel_L2_error  cosine  layer (op)   <- most sensitive first")
    for rel, cos, n, op in rows[: a.top]:
        print(f"{rel:12.5f}  {cos:.5f}  {n} ({op})")
    print("\\nKeep the top rows in fp16 and quantise the rest; re-measure task accuracy.")


if __name__ == "__main__":
    main()
'''
