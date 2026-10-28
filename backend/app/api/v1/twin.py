"""Digital twin v1: execution traces (predicted now; measured traces from execution backends)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.render import figures
from app.schemas.results import Config
from app.services import audit
from app.services import twin_service as twin

router = APIRouter(tags=["digital twin"])
_FIGURE_CACHE: dict[tuple[int, int, str, str], bytes] = {}  # stored traces never change, so a figure is drawn once
_FIGURE_CACHE_MAX = 96


@router.post("/projects/{project_id}/traces", status_code=201)
def create_trace(
    project_id: int,
    which: Literal["baseline", "best", "custom"] = "baseline",
    config: Config | None = None,
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> dict[str, Any]:
    """Predict the operator-level execution of one batch for the current setup (`baseline`), the
    recommended setup of the last analysis (`best`) or a given configuration (`custom`)."""
    p = project_for(db, ctx, project_id)
    try:
        cfg, label = twin.config_for(db, p, which, config, ctx.workspace_id)
        trace = twin.compute_trace(db, p.spec, cfg, ctx.workspace_id)
    except (twin.TraceError, KeyError) as e:
        raise HTTPException(422, str(e).strip("'")) from e
    tid = twin.store(db, ctx.workspace_id, project_id, ctx.user_id, trace, label)
    audit.usage(db, "trace", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=project_id,
                detail={"trace_id": tid, "which": which})  # fmt: skip
    return {"id": tid, "label": label, "trace": trace.model_dump(mode="json")}


@router.get("/projects/{project_id}/traces")
def list_traces(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict]:
    project_for(db, ctx, project_id)
    return twin.list_traces(db, ctx.workspace_id, project_id)


@router.get("/projects/{project_id}/traces/{trace_id}")
def get_trace(project_id: int, trace_id: int, db: Session = Depends(get_db),
              ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:  # fmt: skip
    project_for(db, ctx, project_id)
    row = twin.get_trace(db, ctx.workspace_id, project_id, trace_id)
    if row is None:
        raise HTTPException(404, "trace not found")
    return {"id": row.id, "label": row.label, "created_at": row.created_at.isoformat(), "trace": row.trace}


@router.get("/projects/{project_id}/traces/{trace_id}/export")
def export_trace(
    project_id: int,
    trace_id: int,
    format: Literal["json", "csv"] = "json",
    table: Literal["events", "memory", "tensors"] = Query("events"),
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> Response:
    project_for(db, ctx, project_id)
    row = twin.get_trace(db, ctx.workspace_id, project_id, trace_id)
    if row is None:
        raise HTTPException(404, "trace not found")
    audit.usage(db, "export", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=project_id,
                detail={"what": f"trace.{format}", "trace_id": trace_id}, commit=False)  # fmt: skip
    audit.audit(db, "export.created", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="trace", target_id=trace_id,
                detail={"what": f"trace {table}.{format}" if format == "csv" else "trace.json",
                        "project_id": project_id}, ip=ctx.ip)  # fmt: skip
    base = f"trace-{project_id}-{trace_id}-{row.kind}"
    if format == "json":
        return Response(json.dumps(row.trace, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{base}.json"'})  # fmt: skip
    return Response(twin.trace_csv(row.trace, table), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{base}-{table}.csv"'})  # fmt: skip


@router.get("/projects/{project_id}/traces/{trace_id}/figures/{name}")
def trace_figure(
    project_id: int,
    trace_id: int,
    name: str,
    theme: Literal["light", "dark"] = "light",
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> Response:
    """A matplotlib/seaborn figure (PNG) drawn from a stored trace's own numbers."""
    project_for(db, ctx, project_id)
    if name not in figures.FIGURES:
        raise HTTPException(404, f"unknown figure; choose one of {', '.join(sorted(figures.FIGURES))}")
    row = twin.get_trace(db, ctx.workspace_id, project_id, trace_id)
    if row is None:
        raise HTTPException(404, "trace not found")
    key = (project_id, trace_id, name, theme)
    png = _FIGURE_CACHE.get(key)
    if png is None:
        try:
            png = figures.render(name, row.trace, theme)
        except figures.FigureError as e:
            raise HTTPException(422, str(e)) from e
        if len(_FIGURE_CACHE) >= _FIGURE_CACHE_MAX:
            _FIGURE_CACHE.pop(next(iter(_FIGURE_CACHE)))
        _FIGURE_CACHE[key] = png
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
