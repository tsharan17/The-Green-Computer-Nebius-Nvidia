"""Constraint repair: the cheapest set of edits that turns a failing setup into a certified one.

Given the baseline and every evaluated candidate, find the candidates whose *pessimistic* bounds
satisfy every hard constraint, and rank them by engineering effort (not by energy), then by
energy. Constraints are never relaxed: if no candidate qualifies, the binding constraints are
reported instead of a fake fix.
"""

from __future__ import annotations

from collections import Counter

from app.engine.changes import describe, effort
from app.schemas.advice import Repair, RepairPlan
from app.schemas.hardware import HardwareProfile
from app.schemas.metrics import Feasibility
from app.schemas.results import EvaluationResult

CERTIFIED = (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)
PLAUSIBLE = CERTIFIED + (Feasibility.UNCERTAIN,)


def _energy(r: EvaluationResult) -> float:
    return r.metrics["energy_per_inference"].value


def _fixes(base: EvaluationResult, cand: EvaluationResult) -> list[str]:
    cand_status = {c.name: c.status for c in cand.checks}
    return [
        c.name
        for c in base.checks
        if c.status in ("fail", "uncertain") and cand_status.get(c.name) == "pass"
    ]


def plan_repair(
    baseline: EvaluationResult,
    results: list[EvaluationResult],
    max_plans: int = 3,
    hw: HardwareProfile | None = None,
) -> Repair:
    if baseline.feasibility in CERTIFIED:
        return Repair(
            needed=False, message="Your current setup already meets every requirement."
        )

    def make(r: EvaluationResult, note: str = "") -> RepairPlan:
        return RepairPlan(
            changes=describe(baseline.config, r.config, hw),
            effort_score=effort(baseline.config, r.config),
            feasibility=r.feasibility,
            fixes=_fixes(baseline, r),
            config=r.config,
            energy_saving_percent=(1.0 - _energy(r) / _energy(baseline)) * 100.0,
            note=note,
        )

    def pick(pool: list[EvaluationResult]) -> list[EvaluationResult]:
        ranked = sorted(pool, key=lambda r: (effort(baseline.config, r.config), _energy(r)))
        seen, out = set(), []
        for r in ranked:
            key = tuple(describe(baseline.config, r.config))
            if key and key not in seen:
                seen.add(key)
                out.append(r)
            if len(out) == max_plans:
                break
        return out

    certified = [r for r in results if r.feasibility in CERTIFIED]
    if certified:
        return Repair(
            needed=True,
            plans=[make(r) for r in pick(certified)],
            message="The cheapest changes that make your setup meet every requirement, "
            "ranked by how little work they are.",
        )

    uncertain = [r for r in results if r.feasibility == Feasibility.UNCERTAIN]
    if uncertain:
        return Repair(
            needed=True,
            plans=[
                make(r, "Likely to work but not certain: measure it before relying on it.")
                for r in pick(uncertain)
            ],
            message="No option is certain on predictions alone; these are the cheapest ones "
            "that look likely to work once you measure them.",
        )

    fails = Counter(c.name for r in results for c in r.checks if c.status == "fail")
    n = max(len(results), 1)
    binding = [name for name, k in fails.most_common() if k / n >= 0.9]
    return Repair(
        needed=True,
        binding_constraints=binding,
        message=(
            "No combination of the settings we can change meets every requirement"
            + (f" ({', '.join(binding)} cannot be met)" if binding else "")
            + ". Requirements were not relaxed. Try different hardware or a lighter model "
            "(see the alternatives), or loosen a requirement yourself."
        ),
    )
