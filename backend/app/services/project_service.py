"""Project validation shared by the REST API and the agent tools."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_loader import load_devices, load_models
from app.db import repository as repo
from app.schemas.project import ProjectSpec


class SpecError(ValueError):
    """The project spec refers to something that does not exist or is unsupported."""


def validate_refs(db: Session, spec: ProjectSpec, workspace_id: int | None = None) -> None:
    devices = load_devices(workspace_id)
    if spec.model_id not in load_models() and repo.get_custom_model(
        db, spec.model_id, workspace_id
    ) is None:
        raise SpecError(f"unknown model '{spec.model_id}'")
    if spec.device_id not in devices:
        raise SpecError(f"unknown device '{spec.device_id}'")
    hw = devices[spec.device_id]
    if spec.cooling_id and spec.cooling_id not in {c.id for c in hw.cooling_options}:
        raise SpecError(f"unknown cooling '{spec.cooling_id}' for {hw.id}")
    b = spec.baseline
    if b is not None:
        if b.power_mode_id not in {m.id for m in hw.power_modes}:
            raise SpecError(f"unknown power mode '{b.power_mode_id}' for {hw.id}")
        if b.cooling_id not in {c.id for c in hw.cooling_options}:
            raise SpecError(f"unknown cooling '{b.cooling_id}' for {hw.id}")
        if hw.peak_tflops.get(b.precision, 0) <= 0:
            raise SpecError(f"{hw.id} does not support {b.precision}")
