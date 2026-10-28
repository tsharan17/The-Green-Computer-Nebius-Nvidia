"""A human-readable validation report and graph summary for an ONNX file.

Pure function of the bytes (no execution). Complements onnx_parser: the parser builds the workload
graph for the engine; this explains the file to a person (what it is, what is wrong, what we will
assume) before they rely on any number.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import onnx
from onnx import TensorProto

from app.engine.workload.onnx_parser import FUSED_ELEMENTWISE, VIEW_OPS

# Ops whose cost the parser models explicitly (anything else is counted as data movement).
MODELLED_OPS = {
    "Conv", "ConvTranspose", "Gemm", "MatMul", "MaxPool", "AveragePool", "GlobalAveragePool",
    "ReduceMean", "ReduceSum", "Softmax", "LayerNormalization", "InstanceNormalization",
    "GroupNormalization", "Transpose", "Concat", "Slice", "Split", "Gather", "Resize", "Pad",
    "Expand", "Tile", "Upsample", "ScatterND", "GatherND",
} | VIEW_OPS | FUSED_ELEMENTWISE  # fmt: skip

_DTYPE = {v: k.lower() for k, v in TensorProto.DataType.items()}


def _shape(vi) -> list[int | str]:
    t = vi.type.tensor_type
    if not t.HasField("shape"):
        return []
    return [d.dim_value if d.HasField("dim_value") else (d.dim_param or "?") for d in t.shape.dim]


def onnx_report(data: bytes) -> dict[str, Any]:
    """Never raises: problems are reported in `errors` / `warnings`."""
    errors: list[str] = []
    warnings: list[str] = []
    if data[:1] == b"\x80" or data[:2] == b"PK":
        return {"format": "unknown", "errors": [
            "This looks like a pickle or a zip/torch archive, not ONNX. Export the model with "
            "torch.onnx.export (or tf2onnx) and upload the .onnx file."]}  # fmt: skip
    try:
        model = onnx.load_model_from_string(data)
    except Exception as e:  # noqa: BLE001 - any decode failure is a user-facing error
        return {"format": "unknown", "errors": [f"Not a valid ONNX protobuf: {e}"]}
    try:
        onnx.checker.check_model(model)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"ONNX checker: {str(e).splitlines()[0][:300]}")
    g = model.graph
    init_names = {i.name for i in g.initializer}
    inputs = [
        {"name": vi.name, "dtype": _DTYPE.get(vi.type.tensor_type.elem_type, "?"), "shape": _shape(vi)}
        for vi in g.input if vi.name not in init_names
    ]  # fmt: skip
    outputs = [
        {"name": vi.name, "dtype": _DTYPE.get(vi.type.tensor_type.elem_type, "?"), "shape": _shape(vi)}
        for vi in g.output
    ]  # fmt: skip
    dynamic = sorted({str(d) for io in inputs for d in io["shape"] if isinstance(d, str)})
    if dynamic:
        warnings.append(
            f"Dynamic input dimension(s) {', '.join(dynamic)}: the batch dimension is set to 1; other "
            "dynamic dimensions need a value (dynamic_dim_default) before analysis."
        )
    ops = Counter(n.op_type for n in g.node)
    unmodelled = sorted(op for op in ops if op not in MODELLED_OPS)
    if unmodelled:
        warnings.append(
            "Operators without a cost model (counted as data movement only, so compute may be "
            f"under-estimated): {', '.join(unmodelled)}."
        )
    external = [i.name for i in g.initializer if i.data_location == TensorProto.EXTERNAL]
    if external:
        errors.append(
            "Weights are stored in external data files. Re-save with "
            "onnx.save(model, path, save_as_external_data=False) (files under 2 GB) and upload again."
        )
    custom_domains = sorted({o.domain for o in model.opset_import if o.domain not in ("", "ai.onnx")})
    if custom_domains:
        warnings.append(f"Custom operator domains {', '.join(custom_domains)}: they cannot be costed "
                        "or executed here.")  # fmt: skip
    weight_dtypes = Counter(_DTYPE.get(i.data_type, "?") for i in g.initializer)
    quantised = any(op in ops for op in ("QuantizeLinear", "DequantizeLinear", "QLinearConv",
                                         "QLinearMatMul", "MatMulInteger", "ConvInteger"))  # fmt: skip
    if quantised:
        warnings.append("The graph is already quantised (Q/DQ or integer ops). Pick int8 as the "
                        "current precision so predictions match what you will run.")  # fmt: skip
    n_params = sum(int(math.prod(i.dims)) if i.dims else 1 for i in g.initializer)
    return {
        "format": "onnx",
        "ir_version": model.ir_version,
        "opsets": {(o.domain or "ai.onnx"): o.version for o in model.opset_import},
        "producer": f"{model.producer_name} {model.producer_version}".strip(),
        "graph_name": g.name,
        "inputs": inputs,
        "outputs": outputs,
        "n_nodes": len(g.node),
        "n_initializers": len(g.initializer),
        "parameters": n_params,
        "weight_dtypes": dict(weight_dtypes),
        "op_histogram": dict(ops.most_common()),
        "already_quantised": quantised,
        "errors": errors,
        "warnings": warnings,
    }
