"""Design-space search: sweep, rank, Pareto frontier and software/hardware co-design comparison.

Knobs and their tiers:
  software            precision, batch, input resolution
  hardware configure  power mode, clock trim
  hardware select     cooling solution
Everything is evaluated through the coupled solver, so thermal throttling is inside the search,
not a post-check.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from itertools import product

import numpy as np

from app.engine.changes import describe, effort
from app.engine.evaluator import evaluate, matches
from app.engine.repair import plan_repair
from app.engine.roofline import supports
from app.engine.verdict import build_verdict
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import Calibration, MeasurementIn
from app.schemas.metrics import Feasibility
from app.schemas.model import ModelProfile
from app.schemas.project import Constraints, ProjectSpec, Workload
from app.schemas.results import (
    CoDesign,
    Config,
    EvaluationResult,
    OptimisationResult,
    Point,
    Recommendation,
)

_TIE_LOG = math.log(1.03)  # energies within ~3% share a ranking bucket
# Each unit of engineering effort (see engine/changes.py) costs 1.2% of energy in the ranking, so
# a physical change such as a bigger heatsink must earn its keep.
EFFORT_ENERGY_PENALTY = 0.012

PRECISIONS = ("fp32", "fp16", "int8")
BATCHES = (1, 2, 4, 8, 16)
RESOLUTIONS = (1.0, 0.75, 0.5)
CLOCK_TRIMS = (1.0, 0.85, 0.7)
SPARSITIES = (0.0, 0.4)

_STATE_RANK = {
    Feasibility.VERIFIED_FEASIBLE: 0,
    Feasibility.PREDICTED_FEASIBLE: 0,
    Feasibility.UNCERTAIN: 1,
    Feasibility.INFEASIBLE: 2,
    Feasibility.VERIFIED_INFEASIBLE: 2,
}


def baseline_config(hw: HardwareProfile, cooling_id: str | None) -> Config:
    """Reference point: fp32, batch 1, highest-power mode, the project's cooling."""
    top_mode = max(hw.power_modes, key=lambda m: m.cap_w)
    return Config(
        precision=next((p for p in PRECISIONS if supports(hw, p)), "int8"),
        batch=1,
        power_mode_id=top_mode.id,
        cooling_id=cooling_id or hw.default_cooling,
    )


def search_space(model: ModelProfile, hw: HardwareProfile) -> Iterable[Config]:
    for prec, b, mode, cool, res, trim, spars in product(
        [p for p in PRECISIONS if supports(hw, p)],
        BATCHES,
        [m.id for m in hw.power_modes],
        [c.id for c in hw.cooling_options],
        RESOLUTIONS if model.resolution_scalable else (1.0,),
        CLOCK_TRIMS,
        SPARSITIES,
    ):
        yield Config(
            precision=prec, batch=b, power_mode_id=mode, cooling_id=cool,
            resolution_scale=res, clock_trim=trim, sparsity=spars,
        )  # fmt: skip


def cooling_for_environment(hw: HardwareProfile, spec: ProjectSpec, fitted: str) -> tuple[HardwareProfile, list[str]]:
    """Cooling options the search may pick where the device runs. A sealed enclosure has no air path,
    so options with a fan are left out (the one fitted today always stays). Returns the profile to
    search and the names of the options left out."""
    env = getattr(spec, "environment", None)
    if env is None or not env.sealed_enclosure:
        return hw, []
    keep = [c for c in hw.cooling_options if c.fan_w == 0 or c.id == fitted]
    dropped = [c.name for c in hw.cooling_options if c not in keep]
    return (hw.model_copy(update={"cooling_options": keep}) if dropped else hw), dropped


def quick_space(model: ModelProfile, hw: HardwareProfile, cooling_id: str | None) -> list[Config]:
    """A small space for comparing alternatives fast: precision x batch x power mode x clock."""
    cooling = cooling_id if cooling_id in {c.id for c in hw.cooling_options} else hw.default_cooling
    return [
        Config(precision=p, batch=b, power_mode_id=m.id, cooling_id=cooling, clock_trim=t)
        for p in PRECISIONS
        if supports(hw, p)
        for b in (1, 2, 4)
        for m in hw.power_modes
        for t in (1.0, 0.7)
    ]


def make_evaluator(
    model: ModelProfile,
    hw: HardwareProfile,
    wl: Workload,
    cons: Constraints,
    measured: dict[str, float] | None = None,
    calibrations: dict[str, Calibration] | None = None,
    measurements: list[MeasurementIn] | None = None,
    accuracy_reference: float | None = None,
) -> Callable[[Config], EvaluationResult]:
    """One evaluation context (calibration, measurements, accuracy reference) as a function."""
    measured = measured or {}
    calibrations = calibrations or {}
    measurements = measurements or []

    def ev(cfg: Config) -> EvaluationResult:
        exact = next((m for m in measurements if matches(m, hw, model.id, cfg)), None)
        return evaluate(
            model, hw, cfg, wl, cons, measured.get(cfg.precision),
            calibration=calibrations.get(cfg.precision), measured=exact,
            accuracy_reference=accuracy_reference,
        )  # fmt: skip

    return ev


