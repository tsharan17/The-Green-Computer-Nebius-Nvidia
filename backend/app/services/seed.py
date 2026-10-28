"""Example robotics project, re-seeded on startup so the demo always opens with data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import repository as repo
from app.db.models import ProjectRow
from app.schemas.project import Constraints, ProjectSpec, Workload

EXAMPLE_NAME = "Example: YOLOv8s on a robot (30 FPS, 55 W, 40 °C)"


def example_spec() -> ProjectSpec:
    return ProjectSpec(
        name=EXAMPLE_NAME,
        model_id="yolov8s",
        device_id="jetson-orin-nx",
        cooling_id="passive_small",
        workload=Workload(fps=30, ambient_c=40, hours_per_day=24),
        constraints=Constraints(max_power_w=55, min_accuracy=0.90, max_latency_ms=100),
    )


def seed_example(db: Session) -> None:
    exists = db.scalars(select(ProjectRow).where(ProjectRow.name == EXAMPLE_NAME)).first()
    if exists is None:
        repo.create_project(db, example_spec())
