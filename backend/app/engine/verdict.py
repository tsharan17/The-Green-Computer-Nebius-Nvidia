"""A plain-language verdict: does this setup meet the requirements, and if not, why not."""

from __future__ import annotations

from app.schemas.advice import Savings, Verdict, VerdictSide
from app.schemas.metrics import Feasibility
from app.schemas.project import Constraints, Workload
from app.schemas.results import EvaluationResult

CERTIFIED = (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)


def _problem(r: EvaluationResult, name: str, wl: Workload, cons: Constraints) -> str | None:
    m = r.metrics
    c = next((c for c in r.checks if c.name == name), None)
    if c is None:
        return None
    if name == "power (average)":
        return f"Uses {m['power_avg'].value:.1f} W, above your {c.limit:.0f} W limit"
    if name == "accuracy":
        if cons.accuracy_mode == "relative":
            return (
                f"Accuracy could fall to {m['accuracy'].low * 100:.0f}% of the original "
                f"(you need {c.limit * 100:.0f}%)"
            )
        return f"Accuracy {m['accuracy'].value:.3f}, below the {c.limit:.3f} you need"
    if name == "throughput":
        return f"Only reaches {m['capacity_fps'].value:.0f} frames/s; you need {wl.fps:.0f}"
    if name == "memory fit":
        return "The model does not fit in the device's memory"
    if name in ("temperature held below throttle point", "no sustained throttling"):
        if r.solver.throttled:
            return (
                "Overheats, so the chip slows itself down to "
                f"{r.solver.clock_factor * 100:.0f}% of its speed"
            )
        return "Runs too hot and risks slowing itself down"
    if name == "latency":
        return f"Each frame takes up to {m['latency'].value:.0f} ms; your limit is {c.limit:.0f} ms"
    if name == "memory limit":
        return f"Uses {m['memory'].value:.1f} GB, above your {c.limit:.1f} GB limit"
    return None


def describe_side(r: EvaluationResult, wl: Workload, cons: Constraints) -> VerdictSide:
    failing = [c.name for c in r.checks if c.status == "fail"]
    unsure = [c.name for c in r.checks if c.status == "uncertain"]
    problems: list[str] = []
    seen: set[str] = set()
    for name in failing:
        text = _problem(r, name, wl, cons)
        if text and text not in seen:
            seen.add(text)
            problems.append(text)
    ok = r.feasibility in CERTIFIED
    if ok:
        headline = "Meets every requirement"
    elif failing:
        headline = problems[0] if problems else "Does not meet your requirements"
    elif r.feasibility == Feasibility.UNCERTAIN:
        headline = "Probably meets your requirements, but needs a real measurement"
        if r.notes:
            problems.append(r.notes[0])
        if unsure and not r.notes:
            problems.append("Not certain: " + ", ".join(unsure))
    else:
        headline = "Does not meet your requirements"
    m = r.metrics
    return VerdictSide(
        state=r.feasibility,
        ok=ok,
        headline=headline,
        problems=problems,
        power_w=m["power_avg"].value,
        latency_ms=m["latency"].value,
        temperature_c=m["temperature"].value,
        capacity_fps=m["capacity_fps"].value,
        energy_mj=m["energy_per_inference"].value,
        config=r.config,
    )


def build_verdict(
    baseline: EvaluationResult, best: EvaluationResult | None, wl: Workload, cons: Constraints
) -> Verdict:
    b = describe_side(baseline, wl, cons)
    if best is None:
        return Verdict(
            baseline=b,
            improved=None,
            savings=None,
            fps_required=wl.fps,
            summary=(
                "No setting we can change makes this meet every requirement. "
                "See the alternatives below."
                if not b.ok
                else "Your current setup meets the requirements."
            ),
        )
    i = describe_side(best, wl, cons)
    bm, nm = baseline.metrics, best.metrics

    def pct(old: float, new: float) -> float:
        return (1.0 - new / old) * 100.0 if old else 0.0

    savings = Savings(
        power_percent=pct(bm["power_avg"].value, nm["power_avg"].value),
        energy_percent=pct(bm["energy_per_inference"].value, nm["energy_per_inference"].value),
        cost_per_year=(bm["cost_per_day"].value - nm["cost_per_day"].value) * 365.0,
        carbon_kg_per_year=(bm["carbon_per_day"].value - nm["carbon_per_day"].value) * 365.0,
    )
    if b.ok:
        summary = (
            f"Your setup already works. We found one that uses {savings.energy_percent:.0f}% "
            "less energy."
        )
    else:
        now = (
            f"Your current setup fails ({b.headline[0].lower() + b.headline[1:]})"
            if b.state in (Feasibility.INFEASIBLE, Feasibility.VERIFIED_INFEASIBLE)
            else "Your current setup might work, but that is not certain"
        )
        if i.ok:
            summary = (
                f"{now}. A changed setup meets every requirement and uses "
                f"{savings.power_percent:.0f}% less power."
            )
        else:
            summary = (
                f"{now}. The best option is likely to work but needs a real measurement "
                "to be sure."
            )
    return Verdict(baseline=b, improved=i, savings=savings, fps_required=wl.fps, summary=summary)
