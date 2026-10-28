"""Guided start, demo projects, project health and global search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.data_loader import load_devices
from app.db import repository as repo
from app.schemas.project import Priority
from app.services import audit
from app.services import evaluation_service as svc
from app.services import guide_service as guide
from app.services.model_lookup import visible_models
from app.services.project_service import SpecError, validate_refs

router = APIRouter(tags=["guide"])


@router.get("/guide/use-cases")
def use_cases(_ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    return {"use_cases": guide.USE_CASES, "priorities": guide.PRIORITIES}


class StartIn(BaseModel):
    use_case: str | None = None
    name: str = Field("", max_length=200)
    model_id: str | None = None
    device_id: str | None = None
    priority: Priority = "balanced"
    fps: float | None = Field(None, gt=0, le=100000)
    ambient_c: float | None = Field(None, ge=-40, le=85)
    max_power_w: float | None = Field(None, gt=0, le=5000)
    max_latency_ms: float | None = Field(None, gt=0)
    analyse: bool = True


def _analyse(db: Session, ctx: Ctx, pid: int) -> dict[str, Any]:
    p = repo.get_project(db, pid, ctx.workspace_id)
    assert p is not None
    res = svc.optimise_project(db, pid, p.spec, ctx.workspace_id, ctx.user_id)
    audit.usage(db, "analysis", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=pid,
                detail={"kind": "optimise", "via": "guide"})  # fmt: skip
    return {"outcome": res.baseline.feasibility.value,
            "best_outcome": res.best.feasibility.value if res.best else None}  # fmt: skip


@router.post("/guide/start", status_code=201)
def start(body: StartIn, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    """Create a project from a use case (or from scratch) and, by default, analyse it at once."""
    case = next((c for c in guide.USE_CASES if c["id"] == body.use_case), None)
    if body.use_case and case is None:
        raise HTTPException(422, f"unknown use case '{body.use_case}'")
    base = dict(case or {"id": None, "title": "My project", "model_id": "mobilenet-v2", "device_id": "jetson-orin-nano",
                         "fps": 30, "ambient_c": 25, "max_power_w": 15, "max_latency_ms": None, "priority": "balanced"})  # fmt: skip
    for k in ("model_id", "device_id", "fps", "ambient_c", "max_power_w", "max_latency_ms"):
        v = getattr(body, k)
        if v is not None:
            base[k] = v
    base["priority"] = body.priority
    devices = load_devices(ctx.workspace_id)
    if base["device_id"] not in devices:
        raise HTTPException(422, f"unknown device '{base['device_id']}'")
    if base["model_id"] not in {m.id for m in visible_models(db, ctx.workspace_id)}:
        raise HTTPException(422, f"unknown model '{base['model_id']}'")
    hw = devices[base["device_id"]]
    spec = guide.spec_for(base, body.name.strip() or base["title"], hw)
    try:
        validate_refs(db, spec, ctx.workspace_id)
    except SpecError as e:
        raise HTTPException(422, str(e)) from e
    p = repo.create_project(db, spec, ctx.workspace_id, ctx.user_id)
    audit.audit(db, "project.created", workspace_id=ctx.workspace_id, user_id=ctx.user_id, target_type="project",
                target_id=p.id, detail={"name": spec.name, "via": "guide", "use_case": base.get("id")}, ip=ctx.ip)  # fmt: skip
    out: dict[str, Any] = {"project": p.model_dump(mode="json")}
    if body.analyse:
        out["analysis"] = _analyse(db, ctx, p.id)
    return out


@router.post("/guide/demo", status_code=201)
def demo(db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    """Add five analysed example projects (MCU, edge camera, FPGA, GPU server, cloud). Idempotent."""
    have = {p.spec.name for p in repo.list_projects(db, ctx.workspace_id, include_archived=True)}
    made = []
    for case in guide.USE_CASES:
        if case["id"] == "robot-vision":
            continue  # every account already has the robot example
        name = f"Demo: {case['title']}"
        if name in have:
            continue
        p = repo.create_project(db, guide.spec_for(case, name), ctx.workspace_id, ctx.user_id)
        made.append({"project_id": p.id, "name": name, **_analyse(db, ctx, p.id)})
    audit.audit(db, "workspace.demo_added", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                detail={"projects": len(made)}, ip=ctx.ip)  # fmt: skip
    return {"created": made}


@router.get("/projects-health")
def projects_health(db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    return guide.health(db, ctx.workspace_id)


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=100), db: Session = Depends(get_db),
           ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:  # fmt: skip
    return guide.search(db, ctx.workspace_id, q)
