"""Hardware design what-ifs and elasticities.

Change one chip/board parameter at a time, re-run the SAME workload through the coupled solver,
and report what it buys. This answers "if we could redesign the hardware, what would help?" and
"what limits this workload today?". Results are analytical what-ifs: proving a design change needs
architecture-level modelling, RTL synthesis, and finally silicon.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.schemas.advice import WhatIf
from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile
from app.schemas.metrics import Feasibility
from app.schemas.results import EvaluationResult

VERIFY = (
    "Model it with an architecture-level tool (for example Timeloop/Accelergy), synthesise the "
    "block on an open PDK to check power and area, then prototype; full proof needs silicon."
)
CERTIFIED = (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)

Evaluator = Callable[[HardwareProfile, Config], EvaluationResult]


@dataclass(frozen=True)
class Scenario:
    id: str
    parameter: str
    factor: float
    title: str
    change: str


SCENARIOS = (
    Scenario("bandwidth", "memory bandwidth", 1.25, "Faster memory",
             "Raise memory bandwidth by 25% (wider bus or faster DRAM)"),
    Scenario("compute", "compute throughput", 1.25, "More compute",
             "Add 25% more compute throughput at the precision in use (bigger PE array)"),
    Scenario("dynamic", "dynamic power", 0.8, "More efficient compute",
             "Cut dynamic power by 20% (better process node, lower voltage, tuned dataflow)"),
    Scenario("memory_energy", "memory power", 0.7, "Lower-energy memory",
             "Cut memory access power by 30% (more on-chip SRAM reuse, LPDDR upgrade)"),
    Scenario("idle", "idle power", 0.6, "Power gating",
             "Cut idle/static power by 40% (power-gate unused blocks, efficient regulators)"),
    Scenario("leakage", "leakage power", 0.5, "Lower leakage",
             "Halve leakage (low-leakage process option or body biasing)"),
    Scenario("thermal", "thermal resistance", 0.7, "Better heat path",
             "Lower thermal resistance by 30% (larger heatsink, vapour chamber, better package)"),
)  # fmt: skip


def scaled_hw(hw: HardwareProfile, s: Scenario, cfg: Config) -> HardwareProfile:
    f = s.factor
    if s.id == "bandwidth":
        return hw.model_copy(update={"mem_bw_gbs": hw.mem_bw_gbs * f})
    if s.id == "compute":
        peaks = dict(hw.peak_tflops)
        peaks[cfg.precision] = peaks.get(cfg.precision, 0.0) * f
        return hw.model_copy(update={"peak_tflops": peaks})
    if s.id == "dynamic":
        return hw.model_copy(update={"dyn_w_max": hw.dyn_w_max * f})
    if s.id == "memory_energy":
        return hw.model_copy(update={"mem_w_max": hw.mem_w_max * f})
    if s.id == "idle":
        return hw.model_copy(update={"idle_w": hw.idle_w * f})
    if s.id == "leakage":
        return hw.model_copy(update={"leak_w_25c": hw.leak_w_25c * f})
    if s.id == "thermal":
        cooling = [
            c.model_copy(update={"r_th_k_per_w": c.r_th_k_per_w * f})
            if c.id == cfg.cooling_id
            else c
            for c in hw.cooling_options
        ]
        return hw.model_copy(update={"cooling_options": cooling})
    raise ValueError(s.id)


def _pct(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0 if old else 0.0


def _elasticity(new: float, old: float, factor: float) -> float:
    if old <= 0 or new <= 0 or factor == 1.0:
        return 0.0
    return math.log(new / old) / math.log(factor)


def make_whatifs(
    ev: Evaluator, hw: HardwareProfile, best_cfg: Config, baseline_cfg: Config
) -> list[WhatIf]:
    ref = ev(hw, best_cfg)
    ref_base = ev(hw, baseline_cfg)
    rm = ref.metrics
    out: list[WhatIf] = []
    for s in SCENARIOS:
        if s.id == "compute" and hw.peak_tflops.get(best_cfg.precision, 0) <= 0:
            continue
        hw2 = scaled_hw(hw, s, best_cfg)
        r = ev(hw2, best_cfg)
        m = r.metrics
        fixes = (
            ref_base.feasibility not in CERTIFIED
            and ev(scaled_hw(hw, s, baseline_cfg), baseline_cfg).feasibility in CERTIFIED
        )
        out.append(
            WhatIf(
                id=s.id,
                title=s.title,
                change=s.change,
                parameter=s.parameter,
                factor=s.factor,
                latency_change_percent=_pct(m["batch_time"].value, rm["batch_time"].value),
                energy_change_percent=_pct(
                    m["energy_per_inference"].value, rm["energy_per_inference"].value
                ),
                power_change_percent=_pct(m["power_avg"].value, rm["power_avg"].value),
                temperature_change_c=m["temperature"].value - rm["temperature"].value,
                elasticity_latency=_elasticity(
                    m["batch_time"].value, rm["batch_time"].value, s.factor
                ),
                elasticity_energy=_elasticity(
                    m["energy_per_inference"].value, rm["energy_per_inference"].value, s.factor
                ),
                makes_baseline_feasible=fixes,
                verify_with=VERIFY,
            )
        )
    out.sort(key=lambda w: (w.energy_change_percent, w.latency_change_percent))
    return out


def limiting_factor(ev: Evaluator, hw: HardwareProfile, baseline_cfg: Config) -> str:
    """What holds the current setup back, in plain words."""
    base = ev(hw, baseline_cfg)
    if base.solver.throttled:
        return (
            "heat: the chip overheats and slows itself down, so cooling and power are the "
            "limit, not raw speed"
        )
    bm = base.metrics["batch_time"].value
    gains = {}
    for sid in ("bandwidth", "compute"):
        s = next(x for x in SCENARIOS if x.id == sid)
        if sid == "compute" and hw.peak_tflops.get(baseline_cfg.precision, 0) <= 0:
            continue
        gains[sid] = _elasticity(
            ev(scaled_hw(hw, s, baseline_cfg), baseline_cfg).metrics["batch_time"].value,
            bm,
            s.factor,
        )
    if gains:
        sid, el = min(gains.items(), key=lambda kv: kv[1])
        if el < -0.25:
            return (
                "memory bandwidth: the chip spends its time waiting for data, so more raw "
                "compute would not help much"
                if sid == "bandwidth"
                else "compute throughput: the chip is busy computing, so faster or lower-precision "
                "maths helps most"
            )
    bm_metrics = base.metrics
    if bm_metrics["power_avg"].value > 0 and hw.idle_w / bm_metrics["power_avg"].value > 0.5:
        return "idle power: most of the energy goes to keeping the chip on while it waits"
    return "fixed per-layer overheads: many small steps limit speed, so batching or fusing helps"