def _failed(r: EvaluationResult) -> int:
    return sum(1 for c in r.checks if c.status == "fail")


def _eligible(r: EvaluationResult) -> bool:
    return _STATE_RANK[r.feasibility] <= 1


# The metric each priority minimises among setups that meet every requirement. Requirements are
# never relaxed by a priority; it only orders the setups that already pass.
PRIORITY_METRIC = {
    "balanced": "energy_per_inference",
    "faster": "latency",
    "lower_power": "power_avg",
    "lower_heat": "temperature_unthrottled",
    "lower_cost": "cost_per_day",  # electricity cost of running it; hardware price is not modelled
    "fit_memory": "memory",
}  # fmt: skip
PRIORITY_TITLE = {
    "balanced": "Lowest-energy configuration that meets your hard constraints",
    "faster": "Fastest configuration that meets your hard constraints",
    "lower_power": "Lowest-power configuration that meets your hard constraints",
    "lower_heat": "Coolest-running configuration that meets your hard constraints",
    "lower_cost": "Lowest electricity-cost configuration that meets your hard constraints",
    "fit_memory": "Smallest-memory configuration that meets your hard constraints",
}  # fmt: skip


def _rank_key(r: EvaluationResult, base: Config | None = None, priority: str = "balanced") -> tuple:
    # The priority's metric (plus a small penalty per unit of engineering effort, when a baseline
    # is given). Differences inside the band's noise are ties, broken by energy then latency, so
    # the recommendation does not trade a 4x slower response for a sub-percent gain.
    e = r.metrics["energy_per_inference"].value
    key = PRIORITY_METRIC.get(priority, "energy_per_inference")
    v = r.metrics[key].value
    adj = max(v, 1e-12) * (1.0 + EFFORT_ENERGY_PENALTY * (effort(base, r.config) if base else 0.0))
    return (
        _STATE_RANK[r.feasibility],
        math.floor(math.log(adj) / _TIE_LOG),
        e if key != "energy_per_inference" else r.metrics["latency"].value,
        r.metrics["latency"].value,
        e,
    )


def _pick(results: Iterable[EvaluationResult], base: Config | None = None,
          priority: str = "balanced") -> EvaluationResult | None:  # fmt: skip
    ok = [r for r in results if _eligible(r)]
    return min(ok, key=lambda r: _rank_key(r, base, priority)) if ok else None


def _recommendation(
    base: EvaluationResult, best: EvaluationResult, spec: ProjectSpec, hw: HardwareProfile
) -> Recommendation:
    changes = describe(base.config, best.config, hw) or ["Keep the baseline configuration"]
    bm, nm = base.metrics, best.metrics
    summary = (
        f"Average power {bm['power_avg'].value:.1f} W → {nm['power_avg'].value:.1f} W, "
        f"energy {bm['energy_per_inference'].value:.1f} → "
        f"{nm['energy_per_inference'].value:.1f} mJ/inference, "
        f"steady temperature {bm['temperature'].value:.0f} → {nm['temperature'].value:.0f} °C, "
        f"capacity {bm['capacity_fps'].value:.0f} → {nm['capacity_fps'].value:.0f} fps."
    )
    caveats = list(best.notes)
    lat = nm["latency"].value
    frame_ms = 1e3 / spec.workload.fps
    if spec.constraints.max_latency_ms is None and lat > 2 * frame_ms:
        caveats.append(
            f"No latency limit is set, and this configuration has a worst-case frame latency of "
            f"{lat:.0f} ms ({lat / frame_ms:.1f} frame periods). Set a latency limit if the "
            "application is real-time."
        )
    caveats.append("All values are analytical predictions with stated bands, not measurements.")
    return Recommendation(
        title=PRIORITY_TITLE.get(spec.priority, PRIORITY_TITLE["balanced"]),
        changes=changes,
        summary=summary,
        state=best.feasibility,
        caveats=caveats,
    )


def pareto_mask(objectives: np.ndarray) -> np.ndarray:
    """Boolean mask of non-dominated rows; every column is minimised."""
    n = objectives.shape[0]
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        if not keep[i]:
            continue
        le = np.all(objectives <= objectives[i], axis=1)
        lt = np.any(objectives < objectives[i], axis=1)
        dominators = le & lt
        if dominators.any():
            keep[i] = False
    return keep


