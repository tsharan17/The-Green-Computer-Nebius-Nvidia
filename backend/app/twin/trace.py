"""Digital twin v1: a predicted, operator-level execution trace of one batch on one device.

Built from the SAME equations as the headline numbers, not a separate model:
  * the nominal operating point (clock after thermal throttling, temperature, leakage) comes from
    the coupled solver (engine/solver.py) for this exact config, workload and calibration;
  * per-operator time is the per-layer roofline (engine/roofline.py) at that clock:
        t_i = max(t_compute,i, t_memory,i) + KAPPA * min(...)  + launch overhead;
  * per-operator energy splits the power model (engine/power.py) term by term:
        compute = P_dyn(f) * t_compute,i     memory = P_mem * t_memory,i
        idle/leakage/cooling = P_idle, P_leak(T), P_fan  x  duration
    so summing the events reproduces the engine's batch time and active power;
  * tensor lifetimes come from the ONNX graph wiring (producer -> last consumer), so the working
    memory peak is derived from the graph rather than the engine's conservative fit heuristic.

Everything here is a prediction (`kind="predicted"`). A measured trace only ever comes from an
execution backend or an imported log, and is a different object.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

from app.engine import assumptions as A
from app.engine.roofline import roofline_at
from app.engine.solver import corners, simulate
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import Calibration
from app.schemas.metrics import Provenance
from app.schemas.model import ModelProfile
from app.schemas.project import Workload
from app.schemas.results import Config, EvaluationResult
from app.schemas.twin import (
    Bottleneck,
    Component,
    EnergySplit,
    ExecutionTrace,
    Layout,
    MemoryPath,
    MemoryPoint,
    Stage,
    TensorInfo,
    ThermalView,
    TraceEvent,
    TraceTotals,
)

MAX_STAGES = 10


def _r(x: float, n: int = 6) -> float:
    if x == 0 or not math.isfinite(x):
        return float(x)
    return round(x, max(0, n - 1 - int(math.floor(math.log10(abs(x))))))


def _components(hw: HardwareProfile, cfg: Config, weights_in_flash: bool, onchip: bool) -> list[Component]:
    mode = hw.mode(cfg.power_mode_id)
    cooling = hw.cooling(cfg.cooling_id)
    peak = hw.peak_tflops.get(cfg.precision, 0.0)
    unit = "TOPS" if cfg.precision == "int8" else "TFLOPS"
    mcu = hw.kind == "mcu"
    comps = [
        Component(id="compute", kind="compute", name="Compute engine",
                  detail=f"{peak:g} {unit} peak at {cfg.precision.upper()}, {mode.name}",
                  capacity=peak, capacity_unit=unit, layout=Layout(x=0.30, y=0.32, w=0.36, h=0.30, z=1)),
        Component(id="ram", kind="memory", name="SRAM" if mcu else "Main memory",
                  detail=f"{hw.mem_gb * 1e6:.0f} KB" if hw.mem_gb < 0.01 else f"{hw.mem_gb:g} GB, {hw.mem_bw_gbs:g} GB/s",
                  capacity=hw.mem_gb, capacity_unit="GB", layout=Layout(x=0.71, y=0.32, w=0.29, h=0.30, z=1)),
        Component(id="power", kind="power", name="Power delivery",
                  detail=f"mode cap {mode.cap_w:g} W", capacity=mode.cap_w, capacity_unit="W",
                  layout=Layout(x=0.30, y=0.76, w=0.36, h=0.22, z=0)),
        Component(id="cooling", kind="cooling", name=cooling.name,
                  detail=f"{cooling.r_th_k_per_w:g} K/W" + (f", fan {cooling.fan_w:g} W" if cooling.fan_w else ""),
                  capacity=cooling.r_th_k_per_w, capacity_unit="K/W",
                  layout=Layout(x=0.30, y=0.0, w=0.36, h=0.22, z=2)),
    ]  # fmt: skip
    if weights_in_flash:
        comps.append(Component(id="flash", kind="storage", name="Flash (weights)",
                               detail=f"{(hw.weights_mem_gb or 0) * 1e6:.0f} KB",
                               capacity=hw.weights_mem_gb, capacity_unit="GB",
                               layout=Layout(x=0.71, y=0.70, w=0.29, h=0.28, z=1)))  # fmt: skip
    if onchip:
        comps.append(Component(id="onchip", kind="memory", name="On-chip memory",
                               detail=f"{hw.onchip_weights_mb:g} MB" + (f", {hw.onchip_bw_gbs:g} GB/s" if hw.onchip_bw_gbs else ""),
                               capacity=(hw.onchip_weights_mb or 0) / 1e3, capacity_unit="GB",
                               layout=Layout(x=0.0, y=0.32, w=0.26, h=0.30, z=1)))  # fmt: skip
    return comps


def _stages(events: list[TraceEvent]) -> list[Stage]:
    ops = [e for e in events if e.kind in ("operator", "aggregate")]
    if not ops:
        return []
    total = sum(e.duration_ms for e in ops) or 1.0
    target = total / min(MAX_STAGES, len(ops))
    groups: list[list[TraceEvent]] = [[]]
    acc = 0.0
    for e in ops:
        if groups[-1] and acc >= target and len(groups) < MAX_STAGES:
            groups.append([])
            acc = 0.0
        groups[-1].append(e)
        acc += e.duration_ms
    out = []
    for i, g in enumerate(groups):
        dom = Counter(e.op for e in g).most_common(1)[0][0]
        for e in g:
            e.stage = i
        out.append(Stage(index=i, name=f"Stage {i + 1}: {g[0].name} … {g[-1].name}" if len(g) > 1 else f"Stage {i + 1}: {g[0].name}",
                         first_event=g[0].index, last_event=g[-1].index, start_ms=g[0].start_ms,
                         duration_ms=_r(sum(e.duration_ms for e in g)),
                         energy_mj=_r(sum(e.energy_mj.total for e in g)), dominant_op=dom))  # fmt: skip
    return out


def build_trace(
    model: ModelProfile,
    hw: HardwareProfile,
    cfg: Config,
    workload: Workload,
    result: EvaluationResult,
    calibration: Calibration | None = None,
    graph_source: str = "",
) -> ExecutionTrace:
    """`model` should carry a parsed graph (layers) when one exists; `result` is the evaluation of
    the same (model, hw, cfg, workload) so headline numbers and the trace agree."""
    assumptions: list[str] = []
    corner = corners(model, calibration)[1]
    sim = simulate(model, hw, cfg, workload, corner)
    mode = hw.mode(cfg.power_mode_id)
    cooling = hw.cooling(cfg.cooling_id)
    rf = roofline_at(model, hw, sim.freq_scale, mode.mem_scale, cfg.precision, cfg.batch,
                     cfg.resolution_scale, corner.eta, cfg.sparsity, detail=True)  # fmt: skip
    t_l, t_c, t_m = rf.per_layer  # type: ignore[misc]
    layers = model.layers or []
    has_graph = bool(layers)
    wired = has_graph and model.tensors is not None and any(layer.out_tensors for layer in layers)
    n_rows = len(t_l)
    launch = hw.launch_overhead_s if hw.launch_overhead_s is not None else A.LAUNCH_OVERHEAD_S
    overhead_each = rf.t_overhead_s / n_rows
    dur_s = t_l + overhead_each

    # Agree with the headline batch time (identical unless a measurement replaced it).
    bt = result.metrics["batch_time"]
    k = (bt.value / 1e3) / float(dur_s.sum()) if dur_s.sum() > 0 else 1.0
    measured_total = bt.provenance == Provenance.MEASURED
    if measured_total:
        assumptions.append(
            "The batch time is measured; the split between operators is predicted and scaled to the "
            "measured total."
        )
    prov = "estimated" if measured_total else ("calibrated" if calibration and calibration.latency_calibrated else "analytical")

    # Power terms at the solved operating point (the same ones engine/power.py sums).
    ps = corner.power
    leak_w = hw.leak_w_25c * ps * math.exp(A.K_LEAK * (sim.temp_c - 25.0))
    idle_w, fan_w = hw.idle_w * ps, cooling.fan_w * ps
    dyn_coef = hw.dyn_w_max * ps * (sim.freq_scale**hw.gamma)
    mem_coef = hw.mem_w_max * ps * mode.mem_scale

    bpe = A.BYTES_PER_ELEM[cfg.precision]
    s2 = cfg.resolution_scale**2
    act_scale = cfg.batch * s2 * (1.0 - 0.5 * cfg.sparsity) * bpe
    resident = hw.onchip_weights_mb is not None and rf.weights_gb * 1e9 <= 0.85 * hw.onchip_weights_mb * 1e6
    weights_in_flash = hw.weights_mem_gb is not None
    if has_graph:
        params = np.array([layer.params for layer in layers]) * (1 - cfg.sparsity)
        acts = np.array([layer.act_elems for layer in layers]) * act_scale
        flops = np.array([layer.flops for layer in layers]) * cfg.batch * s2 * (1 - cfg.sparsity)
        names = [layer.name for layer in layers]
        ops = [layer.op for layer in layers]
    else:
        params = np.array([model.params_m * 1e6 * (1 - cfg.sparsity)])
        acts = np.array([model.act_mb_fp32 * 1e6 / 4.0]) * act_scale
        flops = np.array([model.gflops * 1e9 * cfg.batch * s2 * (1 - cfg.sparsity)])
        names, ops = [f"{model.name} (whole model)"], ["Model"]
    w_bytes = np.zeros_like(params) if resident else params * bpe
    bw = hw.mem_bw_gbs * 1e9 * mode.mem_scale

    # --- tensor lifetimes ------------------------------------------------------------------
    tensors: list[TensorInfo] = []
    alloc_at: dict[int, list[str]] = defaultdict(list)
    free_at: dict[int, list[str]] = defaultdict(list)
    n = len(names)
    tensor_bytes: dict[str, float] = {}
    if wired:
        producer: dict[str, int] = {}
        last: dict[str, int] = {}
        for i, layer in enumerate(layers):
            for t in layer.out_tensors:
                producer.setdefault(t, i)
            for t in layer.in_tensors:
                last[t] = i
        outputs = set(model.graph_outputs)
        sizes = model.tensors or {}
        for t, elems in sizes.items():
            p = producer.get(t, -1)
            lu = n if t in outputs else max(last.get(t, p), p)
            b = elems * act_scale
            tensor_bytes[t] = b
            kind = "output" if t in outputs else "input" if p == -1 else "activation"
            tensors.append(TensorInfo(id=t, bytes=_r(b), producer=p, last_use=lu, kind=kind))  # type: ignore[arg-type]
            if p >= 0:
                alloc_at[p].append(t)
            if lu < n:
                free_at[lu].append(t)
        fidelity, fidelity_note = "graph", (
            f"Operator-level trace from {graph_source or 'the ONNX graph'}; tensor lifetimes from the "
            "graph wiring (elementwise and view operators run in place)."
        )
    elif has_graph:
        fidelity, fidelity_note = "graph_approx", (
            "Operator-level times from the graph; this stored model has no tensor wiring, so each "
            "operator's inputs are assumed freed right after it (re-upload the ONNX for exact lifetimes)."
        )
    else:
        fidelity, fidelity_note = "aggregate", (
            "This model has no parsed graph (library figures or manual entry), so the trace is one "
            "aggregate step. Upload its ONNX file for an operator-level trace."
        )

    runtime_b = (hw.runtime_overhead_gb if hw.runtime_overhead_gb is not None else A.RUNTIME_OVERHEAD_GB) * 1e9
    weights_ram_b = 0.0 if weights_in_flash else rf.weights_gb * 1e9
    capacity_b = rf.mem_usable_gb * 1e9

    # --- events -------------------------------------------------------------------------------
    events: list[TraceEvent] = []
    timeline: list[MemoryPoint] = []
    t0 = 0.0
    live = sum(tensor_bytes[x.id] for x in tensors if x.producer == -1)  # model inputs exist up front
    peak_ws = 0.0
    for i in range(n):
        dur = float(dur_s[i]) * k
        tc, tm = float(t_c[i]) * k, float(t_m[i]) * k
        oh = overhead_each * k
        e = EnergySplit(
            compute=dyn_coef * tc * 1e3, memory=mem_coef * tm * 1e3, idle=idle_w * dur * 1e3,
            leakage=leak_w * dur * 1e3, cooling=fan_w * dur * 1e3,
        )  # fmt: skip
        if wired:
            new = sum(tensor_bytes[t] for t in alloc_at[i])
            ws = live + new
            live = ws - sum(tensor_bytes[t] for t in free_at[i])
            alloc, free = alloc_at[i], free_at[i]
        else:
            ws = float(acts[i]) if has_graph else float(acts[0]) * A.LIVE_ACT_FRACTION
            live, alloc, free = 0.0, [], []
        peak_ws = max(peak_ws, ws)
        moved = float(w_bytes[i] + acts[i])
        bw_used = moved / dur if dur > 0 else 0.0
        bound = "overhead" if oh > max(tc, tm) else ("compute" if tc >= tm else "memory")
        used = ["compute", "power", "cooling", "ram"]
        used += ["flash"] if weights_in_flash else []
        used += ["onchip"] if hw.onchip_weights_mb else []
        events.append(TraceEvent(
            index=i, name=names[i], op=ops[i], kind="operator" if has_graph else "aggregate", stage=0,
            start_ms=_r(t0 * 1e3), duration_ms=_r(dur * 1e3), compute_ms=_r(tc * 1e3),
            memory_ms=_r(tm * 1e3), overhead_ms=_r(oh * 1e3), bound=bound,  # type: ignore[arg-type]
            flops=_r(float(flops[i])), weight_bytes=_r(float(w_bytes[i])), activation_bytes=_r(float(acts[i])),
            bandwidth_gbs=_r(bw_used / 1e9), bandwidth_utilisation=_r(bw_used / bw if bw else 0.0),
            alloc=alloc, free=free, working_set_bytes=_r(ws), live_after_bytes=_r(live),
            power_w=_r(e.total / (dur * 1e3)) if dur > 0 else 0.0, energy_mj=e,
            components=sorted(set(used)),
        ))  # fmt: skip
        total_mem = weights_ram_b + runtime_b + ws
        timeline.append(MemoryPoint(t_ms=_r(t0 * 1e3), event=i, weights_bytes=_r(weights_ram_b),
                                    runtime_bytes=_r(runtime_b), activation_bytes=_r(ws),
                                    total_bytes=_r(total_mem), capacity_bytes=_r(capacity_b),
                                    over_capacity=total_mem > capacity_b))  # fmt: skip
        t0 += dur

    batch_s = t0
    period_s = cfg.batch / workload.fps
    idle_s = max(0.0, period_s - batch_s)
    if idle_s > 0:
        floor_w = idle_w + leak_w + fan_w
        events.append(TraceEvent(
            index=n, name="Idle until the next batch", op="idle", kind="idle", stage=0,
            start_ms=_r(batch_s * 1e3), duration_ms=_r(idle_s * 1e3), bound="idle",
            power_w=_r(floor_w), components=["power", "cooling"],
            energy_mj=EnergySplit(idle=idle_w * idle_s * 1e3, leakage=leak_w * idle_s * 1e3,
                                  cooling=fan_w * idle_s * 1e3),
        ))  # fmt: skip
    else:
        assumptions.append(
            f"The device cannot keep up with {workload.fps:g} inputs/s at this setting: there is no idle "
            "time between batches."
        )

    # Agree with the headline energy too: the headline may come from the aggregate model while the
    # trace splits the graph, which moves the total by a few percent. Scale the split, report it.
    ke = 1.0
    e_trace = sum(ev.energy_mj.total for ev in events)
    pw_metric = result.metrics["power_avg"]
    if e_trace > 0 and pw_metric.provenance != Provenance.MEASURED:
        ke = result.metrics["energy_per_inference"].value * cfg.batch / e_trace
        for ev in events:
            ev.energy_mj = EnergySplit(**{f: getattr(ev.energy_mj, f) * ke for f in EnergySplit.model_fields})
            ev.power_w = _r(ev.energy_mj.total / ev.duration_ms) if ev.duration_ms > 0 else 0.0
        if abs(ke - 1) > 0.005:
            assumptions.append(
                f"Event energies are scaled by {ke:.3f} so they add up to the headline energy per inference "
                "(the headline uses the model's aggregate figures; the trace splits its graph)."
            )

    hottest = max((ev.power_w for ev in events), default=1.0) or 1.0
    for ev in events:
        ev.intensity = _r(ev.power_w / hottest, 4)
    stages = _stages(events)

    per_batch = EnergySplit(
        compute=sum(ev.energy_mj.compute for ev in events), memory=sum(ev.energy_mj.memory for ev in events),
        idle=sum(ev.energy_mj.idle for ev in events), leakage=sum(ev.energy_mj.leakage for ev in events),
        cooling=sum(ev.energy_mj.cooling for ev in events),
    )  # fmt: skip
    per_inf = EnergySplit(**{f: _r(getattr(per_batch, f) / cfg.batch) for f in EnergySplit.model_fields})
    period_used = max(period_s, batch_s)
    avg_w = per_batch.total / (period_used * 1e3) if period_used else 0.0
    active_w = sum(ev.energy_mj.total for ev in events[:n]) / (batch_s * 1e3) if batch_s else 0.0

    # --- components, heat and paths -------------------------------------------------------------
    hw_parts = _components(hw, cfg, weights_in_flash, bool(hw.onchip_weights_mb))
    heat = {
        "compute": (per_batch.compute + per_batch.leakage) / (period_used * 1e3),
        "ram": per_batch.memory / (period_used * 1e3),
        "power": per_batch.idle / (period_used * 1e3),
        "cooling": per_batch.cooling / (period_used * 1e3),
    }
    total_heat = sum(heat.values()) or 1.0
    for c in hw_parts:
        c.heat_w = _r(heat.get(c.id, 0.0), 4)
        c.heat_share = _r(heat.get(c.id, 0.0) / total_heat, 4)
        c.provenance = prov  # type: ignore[assignment]
    peak_util = max((ev.bandwidth_utilisation for ev in events[:n]), default=0.0)
    paths = [MemoryPath(id="ram-compute", source="flash" if weights_in_flash else "ram", target="compute",
                        label="weights + activations" if not weights_in_flash else "weights (execute in place)",
                        bandwidth_gbs=_r(bw / 1e9), peak_utilisation=_r(peak_util, 4))]  # fmt: skip
    if weights_in_flash:
        paths.append(MemoryPath(id="sram-compute", source="ram", target="compute", label="activations",
                                bandwidth_gbs=_r(bw / 1e9), peak_utilisation=_r(peak_util, 4)))  # fmt: skip
    if hw.onchip_weights_mb:
        paths.append(MemoryPath(id="onchip-compute", source="onchip", target="compute",
                                label="weights resident on chip" if resident else "on-chip buffers (weights spill)",
                                bandwidth_gbs=_r(hw.onchip_bw_gbs or hw.mem_bw_gbs), peak_utilisation=0.0))  # fmt: skip

    # --- bottlenecks -------------------------------------------------------------------------
    bnecks: list[Bottleneck] = []
    if sim.throttled:
        bnecks.append(Bottleneck(kind="thermal", component="cooling", title="Thermal throttling",
                                 detail=f"The governor holds the clock at {sim.clock_factor:.0%} so the chip stays at "
                                        f"{hw.t_throttle_c:g} °C; every operator runs that much slower."))  # fmt: skip
    over = [p for p in timeline if p.over_capacity]
    if over:
        bnecks.append(Bottleneck(kind="capacity", component="ram", event=over[0].event,
                                 title="Does not fit in memory",
                                 detail=f"Working memory reaches {max(p.total_bytes for p in timeline) / 1e9:.3g} GB; "
                                        f"usable capacity is {capacity_b / 1e9:.3g} GB."))  # fmt: skip
    if idle_s <= 0:
        bnecks.append(Bottleneck(kind="throughput", component="compute", title="Cannot keep up with the input rate",
                                 detail=f"One batch takes {batch_s * 1e3:.3g} ms but inputs arrive every "
                                        f"{period_s * 1e3:.3g} ms."))  # fmt: skip
    for ev in sorted(events[:n], key=lambda x: -x.duration_ms)[:3]:
        share = ev.duration_ms / (batch_s * 1e3) if batch_s else 0
        why = {
            "compute": f"compute-bound: {ev.flops / 1e9:.3g} GFLOP in {ev.duration_ms:.3g} ms",
            "memory": f"memory-bound: moves {(ev.weight_bytes + ev.activation_bytes) / 1e6:.3g} MB at "
                      f"{ev.bandwidth_gbs:.3g} GB/s ({ev.bandwidth_utilisation:.0%} of the path)",
            "overhead": "dominated by per-operator launch overhead",
        }[ev.bound]  # fmt: skip
        bnecks.append(Bottleneck(kind=ev.bound, event=ev.index,  # type: ignore[arg-type]
                                 component="compute" if ev.bound == "compute" else "ram",
                                 title=f"{ev.name} takes {share:.0%} of the batch", detail=why))  # fmt: skip

    # --- thermal ---------------------------------------------------------------------------------
    p_unthr = sim.p_avg_unthrottled_w
    need = (hw.t_throttle_c - workload.ambient_c) / p_unthr if p_unthr > 0 else None
    thermal = ThermalView(
        ambient_c=workload.ambient_c, steady_c=_r(sim.temp_c, 4), unthrottled_c=_r(sim.temp_unthrottled_c, 4),
        throttle_c=hw.t_throttle_c, shutdown_c=hw.t_shutdown_c, throttled=sim.throttled,
        clock_factor=_r(sim.clock_factor, 4), time_to_throttle_s=result.time_to_throttle_s,
        warmup=result.thermal_trace, cooling_name=cooling.name, r_th_k_per_w=cooling.r_th_k_per_w,
        r_th_needed_k_per_w=_r(need, 4) if need is not None and need > 0 else None,
        note="Lumped (single-node) thermal model: one junction temperature for the package, heated by "
             "every power term. The per-component heat split is an attribution of that power, not a "
             "simulated temperature map.",
    )  # fmt: skip

    assumptions += [
        "Predicted by the analytical engine; not a measurement and not a cycle-accurate simulation.",
        f"Operator times use the roofline with an achieved efficiency of "
        f"{(hw.eta_compute.get(cfg.precision, A.ETA_COMPUTE[cfg.precision]) * corner.eta):.2f} (compute) and "
        f"{((hw.eta_memory if hw.eta_memory is not None else A.ETA_MEMORY) * corner.eta):.2f} (memory), "
        f"plus {launch * 1e6:.0f} µs launch overhead per operator.",
        "Energy per operator splits the device power model term by term (dynamic compute, memory, idle, "
        "leakage at the solved temperature, fan).",
    ]
    if resident:
        assumptions.append("All weights fit in on-chip memory, so weight traffic is not charged to external memory.")

    analysis_mem = rf.mem_gb * 1e9
    totals = TraceTotals(
        latency_ms=_r(result.metrics["latency"].value), batch_ms=_r(batch_s * 1e3),
        period_ms=_r(period_used * 1e3), busy_fraction=_r(min(1.0, batch_s / period_s) if period_s else 1.0, 4),
        energy_per_inference_mj=_r(per_batch.total / cfg.batch), energy_per_batch_mj=_r(per_batch.total),
        average_power_w=_r(avg_w), active_power_w=_r(active_w),
        peak_working_memory_bytes=_r(weights_ram_b + runtime_b + peak_ws), analysis_memory_bytes=_r(analysis_mem),
        memory_capacity_bytes=_r(capacity_b), weights_bytes=_r(rf.weights_gb * 1e9),
        flops_per_batch=_r(float(flops.sum())),
    )  # fmt: skip
    for ev in events:
        ev.energy_mj = EnergySplit(**{f: _r(getattr(ev.energy_mj, f)) for f in EnergySplit.model_fields})
    return ExecutionTrace(
        kind="predicted", provenance=prov,  # type: ignore[arg-type]
        source=f"analytical engine {A.ENGINE_VERSION}", fidelity=fidelity,  # type: ignore[arg-type]
        fidelity_note=fidelity_note, model_id=model.id, model_name=model.name, device_id=hw.id,
        device_name=hw.name, config=cfg.model_dump(), precision=cfg.precision, batch=cfg.batch,
        totals=totals, components=hw_parts, paths=paths, tensors=tensors, events=events, stages=stages,
        memory_timeline=timeline, bottlenecks=bnecks, thermal=thermal, energy_per_inference=per_inf,
        assumptions=assumptions, consistency_scale=_r(k, 6), energy_consistency_scale=_r(ke, 6),
    )  # fmt: skip
