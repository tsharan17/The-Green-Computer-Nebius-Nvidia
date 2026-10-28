"""Hardware system graph per project: current graph, versions, build from uploaded design files, diff."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.schemas.system_graph import SystemGraph
from app.services import system_graph_service as svc

router = APIRouter(tags=["system graph"])


class BuildIn(BaseModel):
    artifact_ids: list[int] = Field(min_length=1, max_length=50)
    note: str = Field("", max_length=1000)


class SaveIn(BaseModel):
    graph: SystemGraph
    note: str = Field("", max_length=1000)


@router.get("/projects/{project_id}/system-graph")
def current_graph(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """The latest saved graph, or version 0 built from the device profile (not saved)."""
    return svc.current(db, ctx.workspace_id, project_for(db, ctx, project_id))


@router.get("/projects/{project_id}/system-graph/versions")
def graph_versions(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    project_for(db, ctx, project_id)
    return svc.versions(db, ctx.workspace_id, project_id)


@router.get("/projects/{project_id}/system-graph/sources")
def graph_sources(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    """Uploaded, parsed design files and reports in this workspace that the graph can take in."""
    project_for(db, ctx, project_id)
    return svc.eligible_artifacts(db, ctx.workspace_id, project_id)


@router.get("/projects/{project_id}/system-graph/diff")
def graph_diff(
    project_id: int,
    from_version: int = Query(..., alias="from", ge=0),
    to_version: int = Query(..., alias="to", ge=0),
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> dict[str, Any]:
    try:
        return svc.diff(db, ctx.workspace_id, project_for(db, ctx, project_id), from_version, to_version)
    except svc.GraphError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/projects/{project_id}/system-graph/{version}")
def graph_version(project_id: int, version: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    project_for(db, ctx, project_id)
    row = svc.get_version(db, ctx.workspace_id, project_id, version)
    if row is None:
        raise HTTPException(404, "graph version not found")
    return {**svc._row_view(row), "saved": True, "links": svc.trace_links(db, ctx.workspace_id, project_id)}


@router.post("/projects/{project_id}/system-graph/from-files", status_code=201)
def build_from_files(project_id: int, body: BuildIn, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    """Add what uploaded design files state (KiCad, Verilog, Vivado, HLS) as a new graph version."""
    p = project_for(db, ctx, project_id)
    try:
        return svc.build_from_artifacts(db, ctx.workspace_id, p, body.artifact_ids, ctx.user_id, body.note)
    except svc.GraphError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/projects/{project_id}/system-graph", status_code=201)
def save_graph(project_id: int, body: SaveIn, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:
    """Save an edited graph as a new version (validated: unique ids, existing parents and ends)."""
    p = project_for(db, ctx, project_id)
    try:
        return svc.save(db, ctx.workspace_id, p, body.graph, ctx.user_id, body.note)
    except svc.GraphError as e:
        raise HTTPException(422, str(e)) from e
