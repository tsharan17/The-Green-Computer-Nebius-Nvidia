from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.results import Config


class Workload(BaseModel):
    fps: float = Field(30.0, gt=0)
    ambient_c: float = 40.0
    hours_per_day: float = Field(24.0, ge=0, le=24)
    price_per_kwh: float = Field(0.15, ge=0)
    grid_gco2_per_kwh: float = Field(400.0, ge=0)
    # Real-time stream: frames arrive one at a time at `fps`, so a batch of B must wait for
    # B-1 more frames and the worst-case frame latency includes that wait. Set False for
    # offline / queued workloads where all inputs are already available.
    streaming: bool = True


class Constraints(BaseModel):
    """Hard constraints. Only the user (project form) may change these."""

    max_power_w: float = 55.0
    min_accuracy: float = Field(0.90, gt=0, le=1.0)
    # relative: fraction of the model's baseline accuracy retained
    # absolute: value of the model's own accuracy metric (e.g. mAP)
    accuracy_mode: Literal["relative", "absolute"] = "relative"
    max_latency_ms: float | None = None
    max_memory_gb: float | None = None
    forbid_throttling: bool = True


class Environment(BaseModel):
    """Where the device runs, as far as cooling choices are concerned. Every field is optional;
    an unset field rules nothing out."""

    sealed_enclosure: bool = False  # no vents or open fins (e.g. IP65 outdoor box)
    dusty: bool = False  # fans and vents need filters and cleaning
    max_noise_dba: float | None = Field(None, ge=0)
    max_height_mm: float | None = Field(None, ge=0)  # room above the board for a heatsink
    datacenter: bool = False  # rack with managed airflow
    facility_water: bool = False  # a facility coolant/water loop is available


Priority = Literal["balanced", "faster", "lower_power", "lower_heat", "lower_cost", "fit_memory"]


class ProjectSpec(BaseModel):
    name: str
    # What matters most when several setups meet every requirement (ranking only; it never
    # relaxes a requirement). "balanced" = least energy per input.
    priority: Priority = "balanced"
    # The use case chosen in the first-run guide (free text id, e.g. "edge-camera"); informational.
    use_case: str | None = None
    model_id: str
    device_id: str
    cooling_id: str | None = None
    # The configuration you run today. Defaults to FP32, batch 1, highest power mode.
    baseline: Config | None = None
    workload: Workload = Workload()
    constraints: Constraints = Constraints()
    environment: Environment = Environment()


class ProjectOut(BaseModel):
    id: int
    spec: ProjectSpec
    version: int = 1
    workspace_id: int | None = None
    archived: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
