from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.metrics import Provenance


class Layer(BaseModel):
    """One executable layer of the workload graph (per sample, before precision scaling)."""

    name: str
    op: str
    flops: float  # FLOPs (2 x MACs)
    params: float  # weight elements read by this layer
    act_elems: float  # activation elements read + written (unfused traffic)
    # Graph wiring for tensor lifetimes (canonical tensor names; empty for older stored profiles).
    in_tensors: list[str] = []
    out_tensors: list[str] = []


class ModelProfile(BaseModel):
    """AI model summary. `layers` (ONNX) enables per-layer roofline; otherwise one aggregate."""

    id: str
    name: str
    task: str
    input_desc: str
    params_m: float
    gflops: float  # per sample, FLOPs (2 x MACs)
    n_layers: int
    act_mb_fp32: float  # activation traffic per sample at fp32
    baseline_accuracy: float  # fraction 0..1
    accuracy_metric: str
    # library: published numbers; graph: parsed from ONNX; manual: user-entered (wider bands)
    fidelity: Literal["library", "graph", "manual"] = "library"
    # Whether input resolution can be scaled (vision models).
    resolution_scalable: bool = True
    layers: list[Layer] | None = None
    # Canonical activation tensors (elements per sample) and the graph's inputs/outputs, so the
    # digital twin can derive tensor lifetimes. None for library or manual models.
    tensors: dict[str, float] | None = None
    graph_inputs: list[str] = []
    graph_outputs: list[str] = []
    sources: dict[str, Provenance] = {}
    notes: str = ""