def _codesign(
    base: EvaluationResult,
    results: list[EvaluationResult],
    base_cfg: Config,
) -> CoDesign:
    def hw_same(c: Config) -> bool:
        return (
            c.power_mode_id == base_cfg.power_mode_id
            and c.cooling_id == base_cfg.cooling_id
            and c.clock_trim == base_cfg.clock_trim
        )

    def sw_same(c: Config) -> bool:
        return (
            c.precision == base_cfg.precision
            and c.batch == base_cfg.batch
            and c.resolution_scale == base_cfg.resolution_scale
            and c.sparsity == base_cfg.sparsity
        )

    sw = _pick((r for r in results if hw_same(r.config)), base_cfg)
    hw = _pick((r for r in results if sw_same(r.config)), base_cfg)
    comb = _pick(results, base_cfg)

    def energy(r: EvaluationResult) -> float:
        return r.metrics["energy_per_inference"].value

    factor = None
    interp = (
        "A combined interaction factor needs a feasible software-only, hardware-only and "
        "combined configuration; at least one of them does not exist for this baseline."
    )
    if sw and hw and comb:
        factor = energy(comb) * energy(base) / (energy(sw) * energy(hw))
        if factor < 0.95:
            interp = (
                f"Interaction factor {factor:.2f} (< 1): software and hardware changes "
                "reinforce each other; the combined saving beats the product of the two alone."
            )
        elif factor <= 1.05:
            interp = (
                f"Interaction factor {factor:.2f} (≈ 1): the gains are roughly independent "
                "and multiply."
            )
        else:
            interp = (
                f"Interaction factor {factor:.2f} (> 1): the gains overlap; combining them "
                "saves less than the product of the individual savings."
            )
    return CoDesign(
        baseline=base,
        software_only=sw,
        hardware_only=hw,
        combined=comb,
        interaction_factor=factor,
        interpretation=interp,
    )


def optimise(
    spec: ProjectSpec,
    model: ModelProfile,
    hw: HardwareProfile,
    measured: dict[str, float] | None = None,
    top_n: int = 10,
    calibrations: dict[str, Calibration] | None = None,
    measurements: list[MeasurementIn] | None = None,
    configs: Iterable[Config] | None = None,
    on_result: Callable[[EvaluationResult], None] | None = None,
    accuracy_reference: float | None = None,
) -> OptimisationResult:
    t0 = time.perf_counter()
    wl: Workload = spec.workload
    cons: Constraints = spec.constraints
    base_cfg = spec.baseline or baseline_config(hw, spec.cooling_id)
    hw, cooling_dropped = cooling_for_environment(hw, spec, base_cfg.cooling_id)
    ev = make_evaluator(
        model, hw, wl, cons, measured, calibrations, measurements, accuracy_reference
    )

    baseline = ev(base_cfg)
    results = [ev(c) for c in (configs if configs is not None else search_space(model, hw))]
    if on_result:
        for r in results:
            on_result(r)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.feasibility.value] = counts.get(r.feasibility.value, 0) + 1

    eligible = sorted((r for r in results if _eligible(r)),
                      key=lambda r: _rank_key(r, base_cfg, spec.priority))  # fmt: skip
    best = eligible[0] if eligible else None

    # Pareto set over eligible candidates: energy and latency down, accuracy up. Memory is a
    # hard constraint, not a trade-off axis, so it is not an objective here.
    pareto_ids: set[int] = set()
    if eligible:
        obj = np.array(
            [
                [
                    r.metrics["energy_per_inference"].value,
                    r.metrics["latency"].value,
                    -r.metrics["accuracy"].value,
                ]
                for r in eligible
            ]
        )
        mask = pareto_mask(obj)
        pareto_ids = {id(eligible[i]) for i in np.flatnonzero(mask)}

    points = [
        Point(
            config=r.config,
            state=r.feasibility,
            energy_mj=r.metrics["energy_per_inference"].value,
            latency_ms=r.metrics["latency"].value,
            accuracy=r.metrics["accuracy"].value,
            power_w=r.metrics["power_avg"].value,
            memory_gb=r.metrics["memory"].value,
            temperature_c=r.metrics["temperature"].value,
            pareto=id(r) in pareto_ids,
            best=r is best,
        )
        for r in results
    ]

    codesign = _codesign(baseline, results, base_cfg)
    repair = plan_repair(baseline, results, hw=hw)
    verdict = build_verdict(baseline, best, wl, cons)
    elapsed = time.perf_counter() - t0
    env_note = (f"Left out because the enclosure is sealed (Where it runs): {', '.join(cooling_dropped)}."
                if cooling_dropped else "")  # fmt: skip

    if best is not None:
        return OptimisationResult(
            baseline=baseline,
            best=best,
            top=eligible[:top_n],
            recommendation=_recommendation(baseline, best, spec, hw),
            codesign=codesign,
            points=points,
            evaluated=len(results),
            counts=counts,
            search_seconds=elapsed,
            verdict=verdict,
            repair=repair,
            message=env_note,
        )

    closest = sorted(results, key=lambda r: (_failed(r), r.metrics["energy_per_inference"].value))
    return OptimisationResult(
        baseline=baseline,
        best=None,
        top=closest[:top_n],
        recommendation=None,
        codesign=codesign,
        points=points,
        evaluated=len(results),
        counts=counts,
        search_seconds=elapsed,
        verdict=verdict,
        repair=repair,
        message=(
            "No configuration in the searched space satisfies every hard constraint. "
            "Constraints were not relaxed. Showing the closest candidates by number of "
            "failed checks." + (f" {env_note}" if env_note else "")
        ),
    )
