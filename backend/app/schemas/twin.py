"""Digital twin v1 data model: hardware components, memory paths, tensors and a time-ordered
execution trace, shared by the 2D system map today and a 3D view later (components carry
normalised x/y/z layout hints; events reference components by id).

Versioned: `schema_id` = "green-computer.trace/1". Additive changes keep /1; breaking ones bump it.
A trace is either `predicted` (the analytical engine) or `measured` (an execution backend or an
imported log); the two are never mixed in one trace.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

TRACE_SCHEMA = "green-computer.trace/1"

TraceProvenance = Literal["analytical", "calibrated", "estimated", "measured", "imported"]


class Layout(BaseModel):
    """Normalised position (0..1) on the map; z orders layers for a future 3D view."""

    x: float
    y: float
    w: float
    h: float
    z: float = 0.0


class Component(BaseModel):
    id: str
    kind: Literal["compute", "memory", "storage", "link", "power", "cooling"]
    name: str
    detail: str = ""
    capacity: float | None = None
    capacity_unit: str | None = None
    # Average heat dissipated here over the operating period (W) and its share of the total.
    heat_w: float = 0.0
    heat_share: float = 0.0
    provenance: TraceProvenance = "analytical"
    layout: Layout


class MemoryPath(BaseModel):
    id: str
    source: str
    target: str
    label: str
    bandwidth_gbs: float
    # Peak share of the path's bandwidth used by any event (0..1+; above 1 means saturated).
    peak_utilisation: float = 0.0


class TensorInfo(BaseModel):
    id: str
    bytes: float
    producer: int  # event index, -1 = model input
    last_use: int  # event index, = n_events for model outputs
    kind: Literal["input", "activation", "output"]


class EnergySplit(BaseModel):
    compute: float = 0.0
    memory: float = 0.0
    idle: float = 0.0
    leakage: float = 0.0
    cooling: float = 0.0

    @property
    def total(self) -> float:
        return self.compute + self.memory + self.idle + self.leakage + self.cooling


class TraceEvent(BaseModel):
    index: int
    name: str
    op: str
    kind: Literal["operator", "aggregate", "idle", "queue"]
    stage: int
    start_ms: float
    duration_ms: float
    compute_ms: float = 0.0
    memory_ms: float = 0.0
    overhead_ms: float = 0.0
    bound: Literal["compute", "memory", "overhead", "idle"]
    flops: float = 0.0
    weight_bytes: float = 0.0
    activation_bytes: float = 0.0
    bandwidth_gbs: float = 0.0
    bandwidth_utilisation: float = 0.0
    alloc: list[str] = []
    free: list[str] = []
    working_set_bytes: float = 0.0
    live_after_bytes: float = 0.0
    power_w: float = 0.0
    energy_mj: EnergySplit = EnergySplit()
    components: list[str] = []
    intensity: float = 0.0  # this event's power relative to the hottest event (0..1)


class MemoryPoint(BaseModel):
    t_ms: float
    event: int
    weights_bytes: float
    runtime_bytes: float
    activation_bytes: float
    total_bytes: float
    capacity_bytes: float
    over_capacity: bool


class Stage(BaseModel):
    index: int
    name: str
    first_event: int
    last_event: int
    start_ms: float
    duration_ms: float
    energy_mj: float
    dominant_op: str


class Bottleneck(BaseModel):
    kind: Literal["compute", "memory", "overhead", "thermal", "capacity", "throughput"]
    title: str
    detail: str
    event: int | None = None
    component: str | None = None


class ThermalView(BaseModel):
    ambient_c: float
    steady_c: float
    unthrottled_c: float
    throttle_c: float
    shutdown_c: float
    throttled: bool
    clock_factor: float
    time_to_throttle_s: float | None
    warmup: list[tuple[float, float]]
    cooling_name: str
    r_th_k_per_w: float
    # Thermal resistance the whole system would need to stay below the throttle point.
    r_th_needed_k_per_w: float | None
    note: str


class TraceTotals(BaseModel):
    latency_ms: float
    batch_ms: float
    period_ms: float
    busy_fraction: float
    energy_per_inference_mj: float
    energy_per_batch_mj: float
    average_power_w: float
    active_power_w: float
    peak_working_memory_bytes: float
    analysis_memory_bytes: float  # what the engine's fit check used (conservative heuristic)
    memory_capacity_bytes: float
    weights_bytes: float
    flops_per_batch: float


class ExecutionTrace(BaseModel):
    schema_id: str = TRACE_SCHEMA
    kind: Literal["predicted", "measured"]
    provenance: TraceProvenance
    source: str
    fidelity: Literal["graph", "graph_approx", "aggregate"]
    fidelity_note: str
    model_id: str
    model_name: str
    device_id: str
    device_name: str
    config: dict
    precision: str
    batch: int
    totals: TraceTotals
    components: list[Component]
    paths: list[MemoryPath]
    tensors: list[TensorInfo]
    events: list[TraceEvent]
    stages: list[Stage]
    memory_timeline: list[MemoryPoint]
    bottlenecks: list[Bottleneck]
    # None for measured traces: temperature is only known when a log of it was imported.
    thermal: ThermalView | None = None
    energy_per_inference: EnergySplit
    assumptions: list[str]
    # Factors applied so the trace sums to the headline batch time and energy (1.0 = no change).
    consistency_scale: float = 1.0
    energy_consistency_scale: float = 1.0
