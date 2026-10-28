"""Deeper physics views for a project (multi-node thermal, memory hierarchy, runtime effects) and
the evidence behind every headline number, including how good the device's calibration is."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.data_loader import get_device
from app.db import repository as repo
from app.engine import assumptions as A
from app.engine.evaluator import matches
from app.engine.runtime_effects import memory_hierarchy, runtime_effects
from app.engine.thermal_nodes import thermal_view
from app.schemas.hardware import HardwareProfile
from app.schemas.project import ProjectOut
from app.schemas.results import Config, OptimisationResult
from app.services import measurement_service as ms
from app.services.model_lookup import resolve_model
from app.services.twin_service import compute_trace, config_for

Which = Literal["baseline", "best"]


def physics(db: Session, ws: int, project: ProjectOut, which: Which) -> dict[str, Any]:
    cfg, label = config_for(db, project, which, None, ws)
    spec = project.spec
    hw = get_device(spec.device_id, ws)
    t = compute_trace(db, spec, cfg, ws)
    mode = hw.mode(cfg.power_mode_id)
    out: dict[str, Any] = {"which": which, "label": label, "config": cfg.model_dump(), "trace_kind": t.kind,
                           "trace_provenance": t.provenance}  # fmt: skip
    if t.thermal is not None:
        meas = _measured_temperature(db, ws, hw, spec.model_id, cfg)
        out["thermal"] = thermal_view(hw, [c.model_dump() for c in t.components], t.thermal.ambient_c, t.thermal.steady_c,
                                      t.thermal.r_th_k_per_w, meas[0] if meas else None, meas[1] if meas else "")  # fmt: skip
    else:
        out["thermal"] = None
    out["memory"] = memory_hierarchy(hw, t, mode.mem_scale)
    out["runtime"] = runtime_effects(hw, t, spec.workload.fps, spec.workload.streaming, cfg.batch)
    return out


def _measured_temperature(db: Session, ws: int, hw: HardwareProfile, model_id: str, cfg: Config) -> tuple[float, str] | None:
    model = resolve_model(db, model_id, ws)
    for m in ms.measurements_for(db, hw.id, model.id, ws):
        if m.temperature_c_max is not None and matches(m, hw, model.id, cfg):
            return m.temperature_c_max, f"{m.source} measurement" + (f" ({m.hardware_label})" if m.hardware_label else "")
    return None


# Which profile facts and inputs drive each headline metric (for the evidence panel).
DRIVERS: dict[str, list[tuple[str, str]]] = {
    "latency": [("peak_tflops", "Peak compute"), ("mem_bw_gbs", "Memory bandwidth"), ("launch", "Launch overhead per layer")],
    "batch_time": [("peak_tflops", "Peak compute"), ("mem_bw_gbs", "Memory bandwidth")],
    "capacity_fps": [("peak_tflops", "Peak compute"), ("mem_bw_gbs", "Memory bandwidth")],
    "power_avg": [("power_model", "Power model (idle, dynamic, memory)"), ("power_modes", "Power mode cap")],
    "power_peak": [("power_model", "Power model"), ("power_modes", "Power mode cap")],
    "energy_per_inference": [("power_model", "Power model"), ("peak_tflops", "Peak compute"), ("mem_bw_gbs", "Memory bandwidth")],
    "memory": [("mem_gb", "Memory size"), ("model", "Model weights and activations")],
    "temperature": [("thermal", "Thermal resistance and limits"), ("power_model", "Power model"), ("ambient", "Ambient temperature (your input)")],
    "temperature_unthrottled": [("thermal", "Thermal resistance"), ("power_model", "Power model"), ("ambient", "Ambient temperature (your input)")],
    "thermal_headroom": [("thermal", "Throttle limit and thermal resistance")],
    "clock_factor": [("thermal", "Throttle limit"), ("power_model", "Power model")],
    "accuracy": [("accuracy", "Accuracy retention per precision")],
    "cost_per_day": [("price", "Electricity price (your input)"), ("power_model", "Power model")],
    "carbon_per_day": [("grid", "Grid carbon intensity (your input)"), ("power_model", "Power model")],
}  # fmt: skip

INPUT_KEYS = {"ambient": "your input", "price": "your input", "grid": "your input", "model": "model profile",
              "launch": "engine assumption", "accuracy": "measured retention if imported, else assumed"}  # fmt: skip


def _grade(fit: dict[str, Any] | None) -> tuple[str, str]:
    if not fit or not fit.get("n"):
        return "none", "No measurements of this device at this precision yet."
    n, after, ok = fit["n"], fit.get("error_after"), fit.get("accepted")
    if n < A.CALIB_MIN_POINTS:
        return "too few", f"{n} measurement(s); at least {A.CALIB_MIN_POINTS} are needed to calibrate."
    if not ok:
        return "rejected", fit.get("reason", "The fit did not improve predictions enough.")
    if after is not None and after <= 0.08 and n >= 6:
        return "strong", f"{n} points, {after:.1%} leave-one-out error."
    if after is not None and after <= 0.15:
        return "good", f"{n} points, {after:.1%} leave-one-out error."
    return "fair", f"{n} points, {after:.1%} leave-one-out error." if after is not None else f"{n} points."


def calibration_quality(db: Session, ws: int, device_id: str) -> dict[str, Any]:
    hist = repo.calibration_history(db, device_id, ws)
    latest: dict[str, dict[str, Any]] = {}
    for row in hist:  # newest first
        latest.setdefault(row["precision"], row)
    active = ms.active_calibrations(db, device_id, ws)
    hw = get_device(device_id, ws)
    meas = [m for m in repo.list_measurements(db, device_id=device_id, workspace_id=ws) if not m.synthetic]
    rows = []
    for prec in sorted(set(hw.peak_tflops) | set(latest)):
        h = latest.get(prec)
        lat_grade, lat_why = _grade(h["latency"] if h else None)
        pw_grade, pw_why = _grade(h["power"] if h else None)
        pm = [m for m in meas if m.precision == prec]
        rows.append({
            "precision": prec, "applied": prec in active, "version": h["version"] if h else None,
            "scope": h["scope"] if h else None, "fitted_at": h["created_at"] if h else None,
            "latency": {"grade": lat_grade, "why": lat_why, **({k: h["latency"].get(k) for k in ("n", "error_before", "error_after", "factor")} if h else {})},
            "power": {"grade": pw_grade, "why": pw_why, **({k: h["power"].get(k) for k in ("n", "error_before", "error_after", "factor")} if h else {})},
            "measurements": len(pm), "batches": sorted({m.batch for m in pm}),
            "with_temperature": sum(1 for m in pm if m.temperature_c_max is not None),
        })  # fmt: skip
    return {"device_id": device_id, "device_name": hw.name, "verified_profile": hw.verified, "precisions": rows,
            "note": "Calibration rescales compute efficiency and power to match real runs of this device. It is only "
                    "applied when leave-one-out error improves enough; every refit is kept for audit."}  # fmt: skip


def evidence(db: Session, ws: int, project: ProjectOut) -> dict[str, Any]:
    """For each headline metric of today and the plan: value, band, provenance, and the facts behind it."""
    raw = repo.latest_run(db, project.id, "optimise")
    spec = project.spec
    hw = get_device(spec.device_id, ws)
    sources = {k: (v.value if hasattr(v, "value") else str(v)) for k, v in (hw.sources or {}).items()}
    out: dict[str, Any] = {"device": {"id": hw.id, "name": hw.name, "verified": hw.verified, "notes": hw.notes},
                           "calibration": calibration_quality(db, ws, hw.id), "setups": []}  # fmt: skip
    if raw is None:
        out["message"] = "Analyse the project to see the evidence behind each number."
        return out
    res = OptimisationResult.model_validate(raw)
    active = ms.active_calibrations(db, hw.id, ws)
    for label, ev in (("Today", res.baseline), ("With the plan", res.best)):
        if ev is None:
            continue
        cal = active.get(ev.config.precision)
        metrics = []
        for key, m in ev.metrics.items():
            facts = []
            for d, title in DRIVERS.get(key, []):
                src = INPUT_KEYS.get(d) or sources.get(d) or ("measured" if hw.verified else "library profile")
                facts.append({"fact": title, "source": src})
            calibrated = bool(cal and ((key in ("latency", "batch_time", "capacity_fps") and cal.latency_calibrated)
                                       or (key.startswith("power") and cal.power_calibrated)))  # fmt: skip
            metrics.append({"key": key, "value": m.value, "low": m.low, "high": m.high, "unit": m.unit,
                            "provenance": m.provenance.value, "facts": facts, "calibrated": calibrated,
                            "calibration_version": cal.version if calibrated and cal else None})  # fmt: skip
        out["setups"].append({"label": label, "config": ev.config.model_dump(), "feasibility": ev.feasibility.value,
                              "metrics": metrics, "notes": ev.notes})  # fmt: skip
    out["engine_version"] = res.baseline.engine_version
    return out
