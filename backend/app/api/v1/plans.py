"""Design plans, cooling options and exports."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.services import audit
from app.services import export_service as ex
from app.services import plan_service as plans

router = APIRouter(tags=["plans and exports"])


def _err(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


@router.get("/projects/{project_id}/plan-options")
def plan_options(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Every change that can go into a plan, from the last analysis plus applicable catalogue cooling."""
    p = project_for(db, ctx, project_id)
    try:
        return plans.options(db, p, ctx.workspace_id)
    except plans.PlanError as e:
        raise _err(e) from e


class PlanIn(BaseModel):
    change_ids: list[str] = Field(min_length=1, max_length=40)
    name: str = Field("Design plan", max_length=200)
    note: str = Field("", max_length=1000)


@router.post("/projects/{project_id}/plans/analyse")
def analyse_plan(project_id: int, body: PlanIn, db: Session = Depends(get_db),
                 ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:  # fmt: skip
    """Re-analyse a selection: baseline, each change alone, and all of them combined (not saved)."""
    p = project_for(db, ctx, project_id)
    try:
        out = plans.analyse(db, p, body.change_ids, ctx.workspace_id)
    except plans.PlanError as e:
        raise _err(e) from e
    audit.usage(db, "plan_analysis", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=project_id,
                detail={"changes": len(body.change_ids)})  # fmt: skip
    return out


@router.post("/projects/{project_id}/plans/trace", status_code=201)
def trace_plan(project_id: int, body: PlanIn, db: Session = Depends(get_db),
               ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:  # fmt: skip
    """Predict the execution trace of today's setup with the selected changes applied, so every
    analysis figure can be drawn for it. Stored like any other trace; `kind` stays "predicted"."""
    from app.services import twin_service as twin

    p = project_for(db, ctx, project_id)
    try:
        trace, label = plans.trace_for(db, p, body.change_ids, ctx.workspace_id)
    except plans.PlanError as e:
        raise _err(e) from e
    tid = twin.store(db, ctx.workspace_id, project_id, ctx.user_id, trace, label)
    audit.usage(db, "trace", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=project_id,
                detail={"trace_id": tid, "which": "changes", "changes": len(body.change_ids)})  # fmt: skip
    return {"id": tid, "label": label, "trace": trace.model_dump(mode="json")}


@router.post("/projects/{project_id}/plans", status_code=201)
def save_plan(project_id: int, body: PlanIn, db: Session = Depends(get_db),
              ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:  # fmt: skip
    p = project_for(db, ctx, project_id)
    try:
        row = plans.save(db, p, ctx.workspace_id, ctx.user_id, body.name, body.note, body.change_ids)
    except plans.PlanError as e:
        raise _err(e) from e
    audit.usage(db, "plan_analysis", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=project_id,
                commit=False)  # fmt: skip
    audit.audit(db, "plan.saved", workspace_id=ctx.workspace_id, user_id=ctx.user_id, target_type="plan",
                target_id=row.id, ip=ctx.ip,
                detail={"project_id": project_id, "name": row.name, "version": row.version,
                        "changes": [c["id"] for c in row.changes]})  # fmt: skip
    return plans.plan_view(row, full=True)


@router.get("/projects/{project_id}/plans")
def list_plans(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict]:
    project_for(db, ctx, project_id)
    return plans.list_plans(db, ctx.workspace_id, project_id)


@router.get("/projects/{project_id}/plans/{plan_id}")
def get_plan(project_id: int, plan_id: int, db: Session = Depends(get_db),
             ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:  # fmt: skip
    project_for(db, ctx, project_id)
    row = plans.get_plan(db, ctx.workspace_id, project_id, plan_id)
    if row is None:
        raise HTTPException(404, "plan not found")
    return plans.plan_view(row, full=True)


@router.get("/projects/{project_id}/cooling")
def cooling(project_id: int, which: Literal["baseline", "best"] = "baseline", db: Session = Depends(get_db),
            ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:  # fmt: skip
    """Every applicable catalogue cooling option evaluated for this project, ranked by stated criteria."""
    p = project_for(db, ctx, project_id)
    try:
        return plans.cooling_report(db, p, ctx.workspace_id, which)
    except (plans.PlanError, ValueError, KeyError) as e:
        raise _err(e) from e


# --- exports --------------------------------------------------------------------------------


@router.get("/projects/{project_id}/exports/catalog")
def export_catalog(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict]:
    p = project_for(db, ctx, project_id)
    return ex.catalog(db, p, ctx.workspace_id)


@router.get("/exports/optimised-models")
def optimised_models(_ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    """Optimised model files and whether the platform produces them (only real, checked ones are)."""
    return ex.optimised_catalogue()


@router.post("/reproduce/verify")
def verify_manifest(body: dict[str, Any], _ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Recompute the numbers in a reproducibility manifest with this engine and compare them."""
    from app.services.handoff_service import verify_manifest as verify

    try:
        return verify(body)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"not a usable manifest: {e}") from e


class ExportIn(BaseModel):
    kind: str = Field(max_length=40)
    plan_id: int | None = None


@router.post("/projects/{project_id}/exports", status_code=201)
def create_export(project_id: int, body: ExportIn, request: Request, db: Session = Depends(get_db),
                  ctx: Ctx = Depends(require_writer)) -> dict[str, Any]:  # fmt: skip
    p = project_for(db, ctx, project_id)
    plan = None
    if body.plan_id is not None:
        plan = plans.get_plan(db, ctx.workspace_id, project_id, body.plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")
    blobs = request.app.state.blobs
    try:
        art = ex.generate(db, p, ctx.workspace_id, body.kind, plan, blobs)
    except ex.ExportError as e:
        audit.audit(db, "export.refused", workspace_id=ctx.workspace_id, user_id=ctx.user_id, target_type="project",
                    target_id=project_id, detail={"kind": body.kind, "reason": str(e)[:300], "project_id": project_id},
                    ip=ctx.ip)  # fmt: skip
        raise _err(e) from e
    row = ex.store(db, blobs, ctx.workspace_id, project_id, body.plan_id, body.kind, art, ctx.user_id)
    secs = float(art.validation.get("execution_seconds", 0) or 0)
    if secs:
        audit.usage(db, "execution_seconds", secs, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                    project_id=project_id, detail={"what": body.kind}, commit=False)  # fmt: skip
    audit.usage(db, "export", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=project_id,
                detail={"what": body.kind, "export_id": row.id}, commit=False)  # fmt: skip
    audit.audit(db, "export.created", workspace_id=ctx.workspace_id, user_id=ctx.user_id, target_type="export",
                target_id=row.id, ip=ctx.ip,
                detail={"what": art.filename, "kind": body.kind, "label": art.label, "plan_id": body.plan_id,
                        "sha256": row.sha256, "project_id": project_id})  # fmt: skip
    return ex.view(row)


@router.get("/projects/{project_id}/exports")
def list_exports(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict]:
    project_for(db, ctx, project_id)
    return ex.list_exports(db, ctx.workspace_id, project_id)


@router.get("/projects/{project_id}/exports/{export_id}/download")
def download_export(project_id: int, export_id: int, request: Request, db: Session = Depends(get_db),
                    ctx: Ctx = Depends(require_ctx)) -> Response:  # fmt: skip
    project_for(db, ctx, project_id)
    row = ex.get_export(db, ctx.workspace_id, project_id, export_id)
    if row is None or row.blob_key is None:
        raise HTTPException(404, "export not found")
    return Response(request.app.state.blobs.get(row.blob_key), media_type=row.media_type,
                    headers={"Content-Disposition": f'attachment; filename="{row.filename}"',
                             "X-Green-Computer-Label": row.label})  # fmt: skip
