from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.advice import Advice, Alternative, Repair, Verdict, WhatIf
from app.schemas.config import Config
from app.schemas.metrics import Feasibility, Metric


class ConstraintCheck(BaseModel):
    name: str
    sense: Literal["<=", ">="]
    limit: float
    unit: str
    pessimistic: float
    optimistic: float
    status: Literal["pass", "fail", "uncertain"]
    note: str = ""


class SolverInfo(BaseModel):
    converged: bool
    iterations: int
    throttled: bool
    clock_factor: float  # governor multiplier applied to the clock, 1.0 = no throttling


class EvaluationResult(BaseModel):
    config: Config
    metrics: dict[str, Metric]
    bottleneck: Literal["compute", "memory", "overhead", "heat"]
    throttle_risk: Literal["low", "medium", "high"]
    feasibility: Feasibility
    checks: list[ConstraintCheck]
    notes: list[str] = []
    solver: SolverInfo
    time_to_throttle_s: float | None = None
    # (seconds, deg C) steady-load warm-up curve at the nominal operating point.
    thermal_trace: list[tuple[float, float]] = []
    engine_version: str


class Recommendation(BaseModel):
    title: str
    changes: list[str]
    summary: str
    state: Feasibility
    caveats: list[str]


class Point(BaseModel):
    """Lightweight record of one evaluated candidate, for the Pareto chart."""

    config: Config
    state: Feasibility
    energy_mj: float
    latency_ms: float
    accuracy: float
    power_w: float
    memory_gb: float
    temperature_c: float
    pareto: bool = False
    best: bool = False


class CoDesign(BaseModel):
    """Baseline vs software-only vs hardware-only vs combined, plus interaction factor."""

    baseline: EvaluationResult
    software_only: EvaluationResult | None
    hardware_only: EvaluationResult | None
    combined: EvaluationResult | None
    # I = E_comb * E_base / (E_sw * E_hw). Below 1: gains reinforce; above 1: they overlap.
    interaction_factor: float | None
    interpretation: str


class OptimisationResult(BaseModel):
    baseline: EvaluationResult
    best: EvaluationResult | None
    top: list[EvaluationResult]
    recommendation: Recommendation | None
    codesign: CoDesign
    points: list[Point]
    evaluated: int
    counts: dict[str, int]
    search_seconds: float
    message: str = ""
    verdict: Verdict | None = None
    repair: Repair | None = None
    advice: list[Advice] = []
    alternatives: list[Alternative] = []
    whatifs: list[WhatIf] = []
    limiting_factor: str | None = None
