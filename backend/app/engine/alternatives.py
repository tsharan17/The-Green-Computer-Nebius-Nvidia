"""Other models and other hardware that could do the same job, evaluated under the SAME hard
constraints with a small fast search (precision x batch x power mode x clock).

Model swaps use the models' published accuracy ratio, so "accuracy stays above 90% of the original"
is checked against real published numbers, not a guess.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engine.optimise import optimise, quick_space
from app.engine.verdict import describe_side
from app.schemas.advice import Alternative
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import Calibration
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectSpec
from app.schemas.results import EvaluationResult, OptimisationResult


def _alt(
    kind: str,
    ident: str,
    name: str,
    res: OptimisationResult,
    base_energy: float,
    retention: float | None,
    note: str = "",
    lighter: bool | None = None,
    compared_with: str = "your current setup",
    spec: ProjectSpec | None = None,
) -> Alternative:
    r: EvaluationResult = res.best or res.top[0]
    m = r.metrics
    problem = None
    if spec is not None:
        side = describe_side(r, spec.workload, spec.constraints)
        problem = None if side.ok else (side.problems[0] if side.problems else side.headline)
    e = m["energy_per_inference"].value
    return Alternative(
        kind=kind,  # type: ignore[arg-type]
        id=ident,
        name=name,
        feasibility=r.feasibility,
        config=r.config,
        power_w=m["power_avg"].value,
        energy_mj=e,
        latency_ms=m["latency"].value,
        temperature_c=m["temperature"].value,
        capacity_fps=m["capacity_fps"].value,
        energy_saving_percent=(1.0 - e / base_energy) * 100.0 if base_energy else None,
        accuracy_retention=retention,
        lighter=lighter,
        compared_with=compared_with,
        problem=problem,
        note=note,
    )


def model_alternatives(
    spec: ProjectSpec,
    model: ModelProfile,
    hw: HardwareProfile,
    models: list[ModelProfile],
    baseline: EvaluationResult,
    calibrations: dict[str, Calibration] | None = None,
    best: EvaluationResult | None = None,
) -> list[Alternative]:
    ref = best or baseline
    base_e = ref.metrics["energy_per_inference"].value
    label = "your recommended setup" if best else "your current setup"
    out: list[Alternative] = []
    for alt in models:
        if alt.id == model.id or alt.task != model.task:
            continue
        sub = spec.model_copy(update={"model_id": alt.id, "baseline": None})
        res = optimise(
            sub, alt, hw, calibrations=calibrations,
            configs=quick_space(alt, hw, spec.cooling_id), accuracy_reference=model.baseline_accuracy,
        )  # fmt: skip
        ret = alt.baseline_accuracy / model.baseline_accuracy if model.baseline_accuracy else None
        out.append(
            _alt("model", alt.id, alt.name, res, base_e, ret,
                 f"published {alt.accuracy_metric}: {alt.baseline_accuracy * 100:.1f}% "
                 f"vs {model.baseline_accuracy * 100:.1f}%",
                 lighter=alt.gflops < model.gflops, compared_with=label, spec=sub)  # fmt: skip
        )
    return out


def device_alternatives(
    spec: ProjectSpec,
    model: ModelProfile,
    hw: HardwareProfile,
    devices: list[HardwareProfile],
    baseline: EvaluationResult,
    calibrations_for: Callable[[str], dict[str, Calibration]] | None = None,
    best: EvaluationResult | None = None,
) -> list[Alternative]:
    ref = best or baseline
    base_e = ref.metrics["energy_per_inference"].value
    label = "your recommended setup" if best else "your current setup"
    out: list[Alternative] = []
    for d in devices:
        if d.id == hw.id or not d.alternative_candidate:
            continue
        sub = spec.model_copy(update={"device_id": d.id, "cooling_id": None, "baseline": None})
        res = optimise(
            sub, model, d,
            calibrations=calibrations_for(d.id) if calibrations_for else None,
            configs=quick_space(model, d, None),
        )  # fmt: skip
        out.append(_alt("device", d.id, d.name, res, base_e, None, d.role, compared_with=label, spec=sub))
    return out
