"""Plain-language advice objects: what to change, why, how sure we are, and at what cost."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.config import Config
from app.schemas.metrics import Feasibility

Tier = Literal["model", "software", "configure", "select", "design"]
Confidence = Literal["high", "medium", "low"]
Effort = Literal["low", "medium", "high"]


class VerdictSide(BaseModel):
    """How a configuration fares against the user's requirements, in plain words."""

    state: Feasibility
    ok: bool  # meets every requirement (certified, not merely uncertain)
    headline: str
    problems: list[str]
    power_w: float
    latency_ms: float
    temperature_c: float
    capacity_fps: float
    energy_mj: float
    config: Config


class Savings(BaseModel):
    power_percent: float
    energy_percent: float
    cost_per_year: float
    carbon_kg_per_year: float


class Verdict(BaseModel):
    baseline: VerdictSide
    improved: VerdictSide | None = None
    savings: Savings | None = None
    fps_required: float
    summary: str


class RepairPlan(BaseModel):
    changes: list[str]
    effort_score: float
    feasibility: Feasibility
    fixes: list[str]  # baseline checks this plan turns into a pass
    config: Config
    energy_saving_percent: float
    note: str = ""


class Repair(BaseModel):
    needed: bool
    plans: list[RepairPlan] = []
    binding_constraints: list[str] = []
    message: str = ""


class Alternative(BaseModel):
    kind: Literal["device", "model"]
    id: str
    name: str
    feasibility: Feasibility
    config: Config
    power_w: float
    energy_mj: float
    latency_ms: float
    temperature_c: float
    capacity_fps: float
    energy_saving_percent: float | None = None
    accuracy_retention: float | None = None  # vs the original model, for model swaps
    lighter: bool | None = None  # model swaps: needs less compute than the current model
    compared_with: str = "your current setup"  # what energy_saving_percent is measured against
    problem: str | None = None  # why it does not work, in plain words
    note: str = ""


class WhatIf(BaseModel):
    """A hardware design what-if: change one chip/board parameter, re-run the same workload."""

    id: str
    title: str
    change: str
    parameter: str
    factor: float
    latency_change_percent: float
    energy_change_percent: float
    power_change_percent: float
    temperature_change_c: float
    elasticity_latency: float
    elasticity_energy: float
    makes_baseline_feasible: bool
    verify_with: str


class Advice(BaseModel):
    id: str
    tier: Tier
    title: str
    action: str
    gain: str  # the headline number, e.g. "-38% energy"
    why: str
    effect: dict[str, float | None] = {}
    tradeoffs: list[str] = []
    confidence: Confidence
    confidence_reason: str
    effort: Effort
    validate_steps: list[str] = []
    in_recommended: bool = False
    fixes: list[str] = []
