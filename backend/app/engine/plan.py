"""Design plans: turn recommendations into concrete changes, apply any selection of them, and
compare the baseline, each change on its own, and the combined plan.

Every change is explicit data (`PlanChange.params`), so a saved plan can be re-applied and audited.
Conflicts are resolved by stated rules, never silently:
  * a hardware swap replaces the board, so settings that only exist on the old board (its power
    modes, clock trims and cooling options) are dropped and the swap's own settings are used;
  * a later change to the same setting replaces an earlier one (the plan lists both and says so);
  * design what-ifs (chip/board parameters) apply to whichever board the plan ends up on and are
    labelled directional: they are model what-ifs, not validated designs.
Accuracy is never promised: every model-level change carries its risk and how it can be checked.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from app.engine.cooling_catalogue import CoolingEntry, to_option, with_option
from app.engine.whatif import SCENARIOS, scaled_hw
from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile
from app.schemas.metrics import Provenance
from app.schemas.model import ModelProfile
from app.schemas.results import EvaluationResult, OptimisationResult

ChangeKind = Literal["config", "model", "device", "design", "cooling"]
CONFIG_KEYS = {"precision": "precision", "pruning": "sparsity", "resolution": "resolution_scale",
               "batch": "batch", "power_mode": "power_mode_id", "clock": "clock_trim",
               "cooling": "cooling_id"}  # fmt: skip
DEVICE_SPECIFIC = {"power_mode_id", "clock_trim", "cooling_id"}


class PlanChange(BaseModel):
    id: str
    kind: ChangeKind
    tier: str
    title: str
    params: dict[str, Any]
    risk: str = ""
    confidence: str = "medium"
    directional: bool = False


def changes_from_result(res: OptimisationResult, cooling: list[CoolingEntry]) -> list[PlanChange]:
    """Every selectable change behind an analysis: the recommended settings, model and hardware
    alternatives, design what-ifs, and applicable catalogue cooling."""
    out: list[PlanChange] = []
    best = res.best.config if res.best else None
    alts = {f"{a.kind}:{a.id}": a for a in res.alternatives}
    for a in res.advice:
        if a.id in CONFIG_KEYS and best is not None:
            key = CONFIG_KEYS[a.id]
            risk = "; ".join(a.tradeoffs)
            out.append(PlanChange(id=a.id, kind="config", tier=a.tier, title=a.title,
                                  params={key: getattr(best, key)}, risk=risk, confidence=a.confidence))  # fmt: skip
        elif a.id.startswith("model:"):
            alt = alts.get(a.id)
            ret = alt.accuracy_retention if alt else None
            out.append(PlanChange(id=a.id, kind="model", tier=a.tier, title=a.title,
                                  params={"model_id": a.id.split(":", 1)[1]},
                                  risk=(f"Published accuracy is about {ret:.0%} of the current model's; "
                                        "re-validate on your data." if ret else "Re-validate accuracy on your data."),
                                  confidence=a.confidence))  # fmt: skip
        elif a.id.startswith("device:"):
            alt = alts.get(a.id)
            out.append(PlanChange(id=a.id, kind="device", tier=a.tier, title=a.title,
                                  params={"device_id": a.id.split(":", 1)[1],
                                          "config": alt.config.model_dump() if alt else None},
                                  risk="New hardware to buy, integrate and qualify; no measurements of it here.",
                                  confidence=a.confidence))  # fmt: skip
        elif a.id.startswith("design:"):
            out.append(PlanChange(id=a.id, kind="design", tier="design", title=a.title,
                                  params={"scenario": a.id.split(":", 1)[1]},
                                  risk="Analytical what-if on one chip/board parameter; needs architecture "
                                       "modelling and a prototype to confirm.",
                                  confidence="low", directional=True))  # fmt: skip
    # Design what-ifs that were computed but not surfaced as advice are still selectable.
    have = {c.id for c in out}
    for w in res.whatifs:
        if f"design:{w.id}" not in have:
            out.append(PlanChange(id=f"design:{w.id}", kind="design", tier="design",
                                  title=f"Chip/board idea: {w.title}", params={"scenario": w.id},
                                  risk="Analytical what-if; not a validated design.", confidence="low",
                                  directional=True))  # fmt: skip
    for e in cooling:
        out.append(PlanChange(id=f"cooling:{e.id}", kind="cooling", tier="select", title=f"Cooling: {e.name}",
                              params={"catalogue_id": e.id},
                              risk=f"Catalogue class values ({e.confidence} confidence); confirm with the part's "
                                   "datasheet and a temperature log.", confidence=e.confidence))  # fmt: skip
    return out


class Applied(BaseModel):
    model_id: str
    device_id: str
    config: Config
    hardware_changes: dict[str, Any] = {}
    notes: list[str] = []
    applied: list[str] = []
    skipped: list[str] = []


def apply_changes(
    model: ModelProfile,
    hw: HardwareProfile,
    base: Config,
    changes: list[PlanChange],
    get_model: Callable[[str], ModelProfile],
    get_device: Callable[[str], HardwareProfile],
    cooling: dict[str, CoolingEntry],
) -> tuple[ModelProfile, HardwareProfile, Applied]:
    cfg = base.model_dump()
    notes: list[str] = []
    applied: list[str] = []
    skipped: list[str] = []
    set_by: dict[str, str] = {}
    hw_changes: dict[str, Any] = {}
    order = {"device": 0, "model": 1, "config": 2, "cooling": 3, "design": 4}
    for c in sorted(changes, key=lambda c: order[c.kind]):
        if c.kind == "device":
            try:
                hw = get_device(c.params["device_id"])
            except KeyError:
                skipped.append(c.id)
                notes.append(f"{c.title}: that device is not available here, skipped.")
                continue
            new = c.params.get("config") or {}
            for k in DEVICE_SPECIFIC:
                cfg[k] = new.get(k) or (hw.default_cooling if k == "cooling_id" else
                                        max(hw.power_modes, key=lambda m: m.cap_w).id if k == "power_mode_id" else 1.0)  # fmt: skip
            for k in ("precision", "batch"):
                if new.get(k) is not None:
                    cfg[k] = new[k]
            notes.append(f"{c.title}: board settings (power mode, clock, cooling) now come from {hw.name}.")
        elif c.kind == "model":
            try:
                model = get_model(c.params["model_id"])
            except KeyError:
                skipped.append(c.id)
                notes.append(f"{c.title}: that model is not available here, skipped.")
                continue
        elif c.kind == "config":
            (key, val), = c.params.items()
            if key in DEVICE_SPECIFIC and any(x.kind == "device" for x in changes):
                skipped.append(c.id)
                notes.append(f"{c.title}: replaced by the hardware swap's own {key.replace('_id', '').replace('_', ' ')}.")
                continue
            if key in set_by:
                notes.append(f"{c.title} replaces {set_by[key]} for {key}.")
            cfg[key] = val
            set_by[key] = c.title
        elif c.kind == "cooling":
            entry = cooling.get(c.params["catalogue_id"])
            if entry is None or hw.kind not in entry.applies_to:
                skipped.append(c.id)
                notes.append(f"{c.title}: not applicable to {hw.name}, skipped.")
                continue
            opt = to_option(entry, hw.cooling(cfg["cooling_id"]))
            hw = with_option(hw, opt)
            cfg["cooling_id"] = opt.id
            hw_changes["cooling"] = {"name": opt.name, "r_th_k_per_w": opt.r_th_k_per_w, "active_w": opt.fan_w}
        elif c.kind == "design":
            sc = next((s for s in SCENARIOS if s.id == c.params["scenario"]), None)
            if sc is None:
                skipped.append(c.id)
                continue
            before = hw
            hw = scaled_hw(hw, sc, Config.model_validate(cfg))
            hw_changes[sc.parameter] = {"factor": sc.factor, "change": sc.change,
                                        "before": _param(before, sc.id, cfg), "after": _param(hw, sc.id, cfg)}  # fmt: skip
            hw = hw.model_copy(update={"id": f"{hw.id}+{sc.id}", "name": f"{hw.name} (modified: {sc.title.lower()})"})
            notes.append(f"{c.title}: directional what-if, not a validated design.")
        applied.append(c.id)
    # Settings that do not exist on the final hardware/model fall back, with a note.
    if hw.peak_tflops.get(cfg["precision"], 0) <= 0:
        fallback = next(p for p in ("fp16", "int8", "fp32") if hw.peak_tflops.get(p, 0) > 0)
        notes.append(f"{hw.name} has no {cfg['precision']} support: using {fallback}.")
        cfg["precision"] = fallback
    if cfg["resolution_scale"] != 1.0 and not model.resolution_scalable:
        notes.append(f"{model.name} cannot change input resolution: kept at native size.")
        cfg["resolution_scale"] = 1.0
    if cfg["power_mode_id"] not in {m.id for m in hw.power_modes}:
        cfg["power_mode_id"] = max(hw.power_modes, key=lambda m: m.cap_w).id
    return model, hw, Applied(model_id=model.id, device_id=hw.id.split("+")[0], config=Config.model_validate(cfg),
                              hardware_changes=hw_changes, notes=notes, applied=applied, skipped=skipped)  # fmt: skip


def _param(hw: HardwareProfile, scenario: str, cfg: dict[str, Any]) -> float:
    return {
        "bandwidth": hw.mem_bw_gbs, "compute": hw.peak_tflops.get(cfg["precision"], 0.0), "dynamic": hw.dyn_w_max,
        "memory_energy": hw.mem_w_max, "idle": hw.idle_w, "leakage": hw.leak_w_25c,
        "thermal": hw.cooling(cfg["cooling_id"]).r_th_k_per_w,
    }.get(scenario, 0.0)  # fmt: skip


# --- comparison -------------------------------------------------------------------------------

METRIC_ORDER = ["latency", "batch_time", "capacity_fps", "power_avg", "power_peak", "energy_per_inference",
                "memory", "temperature", "temperature_unthrottled", "thermal_headroom", "clock_factor",
                "accuracy", "cost_per_day", "carbon_per_day", "battery_hours", "weights"]  # fmt: skip
LOWER_IS_BETTER = {"latency", "batch_time", "power_avg", "power_peak", "energy_per_inference", "memory",
                   "temperature", "temperature_unthrottled", "cost_per_day", "carbon_per_day", "weights"}  # fmt: skip


def scenario_view(label: str, model: ModelProfile, hw: HardwareProfile, r: EvaluationResult) -> dict[str, Any]:
    cool = hw.cooling(r.config.cooling_id)
    mode = hw.mode(r.config.power_mode_id)
    return {
        "label": label,
        "feasibility": r.feasibility.value,
        "bottleneck": r.bottleneck,
        "throttled": r.solver.throttled,
        "checks": [c.model_dump() for c in r.checks],
        "notes": r.notes,
        "metrics": {k: r.metrics[k].model_dump(mode="json") for k in METRIC_ORDER if k in r.metrics},
        "parameters": {
            "model": model.name, "parameters_m": model.params_m, "gflops": model.gflops,
            "device": hw.name, "peak_at_precision": hw.peak_tflops.get(r.config.precision, 0.0),
            "memory_bandwidth_gbs": hw.mem_bw_gbs, "memory_gb": hw.mem_gb, "idle_w": hw.idle_w,
            "dynamic_w_max": hw.dyn_w_max, "memory_w_max": hw.mem_w_max, "leakage_w_25c": hw.leak_w_25c,
            "precision": r.config.precision, "batch": r.config.batch,
            "input_resolution": r.config.resolution_scale, "pruned_fraction": r.config.sparsity,
            "power_mode": mode.name, "power_cap_w": mode.cap_w, "clock": r.config.clock_trim,
            "cooling": cool.name, "cooling_r_th_k_per_w": cool.r_th_k_per_w, "cooling_active_w": cool.fan_w,
        },
        "config": r.config.model_dump(),
    }  # fmt: skip


PARAM_UNITS = {
    "parameters_m": "M", "gflops": "GFLOP", "peak_at_precision": "TFLOPS/TOPS", "memory_bandwidth_gbs": "GB/s",
    "memory_gb": "GB", "idle_w": "W", "dynamic_w_max": "W", "memory_w_max": "W", "leakage_w_25c": "W",
    "input_resolution": "× native", "pruned_fraction": "", "power_cap_w": "W", "clock": "× mode clock",
    "cooling_r_th_k_per_w": "K/W", "cooling_active_w": "W", "batch": "inputs",
}  # fmt: skip


def diff_rows(base: dict[str, Any], plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    params = []
    for k, v in base["parameters"].items():
        nv = plan["parameters"].get(k)
        params.append({"name": k, "unit": PARAM_UNITS.get(k, ""), "baseline": v, "plan": nv, "changed": nv != v})
    metrics = []
    for k, m in base["metrics"].items():
        nm = plan["metrics"].get(k)
        if nm is None:
            continue
        b, n = m["value"], nm["value"]
        pct = (n / b - 1) * 100 if b else None
        better = None if pct is None or abs(pct) < 0.5 else ((pct < 0) == (k in LOWER_IS_BETTER))
        metrics.append({"name": k, "unit": m["unit"], "baseline": m, "plan": nm, "change_percent": pct,
                        "better": better,
                        "provenance_changed": m["provenance"] != nm["provenance"]})  # fmt: skip
    return {"parameters": params, "metrics": metrics}


def accuracy_statement(r: EvaluationResult) -> str:
    acc = r.metrics["accuracy"]
    if acc.provenance == Provenance.MEASURED:
        return "Accuracy at this setting is measured."
    if acc.low < acc.high:
        return (f"Accuracy is a prior, not a measurement: expected {acc.value:.1%} with a worst case of "
                f"{acc.low:.1%}. Validate on your data before deploying.")
    return "Accuracy is the model's published figure; no change is predicted, but validate on your data."
