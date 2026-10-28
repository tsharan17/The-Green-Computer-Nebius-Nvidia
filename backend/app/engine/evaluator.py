"""Evaluate one (model, hardware, config, workload) point and certify it.

Every metric's band comes from re-solving the coupled model at an optimistic and a pessimistic
corner (see solver.corners), so uncertainty is propagated through throttling and leakage rather
than attached afterwards.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engine import assumptions as A
from app.engine.accuracy import accuracy_metric
from app.engine.constraints import certify, check
from app.engine.solver import Sim, corners, simulate
from app.engine.thermal import time_to_limit, warmup_trace
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import Calibration, MeasurementIn
from app.schemas.metrics import Feasibility, Metric, Provenance
from app.schemas.model import ModelProfile
from app.schemas.project import Constraints, Workload
from app.schemas.results import Config, ConstraintCheck, EvaluationResult, SolverInfo

AN = Provenance.ANALYTICAL
CAL = Provenance.CALIBRATED


def _band(
    sims: list[Sim], unit: str, get: Callable[[Sim], float], prov: Provenance = AN
) -> Metric:
    """sims = [optimistic, nominal, pessimistic]; band is the min/max over the corners."""
    vals = [get(s) for s in sims]
    return Metric(value=vals[1], unit=unit, low=min(vals), high=max(vals), provenance=prov)


def matches(m: MeasurementIn, hw: HardwareProfile, model_id: str, cfg: Config) -> bool:
    """A measurement is the same operating point as `cfg` (clock trim 1.0 only)."""
    top = max(hw.power_modes, key=lambda p: p.cap_w).id
    return (
        m.device_id == hw.id
        and m.model_id == model_id
        and m.precision == cfg.precision
        and m.batch == cfg.batch
        and m.resolution_scale == cfg.resolution_scale
        and cfg.sparsity == 0.0
        and cfg.clock_trim == 1.0
        and (m.power_mode_id or top) == cfg.power_mode_id
        and (m.cooling_id is None or m.cooling_id == cfg.cooling_id)
        and m.batch_latency_ms is not None
    )


def evaluate(
    model: ModelProfile,
    hw: HardwareProfile,
    config: Config,
    workload: Workload,
    constraints: Constraints,
    measured_retention: float | None = None,
    calibration: Calibration | None = None,
    measured: MeasurementIn | None = None,
    accuracy_reference: float | None = None,
) -> EvaluationResult:
    """`calibration` re-centres and re-bands the model from measurements; `measured` (an exact
    match for this operating point) replaces batch time, capacity and active power with the
    measured values, labelled as such."""
    cooling = hw.cooling(config.cooling_id)
    sims = [simulate(model, hw, config, workload, c) for c in corners(model, calibration)]
    nom = sims[1]
    lat_prov = CAL if calibration and calibration.latency_calibrated else AN
    pw_prov = CAL if calibration and calibration.power_calibrated else AN
    en_prov = CAL if lat_prov == CAL and pw_prov == CAL else AN

    # Worst-case frame latency. For a real-time stream the first frame of a batch also waits
    # for the other B-1 frames to arrive.
    wait_ms = (config.batch - 1) / workload.fps * 1e3 if workload.streaming else 0.0
    latency = _band(sims, "ms", lambda s: s.rf.t_batch_s * 1e3 + wait_ms, lat_prov)
    batch_time = _band(sims, "ms", lambda s: s.rf.t_batch_s * 1e3, lat_prov)
    capacity = _band(sims, "fps", lambda s: s.pw.capacity_fps, lat_prov)
    p_avg = _band(sims, "W", lambda s: s.pw.p_avg_w, pw_prov)
    p_peak = _band(sims, "W", lambda s: s.pw.p_active_w, pw_prov)
    energy = _band(sims, "mJ/inference", lambda s: s.pw.energy_per_inf_mj, en_prov)
    mem = _band(sims, "GB", lambda s: s.rf.mem_gb)
    temp = _band(sims, "°C", lambda s: s.temp_c)
    temp_free = _band(sims, "°C", lambda s: s.temp_unthrottled_c)
    headroom = Metric(
        value=hw.t_throttle_c - temp_free.value,
        unit="°C",
        low=hw.t_throttle_c - temp_free.high,
        high=hw.t_throttle_c - temp_free.low,
        provenance=AN,
    )
    clock = _band(sims, "× clock", lambda s: s.clock_factor)
    kwh = [s.pw.p_avg_w * workload.hours_per_day / 1000.0 for s in sims]
    cost = Metric(
        value=kwh[1] * workload.price_per_kwh,
        unit="currency/day",
        low=min(kwh) * workload.price_per_kwh,
        high=max(kwh) * workload.price_per_kwh,
        provenance=AN,
    )
    gco2 = workload.grid_gco2_per_kwh / 1000.0
    carbon = Metric(
        value=kwh[1] * gco2,
        unit="kgCO2e/day",
        low=min(kwh) * gco2,
        high=max(kwh) * gco2,
        provenance=AN,
    )
    acc, acc_measured = accuracy_metric(
        model, config.precision, constraints, measured_retention, config.resolution_scale,
        config.sparsity, accuracy_reference,
    )

    if measured is not None and measured.batch_latency_ms is not None:
        mean = measured.batch_latency_ms
        lo = min(measured.latency_ms_p50 or mean, mean)
        hi = max(measured.latency_ms_p95 or mean, mean)
        MS = Provenance.MEASURED
        batch_time = Metric(value=mean, unit="ms", low=lo, high=hi, provenance=MS)
        latency = Metric(
            value=mean + wait_ms, unit="ms", low=lo + wait_ms, high=hi + wait_ms, provenance=MS
        )
        b = config.batch * 1e3
        capacity = Metric(value=b / mean, unit="fps", low=b / hi, high=b / lo, provenance=MS)
        if measured.power_w_avg is not None:
            p_peak = Metric(
                value=measured.power_w_avg,
                unit="W",
                low=measured.power_w_avg,
                high=max(measured.power_w_peak or 0.0, measured.power_w_avg),
                provenance=MS,
            )

    if temp_free.low >= hw.t_throttle_c:
        throttle_risk = "high"
    elif temp_free.high >= hw.t_throttle_c:
        throttle_risk = "medium"
    else:
        throttle_risk = "low"

    bottleneck = nom.rf.bottleneck
    if nom.throttled and throttle_risk == "high":
        bottleneck = "heat"

    acc_note = (
        "relative to baseline"
        if constraints.accuracy_mode == "relative"
        else "absolute value of the model accuracy metric"
    )
    metrics_extra: dict[str, Metric] = {}
    notes_pre: list[str] = []
    wh = hw.extras.get("battery_wh")
    if isinstance(wh, (int, float)) and p_avg.value > 0:
        eff = hw.extras.get("regulator_efficiency", 0.9)
        eff = float(eff) if isinstance(eff, (int, float)) else 0.9
        metrics_extra["battery_hours"] = Metric(
            value=wh * eff / p_avg.value, unit="h", low=wh * eff / max(p_avg.high, 1e-9),
            high=wh * eff / max(p_avg.low, 1e-9), provenance=pw_prov,
        )
    checks: list[tuple[ConstraintCheck, Provenance]] = [
        (check("power (average)", "<=", constraints.max_power_w, p_avg), AN),
        (check("accuracy", ">=", constraints.min_accuracy, acc, note=acc_note), acc.provenance),
        (check("throughput", ">=", workload.fps, capacity, note="after any throttling"),
            capacity.provenance,
        ),
        (
            check(
                "memory fit",
                "<=",
                nom.rf.mem_usable_gb,
                mem,
                note="usable device memory",
            ),
            AN,
        ),
        (
            check("temperature held below throttle point", "<=", hw.t_throttle_c, temp), AN,
        ),
    ]  # fmt: skip
    if hw.weights_mem_gb is not None:
        weights = _band(sims, "GB", lambda s: s.rf.weights_gb)
        metrics_extra["weights"] = weights
        checks.append((check(
            "weights fit in storage", "<=", hw.weights_mem_gb * 0.95, weights,
            note="flash / model memory",
        ), AN))  # fmt: skip
        if weights.value > hw.weights_mem_gb * 0.95:
            notes_pre.append("Model weights do not fit in the device's weight storage.")
    if constraints.max_latency_ms is not None:
        checks.append((check(
                    "latency",
                    "<=",
                    constraints.max_latency_ms,
                    latency,
                    note="worst-case frame, incl. batch wait" if wait_ms else "",
                ),
                latency.provenance,
            )
        )
    if constraints.max_memory_gb is not None:
        checks.append((check("memory limit", "<=", constraints.max_memory_gb, mem), AN))
    if constraints.forbid_throttling:
        checks.append((check("no sustained throttling", "<=", hw.t_throttle_c, temp_free), AN))

    state = certify(checks)
    notes: list[str] = list(notes_pre)
    capped = (
        config.precision in A.UNMEASURED_CAPPED_PRECISIONS
        or config.resolution_scale < 1.0
        or config.sparsity > 0.0
    )
    if state == Feasibility.PREDICTED_FEASIBLE and not acc_measured and capped:
        state = Feasibility.UNCERTAIN
        if config.precision in A.UNMEASURED_CAPPED_PRECISIONS:
            what = f"{config.precision} accuracy"
        elif config.sparsity > 0.0:
            what = "the pruned model's accuracy (it needs fine-tuning and measuring)"
        else:
            what = "the reduced-resolution accuracy"
        notes.append(
            f"{what} is an unmeasured prior; measure it on your validation set before relying "
            "on this configuration."
        )
    if mem.value > nom.rf.mem_usable_gb:
        notes.append("Model does not fit in usable device memory.")
    if nom.throttled and not nom.overheated:
        notes.append(
            f"The thermal governor lowers the clock to {nom.clock_factor:.0%} to hold "
            f"{hw.t_throttle_c:.0f} °C, which cuts capacity to {nom.pw.capacity_fps:.1f} fps."
        )
    if nom.overheated:
        notes.append(
            "Even at the minimum clock this cooling cannot hold the throttle temperature "
            "(or leakage runs away): the configuration is not thermally sustainable."
        )
    if model.fidelity == "manual":
        notes.append("Manual model parameters: uncertainty bands are widened.")

    t_throttle = None
    trace: list[tuple[float, float]] = []
    r_th = cooling.r_th_k_per_w
    if nom.p_avg_unthrottled_w > 0:
        if nom.throttled:
            t_throttle = time_to_limit(
                nom.p_avg_unthrottled_w, r_th, cooling.c_th_j_per_k, workload.ambient_c,
                hw.t_throttle_c,
            )  # fmt: skip
        trace = warmup_trace(
            nom.p_avg_unthrottled_w, r_th, cooling.c_th_j_per_k, workload.ambient_c,
            hw.t_throttle_c if nom.throttled else None,
        )  # fmt: skip

    metrics = {
        "latency": latency,
        "batch_time": batch_time,
        "capacity_fps": capacity,
        "power_avg": p_avg,
        "power_peak": p_peak,
        "energy_per_inference": energy,
        "memory": mem,
        "temperature": temp,
        "temperature_unthrottled": temp_free,
        "thermal_headroom": headroom,
        "clock_factor": clock,
        "accuracy": acc,
        "cost_per_day": cost,
        "carbon_per_day": carbon,
        **metrics_extra,
    }
    return EvaluationResult(
        config=config,
        metrics=metrics,
        bottleneck=bottleneck,  # type: ignore[arg-type]
        throttle_risk=throttle_risk,  # type: ignore[arg-type]
        feasibility=state,
        checks=[c for c, _ in checks],
        notes=notes,
        solver=SolverInfo(
            converged=nom.converged,
            iterations=nom.iterations,
            throttled=nom.throttled,
            clock_factor=nom.clock_factor,
        ),
        time_to_throttle_s=t_throttle,
        thermal_trace=trace,
        engine_version=A.ENGINE_VERSION,
    )
