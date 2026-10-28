"""Real execution of a project's model on an available backend (local CPU required, GPU optional)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.providers.execution import backend_infos
from app.security import limit_analyse
from app.services import audit
from app.services import execution_service as ex

router = APIRouter(tags=["execution"])


@router.get("/execution/backends")
def backends(_ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    return [b.as_dict() for b in backend_infos()]


class RunIn(BaseModel):
    backend: str = "local-cpu"
    batch: int = Field(1, ge=1, le=64)
    runs: int = Field(30, ge=3, le=500)
    warmup: int = Field(5, ge=0, le=50)


@router.post("/projects/{project_id}/executions", status_code=202)
def run(project_id: int, body: RunIn, request: Request, db: Session = Depends(get_db),
        ctx: Ctx = Depends(require_writer), _rl: None = Depends(limit_analyse)) -> dict[str, Any]:  # fmt: skip
    p = project_for(db, ctx, project_id)
    try:
        row = ex.start(request.app.state.session_factory, db, p, ctx.workspace_id, ctx.user_id, body.backend,
                       body.batch, body.runs, body.warmup, request.app.state.blobs)  # fmt: skip
    except ex.ExecutionError as e:
        raise HTTPException(409, str(e)) from e
    audit.audit(db, "execution.started", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="execution", target_id=row.id,
                detail={"project_id": project_id, "backend": body.backend, "batch": body.batch}, ip=ctx.ip)  # fmt: skip
    return ex.view(row)


@router.get("/projects/{project_id}/executions")
def list_runs(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict]:
    project_for(db, ctx, project_id)
    return ex.list_jobs(db, ctx.workspace_id, project_id)


@router.get("/projects/{project_id}/executions/{job_id}")
def get_run(project_id: int, job_id: int, db: Session = Depends(get_db),
            ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:  # fmt: skip
    project_for(db, ctx, project_id)
    row = ex.get_job(db, ctx.workspace_id, project_id, job_id)
    if row is None:
        raise HTTPException(404, "run not found")
    return ex.view(row)
