"""Deeper physics per project (multi-node thermal, memory hierarchy, runtime effects), the evidence
behind each headline number, and calibration quality per device. Read-only; nothing is stored."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx
from app.data_loader import load_devices
from app.services import physics_service as svc
from app.services.twin_service import TraceError

router = APIRouter(tags=["physics and evidence"])


@router.get("/projects/{project_id}/physics")
def project_physics(
    project_id: int,
    which: Literal["baseline", "best"] = "baseline",
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> dict[str, Any]:
    """Multi-node thermal attribution (labelled against the lumped decision model and any measured
    temperature), the memory hierarchy, and runtime effects outside the headline latency."""
    p = project_for(db, ctx, project_id)
    try:
        return svc.physics(db, ctx.workspace_id, p, which)
    except TraceError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/projects/{project_id}/evidence")
def project_evidence(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Every headline metric with its band, provenance, the profile facts and inputs behind it, and
    whether a calibration was applied."""
    return svc.evidence(db, ctx.workspace_id, project_for(db, ctx, project_id))


@router.get("/calibration/quality")
def calibration_quality(device_id: str, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    if device_id not in load_devices(ctx.workspace_id):
        raise HTTPException(404, "device not found")
    return svc.calibration_quality(db, ctx.workspace_id, device_id)
