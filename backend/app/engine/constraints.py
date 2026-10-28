"""Five-state constraint certification using pessimistic bounds.

For a '<=' limit the pessimistic value is the metric's high bound; for '>=' it is the low
bound. A constraint passes only if the pessimistic bound passes, fails only if even the
optimistic bound fails, and is otherwise uncertain. Limits are never relaxed here.
"""

from __future__ import annotations

from app.schemas.metrics import Feasibility, Metric, Provenance
from app.schemas.results import ConstraintCheck


def check(name: str, sense: str, limit: float, metric: Metric, note: str = "") -> ConstraintCheck:
    if sense == "<=":
        pess, opt = metric.high, metric.low
        if opt > limit:
            status = "fail"
        elif pess <= limit:
            status = "pass"
        else:
            status = "uncertain"
    elif sense == ">=":
        pess, opt = metric.low, metric.high
        if opt < limit:
            status = "fail"
        elif pess >= limit:
            status = "pass"
        else:
            status = "uncertain"
    else:
        raise ValueError(f"unknown sense {sense}")
    return ConstraintCheck(
        name=name,
        sense=sense,  # type: ignore[arg-type]
        limit=limit,
        unit=metric.unit,
        pessimistic=pess,
        optimistic=opt,
        status=status,  # type: ignore[arg-type]
        note=note,
    )


def certify(checks: list[tuple[ConstraintCheck, Provenance]]) -> Feasibility:
    """Combine checks. 'verified' states require every involved metric to be measured."""
    fails = [p for c, p in checks if c.status == "fail"]
    if fails:
        if all(p == Provenance.MEASURED for p in fails):
            return Feasibility.VERIFIED_INFEASIBLE
        return Feasibility.INFEASIBLE
    if any(c.status == "uncertain" for c, _ in checks):
        return Feasibility.UNCERTAIN
    if all(p == Provenance.MEASURED for _, p in checks):
        return Feasibility.VERIFIED_FEASIBLE
    return Feasibility.PREDICTED_FEASIBLE
