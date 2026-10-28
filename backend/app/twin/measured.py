"""Measured execution trace: what ONNX Runtime's profiler recorded on a real run.

Only what was measured goes in: operator start times and durations, end-to-end latency, peak process
memory. Power, energy and temperature are not measured by a CPU run here, so they are absent (zero
with an explicit statement), never filled in from the model. A measured trace is a separate object
from a predicted one; comparisons put the two side by side.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.schemas.twin import (
    Bottleneck,
    EnergySplit,
    ExecutionTrace,
    Stage,
    TraceEvent,
    TraceTotals,
)


def build_measured_trace(bench: dict[str, Any], model_id: str, model_name: str, device_id: str,
                         device_name: str, precision: str, backend: str) -> ExecutionTrace:  # fmt: skip
    ops = sorted(bench.get("operators", []), key=lambda o: o["start_us"])
    events = [
        TraceEvent(index=i, name=o["name"], op=o["op"] or "op", kind="operator", stage=0,
                   start_ms=o["start_us"] / 1e3, duration_ms=o["duration_us"] / 1e3, bound="compute",
                   components=["compute"])
        for i, o in enumerate(ops)
    ]  # fmt: skip
    lat = bench["latency_ms"]
    batch = int(bench.get("batch", 1))
    # Stages by op type share, so the measured and predicted views group the same way.
    total = sum(e.duration_ms for e in events) or 1.0
    stages: list[Stage] = []
    if events:
        target = total / min(10, len(events))
        acc, start = 0.0, 0
        for i, e in enumerate(events):
            acc += e.duration_ms
            if acc >= target or i == len(events) - 1:
                grp = events[start : i + 1]
                for g in grp:
                    g.stage = len(stages)
                dom = max({g.op for g in grp}, key=lambda op: sum(x.duration_ms for x in grp if x.op == op))
                stages.append(Stage(index=len(stages), name=f"Stage {len(stages) + 1}", first_event=start,
                                    last_event=i, start_ms=grp[0].start_ms,
                                    duration_ms=sum(g.duration_ms for g in grp), energy_mj=0.0, dominant_op=dom))  # fmt: skip
                acc, start = 0.0, i + 1
    slow = sorted(events, key=lambda e: -e.duration_ms)[:3]
    rss = bench.get("peak_rss_bytes")
    notes = [
        f"Measured with ONNX Runtime {bench.get('onnxruntime')} ({', '.join(bench.get('providers', []))}) on "
        f"synthetic inputs {bench.get('inputs')}: {bench.get('runs')} timed runs after {bench.get('warmup')} warm-up runs.",
        "Operator times come from one profiled run; the runtime fuses and reorders operators, so names may "
        "differ from the ONNX graph.",
        "Power, energy and temperature were not measured in this run (no power meter on this path). Import a "
        "power log under Verify to add them.",
        *bench.get("notes", []),
    ]
    return ExecutionTrace(
        kind="measured", provenance="measured", source=f"{backend}: onnxruntime {bench.get('onnxruntime')}",
        fidelity="graph", fidelity_note="Operator events recorded by the ONNX Runtime profiler.",
        model_id=model_id, model_name=model_name, device_id=device_id, device_name=device_name,
        config={"precision": precision, "batch": batch}, precision=precision, batch=batch,
        totals=TraceTotals(
            latency_ms=lat["mean"], batch_ms=lat["mean"], period_ms=lat["mean"], busy_fraction=1.0,
            energy_per_inference_mj=0.0, energy_per_batch_mj=0.0, average_power_w=0.0, active_power_w=0.0,
            peak_working_memory_bytes=float(rss or 0), analysis_memory_bytes=0.0, memory_capacity_bytes=0.0,
            weights_bytes=0.0, flops_per_batch=0.0,
        ),
        components=[], paths=[], tensors=[], events=events, stages=stages, memory_timeline=[],
        bottlenecks=[Bottleneck(kind="compute", title=f"{e.name} takes {e.duration_ms / total:.0%} of the profiled run",
                                detail=f"{e.op}, {e.duration_ms:.3g} ms measured", event=e.index) for e in slow],
        thermal=None, energy_per_inference=EnergySplit(), assumptions=notes,
    )  # fmt: skip


def compare_by_op(predicted: ExecutionTrace, measured: ExecutionTrace) -> list[dict[str, Any]]:
    """Time per operator type, predicted vs measured (names differ after runtime fusion; types do not)."""
    p: dict[str, float] = defaultdict(float)
    m: dict[str, float] = defaultdict(float)
    for e in predicted.events:
        if e.kind == "operator":
            p[e.op] += e.duration_ms
    for e in measured.events:
        m[e.op] += e.duration_ms
    rows = []
    for op in sorted(set(p) | set(m), key=lambda k: -(m.get(k, 0) + p.get(k, 0))):
        pm, mm = p.get(op, 0.0), m.get(op, 0.0)
        rows.append({"op": op, "predicted_ms": pm, "measured_ms": mm,
                     "error_percent": (pm / mm - 1) * 100 if mm > 0 else None})  # fmt: skip
    return rows
